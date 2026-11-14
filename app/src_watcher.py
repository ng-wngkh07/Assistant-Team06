"""Managed operator src/ filesystem watcher and incremental single-file indexer."""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app import file_lock as _file_lock
from app.config import (
    DATA_DIR,
    DB_PATH,
    DEFAULT_EMBED_MODEL,
    SRC_DIR,
    SRC_WATCH_DEBOUNCE_SECONDS,
    SRC_WATCH_POLL_SECONDS,
    is_src_auto_watch_enabled,
)
from app.db import secure_dir_permissions, secure_file_permissions
import inspect
from app.gpu_lock import GPUBusyError, IndexingCancelledError
from app.indexer import (
    CorpusBusyError,
    FileModifiedDuringIndexingError,
    KnowledgeIndexer,
)
from app.pdf_extractor import PDFExtractor
from app.runtime_health import runtime_failure_message
from app.vision_ocr import BACKEND_OLLAMA_VISION, VisionOCRManager, classify_ocr_failure

logger = logging.getLogger(__name__)


def _is_auto_ocr_enabled() -> bool:
    raw = os.getenv("STUDY_SRC_AUTO_OCR", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")

_IGNORE_TEMP_SUFFIXES = (
    ".tmp",
    ".part",
    ".crdownload",
    ".download",
    ".partial",
    ".swp",
    ".swx",
    ".bak",
    ".lock",
)
_USABLE_DB_STATUSES = ("indexed", "ocr_partial_reviewed", "scanned_unocred")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_ignored_name(name: str) -> bool:
    if not name:
        return True
    if name.startswith(".") or name.startswith("~$") or name.startswith("~"):
        return True
    if name.endswith("~"):
        return True
    lower = name.lower()
    return any(lower.endswith(suffix) for suffix in _IGNORE_TEMP_SUFFIXES)


def _sanitize_error(
    err: Optional[Any],
    *,
    reason_code: str = "extract_error",
    paths_to_redact: Tuple[str, ...] = (),
) -> Tuple[str, str]:
    """Map watcher/indexing failures to bounded user-safe messages without host paths or tracebacks."""
    if isinstance(err, GPUBusyError):
        return (
            "gpu_busy",
            "GPU hoặc tiến trình mô hình đang bận; sẽ tự động thử lại khi sẵn sàng.",
        )
    if isinstance(err, CorpusBusyError):
        return (
            "corpus_busy",
            "Chỉ mục đang được tác vụ khác cập nhật; chờ lượt tiếp theo.",
        )
    if isinstance(err, FileModifiedDuringIndexingError):
        return (
            "file_modified",
            "Tệp nguồn đang tiếp tục thay đổi; chờ tệp ổn định trước khi lập chỉ mục.",
        )
    if isinstance(err, PermissionError):
        return (
            "permission_denied",
            "Không đủ quyền đọc tệp nguồn trong thư mục tài liệu.",
        )

    raw_text = str(err or "").strip()
    lower = raw_text.lower()
    if reason_code == "ocr_timeout" or "timeout" in lower or "timed out" in lower:
        return (
            "ocr_timeout",
            "OCR cục bộ quá thời gian cho phép trên trang tài liệu; sẽ thử lại theo lịch lùi.",
        )
    if reason_code == "ocr_truncated" or "cắt cụt" in lower or "length" in lower:
        return (
            "ocr_truncated",
            "Phản hồi OCR chạm giới hạn độ dài token và bị cắt cụt; giữ nguyên trang chờ kiểm tra.",
        )
    if reason_code == "ocr_invalid_json":
        return (
            "ocr_invalid_json",
            "Đầu ra OCR cục bộ không phải JSON hợp lệ; giữ nguyên trang chưa đưa vào chỉ mục.",
        )
    if reason_code == "ocr_ambiguous":
        return (
            "ocr_ambiguous",
            "Bản nhận diện OCR còn vùng mơ hồ hoặc khó đọc; chuyển trạng thái HOLD chờ kiểm duyệt.",
        )
    if reason_code == "ocr_unavailable":
        return (
            "ocr_unavailable",
            "Mô hình hoặc môi trường OCR cục bộ hiện chưa sẵn sàng trên máy.",
        )
    if reason_code == "ocr_runtime_error":
        return (
            "ocr_runtime_error",
            "Lỗi chạy bộ nhận diện OCR cục bộ; giữ nguyên trạng thái an toàn để thử lại sau.",
        )
    if reason_code == "empty_document" or "trống" in lower:
        return (
            "empty_document",
            "Tệp nguồn trống hoặc không có nội dung văn bản để lập chỉ mục.",
        )
    if any(tok in lower for tok in ("digest", "thay thế âm thầm", "embedding", "ollama", "mlx", "metal", "nanobind")):
        msg = runtime_failure_message(raw_text) if any(k in lower for k in ("metal", "nanobind", "sqlite", "database")) else (
            "Mô hình vector cục bộ không khớp hoặc chưa sẵn sàng; không thay thế mô hình tự động."
        )
        return ("embedding_error", msg)
    if any(tok in lower for tok in ("pdf", "trích xuất", "extract", "corrupt", "hỏng", "format", "fz_error", "mupdf")):
        return (
            "extract_error",
            "Không thể đọc hoặc trích xuất tài liệu (tệp có thể bị hỏng hoặc sai định dạng).",
        )

    cleaned = raw_text
    for p in paths_to_redact:
        if p:
            cleaned = cleaned.replace(p, "<src>")
    if not cleaned or "traceback" in lower or "/" in cleaned or "\\" in cleaned:
        return (
            reason_code or "watch_error",
            "Không thể xử lý tài liệu tự động; kiểm tra định dạng tệp nguồn.",
        )
    return (reason_code or "watch_error", cleaned[:240])


class SrcWatcher:
    """Polls operator SRC_DIR safely, debounces writes, and incrementally indexes new sources."""

    def __init__(
        self,
        src_dir: Path = SRC_DIR,
        db_path: Path = DB_PATH,
        data_dir: Path = DATA_DIR,
        *,
        poll_interval: float = SRC_WATCH_POLL_SECONDS,
        debounce_seconds: float = SRC_WATCH_DEBOUNCE_SECONDS,
        max_retries: int = 3,
        base_backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 120.0,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
        state_path: Optional[Path] = None,
        enabled_override: Optional[bool] = None,
        auto_ocr: Optional[bool] = None,
        ocr_batch_pages: int = 2,
        ocr_backend: str = BACKEND_OLLAMA_VISION,
    ) -> None:
        self.src_dir = Path(src_dir)
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.poll_interval = max(0.02, float(poll_interval))
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.max_retries = max(1, int(max_retries))
        self.base_backoff_seconds = max(0.05, float(base_backoff_seconds))
        self.max_backoff_seconds = max(self.base_backoff_seconds, float(max_backoff_seconds))
        self.embed_model = embed_model
        self.state_path = Path(state_path) if state_path else (self.data_dir / "runtime" / "src_watcher_state.json")
        self._enabled_override = enabled_override
        self._auto_ocr_override = auto_ocr
        self.ocr_batch_pages = max(1, int(ocr_batch_pages))
        self.ocr_backend = ocr_backend

        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._op_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stopping = False
        self._last_scan_at: Optional[str] = None
        self._last_scan_duration_ms: Optional[float] = None
        self._last_ocr_available: Optional[bool] = None
        self._last_error: Optional[str] = None
        self._last_error_code: Optional[str] = None
        self._items: Dict[str, Dict[str, Any]] = {}
        self._last_state_stat: Optional[Tuple[int, int]] = None
        self._rescan_requested = False
        self._load_state()

    @property
    def auto_ocr(self) -> bool:
        if self._auto_ocr_override is not None:
            return bool(self._auto_ocr_override)
        return _is_auto_ocr_enabled()

    def is_enabled(self) -> bool:
        if self._enabled_override is not None:
            return bool(self._enabled_override)
        return is_src_auto_watch_enabled()

    def _runtime_dir(self) -> Path:
        runtime_dir = self.state_path.parent
        runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        secure_dir_permissions(runtime_dir)
        return runtime_dir

    def _load_state(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            st = self.state_path.stat()
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return

        with self._state_lock:
            raw_items = payload.get("items")
            if isinstance(raw_items, dict):
                restored: Dict[str, Dict[str, Any]] = {}
                now = time.time()
                for rel, entry in raw_items.items():
                    if not isinstance(entry, dict):
                        continue
                    item = dict(entry)
                    item["rel_path"] = str(item.get("rel_path") or rel)
                    # Crash recovery: never leave stale 'processing' or 'ocr_running' stranded after restart.
                    if item.get("status") in ("processing", "ocr_running"):
                        prev_st = item.get("status")
                        item["status"] = "needs_ocr" if prev_st == "ocr_running" else "pending"
                        item["reason_code"] = "recovered_after_restart"
                        item["message"] = "Khôi phục trạng thái chờ xử lý sau khi khởi động lại."
                        item.setdefault("stable_since", now)
                    restored[str(rel)] = item
                self._items = restored
            self._last_scan_at = payload.get("last_scan_at")
            self._last_scan_duration_ms = payload.get("last_scan_duration_ms")
            self._last_ocr_available = payload.get("last_ocr_available")
            self._last_error = payload.get("last_error")
            self._last_error_code = payload.get("last_error_code")
            self._last_state_stat = (int(st.st_mtime_ns), int(st.st_size))

    def _refresh_state_if_newer(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            st = self.state_path.stat()
            stat_key = (int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            return
        if stat_key != self._last_state_stat:
            self._load_state()

    def _save_state(self) -> None:
        runtime_dir = self._runtime_dir()
        with self._state_lock:
            payload = {
                "version": 1,
                "last_scan_at": self._last_scan_at,
                "last_scan_duration_ms": self._last_scan_duration_ms,
                "last_ocr_available": self._last_ocr_available,
                "last_error": self._last_error,
                "last_error_code": self._last_error_code,
                "items": self._items,
            }
            encoded = json.dumps(payload, ensure_ascii=False, indent=2)

        tmp_name = f"{self.state_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp_path = runtime_dir / tmp_name
        tmp_path.write_text(encoded, encoding="utf-8")
        secure_file_permissions(tmp_path)
        os.replace(tmp_path, self.state_path)
        secure_file_permissions(self.state_path)
        try:
            st = self.state_path.stat()
            self._last_state_stat = (int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            pass

    def _ingest_lock_active(self) -> bool:
        """Yield politely if UI study-material ingest owns runtime/ingest.lock."""
        lock_path = self.data_dir / "runtime" / "ingest.lock"
        if not lock_path.exists():
            return False
        fd = None
        try:
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
            _file_lock.flock(fd, _file_lock.LOCK_EX | _file_lock.LOCK_NB)
            _file_lock.flock(fd, _file_lock.LOCK_UN)
            return False
        except (BlockingIOError, OSError):
            return True
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _try_acquire_scan_lock(self) -> Optional[int]:
        runtime_dir = self._runtime_dir()
        lock_path = runtime_dir / "src_watcher.scan.lock"
        fd = None
        try:
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
            _file_lock.flock(fd, _file_lock.LOCK_EX | _file_lock.LOCK_NB)
            return fd
        except (BlockingIOError, OSError):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            return None

    @staticmethod
    def _release_scan_lock(fd: Optional[int]) -> None:
        if fd is None:
            return
        try:
            _file_lock.flock(fd, _file_lock.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def _discover_candidates(self) -> Dict[str, Tuple[Path, int, int]]:
        """Discover non-ignored supported files inside src_dir without following symlink escapes."""
        candidates: Dict[str, Tuple[Path, int, int]] = {}
        if not self.src_dir.is_dir():
            return candidates

        resolved_src = self.src_dir.resolve()
        visited_dirs = set()
        for root, dirs, files in os.walk(self.src_dir, followlinks=False):
            root_path = Path(root)
            try:
                root_stat = root_path.stat()
                dir_key = (root_stat.st_dev, root_stat.st_ino)
            except OSError:
                dirs[:] = []
                continue
            if dir_key in visited_dirs:
                dirs[:] = []
                continue
            visited_dirs.add(dir_key)

            safe_dirs = []
            for d in sorted(dirs):
                if d.startswith(".") or d.startswith("~"):
                    continue
                cand_dir = root_path / d
                if cand_dir.is_symlink() or not PDFExtractor.is_safe_path(cand_dir, resolved_src):
                    continue
                safe_dirs.append(d)
            dirs[:] = safe_dirs

            for fname in sorted(files):
                if _is_ignored_name(fname):
                    continue
                fpath = root_path / fname
                if not PDFExtractor.is_safe_path(fpath, resolved_src):
                    continue
                if fpath.suffix.lower() not in PDFExtractor.SUPPORTED_EXTENSIONS:
                    continue
                try:
                    st = fpath.stat()
                except OSError:
                    continue
                rel = str(fpath.relative_to(self.src_dir))
                candidates[rel] = (fpath, int(st.st_size), int(st.st_mtime_ns))
        return candidates

    def _get_existing_indexed_map(self) -> Dict[str, Dict[str, Any]]:
        if not self.db_path.is_file():
            return {}
        try:
            indexer = KnowledgeIndexer(db_path=self.db_path, src_dir=self.src_dir)
            return indexer.get_indexed_files_map()
        except Exception:
            return {}

    def _redact_paths(self) -> Tuple[str, ...]:
        return (
            str(self.src_dir),
            str(self.src_dir.resolve()) if self.src_dir.exists() else "",
            str(self.data_dir),
            str(self.db_path),
            str(self.state_path),
        )

    def _compute_backoff(self, attempts: int) -> float:
        exp = max(0, attempts - 1)
        return min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** exp))

    def _ocr_cache_dir(self) -> Path:
        from app import vision_ocr
        default_cache = DATA_DIR / "ocr_cache"
        if self.data_dir != DATA_DIR and vision_ocr.CACHE_DIR == default_cache:
            return self.data_dir / "ocr_cache"
        return Path(vision_ocr.CACHE_DIR) if isinstance(vision_ocr.CACHE_DIR, Path) else (self.data_dir / "ocr_cache")

    def _can_run_local_ocr(self) -> bool:
        if not self.auto_ocr:
            return False
        try:
            return bool(VisionOCRManager.ocr_available(self.ocr_backend))
        except Exception:
            return False

    @staticmethod
    def _render_single_page_bytes(fpath: Path, page_num: int) -> bytes:
        ext = fpath.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            return fpath.read_bytes()
        import pymupdf
        doc = pymupdf.open(str(fpath))
        try:
            if page_num < 1 or page_num > len(doc):
                raise ValueError(f"Trang {page_num} vượt quá phạm vi tài liệu ({len(doc)} trang).")
            pix = doc[page_num - 1].get_pixmap(dpi=150)
            return pix.tobytes("png")
        finally:
            doc.close()

    def _collect_review_snapshot(
        self,
        indexer: KnowledgeIndexer,
    ) -> Tuple[Dict[Tuple[str, int], Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]]]:
        review_states = indexer._load_verified_page_reviews()
        per_doc: Dict[Tuple[str, str], Dict[str, Any]] = {}

        for (norm_fn, p_num), state in review_states.items():
            s_hash = str(state.get("source_hash") or "")
            if not s_hash or not state.get("is_verified"):
                continue
            bucket = per_doc.setdefault(
                (norm_fn, s_hash),
                {"verified_pages": {}, "captured_entries": {}, "fingerprint": ""},
            )
            bucket["verified_pages"][p_num] = state

        cache_dirs: List[Any] = []
        seen: set[str] = set()
        for cand in [self._ocr_cache_dir(), *indexer._ocr_cache_dirs()]:
            if isinstance(cand, Path):
                if not cand.exists():
                    continue
                resolved = str(cand.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                cache_dirs.append(cand)
            elif hasattr(cand, "glob"):
                cache_dirs.append(cand)

        latest_uncaptured_order: Dict[Tuple[str, str, int], Any] = {}
        for cdir in cache_dirs:
            try:
                files = cdir.glob("*.json")
            except Exception:
                continue
            for cp in files:
                try:
                    entry = json.loads(cp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                rel = entry.get("source_relpath") or entry.get("filename")
                s_hash = str(entry.get("source_hash") or entry.get("source_sha256") or "")
                p_num = entry.get("page_num") or entry.get("page")
                if not rel or not s_hash or type(p_num) is not int or p_num < 1:
                    continue
                norm_fn = str(Path(rel))
                if entry.get("is_verified") and not entry.get("needs_review"):
                    continue
                order = (entry.get("created_at") or "", getattr(cp, "name", str(cp)))
                key3 = (norm_fn, s_hash, p_num)
                if key3 not in latest_uncaptured_order or order > latest_uncaptured_order[key3]:
                    latest_uncaptured_order[key3] = order
                    bucket = per_doc.setdefault(
                        (norm_fn, s_hash),
                        {"verified_pages": {}, "captured_entries": {}, "fingerprint": ""},
                    )
                    bucket["captured_entries"][p_num] = entry

        for _, bucket in per_doc.items():
            ver_tuples = []
            for p_num, st in sorted(bucket["verified_pages"].items()):
                txt_sha = hashlib.sha256((st.get("text") or "").encode("utf-8")).hexdigest()
                ver_tuples.append([
                    p_num,
                    bool(st.get("is_verified")),
                    st.get("completeness", "complete"),
                    bool(st.get("is_blank")),
                    txt_sha,
                ])
            bucket["fingerprint"] = (
                hashlib.sha256(json.dumps(ver_tuples, sort_keys=True).encode("utf-8")).hexdigest()
                if ver_tuples
                else ""
            )

        return review_states, per_doc

    @staticmethod
    def _pages_needing_ocr(
        indexer: KnowledgeIndexer,
        db_row: Dict[str, Any],
        fpath: Path,
    ) -> Tuple[int, List[int]]:
        total_pages = int(db_row.get("total_pages") or 1)
        is_scanned = bool(db_row.get("is_scanned"))
        db_status = db_row.get("status")
        pages_meta: List[Dict[str, Any]] = []
        try:
            with indexer.get_connection() as conn:
                qrow = conn.execute(
                    "SELECT quality_metadata FROM document_source_quality WHERE doc_id = ?",
                    (db_row["id"],),
                ).fetchone()
            if qrow and qrow["quality_metadata"]:
                meta = json.loads(qrow["quality_metadata"])
                pages_meta = meta.get("pages") or []
        except Exception:
            pages_meta = []

        needing: List[int] = []
        if pages_meta:
            for p in pages_meta:
                p_num = p.get("page_num")
                if type(p_num) is not int or p_num < 1:
                    continue
                if not p.get("has_text") or (is_scanned and p.get("needs_review")):
                    needing.append(p_num)
        elif is_scanned or db_status in ("scanned_unocred", "ocr_partial_reviewed"):
            needing = list(range(1, total_pages + 1))
        elif fpath.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and db_status == "scanned_unocred":
            needing = [1]
        return total_pages, sorted(set(needing))

    def _sync_vectors_for_doc(
        self,
        indexer: KnowledgeIndexer,
        item: Dict[str, Any],
        db_row: Dict[str, Any],
        now_wall: float,
    ) -> Tuple[bool, bool, bool]:
        doc_id = db_row.get("id")
        if doc_id is None:
            return False, False, False
        with indexer.get_connection() as conn:
            total_c, compat_c = indexer._count_compatible_vectors(
                conn, doc_id=doc_id, require_1024=True
            )
        if total_c == 0:
            item["vectors_ready"] = False
            item["embedding_status"] = "none"
            item.pop("vector_error", None)
            return False, False, False
        if compat_c >= total_c:
            item["vectors_ready"] = True
            item["embedding_status"] = "vectorized"
            item.pop("vector_error", None)
            item.pop("vector_next_retry_at", None)
            return True, False, False

        if not self.embed_model:
            item["vectors_ready"] = False
            item["embedding_status"] = "partial" if compat_c > 0 else "fts_only"
            return False, False, False

        if now_wall < float(item.get("vector_next_retry_at") or 0.0):
            item["vectors_ready"] = False
            item["embedding_status"] = "partial" if compat_c > 0 else "vector_pending"
            return False, False, False

        try:
            indexer.backfill_missing_embeddings(
                embed_model=self.embed_model,
                doc_id=doc_id,
                cancel_event=self._stop_event,
                gpu_timeout=0.5,
            )
            with indexer.get_connection() as conn:
                _, compat_after = indexer._count_compatible_vectors(
                    conn, doc_id=doc_id, require_1024=True
                )
            ready = compat_after >= total_c
            item["vectors_ready"] = ready
            item["embedding_status"] = (
                "vectorized" if ready else ("partial" if compat_after > 0 else "vector_pending")
            )
            if ready:
                item.pop("vector_error", None)
                item.pop("vector_next_retry_at", None)
            return ready, False, bool(ready and compat_c < total_c)
        except IndexingCancelledError:
            raise
        except GPUBusyError as exc:
            _, msg = _sanitize_error(exc, paths_to_redact=self._redact_paths())
            item["vectors_ready"] = False
            item["embedding_status"] = "vector_pending"
            item["vector_error"] = msg
            item["vector_next_retry_at"] = now_wall + min(1.0, self.poll_interval)
            return False, True, False
        except Exception as exc:
            _, msg = _sanitize_error(exc, reason_code="embedding_error", paths_to_redact=self._redact_paths())
            item["vectors_ready"] = False
            item["embedding_status"] = "partial" if compat_c > 0 else "vector_pending"
            item["vector_error"] = msg
            item["vector_next_retry_at"] = now_wall + self._compute_backoff(1)
            return False, False, False

    def _reconcile_ocr_pages(
        self,
        *,
        item: Dict[str, Any],
        items: Dict[str, Dict[str, Any]],
        rel_path: str,
        fpath: Path,
        current_hash: str,
        db_row: Dict[str, Any],
        indexer: KnowledgeIndexer,
        doc_snapshot: Dict[str, Any],
        ocr_available: bool,
        ocr_budget_remaining: int,
        now_wall: float,
    ) -> Tuple[int, bool, bool]:
        """Run bounded per-page OCR & compute durable page progress.

        Returns (ocr_budget_remaining, waiting_gpu, needs_index_refresh).
        """
        total_pages, pages_needing_ocr = self._pages_needing_ocr(indexer, db_row, fpath)
        item["total_pages"] = total_pages
        item["is_scanned"] = bool(db_row.get("is_scanned"))
        item["extracted_pages_count"] = int(db_row.get("extracted_pages_count") or 0)

        verified_pages: Dict[int, Any] = doc_snapshot.get("verified_pages") or {}
        captured_entries: Dict[int, Dict[str, Any]] = doc_snapshot.get("captured_entries") or {}

        if not pages_needing_ocr:
            item.pop("ocr_progress", None)
            return ocr_budget_remaining, False, False

        prev_prog = item.get("ocr_progress") or {}
        if prev_prog.get("source_hash") == current_hash and isinstance(prev_prog.get("page_states"), dict):
            page_states: Dict[str, Dict[str, Any]] = dict(prev_prog["page_states"])
        else:
            page_states = {}

        mgr = VisionOCRManager(backend=self.ocr_backend, cache_dir=self._ocr_cache_dir())
        waiting_gpu = False

        for p_num in pages_needing_ocr:
            if self._stop_event.is_set():
                break
            p_key = str(p_num)
            if p_num in verified_pages:
                ver_info = verified_pages[p_num]
                comp = ver_info.get("completeness", "complete")
                page_states[p_key] = {
                    "status": "blank_verified" if ver_info.get("is_blank") else ("partial_verified" if comp != "complete" else "verified"),
                    "completeness": comp,
                    "is_blank": bool(ver_info.get("is_blank")),
                    "updated_at": _utc_now_iso(),
                }
                continue

            if p_num in captured_entries:
                c_entry = captured_entries[p_num]
                c_status = "hold" if c_entry.get("has_uncertainty") else "captured_pending_review"
                page_states[p_key] = {
                    "status": c_status,
                    "cache_key": c_entry.get("cache_key"),
                    "is_blank": bool(c_entry.get("is_blank")),
                    "has_uncertainty": bool(c_entry.get("has_uncertainty")),
                    "reason_code": "ocr_ambiguous" if c_entry.get("has_uncertainty") else "captured_pending_review",
                    "updated_at": c_entry.get("created_at") or _utc_now_iso(),
                }
                continue

            prev_pstate = page_states.get(p_key) or {}
            if prev_pstate.get("status") == "error":
                if prev_pstate.get("exhausted") or now_wall < float(prev_pstate.get("next_retry_at") or 0.0):
                    continue

            if not ocr_available:
                page_states[p_key] = {
                    "status": "needs_ocr",
                    "reason_code": "ocr_unavailable",
                    "updated_at": _utc_now_iso(),
                }
                continue

            if ocr_budget_remaining <= 0 or waiting_gpu:
                if p_key not in page_states or page_states[p_key].get("status") == "ocr_running":
                    page_states[p_key] = {
                        "status": "ocr_queued",
                        "reason_code": "queued",
                        "updated_at": _utc_now_iso(),
                    }
                continue

            item["status"] = "ocr_running"
            item["reason_code"] = "ocr_running"
            item["message"] = f"Đang chạy OCR cục bộ trang {p_num}/{total_pages}."
            page_states[p_key] = {
                "status": "ocr_running",
                "updated_at": _utc_now_iso(),
            }
            item["ocr_progress"] = {
                "source_hash": current_hash,
                "total_pages": total_pages,
                "pages_needing_ocr": pages_needing_ocr,
                "page_states": page_states,
            }
            items[rel_path] = item
            with self._state_lock:
                self._items = items
            self._save_state()

            ocr_budget_remaining -= 1
            try:
                img_bytes = self._render_single_page_bytes(fpath, p_num)
                ocr_kwargs: Dict[str, Any] = {
                    "image_bytes": img_bytes,
                    "source_hash": current_hash,
                    "source_relpath": rel_path,
                    "page_num": p_num,
                }
                try:
                    sig_p = inspect.signature(mgr.ocr_image_bytes).parameters
                    has_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_p.values())
                    if has_kw or "cancel_event" in sig_p:
                        ocr_kwargs["cancel_event"] = self._stop_event
                    if has_kw or "gpu_timeout" in sig_p:
                        ocr_kwargs["gpu_timeout"] = 0.5
                except (TypeError, ValueError):
                    pass
                entry = mgr.ocr_image_bytes(**ocr_kwargs)
                if entry.get("is_verified") and not entry.get("needs_review"):
                    # Keep capture != verified gate strictly: never trust unverified capture claiming verified without review fields.
                    if not entry.get("verified_by"):
                        entry = dict(entry, is_verified=False, needs_review=True)
                has_unc = bool(entry.get("has_uncertainty"))
                p_st = "hold" if has_unc else "captured_pending_review"
                page_states[p_key] = {
                    "status": p_st,
                    "cache_key": entry.get("cache_key"),
                    "is_blank": bool(entry.get("is_blank")),
                    "has_uncertainty": has_unc,
                    "reason_code": "ocr_ambiguous" if has_unc else "captured_pending_review",
                    "updated_at": _utc_now_iso(),
                }
            except IndexingCancelledError:
                page_states[p_key] = {
                    "status": "ocr_queued",
                    "reason_code": "cancelled_on_stop",
                    "updated_at": _utc_now_iso(),
                }
                raise
            except GPUBusyError as exc:
                _, msg = _sanitize_error(exc, paths_to_redact=self._redact_paths())
                page_states[p_key] = {
                    "status": "waiting_gpu",
                    "reason_code": "gpu_busy",
                    "message": msg,
                    "updated_at": _utc_now_iso(),
                }
                waiting_gpu = True
            except Exception as exc:
                r_code = classify_ocr_failure(exc)
                s_code, s_msg = _sanitize_error(exc, reason_code=r_code, paths_to_redact=self._redact_paths())
                if r_code == "ocr_ambiguous":
                    page_states[p_key] = {
                        "status": "hold",
                        "reason_code": s_code,
                        "message": s_msg,
                        "updated_at": _utc_now_iso(),
                    }
                else:
                    p_att = int(prev_pstate.get("attempts") or 0) + 1
                    p_exh = p_att >= self.max_retries
                    page_states[p_key] = {
                        "status": "error",
                        "reason_code": s_code,
                        "message": s_msg,
                        "attempts": p_att,
                        "exhausted": p_exh,
                        "next_retry_at": None if p_exh else now_wall + self._compute_backoff(p_att),
                        "updated_at": _utc_now_iso(),
                    }
                break

        pages_captured = 0
        pages_reviewed = 0
        pages_blank = 0
        pages_hold = 0
        pages_error = 0
        pages_uncaptured = 0
        first_error: Optional[Dict[str, Any]] = None

        for p_num in pages_needing_ocr:
            ps = page_states.get(str(p_num)) or {}
            st = ps.get("status") or "needs_ocr"
            if st in ("verified", "blank_verified"):
                pages_captured += 1
                pages_reviewed += 1
                if st == "blank_verified" or ps.get("is_blank"):
                    pages_blank += 1
            elif st == "partial_verified":
                pages_captured += 1
                pages_hold += 1
            elif st == "captured_pending_review":
                pages_captured += 1
                if ps.get("is_blank"):
                    pages_blank += 1
            elif st == "hold":
                pages_captured += 1
                pages_hold += 1
            elif st == "error":
                pages_error += 1
                if first_error is None:
                    first_error = ps
            else:
                pages_uncaptured += 1

        pages_remaining = max(0, len(pages_needing_ocr) - pages_reviewed)
        item["ocr_progress"] = {
            "source_hash": current_hash,
            "total_pages": total_pages,
            "ocr_pages_total": len(pages_needing_ocr),
            "pages_needing_ocr": pages_needing_ocr,
            "page_states": page_states,
            "pages_captured": pages_captured,
            "pages_reviewed": pages_reviewed,
            "pages_blank": pages_blank,
            "pages_hold": pages_hold,
            "pages_error": pages_error,
            "pages_uncaptured": pages_uncaptured,
            "pages_remaining": pages_remaining,
        }
        return ocr_budget_remaining, waiting_gpu, first_error is not None and pages_uncaptured == 0 and pages_captured == 0

    def _classify_ocr_item_state(
        self,
        *,
        item: Dict[str, Any],
        db_row: Dict[str, Any],
        ocr_available: bool,
        waiting_gpu: bool,
        now_wall: float,
        ran_index: bool,
        summary: Dict[str, int],
    ) -> None:
        prog = item.get("ocr_progress")
        if not isinstance(prog, dict) or int(prog.get("ocr_pages_total") or 0) <= 0:
            item.pop("ocr_progress", None)
            item["status"] = "indexed"
            item["reason_code"] = "indexed" if ran_index else (item.get("reason_code") or "unchanged")
            item["message"] = (
                "Đã lập chỉ mục tự động (kèm vector)."
                if item.get("vectors_ready")
                else (
                    "Đã lập chỉ mục (chờ vector cục bộ)."
                    if item.get("vector_error") or item.get("embedding_status") in ("vector_pending", "partial")
                    else "Đã lập chỉ mục từ khóa (chưa có vector)."
                )
            )
            item["stale"] = False
            item["attempts"] = 0
            item["exhausted"] = False
            summary["indexed" if ran_index else "skipped"] += 1
            return

        total_ocr = int(prog.get("ocr_pages_total") or 0)
        pages_captured = int(prog.get("pages_captured") or 0)
        pages_reviewed = int(prog.get("pages_reviewed") or 0)
        pages_blank = int(prog.get("pages_blank") or 0)
        pages_hold = int(prog.get("pages_hold") or 0)
        pages_error = int(prog.get("pages_error") or 0)
        pages_uncaptured = int(prog.get("pages_uncaptured") or 0)
        pages_remaining = int(prog.get("pages_remaining") or 0)
        page_states = prog.get("page_states") or {}
        db_status = db_row.get("status")

        if pages_remaining == 0:
            item["status"] = "indexed"
            item["stale"] = False
            item["attempts"] = 0
            item["exhausted"] = False
            if db_status == "scanned_unocred" and pages_blank >= total_ocr:
                item["reason_code"] = "verified_blank"
                item["message"] = "Tài liệu đã được xác nhận là trang trống có chủ đích."
                item["vectors_ready"] = False
                item["embedding_status"] = "none"
            else:
                item["reason_code"] = "indexed"
                item["message"] = (
                    "Đã duyệt OCR và lập chỉ mục tự động (kèm vector)."
                    if item.get("vectors_ready")
                    else "Đã duyệt OCR và lập chỉ mục từ khóa (chờ vector)."
                )
            summary["indexed" if ran_index else "skipped"] += 1
            return

        item["stale"] = False
        first_err = next((ps for ps in page_states.values() if isinstance(ps, dict) and ps.get("status") == "error"), None)
        has_ambiguous = any(isinstance(ps, dict) and ps.get("reason_code") == "ocr_ambiguous" for ps in page_states.values())

        if waiting_gpu and pages_uncaptured > 0:
            item["status"] = "waiting_gpu"
            item["reason_code"] = "gpu_busy"
            item["message"] = "GPU hoặc tiến trình mô hình OCR đang bận; sẽ tự động thử lại khi sẵn sàng."
            item["next_retry_at"] = now_wall + min(1.0, self.poll_interval)
            summary["waiting_gpu"] += 1
        elif pages_error > 0 and pages_uncaptured == 0 and pages_captured == 0 and first_err is not None:
            item["status"] = "error"
            item["reason_code"] = first_err.get("reason_code") or "ocr_runtime_error"
            item["message"] = first_err.get("message") or "Lỗi OCR cục bộ; giữ nguyên tài liệu chờ thử lại."
            item["attempts"] = int(first_err.get("attempts") or 1)
            item["exhausted"] = bool(first_err.get("exhausted"))
            item["next_retry_at"] = first_err.get("next_retry_at")
            summary["errors"] += 1
        elif pages_uncaptured > 0 and ocr_available:
            item["status"] = "needs_ocr"
            item["reason_code"] = "ocr_queued"
            item["message"] = (
                f"Đang xếp hàng OCR cục bộ ({pages_captured}/{total_ocr} trang đã chụp)."
                if pages_captured > 0
                else "Tài liệu scan đang xếp hàng chạy OCR cục bộ tự động."
            )
            summary["needs_ocr"] += 1
        elif pages_hold > 0:
            item["status"] = "hold"
            item["reason_code"] = "ocr_ambiguous" if has_ambiguous else "partial_review_hold"
            item["message"] = "Một số trang/vùng OCR đang ở trạng thái HOLD chờ kiểm duyệt thủ công."
            summary["hold"] += 1
        elif pages_captured > pages_reviewed:
            item["status"] = "captured_pending_review"
            item["reason_code"] = "captured_pending_review"
            item["message"] = f"Đã chụp OCR {pages_captured}/{total_ocr} trang; chờ kiểm duyệt nguồn trước khi đưa vào tìm kiếm."
            summary["captured_pending_review"] += 1
        else:
            item["status"] = "needs_ocr"
            item["reason_code"] = "ocr_unavailable" if self.auto_ocr else "scanned_unocred"
            item["message"] = (
                "Mô hình OCR cục bộ hiện chưa sẵn sàng; giữ nguyên tài liệu chờ rà soát."
                if self.auto_ocr
                else "Tài liệu dạng ảnh/scan cần OCR hoặc rà soát nguồn."
            )
            summary["needs_ocr"] += 1

    def scan_once(self) -> Dict[str, Any]:
        """Execute one synchronous scan, settle check, OCR/review continuation, and vector sync pass."""
        summary: Dict[str, Any] = {
            "scanned": 0,
            "indexed": 0,
            "skipped": 0,
            "pending": 0,
            "needs_ocr": 0,
            "ocr_running": 0,
            "captured_pending_review": 0,
            "hold": 0,
            "needs_update": 0,
            "missing_source": 0,
            "waiting_gpu": 0,
            "vectors_synced": 0,
            "errors": 0,
            "deferred_for_lock": False,
        }
        if not self._op_lock.acquire(blocking=False):
            summary["deferred_for_lock"] = True
            return summary

        scan_fd: Optional[int] = None
        start_mono = time.monotonic()
        try:
            scan_fd = self._try_acquire_scan_lock()
            if scan_fd is None:
                summary["deferred_for_lock"] = True
                return summary
            if self._ingest_lock_active():
                summary["deferred_for_lock"] = True
                return summary

            self._load_state()
            self._rescan_requested = False
            now_wall = time.time()
            disk_files = self._discover_candidates()
            summary["scanned"] = len(disk_files)
            indexed_map = self._get_existing_indexed_map()

            with self._state_lock:
                items = dict(self._items)

            # 1. Track missing originals for already-usable DB records without auto-deleting DB rows.
            for rel_path, db_row in indexed_map.items():
                if db_row.get("status") not in _USABLE_DB_STATUSES:
                    continue
                if rel_path not in disk_files:
                    items[rel_path] = {
                        "rel_path": rel_path,
                        "status": "missing_source",
                        "reason_code": "missing_source",
                        "message": "Tệp nguồn không còn trong thư mục nguồn; chỉ mục cũ được giữ lại chờ xác nhận.",
                        "indexed_hash": db_row.get("file_hash"),
                        "stale": True,
                        "vectors_ready": False,
                        "embedding_status": "none",
                        "attempts": 0,
                        "updated_at": _utc_now_iso(),
                    }
                    summary["missing_source"] += 1

            # Drop transient state entries for files deleted before ever entering DB.
            for rel_path in list(items.keys()):
                if rel_path not in disk_files and rel_path not in indexed_map:
                    del items[rel_path]

            indexer: Optional[KnowledgeIndexer] = None
            review_states = None
            review_per_doc: Dict[Tuple[str, str], Dict[str, Any]] = {}
            if disk_files or indexed_map:
                indexer = KnowledgeIndexer(
                    db_path=self.db_path,
                    src_dir=self.src_dir,
                    ocr_cache_dir=self._ocr_cache_dir(),
                )
                review_states, review_per_doc = self._collect_review_snapshot(indexer)

            ocr_available_checked = False
            ocr_available = False
            if not self.auto_ocr:
                ocr_available_checked = True
                ocr_available = False
            ocr_budget_remaining = max(1, int(self.ocr_batch_pages))

            def _ensure_ocr_available() -> bool:
                nonlocal ocr_available_checked, ocr_available
                if not ocr_available_checked:
                    ocr_available = self._can_run_local_ocr()
                    ocr_available_checked = True
                return ocr_available

            # 2. Reconcile discovered disk files in deterministic order.
            for rel_path in sorted(disk_files.keys()):
                if self._stop_event.is_set():
                    break
                fpath, size, mtime_ns = disk_files[rel_path]
                prev = dict(items.get(rel_path) or {})
                db_row = indexed_map.get(rel_path)
                db_usable = bool(db_row and db_row.get("status") in _USABLE_DB_STATUSES)
                stat_changed = (
                    prev.get("file_size") != size
                    or prev.get("mtime_ns") != mtime_ns
                )

                # Never mask an already-indexed changed file as non-stale "pending" during debounce.
                if db_usable and (stat_changed or prev.get("stale") or prev.get("status") == "needs_update"):
                    try:
                        live_hash = PDFExtractor.calculate_file_hash(fpath)
                    except OSError:
                        live_hash = ""
                    if live_hash and live_hash != db_row.get("file_hash"):
                        items[rel_path] = {
                            "rel_path": rel_path,
                            "status": "needs_update",
                            "reason_code": "source_hash_changed",
                            "message": "Tệp nguồn đã thay đổi so với bản đã lập chỉ mục; giữ nguyên bản cũ chờ cập nhật thủ công.",
                            "file_size": size,
                            "mtime_ns": mtime_ns,
                            "first_seen_at": prev.get("first_seen_at") or now_wall,
                            "stable_since": now_wall if stat_changed else float(prev.get("stable_since", now_wall)),
                            "last_seen_at": now_wall,
                            "file_hash": live_hash,
                            "indexed_hash": db_row.get("file_hash"),
                            "attempts": 0,
                            "next_retry_at": 0.0,
                            "exhausted": False,
                            "stale": True,
                            "vectors_ready": prev.get("vectors_ready", False),
                            "embedding_status": prev.get("embedding_status", "none"),
                            "updated_at": _utc_now_iso(),
                        }
                        summary["needs_update"] += 1
                        continue

                # New bytes clear previous retry counters and transient errors immediately.
                if stat_changed:
                    prev_status = prev.get("status")
                    item: Dict[str, Any] = {
                        "rel_path": rel_path,
                        "status": "pending",
                        "reason_code": "content_changed" if prev_status in ("error", "needs_update") else "discovered",
                        "message": "Đang chờ tệp ổn định trước khi lập chỉ mục.",
                        "file_size": size,
                        "mtime_ns": mtime_ns,
                        "first_seen_at": prev.get("first_seen_at") or now_wall,
                        "stable_since": now_wall,
                        "last_seen_at": now_wall,
                        "indexed_hash": db_row.get("file_hash") if db_usable else prev.get("indexed_hash"),
                        "attempts": 0,
                        "next_retry_at": 0.0,
                        "exhausted": False,
                        "stale": False,
                        "vectors_ready": prev.get("vectors_ready", False),
                        "embedding_status": prev.get("embedding_status", "none"),
                        "updated_at": _utc_now_iso(),
                    }
                    items[rel_path] = item
                    if self.debounce_seconds > 0:
                        summary["pending"] += 1
                        continue
                else:
                    item = prev or {
                        "rel_path": rel_path,
                        "status": "pending",
                        "file_size": size,
                        "mtime_ns": mtime_ns,
                        "first_seen_at": now_wall,
                        "stable_since": now_wall,
                        "attempts": 0,
                    }
                    item["last_seen_at"] = now_wall

                # Debounce guard while writes settle across polls.
                elapsed_stable = now_wall - float(item.get("stable_since", now_wall))
                if elapsed_stable < self.debounce_seconds:
                    item["status"] = "pending"
                    item["reason_code"] = "debouncing"
                    item["message"] = "Đang chờ tệp hoàn tất sao chép."
                    item["updated_at"] = _utc_now_iso()
                    items[rel_path] = item
                    summary["pending"] += 1
                    continue

                if db_usable and item.get("status") == "needs_update":
                    summary["needs_update"] += 1
                    continue

                if not stat_changed and db_usable and item.get("file_hash"):
                    current_hash = str(item.get("file_hash"))
                else:
                    try:
                        current_hash = PDFExtractor.calculate_file_hash(fpath)
                    except OSError as exc:
                        code, msg = _sanitize_error(exc, reason_code="read_error", paths_to_redact=self._redact_paths())
                        attempts = int(item.get("attempts") or 0) + 1
                        exhausted = attempts >= self.max_retries
                        item.update({
                            "status": "error",
                            "reason_code": code,
                            "message": msg,
                            "attempts": attempts,
                            "exhausted": exhausted,
                            "next_retry_at": None if exhausted else now_wall + self._compute_backoff(attempts),
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["errors"] += 1
                        continue

                if db_usable and db_row.get("file_hash") != current_hash:
                    item.update({
                        "status": "needs_update",
                        "reason_code": "source_hash_changed",
                        "message": "Tệp nguồn đã thay đổi so với bản đã lập chỉ mục; giữ nguyên bản cũ chờ cập nhật thủ công.",
                        "file_hash": current_hash,
                        "indexed_hash": db_row.get("file_hash"),
                        "stale": True,
                        "attempts": 0,
                        "exhausted": False,
                        "updated_at": _utc_now_iso(),
                    })
                    item.pop("ocr_progress", None)
                    item.pop("review_fingerprint", None)
                    items[rel_path] = item
                    summary["needs_update"] += 1
                    continue

                norm_rel = str(Path(rel_path))
                doc_snapshot = review_per_doc.get((norm_rel, current_hash)) or {
                    "verified_pages": {},
                    "captured_entries": {},
                    "fingerprint": "",
                }
                snap_fp = doc_snapshot.get("fingerprint") or ""
                has_new_review = bool(snap_fp) and (
                    snap_fp != item.get("review_fingerprint")
                    or (db_usable and db_row.get("status") == "scanned_unocred")
                )

                # Respect bounded retries & exponential backoff on unchanging failing files.
                if item.get("status") == "error" and not stat_changed and not has_new_review:
                    if current_hash and current_hash == item.get("file_hash"):
                        if item.get("exhausted"):
                            summary["errors"] += 1
                            continue
                        if now_wall < float(item.get("next_retry_at") or 0.0):
                            summary["errors"] += 1
                            continue
                    else:
                        item["attempts"] = 0
                        item["exhausted"] = False
                        item["next_retry_at"] = 0.0

                if item.get("status") == "waiting_gpu" and now_wall < float(item.get("next_retry_at") or 0.0) and not has_new_review:
                    summary["waiting_gpu"] += 1
                    continue

                if indexer is None:
                    indexer = KnowledgeIndexer(
                        db_path=self.db_path,
                        src_dir=self.src_dir,
                        ocr_cache_dir=self._ocr_cache_dir(),
                    )
                    review_states, review_per_doc = self._collect_review_snapshot(indexer)
                    doc_snapshot = review_per_doc.get((norm_rel, current_hash)) or doc_snapshot
                    snap_fp = doc_snapshot.get("fingerprint") or ""
                    has_new_review = bool(snap_fp) and (
                        snap_fp != item.get("review_fingerprint")
                        or (db_usable and db_row.get("status") == "scanned_unocred")
                    )

                ran_index = False
                need_index_pass = (not db_usable) or has_new_review

                if need_index_pass:
                    # Commit 'processing' before touching indexer so crashes remain recoverable.
                    item["status"] = "processing"
                    item["file_hash"] = current_hash
                    item["last_attempt_at"] = now_wall
                    item["updated_at"] = _utc_now_iso()
                    items[rel_path] = item
                    with self._state_lock:
                        self._items = items
                    self._save_state()

                    res: Optional[Dict[str, Any]] = None
                    try:
                        index_kwargs: Dict[str, Any] = {
                            "ocr": False,
                            "force": bool(has_new_review),
                            "embed_model": self.embed_model,
                            "require_new": True,
                            "latest_page_review_states": review_states,
                            "gpu_timeout": 0.5,
                        }
                        try:
                            idx_params = inspect.signature(indexer.index_file).parameters
                            if "cancel_event" in idx_params or any(
                                p.kind == inspect.Parameter.VAR_KEYWORD for p in idx_params.values()
                            ):
                                index_kwargs["cancel_event"] = self._stop_event
                        except (TypeError, ValueError):
                            pass
                        try:
                            res = indexer.index_file(fpath, **index_kwargs)
                        except GPUBusyError as exc:
                            if not has_new_review or not self.embed_model:
                                raise
                            # Preserve newly accepted human review chunks immediately in fts_only/vector_pending.
                            _, vec_msg = _sanitize_error(exc, paths_to_redact=self._redact_paths())
                            fallback_kwargs = dict(index_kwargs, embed_model=None)
                            res = indexer.index_file(fpath, **fallback_kwargs)
                            item["vector_error"] = vec_msg
                            item["vector_next_retry_at"] = now_wall + min(1.0, self.poll_interval)
                        ran_index = True
                    except IndexingCancelledError:
                        item.update({
                            "status": "needs_update" if (db_usable and prev.get("stale")) else "pending",
                            "reason_code": "cancelled_on_stop",
                            "message": "Đã dừng tác vụ lập chỉ mục theo yêu cầu tắt máy chủ.",
                            "attempts": 0,
                            "exhausted": False,
                            "stale": bool(db_usable and prev.get("stale")),
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["needs_update" if item["stale"] else "pending"] += 1
                        break
                    except GPUBusyError as exc:
                        code, msg = _sanitize_error(exc, paths_to_redact=self._redact_paths())
                        item.update({
                            "status": "waiting_gpu",
                            "reason_code": code,
                            "message": msg,
                            "next_retry_at": now_wall + min(1.0, self.poll_interval),
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["waiting_gpu"] += 1
                        continue
                    except FileModifiedDuringIndexingError as exc:
                        code, msg = _sanitize_error(exc, paths_to_redact=self._redact_paths())
                        item.update({
                            "status": "pending",
                            "reason_code": code,
                            "message": msg,
                            "stable_since": time.time(),
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["pending"] += 1
                        continue
                    except CorpusBusyError as exc:
                        code, msg = _sanitize_error(exc, paths_to_redact=self._redact_paths())
                        item.update({
                            "status": "pending",
                            "reason_code": code,
                            "message": msg,
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["pending"] += 1
                        continue
                    except Exception as exc:
                        code, msg = _sanitize_error(exc, paths_to_redact=self._redact_paths())
                        attempts = int(item.get("attempts") or 0) + 1
                        exhausted = attempts >= self.max_retries
                        item.update({
                            "status": "error",
                            "reason_code": code,
                            "message": msg,
                            "attempts": attempts,
                            "exhausted": exhausted,
                            "next_retry_at": None if exhausted else now_wall + self._compute_backoff(attempts),
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["errors"] += 1
                        continue

                    res_status = (res or {}).get("status")
                    if res_status == "needs_update":
                        item.update({
                            "status": "needs_update",
                            "reason_code": "source_hash_changed",
                            "message": "Tệp nguồn đã thay đổi so với bản đã lập chỉ mục; giữ nguyên bản cũ chờ cập nhật thủ công.",
                            "file_hash": res.get("file_hash", current_hash),
                            "indexed_hash": res.get("existing_hash"),
                            "stale": True,
                            "attempts": 0,
                            "exhausted": False,
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["needs_update"] += 1
                        continue
                    if res_status == "error":
                        raw_err = (res or {}).get("error") or ""
                        code, msg = _sanitize_error(
                            raw_err,
                            reason_code="empty_document" if "trống" in raw_err.lower() else "extract_error",
                            paths_to_redact=self._redact_paths(),
                        )
                        attempts = int(item.get("attempts") or 0) + 1
                        exhausted = attempts >= self.max_retries
                        item.update({
                            "status": "error",
                            "reason_code": code,
                            "message": msg,
                            "file_hash": (res or {}).get("file_hash", current_hash),
                            "attempts": attempts,
                            "exhausted": exhausted,
                            "next_retry_at": None if exhausted else now_wall + self._compute_backoff(attempts),
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["errors"] += 1
                        continue

                    if snap_fp:
                        item["review_fingerprint"] = snap_fp
                    item["file_hash"] = (res or {}).get("file_hash", current_hash)
                    item["indexed_hash"] = (res or {}).get("file_hash", current_hash)
                    item["vectors_ready"] = bool((res or {}).get("vectors_ready"))
                    item["embedding_status"] = (res or {}).get("embedding_status", "fts_only")
                    if (res or {}).get("vector_error"):
                        item["vector_error"] = (res or {})["vector_error"]
                    elif item["vectors_ready"]:
                        item.pop("vector_error", None)
                        item.pop("vector_next_retry_at", None)
                    db_row = indexer.get_indexed_files_map().get(rel_path) or {
                        "filename": rel_path,
                        "file_hash": item["file_hash"],
                        "status": "scanned_unocred" if res_status == "scanned_unocred" else "indexed",
                        "is_scanned": res_status == "scanned_unocred",
                        "total_pages": int((res or {}).get("total_pages") or 1),
                        "extracted_pages_count": int((res or {}).get("extracted_pages") or 0),
                    }
                    db_usable = db_row.get("status") in _USABLE_DB_STATUSES

                item["file_hash"] = current_hash
                item["indexed_hash"] = current_hash
                item["total_pages"] = int(db_row.get("total_pages") or item.get("total_pages") or 1) if db_row else int(item.get("total_pages") or 1)
                item["is_scanned"] = bool(db_row.get("is_scanned")) if db_row else False
                item["extracted_pages_count"] = int(db_row.get("extracted_pages_count") or 0) if db_row else 0

                if db_row and db_row.get("status") in ("indexed", "ocr_partial_reviewed"):
                    try:
                        v_ready, vec_busy, v_synced = self._sync_vectors_for_doc(indexer, item, db_row, now_wall)
                        if not ran_index and v_synced:
                            summary["vectors_synced"] += 1
                    except IndexingCancelledError:
                        item.update({
                            "status": "pending",
                            "reason_code": "cancelled_on_stop",
                            "message": "Đã dừng đồng bộ vector theo yêu cầu tắt máy chủ.",
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["pending"] += 1
                        break
                    if db_row.get("id") is None and not ran_index:
                        pass
                else:
                    item["vectors_ready"] = False
                    item["embedding_status"] = "none"

                needs_ocr_surface = bool(
                    db_row
                    and (
                        db_row.get("status") in ("scanned_unocred", "ocr_partial_reviewed")
                        or bool(db_row.get("is_scanned"))
                    )
                )
                waiting_ocr_gpu = False
                if needs_ocr_surface:
                    can_ocr_now = _ensure_ocr_available()
                    try:
                        ocr_budget_remaining, waiting_ocr_gpu, _ = self._reconcile_ocr_pages(
                            item=item,
                            items=items,
                            rel_path=rel_path,
                            fpath=fpath,
                            current_hash=current_hash,
                            db_row=db_row,
                            indexer=indexer,
                            doc_snapshot=doc_snapshot,
                            ocr_available=can_ocr_now,
                            ocr_budget_remaining=ocr_budget_remaining,
                            now_wall=now_wall,
                        )
                    except IndexingCancelledError:
                        item.update({
                            "status": "needs_ocr",
                            "reason_code": "cancelled_on_stop",
                            "message": "Đã dừng tác vụ OCR theo yêu cầu tắt máy chủ.",
                            "updated_at": _utc_now_iso(),
                        })
                        items[rel_path] = item
                        summary["needs_ocr"] += 1
                        break
                else:
                    item.pop("ocr_progress", None)

                item["updated_at"] = _utc_now_iso()
                self._classify_ocr_item_state(
                    item=item,
                    db_row=db_row or {},
                    ocr_available=ocr_available if ocr_available_checked else bool(self._last_ocr_available),
                    waiting_gpu=waiting_ocr_gpu,
                    now_wall=now_wall,
                    ran_index=ran_index,
                    summary=summary,
                )
                items[rel_path] = item

            if not ocr_available_checked and sys.platform == "darwin" and self.auto_ocr:
                ocr_available = self._can_run_local_ocr()
                ocr_available_checked = True

            with self._state_lock:
                self._items = items
                self._last_scan_at = _utc_now_iso()
                self._last_scan_duration_ms = round((time.monotonic() - start_mono) * 1000.0, 2)
                if ocr_available_checked:
                    self._last_ocr_available = ocr_available
                elif self._last_ocr_available is None:
                    self._last_ocr_available = False if not self.auto_ocr else None
                self._last_error = None
                self._last_error_code = None
            self._save_state()
            return summary
        except Exception as exc:
            logger.exception("Watcher scan failed")
            code, msg = _sanitize_error(exc, reason_code="scan_failed", paths_to_redact=self._redact_paths())
            with self._state_lock:
                self._last_scan_at = _utc_now_iso()
                self._last_scan_duration_ms = round((time.monotonic() - start_mono) * 1000.0, 2)
                self._last_error = msg
                self._last_error_code = code
            try:
                self._save_state()
            except Exception:
                pass
            return summary
        finally:
            self._release_scan_lock(scan_fd)
            self._op_lock.release()

    def sync_after_manual_index(self) -> None:
        """Refresh persisted watcher flags cleanly after a manual index_all() run."""
        if not self.is_enabled() and not self.state_path.exists():
            return
        with self._op_lock:
            self._load_state()
            indexed_map = self._get_existing_indexed_map()
            disk_files = self._discover_candidates()
            indexer = KnowledgeIndexer(db_path=self.db_path, src_dir=self.src_dir) if self.db_path.is_file() else None
            with self._state_lock:
                items = dict(self._items)
                for rel_path in list(items.keys()):
                    db_row = indexed_map.get(rel_path)
                    if db_row is None and rel_path not in disk_files:
                        del items[rel_path]
                        continue
                    if db_row and rel_path in disk_files:
                        try:
                            cur_hash = PDFExtractor.calculate_file_hash(disk_files[rel_path][0])
                        except OSError:
                            cur_hash = None
                        if cur_hash and db_row.get("file_hash") == cur_hash and db_row.get("status") in _USABLE_DB_STATUSES:
                            mapped = "needs_ocr" if db_row.get("status") == "scanned_unocred" else "indexed"
                            total_c = 0
                            compat_c = 0
                            emb_c = 0
                            if indexer is not None and db_row.get("id") is not None:
                                with indexer.get_connection() as conn:
                                    total_c, compat_c = indexer._count_compatible_vectors(
                                        conn, doc_id=db_row["id"], require_1024=True
                                    )
                                    emb_c = int(
                                        conn.execute(
                                            "SELECT COUNT(embedding) FROM chunks WHERE doc_id = ?",
                                            (db_row["id"],),
                                        ).fetchone()[0]
                                        or 0
                                    )
                            ready = bool(total_c > 0 and compat_c >= total_c)
                            emb_status = (
                                "none"
                                if total_c == 0
                                else (
                                    "vectorized"
                                    if ready
                                    else (
                                        "partial"
                                        if compat_c > 0
                                        else ("vector_pending" if (emb_c > 0 or self.embed_model) else "fts_only")
                                    )
                                )
                            )
                            items[rel_path].update({
                                "status": mapped,
                                "file_hash": cur_hash,
                                "indexed_hash": cur_hash,
                                "total_pages": int(db_row.get("total_pages") or 1),
                                "extracted_pages_count": int(db_row.get("extracted_pages_count") or 0),
                                "is_scanned": bool(db_row.get("is_scanned")),
                                "vectors_ready": ready,
                                "embedding_status": emb_status,
                                "stale": False,
                                "attempts": 0,
                                "exhausted": False,
                                "updated_at": _utc_now_iso(),
                            })
                            if ready:
                                items[rel_path].pop("vector_error", None)
                                items[rel_path].pop("vector_next_retry_at", None)
                self._items = items
            self._save_state()
        from app.source_freshness import clear_freshness_cache
        clear_freshness_cache()

    def sync_after_review(self, filename: Optional[str] = None) -> Dict[str, Any]:
        """Clear freshness caches and trigger immediate reconciliation after an OCR/source review."""
        from app.source_freshness import clear_freshness_cache
        clear_freshness_cache()
        summary = self.scan_once()
        if summary.get("deferred_for_lock"):
            with self._state_lock:
                self._rescan_requested = True
        return summary

    def _run_loop(self) -> None:
        logger.info("Managed src/ filesystem watcher started")
        try:
            while not self._stop_event.is_set():
                self.scan_once()
                if self._stop_event.is_set():
                    break
                with self._state_lock:
                    rescan_now = bool(self._rescan_requested)
                    has_settling = any(
                        it.get("status") in ("pending", "waiting_gpu", "ocr_running")
                        or (
                            it.get("status") == "needs_ocr"
                            and it.get("reason_code") == "ocr_queued"
                            and bool(self._last_ocr_available)
                        )
                        for it in self._items.values()
                    )
                if rescan_now:
                    continue
                sleep_for = (
                    min(self.poll_interval, max(0.05, self.debounce_seconds))
                    if has_settling
                    else self.poll_interval
                )
                self._stop_event.wait(timeout=max(0.02, float(sleep_for)))
        finally:
            with self._lifecycle_lock:
                self._running = False
                self._stopping = False
                if self._thread is threading.current_thread():
                    self._thread = None
            logger.info("Managed src/ filesystem watcher stopped")

    def start(self) -> bool:
        """Start the managed background watcher thread if no worker thread is alive."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stopping:
                    logger.warning("Cannot start SrcWatcher while previous worker thread is still stopping.")
                    return False
                return True
            self._thread = None
            self._stopping = False
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="study-agent-src-watcher",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> bool:
        """Signal cooperative cancellation, reap active vision workers, and wait for thread exit."""
        with self._lifecycle_lock:
            thread = self._thread
            self._stop_event.set()
            if thread is not None and thread.is_alive():
                self._stopping = True
        try:
            from app.native_vision import cancel_active_vision_workers
            cancel_active_vision_workers()
        except Exception:
            pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.01, float(timeout)))
        stopped = thread is None or not thread.is_alive()
        with self._lifecycle_lock:
            if stopped:
                if self._thread is thread:
                    self._thread = None
                self._running = False
                self._stopping = False
            else:
                self._running = True
                self._stopping = True
        return stopped

    def get_status(self) -> Dict[str, Any]:
        """Return sanitized public status (never exposes host filesystem paths, tracebacks, or secrets)."""
        self._refresh_state_if_newer()
        enabled = self.is_enabled()
        with self._lifecycle_lock:
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            if not thread_alive and self._thread is not None:
                self._thread = None
                self._running = False
                self._stopping = False
            running = thread_alive
            stopping = bool(self._stopping and thread_alive)
        with self._state_lock:
            raw_items = [dict(it) for it in self._items.values()]
            last_scan_at = self._last_scan_at
            last_scan_duration_ms = self._last_scan_duration_ms
            last_ocr_available = self._last_ocr_available
            last_error = self._last_error
            last_error_code = self._last_error_code

        counts = {
            "pending": 0,
            "processing": 0,
            "indexed": 0,
            "needs_ocr": 0,
            "ocr_running": 0,
            "captured_pending_review": 0,
            "hold": 0,
            "needs_update": 0,
            "missing_source": 0,
            "waiting_gpu": 0,
            "error": 0,
        }
        public_items: List[Dict[str, Any]] = []
        ocr_pages_total = 0
        ocr_pages_captured = 0
        ocr_pages_reviewed = 0
        ocr_pages_hold = 0
        ocr_pages_error = 0
        ocr_pages_remaining = 0
        vector_pending_count = 0

        for entry in sorted(raw_items, key=lambda x: str(x.get("rel_path") or "")):
            st = str(entry.get("status") or "pending")
            if st in counts:
                counts[st] += 1
            prog = entry.get("ocr_progress") if isinstance(entry.get("ocr_progress"), dict) else {}
            doc_ocr_total = int(prog.get("ocr_pages_total") or 0)
            doc_ocr_captured = int(prog.get("pages_captured") or 0)
            doc_ocr_reviewed = int(prog.get("pages_reviewed") or 0)
            doc_ocr_hold = int(prog.get("pages_hold") or 0)
            doc_ocr_error = int(prog.get("pages_error") or 0)
            doc_ocr_uncaptured = int(prog.get("pages_uncaptured") or 0)
            doc_ocr_remaining = int(prog.get("pages_remaining") or 0)
            doc_ocr_blank = int(prog.get("pages_blank") or 0)
            doc_ocr_pending_rev = max(0, doc_ocr_captured - doc_ocr_reviewed)

            ocr_pages_total += doc_ocr_total
            ocr_pages_captured += doc_ocr_captured
            ocr_pages_reviewed += doc_ocr_reviewed
            ocr_pages_hold += doc_ocr_hold
            ocr_pages_error += doc_ocr_error
            ocr_pages_remaining += doc_ocr_remaining

            extracted_pages = int(entry.get("extracted_pages_count") or 0)
            vectors_ready = bool(entry.get("vectors_ready", False))
            emb_status = entry.get("embedding_status", "none")
            if emb_status in ("vector_pending", "partial") or (
                extracted_pages > 0 and not vectors_ready and st in ("indexed", "captured_pending_review", "hold")
            ):
                vector_pending_count += 1

            item_pub: Dict[str, Any] = {
                "filename": entry.get("rel_path"),
                "status": st,
                "reason_code": entry.get("reason_code"),
                "message": entry.get("message"),
                "total_pages": int(entry.get("total_pages") or 0),
                "extracted_pages_count": extracted_pages,
                "searchable": bool(extracted_pages > 0 and st in ("indexed", "captured_pending_review", "hold", "needs_ocr", "needs_update")),
                "vectors_ready": vectors_ready,
                "embedding_status": emb_status,
                "stale": bool(entry.get("stale", False)),
                "attempts": int(entry.get("attempts") or 0),
                "ocr_pages_total": doc_ocr_total,
                "ocr_pages_captured": doc_ocr_captured,
                "ocr_pages_reviewed": doc_ocr_reviewed,
                "ocr_pages_pending_review": doc_ocr_pending_rev,
                "ocr_pages_hold": doc_ocr_hold,
                "ocr_pages_error": doc_ocr_error,
                "ocr_pages_uncaptured": doc_ocr_uncaptured,
                "ocr_pages_remaining": doc_ocr_remaining,
                "ocr_pages_blank": doc_ocr_blank,
                "updated_at": entry.get("updated_at"),
            }
            if entry.get("vector_error"):
                item_pub["vector_error"] = entry.get("vector_error")
            public_items.append(item_pub)

        mode = (
            "stopping"
            if stopping
            else ("running" if running else ("error" if last_error else ("stopped" if enabled else "off")))
        )
        return {
            "enabled": enabled,
            "running": running,
            "stopping": stopping,
            "mode": mode,
            "auto_ocr": self.auto_ocr,
            "ocr_backend": self.ocr_backend,
            "ocr_runtime_available": last_ocr_available,
            "last_scan_at": last_scan_at,
            "last_scan_duration_ms": last_scan_duration_ms,
            "poll_interval_seconds": self.poll_interval,
            "debounce_seconds": self.debounce_seconds,
            "counts": counts,
            "discovered_count": sum(1 for e in raw_items if e.get("status") != "missing_source"),
            "pending_count": counts["pending"] + counts["processing"],
            "indexed_count": counts["indexed"],
            "needs_ocr_count": counts["needs_ocr"],
            "ocr_running_count": counts["ocr_running"],
            "captured_pending_review_count": counts["captured_pending_review"],
            "hold_count": counts["hold"],
            "needs_update_count": counts["needs_update"],
            "missing_source_count": counts["missing_source"],
            "waiting_gpu_count": counts["waiting_gpu"],
            "vector_pending_count": vector_pending_count,
            "error_count": counts["error"],
            "ocr_pages_total": ocr_pages_total,
            "ocr_pages_captured": ocr_pages_captured,
            "ocr_pages_reviewed": ocr_pages_reviewed,
            "ocr_pages_pending_review": max(0, ocr_pages_captured - ocr_pages_reviewed),
            "ocr_pages_hold": ocr_pages_hold,
            "ocr_pages_error": ocr_pages_error,
            "ocr_pages_remaining": ocr_pages_remaining,
            "last_error": last_error,
            "last_error_code": last_error_code,
            "items": public_items,
        }
