import os
import sys
import hashlib
import inspect
import json
import sqlite3
import struct
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

from app import file_lock as _file_lock
from app.config import (
    SRC_DIR, DB_PATH, DEFAULT_EMBED_MODEL,
    CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS, DATA_DIR
)
from app.gpu_lock import GPUBusyError, IndexingCancelledError, gpu_coordinator
from app.pdf_extractor import PDFExtractor, PDFExtractionResult
from app.chunker import TextChunker
from app.model_resolver import reject_retired_model
from app.ollama_client import OllamaClient
from app.response_projection import sanitize_error_detail
from app.source_quality import (
    PIPELINE_VERSION, save_source_quality_sidecar, load_source_quality_sidecar
)

logger = logging.getLogger(__name__)


def _acquire_inference_lock(timeout: float = 30.0, cancel_event: Optional[threading.Event] = None):
    fn = gpu_coordinator.acquire_for_inference
    try:
        params = inspect.signature(fn).parameters
        has_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        kwargs: Dict[str, Any] = {}
        if has_kw or "timeout" in params:
            kwargs["timeout"] = timeout
        if (has_kw or "cancel_event" in params) and cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        return fn(**kwargs)
    except (TypeError, ValueError):
        return fn()


def _is_digest_mismatch_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(tok in msg for tok in ("digest", "không khớp", "changed during", "embedding artifact"))


class CorpusBusyError(TimeoutError):
    """Raised when the per-corpus writer lock cannot be acquired in time."""


class FileModifiedDuringIndexingError(RuntimeError):
    """Raised when a source file changes size, mtime, or hash before DB commit."""


_WRITER_LOCKS_GUARD = threading.Lock()
_WRITER_LOCKS: Dict[str, threading.RLock] = {}
_WRITER_LOCAL = threading.local()


@contextmanager
def corpus_writer_lock(
    db_path: Path,
    timeout: Optional[float] = None,
    cancel_event: Optional[threading.Event] = None,
):
    """Serialize corpus index writes per DB path across threads and worker processes."""
    if cancel_event is not None and cancel_event.is_set():
        raise IndexingCancelledError("Đã hủy tác vụ cập nhật chỉ mục theo yêu cầu dừng.")
    resolved = str(Path(db_path).resolve())
    with _WRITER_LOCKS_GUARD:
        tlock = _WRITER_LOCKS.setdefault(resolved, threading.RLock())

    acquired_thread = False
    if cancel_event is not None:
        deadline = None if timeout is None else (time.monotonic() + max(0.0, float(timeout)))
        while True:
            if cancel_event.is_set():
                raise IndexingCancelledError("Đã hủy tác vụ cập nhật chỉ mục theo yêu cầu dừng.")
            step = 0.05 if deadline is None else max(0.0, min(0.05, deadline - time.monotonic()))
            if tlock.acquire(timeout=step):
                acquired_thread = True
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
    elif timeout is None:
        acquired_thread = tlock.acquire()
    else:
        acquired_thread = tlock.acquire(timeout=max(0.0, float(timeout)))
    if not acquired_thread:
        raise CorpusBusyError("Chỉ mục đang được cập nhật bởi tác vụ khác.")

    depths = getattr(_WRITER_LOCAL, "depths", None)
    if depths is None:
        depths = {}
        _WRITER_LOCAL.depths = depths
    depth = depths.get(resolved, 0)
    fd: Optional[int] = None

    try:
        if depth == 0:
            lock_path = Path(resolved).parent / f".{Path(resolved).name}.writer.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
            if timeout is None and cancel_event is None:
                _file_lock.flock(fd, _file_lock.LOCK_EX)
            else:
                deadline = None if timeout is None else (time.monotonic() + max(0.0, float(timeout)))
                locked = False
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise IndexingCancelledError("Đã hủy tác vụ cập nhật chỉ mục theo yêu cầu dừng.")
                    try:
                        _file_lock.flock(fd, _file_lock.LOCK_EX | _file_lock.LOCK_NB)
                        locked = True
                        break
                    except (BlockingIOError, OSError):
                        if deadline is not None and time.monotonic() >= deadline:
                            break
                        if cancel_event is not None:
                            cancel_event.wait(timeout=0.05)
                        else:
                            time.sleep(0.05)
                if not locked:
                    raise CorpusBusyError("Chỉ mục đang được cập nhật bởi tiến trình khác.")
        depths[resolved] = depth + 1
        yield
    finally:
        depths[resolved] = max(0, depths.get(resolved, 1) - 1)
        if fd is not None:
            try:
                _file_lock.flock(fd, _file_lock.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
        tlock.release()


def pack_vector(vec: List[float]) -> bytes:
    """Pack float list into binary bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> np.ndarray:
    """Unpack binary bytes into numpy float32 array."""
    count = len(blob) // 4
    return np.array(struct.unpack(f"{count}f", blob), dtype=np.float32)


def _is_compatible_vector_row(
    text: str,
    blob: Any,
    prov_row: Any,
    *,
    expected_digest: Optional[str] = None,
    require_1024: bool = False,
) -> bool:
    """Validate stored vector bytes, provenance hashes/flags/dimensions, and finite non-zero norm."""
    if blob is None or prov_row is None:
        return False
    try:
        blob_bytes = bytes(blob)
    except (TypeError, ValueError):
        return False
    if not blob_bytes or (len(blob_bytes) % 4) != 0:
        return False

    try:
        full_text = int(prov_row["full_text"] or 0)
        dims = int(prov_row["dimensions"] or 0)
        model_digest = str(prov_row["model_digest"] or "").strip()
        text_sha256 = str(prov_row["text_sha256"] or "")
        vector_sha256 = str(prov_row["vector_sha256"] or "")
    except (KeyError, IndexError, TypeError, ValueError):
        return False

    if full_text != 1 or dims <= 0:
        return False
    if require_1024 and dims != 1024:
        return False
    if len(blob_bytes) != dims * 4:
        return False
    if not model_digest:
        return False
    expected = str(expected_digest or "").strip()
    if not expected or model_digest != expected:
        return False

    actual_text_sha = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    actual_vec_sha = hashlib.sha256(blob_bytes).hexdigest()
    if text_sha256 != actual_text_sha or vector_sha256 != actual_vec_sha:
        return False

    try:
        vec = unpack_vector(blob_bytes)
    except Exception:
        return False
    if not isinstance(vec, np.ndarray) or vec.shape != (dims,) or not np.all(np.isfinite(vec)):
        return False
    norm = float(np.linalg.norm(vec))
    return bool(np.isfinite(norm) and norm > 0.0)


class KnowledgeIndexer:
    def __init__(
        self,
        db_path: Path = DB_PATH,
        src_dir: Path = SRC_DIR,
        ocr_cache_dir: Optional[Path] = None,
    ):
        self.db_path = db_path
        self.src_dir = src_dir
        self._ocr_cache_dir_override = Path(ocr_cache_dir) if ocr_cache_dir is not None else None
        self.ollama = OllamaClient()
        self._init_db()

    @property
    def ocr_cache_dir(self) -> Path:
        if self._ocr_cache_dir_override is not None:
            return self._ocr_cache_dir_override
        return Path(self.db_path).resolve().parent / "ocr_cache"

    def _ocr_cache_dirs(self) -> List[Any]:
        from app import vision_ocr
        candidates: List[Any] = []
        seen_paths: set[str] = set()
        for item in (vision_ocr.CACHE_DIR, self.ocr_cache_dir):
            if item is None:
                continue
            try:
                if isinstance(item, Path):
                    if not item.exists():
                        continue
                    key = str(item.resolve())
                    if key in seen_paths:
                        continue
                    seen_paths.add(key)
                    candidates.append(item)
                elif hasattr(item, "glob"):
                    candidates.append(item)
            except OSError:
                continue
        return candidates

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        """Create database tables, migration columns, and FTS5 virtual table."""
        with self.get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                clean_title TEXT NOT NULL,
                filepath TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_hash TEXT NOT NULL,
                total_pages INTEGER NOT NULL,
                extracted_pages_count INTEGER NOT NULL,
                is_scanned INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error_message TEXT,
                pipeline_version TEXT NOT NULL DEFAULT 'v1',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                book_title TEXT NOT NULL,
                filename TEXT NOT NULL,
                page_num INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(doc_id, page_num);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                book_title,
                text,
                content='chunks',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 0'
            );

            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, book_title, text) VALUES (new.id, new.book_title, new.text);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, book_title, text) VALUES('delete', old.id, old.book_title, old.text);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, book_title, text) VALUES('delete', old.id, old.book_title, old.text);
                INSERT INTO chunks_fts(rowid, book_title, text) VALUES (new.id, new.book_title, new.text);
            END;

            CREATE TABLE IF NOT EXISTS document_source_quality (
                doc_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                pipeline_version TEXT NOT NULL,
                needs_review INTEGER NOT NULL DEFAULT 0,
                quality_warnings TEXT,
                raw_char_count INTEGER NOT NULL DEFAULT 0,
                raw_capture_hash TEXT,
                blocks_count INTEGER NOT NULL DEFAULT 0,
                quality_metadata TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS corpus_vector_provenance (
                chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                text_sha256 TEXT NOT NULL,
                model_digest TEXT NOT NULL,
                vector_sha256 TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                full_text INTEGER NOT NULL,
                embedded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_provenance_digest ON corpus_vector_provenance(model_digest);
            """)

            # Migration: Ensure pipeline_version column exists in documents
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()]
            if "pipeline_version" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN pipeline_version TEXT NOT NULL DEFAULT 'v1'")

            cols_q = [row["name"] for row in conn.execute("PRAGMA table_info(document_source_quality)").fetchall()]
            if "raw_capture_hash" not in cols_q:
                conn.execute("ALTER TABLE document_source_quality ADD COLUMN raw_capture_hash TEXT")

    def get_indexed_files_map(self) -> Dict[str, Dict[str, Any]]:
        """Return dict of {filename: {file_hash, filepath, status, id, pipeline_version, ...}}"""
        with self.get_connection() as conn:
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()]
            pipe_col = ", pipeline_version" if "pipeline_version" in cols else ""
            cursor = conn.execute(f"SELECT id, filename, filepath, file_hash, status, total_pages, extracted_pages_count, is_scanned{pipe_col} FROM documents")
            return {row["filename"]: dict(row) for row in cursor.fetchall()}

    def backfill_missing_embeddings(
        self,
        embed_model: str = DEFAULT_EMBED_MODEL,
        batch_size: int = 32,
        limit: int = 0,
        progress_callback: Optional[callable] = None,
        *,
        doc_id: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        gpu_timeout: float = 30.0,
    ) -> int:
        """
        Populate embeddings for all chunks that currently have embedding IS NULL.
        Allows adding vector search capability to already indexed files.
        """
        if cancel_event is not None and cancel_event.is_set():
            raise IndexingCancelledError("Đã hủy bổ sung vector nhúng theo yêu cầu dừng.")

        use_native = False
        native_client = None
        vector_identity = None
        if embed_model:
            reject_retired_model(embed_model)

        with self.get_connection() as conn:
            installed_digests, has_unprovenanced = self._corpus_vector_state(conn)

        is_native_target = self._is_native_embed_target(
            embed_model,
            installed_digests=installed_digests,
            for_write=True,
        )

        if sys.platform == "darwin" and is_native_target:
            from app.native_embedding import NativeEmbeddingClient
            if NativeEmbeddingClient.available():
                use_native = True
                native_client = NativeEmbeddingClient()
                resolved_model = str(native_client.get_identity() or "").strip()
                if not resolved_model:
                    raise RuntimeError("Không xác minh được digest mô hình native MLX embedding; từ chối ghi vector.")
                self._validate_corpus_digest_for_write(
                    installed_digests,
                    has_unprovenanced,
                    resolved_model,
                    "Native BGE",
                )
            else:
                logger.info("Mô hình native MLX embedding chưa khả dụng trên Mac; không thể bổ sung embedding.")
                return 0
        else:
            if has_unprovenanced or len(installed_digests) > 1:
                self._validate_corpus_digest_for_write(
                    installed_digests,
                    has_unprovenanced,
                    None,
                    "Ollama embedding",
                )
            resolved_model = self.ollama.find_best_embed_model(embed_model) or embed_model
            vector_identity = self.ollama.embedding_identity(resolved_model)
            if not vector_identity:
                raise RuntimeError("Không xác minh được digest mô hình embedding cục bộ; không ghi vector không rõ nguồn gốc.")
            self._validate_corpus_digest_for_write(
                installed_digests,
                has_unprovenanced,
                vector_identity,
                "Ollama embedding",
            )
            with _acquire_inference_lock(timeout=gpu_timeout, cancel_event=cancel_event):
                test_emb = self.ollama.get_embedding("test", model=resolved_model)
            if test_emb is None:
                logger.info(f"Mô hình embedding '{resolved_model}' chưa khả dụng trong Ollama để bổ sung embedding.")
                return 0

        target_digest = resolved_model if use_native else vector_identity

        with self.get_connection() as conn:
            where_doc = "WHERE c.doc_id = ?" if doc_id is not None else ""
            params: List[Any] = [doc_id] if doc_id is not None else []
            candidate_rows = conn.execute(
                f"""
                SELECT c.id, c.text, c.embedding,
                       p.chunk_id AS prov_chunk_id,
                       p.text_sha256, p.model_digest, p.vector_sha256, p.dimensions, p.full_text
                FROM chunks c
                LEFT JOIN corpus_vector_provenance p ON p.chunk_id = c.id
                {where_doc}
                ORDER BY c.id
                """,
                params,
            ).fetchall()

        missing_chunks = []
        for row in candidate_rows:
            prov = row if row["prov_chunk_id"] is not None else None
            if not _is_compatible_vector_row(
                row["text"],
                row["embedding"],
                prov,
                expected_digest=target_digest,
                require_1024=False,
            ):
                missing_chunks.append(row)
                if limit > 0 and len(missing_chunks) >= limit:
                    break

        total_missing = len(missing_chunks)
        if total_missing == 0:
            return 0

        logger.info(f"Bắt đầu bổ sung embedding cho {total_missing} đoạn chỉ mục bằng mô hình '{resolved_model}'...")
        count = 0
        import hashlib
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        with corpus_writer_lock(self.db_path, cancel_event=cancel_event), self.get_connection() as conn:
            for start in range(0, total_missing, batch_size):
                if cancel_event is not None and cancel_event.is_set():
                    raise IndexingCancelledError("Đã hủy bổ sung vector nhúng theo yêu cầu dừng.")
                batch = missing_chunks[start:start + batch_size]
                with _acquire_inference_lock(timeout=gpu_timeout, cancel_event=cancel_event):
                    if use_native and native_client:
                        try:
                            n_params = inspect.signature(native_client.get_batch_embeddings).parameters
                            accepts_cancel = ("cancel_event" in n_params) or any(
                                p.kind == inspect.Parameter.VAR_KEYWORD for p in n_params.values()
                            )
                        except (TypeError, ValueError):
                            accepts_cancel = False
                        if cancel_event is not None and accepts_cancel:
                            vectors = native_client.get_batch_embeddings(
                                [row["text"] for row in batch], max_length=2048, cancel_event=cancel_event
                            )
                        else:
                            vectors = native_client.get_batch_embeddings(
                                [row["text"] for row in batch], max_length=2048
                            )
                        if native_client.get_identity() != resolved_model:
                            raise RuntimeError("Embedding artifact changed during backfill")
                    else:
                        vectors = self.ollama.get_batch_embeddings(
                            [row["text"] for row in batch], model=resolved_model
                        )
                        if self.ollama.embedding_identity(resolved_model) != vector_identity:
                            raise RuntimeError("Embedding artifact changed during backfill")
                if len(vectors) != len(batch):
                    raise RuntimeError("Embedding returned an incomplete batch")
                if any(not vec for vec in vectors):
                    raise RuntimeError("Embedding backend did not return every requested vector")
                for row, vec in zip(batch, vectors):
                    vec_blob = pack_vector(vec)
                    conn.execute(
                        "UPDATE chunks SET embedding = ? WHERE id = ?",
                        (vec_blob, row["id"]),
                    )
                    text_sha = hashlib.sha256(row["text"].encode("utf-8")).hexdigest()
                    vec_sha = hashlib.sha256(vec_blob).hexdigest()
                    conn.execute("""
                        INSERT OR REPLACE INTO corpus_vector_provenance
                        (chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (row["id"], text_sha, resolved_model if use_native else vector_identity, vec_sha, len(vec), 1, now_iso))
                    count += 1
                conn.commit()
                if progress_callback:
                    done = min(start + len(batch), total_missing)
                    progress_callback(done, total_missing, f"Đã bổ sung {count}/{total_missing} vector embedding...")

        if count > 0:
            from app.source_freshness import clear_freshness_cache
            clear_freshness_cache()
        return count

    def _resolve_embedding_config(
        self,
        embed_model: Optional[str],
        *,
        gpu_timeout: float = 30.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[bool, Optional[str], bool]:
        """Resolve whether real vector embedding is available without silent model substitution."""
        if not embed_model:
            return False, None, False
        reject_retired_model(embed_model)

        with self.get_connection() as conn:
            installed_digests, has_unprovenanced = self._corpus_vector_state(conn)

        use_native = self._is_native_embed_target(
            embed_model,
            installed_digests=installed_digests,
            for_write=True,
        )

        if use_native:
            if sys.platform != "darwin":
                logger.info("Mô hình native MLX embedding chỉ khả dụng trên macOS.")
                return False, None, True
            from app.native_embedding import NativeEmbeddingClient
            if NativeEmbeddingClient.available():
                current_identity = str(NativeEmbeddingClient().get_identity() or "").strip()
                if not current_identity:
                    raise RuntimeError("Không xác minh được digest mô hình native MLX embedding.")
                self._validate_corpus_digest_for_write(
                    installed_digests,
                    has_unprovenanced,
                    current_identity,
                    "Native BGE",
                )
                return True, "bge-m3-mlx-fp16", True
            logger.info("Mô hình native MLX embedding chưa khả dụng.")
            return False, None, True

        if has_unprovenanced or len(installed_digests) > 1:
            self._validate_corpus_digest_for_write(
                installed_digests,
                has_unprovenanced,
                None,
                "Ollama embedding",
            )
        resolved_embed_model = self.ollama.find_best_embed_model(embed_model)
        if resolved_embed_model:
            actual_digest = self.ollama.embedding_identity(resolved_embed_model)
            if not actual_digest:
                logger.info(f"Không xác minh được digest của mô hình embedding '{resolved_embed_model}'.")
                return False, None, False
            self._validate_corpus_digest_for_write(
                installed_digests,
                has_unprovenanced,
                actual_digest,
                "Ollama embedding",
            )
            with _acquire_inference_lock(timeout=gpu_timeout, cancel_event=cancel_event):
                test_emb = self.ollama.get_embedding("test", model=resolved_embed_model)
            if test_emb is not None:
                return True, resolved_embed_model, False
            logger.info(f"Mô hình embedding '{resolved_embed_model}' chưa khả dụng trong Ollama.")
        return False, None, False

    def _load_verified_page_reviews(self) -> Dict[Tuple[str, int], Dict[str, Any]]:
        """Pre-load verified human page reviews strictly keyed by canonical path and page number."""
        latest_page_review_states: Dict[Tuple[str, int], Dict[str, Any]] = {}

        try:
            from app.source_page_reviews import latest_page_reviews
            reviews_root = self.src_dir.parent if hasattr(self.src_dir, "parent") else Path.cwd()
            spr = latest_page_reviews(reviews_root)
            for (r_fn, r_page), (r_path, r_data) in spr.items():
                if type(r_page) is not int or r_page < 1 or not r_fn:
                    continue
                norm_fn = str(Path(r_fn))
                order = (r_data.get("reviewed_at") or "", str(r_path))
                prev = latest_page_review_states.get((norm_fn, r_page))
                if prev is None or order > prev["order"]:
                    completeness = r_data.get("completeness") or "complete"
                    is_ver = (r_data.get("image_reviewed") is True and completeness in ("complete", "partial"))
                    txt = r_data.get("transcript") or r_data.get("text") or ""
                    is_blank = (r_data.get("kind") == "blank")
                    if is_blank:
                        txt = ""
                    s_sha = r_data.get("source_sha256") or r_data.get("source_hash") or ""
                    latest_page_review_states[(norm_fn, r_page)] = {
                        "order": order,
                        "source_hash": s_sha,
                        "is_verified": is_ver,
                        "is_blank": is_blank,
                        "completeness": completeness,
                        "text": txt,
                        "needs_review": bool(r_data.get("unresolved")) or completeness != "complete",
                        "review": r_data,
                    }
        except Exception as e:
            logger.debug(f"Could not load source_page_reviews: {e}")

        for cache_dir in self._ocr_cache_dirs():
            try:
                candidates = cache_dir.glob("*.json")
            except Exception:
                continue
            for cp in candidates:
                try:
                    entry = json.loads(cp.read_text(encoding="utf-8"))
                    if not isinstance(entry, dict):
                        continue
                    r_fn = entry.get("source_relpath") or entry.get("filename")
                    r_page = entry.get("page_num") or entry.get("page")
                    if not r_fn or type(r_page) is not int or r_page < 1:
                        continue
                    norm_fn = str(Path(r_fn))
                    order = (entry.get("verified_at") or entry.get("reviewed_at") or "", str(cp))
                    prev = latest_page_review_states.get((norm_fn, r_page))
                    if prev is None or order > prev["order"]:
                        is_blank_ver = bool(
                            entry.get("is_verified")
                            and not entry.get("needs_review")
                            and entry.get("is_blank")
                            and (entry.get("status") == "VERIFIED_BLANK" or not entry.get("text"))
                        )
                        is_ver = bool(
                            entry.get("is_verified")
                            and not entry.get("needs_review")
                            and (bool(entry.get("text")) or is_blank_ver)
                        )
                        completeness = (
                            "partial"
                            if (entry.get("full_page_text_reviewed") is False or entry.get("pending_region_ids"))
                            else "complete"
                        )
                        s_sha = entry.get("source_hash") or entry.get("source_sha256") or ""
                        latest_page_review_states[(norm_fn, r_page)] = {
                            "order": order,
                            "source_hash": s_sha,
                            "is_verified": is_ver,
                            "is_blank": is_blank_ver,
                            "completeness": completeness,
                            "text": entry.get("text") or "",
                            "needs_review": bool(entry.get("needs_review")) or completeness != "complete",
                        }
                except Exception:
                    continue

        return latest_page_review_states

    def _chunk_extracted_pages(
        self,
        extract_res: PDFExtractionResult,
        rel_path: str,
        file_hash: str,
        latest_page_review_states: Dict[Tuple[str, int], Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Build page chunks while honoring verified reviews bound strictly to path and hash."""
        from app.source_page_reviews import validate_page_review, reviewed_page_text

        norm_rel = str(Path(rel_path))
        all_chunks: List[Dict[str, Any]] = []
        extracted_pages_count = 0
        for page in extract_res.extracted_pages:
            p_num = page["page_num"]
            rev_state = latest_page_review_states.get((norm_rel, p_num))
            if rev_state and rev_state["source_hash"] == file_hash and rev_state["is_verified"]:
                review = rev_state.get("review")
                if review:
                    validate_page_review(review, rel_path, p_num, file_hash)
                    page_text = reviewed_page_text(dict(review, editorial=""))
                    page["needs_review"] = review["completeness"] != "complete"
                else:
                    page_text = rev_state["text"]
                page_has_text = len(page_text.strip()) > 0
            else:
                page_text = page.get("text")
                page_has_text = page.get("has_text")

            if page_has_text and page_text:
                extracted_pages_count += 1
                chunks = TextChunker.chunk_page(
                    book_title=extract_res.clean_title,
                    filename=rel_path,
                    page_num=p_num,
                    page_text=page_text,
                )
                all_chunks.extend(chunks)
        return all_chunks, extracted_pages_count

    @staticmethod
    def _call_extract_file(
        file_path: Path,
        *,
        is_force: bool,
        ocr: bool,
        rel_path: str,
        cancel_event: Optional[threading.Event] = None,
    ) -> PDFExtractionResult:
        """Invoke PDFExtractor.extract_file cleanly while staying compatible with minimal test stubs."""
        if cancel_event is not None and cancel_event.is_set():
            raise IndexingCancelledError("Đã hủy trích xuất tài liệu theo yêu cầu dừng.")
        try:
            params = inspect.signature(PDFExtractor.extract_file).parameters
            accepts_rel_path = ("rel_path" in params) or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            accepts_cancel = ("cancel_event" in params) or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            accepts_rel_path = False
            accepts_cancel = False

        kwargs: Dict[str, Any] = {}
        if is_force:
            kwargs["force_ocr"] = True
        if accepts_rel_path:
            kwargs["rel_path"] = rel_path
        if cancel_event is not None and accepts_cancel:
            kwargs["cancel_event"] = cancel_event
        extract_res = PDFExtractor.extract_file(file_path, **kwargs)
        if ocr and extract_res.is_scanned and not is_force:
            if cancel_event is not None and cancel_event.is_set():
                raise IndexingCancelledError("Đã hủy trích xuất OCR theo yêu cầu dừng.")
            kwargs_ocr: Dict[str, Any] = {"ocr": True}
            if accepts_rel_path:
                kwargs_ocr["rel_path"] = rel_path
            if cancel_event is not None and accepts_cancel:
                kwargs_ocr["cancel_event"] = cancel_event
            extract_res = PDFExtractor.extract_file(file_path, **kwargs_ocr)
        return extract_res

    @staticmethod
    def _verify_unchanged_file(file_path: Path, expected_size: int, expected_mtime_ns: int, expected_hash: str) -> None:
        """Ensure file metadata and content digest still match pre-extraction identity."""
        if not file_path.exists() or not file_path.is_file():
            raise FileModifiedDuringIndexingError("Tệp nguồn đã bị di chuyển hoặc xóa trước khi lưu chỉ mục.")
        stat = file_path.stat()
        if stat.st_size != expected_size or stat.st_mtime_ns != expected_mtime_ns:
            raise FileModifiedDuringIndexingError("Tệp nguồn đang thay đổi trong lúc lập chỉ mục.")
        current_hash = PDFExtractor.calculate_file_hash(file_path)
        if current_hash != expected_hash:
            raise FileModifiedDuringIndexingError("Mã băm tệp nguồn thay đổi trong lúc lập chỉ mục.")

    def index_file(
        self,
        target: Path | str,
        *,
        ocr: bool = False,
        force: bool = False,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
        require_new: bool = False,
        force_ocr_files: Optional[set[str]] = None,
        latest_page_review_states: Optional[Dict[Tuple[str, int], Dict[str, Any]]] = None,
        gpu_timeout: float = 30.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        """Index a single verified file without pruning unrelated docs or global backfilling."""
        if cancel_event is not None and cancel_event.is_set():
            raise IndexingCancelledError("Đã hủy tác vụ lập chỉ mục theo yêu cầu dừng.")
        if not self.src_dir.exists():
            raise FileNotFoundError("Thư mục nguồn không tồn tại.")

        file_path = Path(target) if Path(target).is_absolute() else (self.src_dir / target)
        if not PDFExtractor.is_safe_path(file_path, self.src_dir):
            raise ValueError("Tệp nguồn nằm ngoài thư mục nguồn hợp lệ.")
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError("Không tìm thấy tệp nguồn.")

        rel_path = str(file_path.relative_to(self.src_dir))
        force_ocr_set = force_ocr_files or set()
        reviews = (
            latest_page_review_states
            if latest_page_review_states is not None
            else self._load_verified_page_reviews()
        )

        lock_ctx = (
            corpus_writer_lock(self.db_path, cancel_event=cancel_event)
            if cancel_event is not None
            else corpus_writer_lock(self.db_path)
        )
        with lock_ctx:
            if cancel_event is not None and cancel_event.is_set():
                raise IndexingCancelledError("Đã hủy tác vụ lập chỉ mục theo yêu cầu dừng.")
            pre_stat = file_path.stat()
            pre_size = pre_stat.st_size
            pre_mtime_ns = pre_stat.st_mtime_ns
            file_hash = PDFExtractor.calculate_file_hash(file_path)
            current_abs_path = str(file_path.resolve())

            indexed_map = self.get_indexed_files_map()
            existing = indexed_map.get(rel_path)
            if existing and existing["status"] in ("indexed", "scanned_unocred", "ocr_partial_reviewed"):
                pipeline_matched = (existing.get("pipeline_version") == PIPELINE_VERSION)
                if (
                    not force
                    and existing["file_hash"] == file_hash
                    and (pipeline_matched or require_new)
                    and rel_path not in force_ocr_set
                    and not (ocr and existing["status"] == "scanned_unocred")
                ):
                    if not require_new and existing.get("filepath") != current_abs_path:
                        with self.get_connection() as conn:
                            conn.execute(
                                "UPDATE documents SET filepath = ? WHERE id = ?",
                                (current_abs_path, existing["id"]),
                            )
                    return {
                        "filename": rel_path,
                        "status": "skipped",
                        "reason": "unchanged" if pipeline_matched else "existing_protected",
                        "db_status": existing["status"],
                        "file_hash": file_hash,
                    }
                if require_new and existing["file_hash"] != file_hash:
                    return {
                        "filename": rel_path,
                        "status": "needs_update",
                        "reason": "source_hash_changed",
                        "db_status": existing["status"],
                        "existing_hash": existing["file_hash"],
                        "file_hash": file_hash,
                    }

            can_embed, resolved_embed_model, _use_native = self._resolve_embedding_config(embed_model)
            if can_embed:
                allowed, busy_msg = gpu_coordinator.check_inference_allowed()
                if not allowed:
                    raise GPUBusyError(busy_msg)

            is_force = (rel_path in force_ocr_set)
            extract_res = self._call_extract_file(
                file_path, is_force=is_force, ocr=ocr, rel_path=rel_path, cancel_event=cancel_event
            )

            extract_res.filename = rel_path
            extract_res.file_hash = file_hash

            if extract_res.error_message or extract_res.failed_pages:
                err_msg = extract_res.error_message or f"Lỗi trích xuất trang: {extract_res.failed_pages}"
                if existing is None or existing.get("status") == "error":
                    self._save_document_record(extract_res, status="error", is_scanned=False)
                return {
                    "filename": rel_path,
                    "status": "error",
                    "error": err_msg,
                    "file_hash": file_hash,
                }

            all_chunks, extracted_pages_count = self._chunk_extracted_pages(
                extract_res, rel_path, file_hash, reviews
            )

            if extract_res.is_scanned and not all_chunks:
                if cancel_event is not None and cancel_event.is_set():
                    raise IndexingCancelledError("Đã hủy lưu chỉ mục theo yêu cầu dừng.")
                self._verify_unchanged_file(file_path, pre_size, pre_mtime_ns, file_hash)
                with self.get_connection() as conn:
                    doc_id = self._save_document_record(
                        extract_res, status="scanned_unocred", is_scanned=True, connection=conn
                    )
                    self._save_quality_record(conn, doc_id, extract_res)
                return {
                    "filename": rel_path,
                    "status": "scanned_unocred",
                    "total_pages": extract_res.total_pages,
                    "chunks_count": 0,
                    "embedded_chunks_count": 0,
                    "vectors_ready": False,
                    "embedding_status": "none",
                    "file_hash": file_hash,
                }

            if require_new and not all_chunks:
                return {
                    "filename": rel_path,
                    "status": "error",
                    "error": "Tệp nguồn trống hoặc không có nội dung văn bản để lập chỉ mục.",
                    "file_hash": file_hash,
                }

            vector_error: Optional[str] = None
            if can_embed and resolved_embed_model:
                try:
                    with _acquire_inference_lock(timeout=gpu_timeout, cancel_event=cancel_event):
                        prepared_rows, prov_rows = self._prepare_chunk_rows(
                            all_chunks, True, resolved_embed_model, cancel_event=cancel_event
                        )
                except (GPUBusyError, IndexingCancelledError):
                    raise
                except Exception as exc:
                    if _is_digest_mismatch_error(exc):
                        raise
                    vector_error = sanitize_error_detail(str(exc)) or "Không thể tạo vector nhúng"
                    logger.warning("Vector generation failed during index_file for '%s': %s", rel_path, vector_error)
                    prepared_rows, prov_rows = self._prepare_chunk_rows(
                        all_chunks, False, None, cancel_event=cancel_event
                    )
            else:
                prepared_rows, prov_rows = self._prepare_chunk_rows(
                    all_chunks, False, None, cancel_event=cancel_event
                )

            if cancel_event is not None and cancel_event.is_set():
                raise IndexingCancelledError("Đã hủy lưu chỉ mục theo yêu cầu dừng.")
            self._verify_unchanged_file(file_path, pre_size, pre_mtime_ns, file_hash)

            doc_status = "indexed"
            norm_rel = str(Path(rel_path))
            if extract_res.is_scanned:
                complete_pages = 0
                for page in extract_res.extracted_pages:
                    p_num = page["page_num"]
                    rev_state = reviews.get((norm_rel, p_num))
                    if rev_state and rev_state["source_hash"] == file_hash and rev_state["is_verified"]:
                        if rev_state.get("completeness", "complete") == "complete":
                            complete_pages += 1
                    elif page.get("has_text") and not page.get("needs_review"):
                        complete_pages += 1
                doc_status = "indexed" if complete_pages >= extract_res.total_pages else "ocr_partial_reviewed"
            else:
                for page in extract_res.extracted_pages:
                    if page.get("has_text") or not page.get("needs_review"):
                        continue
                    p_num = page["page_num"]
                    rev_state = reviews.get((norm_rel, p_num))
                    if not (
                        rev_state
                        and rev_state["source_hash"] == file_hash
                        and rev_state["is_verified"]
                        and rev_state.get("completeness", "complete") == "complete"
                    ):
                        doc_status = "ocr_partial_reviewed"
                        break

            with self.get_connection() as conn:
                doc_id = self._save_document_record(
                    extract_res,
                    status=doc_status,
                    is_scanned=extract_res.is_scanned,
                    extracted_pages_count=extracted_pages_count,
                    connection=conn,
                )
                chunks_inserted = self._insert_chunk_rows(conn, doc_id, prepared_rows, prov_rows)
                self._save_quality_record(conn, doc_id, extract_res)
                total_doc_chunks, compat_doc_chunks = self._count_compatible_vectors(
                    conn,
                    doc_id=doc_id,
                    require_1024=True,
                )

            from app.source_freshness import clear_freshness_cache
            clear_freshness_cache()

            embedded_chunks_count = sum(1 for row in prepared_rows if row[5] is not None)
            vectors_ready = bool(total_doc_chunks > 0 and compat_doc_chunks >= total_doc_chunks)
            embedding_status = (
                "vectorized"
                if vectors_ready
                else (
                    "partial"
                    if compat_doc_chunks > 0
                    else ("vector_pending" if (vector_error or embedded_chunks_count > 0) else "fts_only")
                )
            )

            result_payload = {
                "filename": rel_path,
                "doc_id": doc_id,
                "clean_title": extract_res.clean_title,
                "status": doc_status,
                "total_pages": extract_res.total_pages,
                "extracted_pages": extracted_pages_count,
                "chunks_count": chunks_inserted,
                "embedded_chunks_count": embedded_chunks_count,
                "vectors_ready": vectors_ready,
                "embedding_status": embedding_status,
                "failed_pages": extract_res.failed_pages,
                "ocr_pages": extract_res.ocr_pages,
                "needs_review": extract_res.needs_review,
                "quality_warnings": extract_res.quality_warnings,
                "file_hash": file_hash,
            }
            if vector_error:
                result_payload["vector_error"] = vector_error
            return result_payload

    def index_all(
        self,
        force: bool = False,
        ocr: bool = False,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
        progress_callback: Optional[callable] = None,
        force_ocr_files: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        """
        Index all PDFs from SRC_DIR.
        Supports incremental updates (skips unchanged files with matching pipeline version unless force=True).
        Detects obsolete pipeline versions and migrates them safely.
        Automatically backfills vector embeddings if embed_model is available.
        """
        report = {
            "total_files": 0,
            "indexed_files": 0,
            "skipped_files": 0,
            "removed_files": 0,
            "scanned_files": [],
            "error_files": [],
            "total_pages_indexed": 0,
            "total_chunks_created": 0,
            "embeddings_backfilled": 0,
            "embedding_enabled": False,
            "details": []
        }

        if not self.src_dir.exists():
            report["error"] = f"Thư mục nguồn {self.src_dir} không tồn tại."
            return report

        with corpus_writer_lock(self.db_path):
            discovery = PDFExtractor.discover_source_files(self.src_dir)
            source_files = discovery["supported"]
            report["total_files"] = len(source_files)
            report["unsupported_files"] = discovery.get("unsupported", [])
            report["symlink_violations"] = discovery.get("symlink_violations", [])

            can_embed, resolved_embed_model, _ = self._resolve_embedding_config(embed_model)
            report["embedding_enabled"] = bool(can_embed)

            indexed_map = self.get_indexed_files_map()
            force_ocr_files = force_ocr_files or set()
            current_keys = {str(p.relative_to(self.src_dir)) for p in source_files}

            obsolete_filenames = set(indexed_map) - current_keys
            if obsolete_filenames:
                with self.get_connection() as conn:
                    conn.executemany(
                        "DELETE FROM documents WHERE filename = ?",
                        [(name,) for name in obsolete_filenames],
                    )
                report["removed_files"] = len(obsolete_filenames)

            latest_page_review_states = self._load_verified_page_reviews()

            for idx, file_path in enumerate(source_files, 1):
                rel_path = str(file_path.relative_to(self.src_dir))
                file_hash = PDFExtractor.calculate_file_hash(file_path)
                current_abs_path = str(file_path.resolve())

                if progress_callback:
                    progress_callback(idx, len(source_files), rel_path, "Đang xử lý...")

                if not force and rel_path in indexed_map:
                    existing = indexed_map[rel_path]
                    pipeline_matched = (existing.get("pipeline_version") == PIPELINE_VERSION)
                    if (
                        existing["file_hash"] == file_hash
                        and pipeline_matched
                        and existing["status"] in ("indexed", "scanned_unocred", "ocr_partial_reviewed")
                        and rel_path not in force_ocr_files
                        and not (ocr and existing["status"] == "scanned_unocred")
                    ):
                        if existing.get("filepath") != current_abs_path:
                            with self.get_connection() as conn:
                                conn.execute(
                                    "UPDATE documents SET filepath = ? WHERE id = ?",
                                    (current_abs_path, existing["id"]),
                                )
                            existing["filepath"] = current_abs_path

                        report["skipped_files"] += 1
                        report["details"].append({
                            "filename": rel_path,
                            "status": "skipped",
                            "reason": "File không thay đổi (đã lập chỉ mục, khớp phiên bản pipeline)"
                        })
                        if existing["is_scanned"]:
                            report["scanned_files"].append(rel_path)
                        continue

                is_force = (rel_path in force_ocr_files)
                extract_res = self._call_extract_file(
                    file_path, is_force=is_force, ocr=ocr, rel_path=rel_path
                )

                extract_res.filename = rel_path

                if extract_res.error_message or extract_res.failed_pages:
                    err_msg = extract_res.error_message or f"Lỗi trích xuất trang: {extract_res.failed_pages}"
                    report["error_files"].append({"filename": rel_path, "error": err_msg})
                    if rel_path not in indexed_map:
                        self._save_document_record(extract_res, status="error", is_scanned=False)
                    report["details"].append({
                        "filename": rel_path,
                        "status": "error",
                        "error": err_msg
                    })
                    continue

                all_chunks, extracted_pages_count = self._chunk_extracted_pages(
                    extract_res, rel_path, file_hash, latest_page_review_states
                )

                if extract_res.is_scanned and not all_chunks:
                    report["scanned_files"].append(rel_path)
                    with self.get_connection() as conn:
                        doc_id = self._save_document_record(extract_res, status="scanned_unocred", is_scanned=True, connection=conn)
                        self._save_quality_record(conn, doc_id, extract_res)
                    report["details"].append({
                        "filename": rel_path,
                        "status": "scanned_unocred",
                        "total_pages": extract_res.total_pages,
                        "warning": "Tài liệu dạng scan/ảnh (không có lớp chữ số OCR)"
                    })
                    continue

                prepared_rows, prov_rows = self._prepare_chunk_rows(all_chunks, can_embed, resolved_embed_model)

                doc_status = "indexed"
                if extract_res.is_scanned:
                    doc_status = "indexed" if extracted_pages_count == extract_res.total_pages else "ocr_partial_reviewed"

                with self.get_connection() as conn:
                    doc_id = self._save_document_record(
                        extract_res, status=doc_status, is_scanned=extract_res.is_scanned,
                        extracted_pages_count=extracted_pages_count, connection=conn,
                    )
                    chunks_inserted = self._insert_chunk_rows(conn, doc_id, prepared_rows, prov_rows)
                    self._save_quality_record(conn, doc_id, extract_res)

                report["indexed_files"] += 1
                report["total_pages_indexed"] += extracted_pages_count
                report["total_chunks_created"] += chunks_inserted
                report["details"].append({
                    "filename": rel_path,
                    "clean_title": extract_res.clean_title,
                    "status": "indexed",
                    "total_pages": extract_res.total_pages,
                    "extracted_pages": extracted_pages_count,
                    "chunks_count": chunks_inserted,
                    "failed_pages": extract_res.failed_pages,
                    "ocr_pages": extract_res.ocr_pages,
                    "needs_review": extract_res.needs_review,
                    "quality_warnings": extract_res.quality_warnings,
                })

            if can_embed and resolved_embed_model:
                backfilled = self.backfill_missing_embeddings(
                    embed_model=resolved_embed_model,
                    progress_callback=lambda cur, tot, msg: progress_callback(len(source_files), len(source_files), "Embedding Backfill", msg) if progress_callback else None
                )
                report["embeddings_backfilled"] = backfilled

        from app.source_freshness import clear_freshness_cache
        clear_freshness_cache()
        return report

    def _save_document_record(
        self,
        res: PDFExtractionResult,
        status: str,
        is_scanned: bool,
        extracted_pages_count: int = 0,
        *,
        connection: Optional[sqlite3.Connection] = None
    ) -> int:
        """Upsert document record in DB and delete old chunks if re-indexing. Preserves existing doc_id."""
        if connection is None:
            with self.get_connection() as conn:
                return self._save_document_record(
                    res, status, is_scanned, extracted_pages_count, connection=conn,
                )
        conn = connection

        pipe_ver = getattr(res, "pipeline_version", PIPELINE_VERSION)
        row = conn.execute("SELECT id FROM documents WHERE filename = ?", (res.filename,)).fetchone()
        if row:
            doc_id = row["id"]
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("""
            UPDATE documents SET
                clean_title = ?,
                filepath = ?,
                file_size = ?,
                file_hash = ?,
                total_pages = ?,
                extracted_pages_count = ?,
                is_scanned = ?,
                status = ?,
                error_message = ?,
                pipeline_version = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (
                res.clean_title, str(res.filepath), res.file_size_bytes,
                res.file_hash, res.total_pages, extracted_pages_count,
                1 if is_scanned else 0, status, res.error_message, pipe_ver, doc_id
            ))
        else:
            cursor = conn.execute("""
            INSERT INTO documents (
                filename, clean_title, filepath, file_size, file_hash,
                total_pages, extracted_pages_count, is_scanned, status, error_message, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                res.filename, res.clean_title, str(res.filepath), res.file_size_bytes,
                res.file_hash, res.total_pages, extracted_pages_count,
                1 if is_scanned else 0, status, res.error_message, pipe_ver
            ))
            doc_id = cursor.lastrowid
        return doc_id

    def _save_quality_record(self, conn: sqlite3.Connection, doc_id: int, res: PDFExtractionResult):
        """Save source quality and raw block metadata to database and persisted sidecar."""
        import hashlib
        raw_all = "".join(p.get("raw_text", "") for p in res.extracted_pages)
        raw_capture_hash = hashlib.sha256(raw_all.encode("utf-8")).hexdigest()

        warnings_json = json.dumps(res.quality_warnings, ensure_ascii=False)
        blocks_count = sum(len(p.get("blocks", [])) for p in res.extracted_pages)
        meta = {
            "filename": res.filename,
            "file_hash": res.file_hash,
            "pipeline_version": res.pipeline_version,
            "raw_capture_hash": raw_capture_hash,
            "total_pages": res.total_pages,
            "failed_pages": res.failed_pages,
            "needs_review": res.needs_review,
            "quality_warnings": res.quality_warnings,
            "pages_summary": getattr(res, "raw_capture", {}).get("pages_summary", []),
            "pages": res.extracted_pages
        }
        meta_json = json.dumps(meta, ensure_ascii=False)

        conn.execute("""
            INSERT INTO document_source_quality (
                doc_id, filename, file_hash, pipeline_version,
                needs_review, quality_warnings, raw_char_count,
                raw_capture_hash, blocks_count, quality_metadata, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(doc_id) DO UPDATE SET
                filename=excluded.filename,
                file_hash=excluded.file_hash,
                pipeline_version=excluded.pipeline_version,
                needs_review=excluded.needs_review,
                quality_warnings=excluded.quality_warnings,
                raw_char_count=excluded.raw_char_count,
                raw_capture_hash=excluded.raw_capture_hash,
                blocks_count=excluded.blocks_count,
                quality_metadata=excluded.quality_metadata,
                updated_at=CURRENT_TIMESTAMP
        """, (
            doc_id, res.filename, res.file_hash, res.pipeline_version,
            1 if res.needs_review else 0, warnings_json, res.total_chars,
            raw_capture_hash, blocks_count, meta_json
        ))


    def _save_chunks(
        self,
        doc_id: int,
        chunks: List[Dict[str, Any]],
        can_embed: bool = False,
        embed_model: Optional[str] = None
    ) -> int:
        """Insert chunk records and optional embeddings in one transaction."""
        rows, prov_rows = self._prepare_chunk_rows(chunks, can_embed, embed_model)
        with self.get_connection() as conn:
            return self._insert_chunk_rows(conn, doc_id, rows, prov_rows)

    def _prepare_chunk_rows(self, chunks, can_embed, embed_model, cancel_event: Optional[threading.Event] = None):
        rows = []
        prov_rows = []
        use_native = False
        native_client = None
        native_identity: Optional[str] = None
        initial_digest: Optional[str] = None
        installed_digests: List[str] = []
        has_unprovenanced = False

        if can_embed and embed_model:
            reject_retired_model(embed_model)
            with self.get_connection() as conn:
                installed_digests, has_unprovenanced = self._corpus_vector_state(conn)

            use_native = self._is_native_embed_target(
                embed_model,
                installed_digests=installed_digests,
                for_write=True,
            )
            if use_native and sys.platform != "darwin":
                if any(d.startswith("bge-m3-mlx-fp16@") for d in installed_digests):
                    raise RuntimeError("Cannot index native BGE pack without native MLX on macOS")
                raise RuntimeError("Native embedding requires macOS")

            if sys.platform == "darwin" and use_native:
                from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
                if NativeEmbeddingClient.available():
                    native_client = NativeEmbeddingClient()
                    native_identity = str(getattr(native_client, "get_identity", lambda: BGE_NATIVE_IDENTITY)() or "").strip()
                    if not native_identity:
                        raise RuntimeError("Không xác minh được digest mô hình native MLX embedding.")
                    self._validate_corpus_digest_for_write(
                        installed_digests,
                        has_unprovenanced,
                        native_identity,
                        "Native BGE",
                    )
                else:
                    raise RuntimeError("Mô hình MLX embedding chưa sẵn sàng trên Mac; không được hạ cấp sang HTTP.")
            else:
                initial_digest = self.ollama.embedding_identity(embed_model)
                if not initial_digest:
                    raise RuntimeError("Không xác minh được digest mô hình embedding cục bộ.")
                self._validate_corpus_digest_for_write(
                    installed_digests,
                    has_unprovenanced,
                    initial_digest,
                    "Ollama embedding",
                )

        import hashlib
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        for start in range(0, len(chunks), 32):
            if cancel_event is not None and cancel_event.is_set():
                raise IndexingCancelledError("Đã hủy tạo vector nhúng theo yêu cầu dừng.")
            batch = chunks[start:start + 32]
            if can_embed and embed_model:
                if use_native and native_client:
                    try:
                        n_params = inspect.signature(native_client.get_batch_embeddings).parameters
                        accepts_cancel = ("cancel_event" in n_params) or any(
                            p.kind == inspect.Parameter.VAR_KEYWORD for p in n_params.values()
                        )
                    except (TypeError, ValueError):
                        accepts_cancel = False
                    if cancel_event is not None and accepts_cancel:
                        vectors = native_client.get_batch_embeddings(
                            [c["text"] for c in batch], max_length=2048, cancel_event=cancel_event
                        )
                    else:
                        vectors = native_client.get_batch_embeddings([c["text"] for c in batch], max_length=2048)
                    model_digest = str(getattr(native_client, "get_identity", lambda: BGE_NATIVE_IDENTITY)() or "").strip()
                    if native_identity and model_digest != native_identity:
                        raise RuntimeError("Mô hình native embedding đã thay đổi trong khi lập chỉ mục.")
                else:
                    model_digest = self.ollama.embedding_identity(embed_model)
                    if not model_digest:
                        raise RuntimeError("Không xác minh được digest mô hình embedding cục bộ.")
                    if initial_digest and model_digest != initial_digest:
                        raise RuntimeError("Mô hình embedding đã thay đổi trong khi lập chỉ mục.")
                    vectors = self.ollama.get_batch_embeddings([c["text"] for c in batch], model=embed_model)
                    if self.ollama.embedding_identity(embed_model) != model_digest:
                        raise RuntimeError("Mô hình embedding đã thay đổi trong khi lập chỉ mục.")
            else:
                vectors = [None] * len(batch)
                model_digest = None
            if len(vectors) != len(batch):
                raise ValueError("Embedding count does not match chunk count")
            if can_embed and embed_model and any(not vec for vec in vectors):
                raise RuntimeError("Embedding backend did not return every requested vector")
            for c, vec in zip(batch, vectors):
                row_idx = len(rows)
                blob = pack_vector(vec) if vec else None
                rows.append((c["book_title"], c["filename"], c["page_num"],
                             c["chunk_index"], c["text"], blob))
                if blob and model_digest:
                    t_sha = hashlib.sha256(c["text"].encode("utf-8")).hexdigest()
                    v_sha = hashlib.sha256(blob).hexdigest()
                    prov_rows.append((row_idx, t_sha, model_digest, v_sha, len(vec), 1, now_iso))
        return rows, prov_rows

    @staticmethod
    def _insert_chunk_rows(conn, doc_id, rows, prov_rows=None):
        if isinstance(rows, tuple) and len(rows) == 2 and prov_rows is None:
            rows, prov_rows = rows
        inserted_ids = []
        for row in rows:
            cursor = conn.execute("""
                INSERT INTO chunks (doc_id, book_title, filename, page_num, chunk_index, text, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (doc_id, *row))
            inserted_ids.append(cursor.lastrowid)

        if prov_rows:
            p_data = []
            for row_idx, t_sha, m_digest, v_sha, dims, full_text, emb_at in prov_rows:
                chunk_id = inserted_ids[row_idx]
                p_data.append((chunk_id, t_sha, m_digest, v_sha, dims, full_text, emb_at))
            conn.executemany("""
                INSERT OR REPLACE INTO corpus_vector_provenance (chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, p_data)
        return len(rows)

    def _corpus_vector_state(self, conn: sqlite3.Connection) -> Tuple[List[str], bool]:
        """Return (distinct_nonempty_model_digests, has_unprovenanced_embeddings)."""
        digests: List[str] = []
        has_unprovenanced = False
        try:
            rows = conn.execute(
                "SELECT DISTINCT TRIM(model_digest) FROM corpus_vector_provenance "
                "WHERE model_digest IS NOT NULL AND TRIM(model_digest) != ''"
            ).fetchall()
            digests = sorted({str(r[0]).strip() for r in rows if str(r[0] or "").strip()})
            unbound_row = conn.execute(
                """
                SELECT 1
                FROM chunks c
                LEFT JOIN corpus_vector_provenance p ON p.chunk_id = c.id
                WHERE c.embedding IS NOT NULL
                  AND (p.chunk_id IS NULL OR COALESCE(TRIM(p.model_digest), '') = '')
                LIMIT 1
                """
            ).fetchone()
            has_unprovenanced = bool(unbound_row)
        except sqlite3.Error:
            pass
        return digests, has_unprovenanced

    @staticmethod
    def _validate_corpus_digest_for_write(
        installed_digests: List[str],
        has_unprovenanced: bool,
        target_digest: Optional[str],
        provider_label: str,
    ) -> None:
        """Refuse silent migration, cross-provider mixing, or overwrite of unprovenanced/retired vectors."""
        if has_unprovenanced:
            raise RuntimeError(
                f"{provider_label} corpus chứa vector chưa rõ nguồn gốc/digest; từ chối thay thế âm thầm."
            )
        if len(installed_digests) > 1:
            raise RuntimeError(
                f"{provider_label} digest không đồng nhất trong corpus; từ chối thay thế âm thầm."
            )
        if len(installed_digests) == 1 and installed_digests[0] != target_digest:
            raise RuntimeError(
                f"{provider_label} digest không khớp với corpus đã cài đặt; từ chối thay thế âm thầm."
            )

    def _is_native_embed_target(
        self,
        embed_model: Optional[str],
        *,
        installed_digests: Optional[List[str]] = None,
        for_write: bool = False,
    ) -> bool:
        """Route between native macOS MLX BGE-M3 and Ollama without silent cross-provider fallback."""
        native_embed_models = {"bge-m3-mlx-fp16", "bge-m3-mlx", "native-bge", "bge-m3"}
        embed_backend = os.getenv("EMBED_BACKEND", "").strip().lower()
        target_model = str(embed_model or "").strip()

        if embed_backend == "ollama":
            return False
        if embed_backend in ("native", "mlx"):
            return True

        digests = list(installed_digests or [])
        if for_write and len(digests) > 1:
            raise RuntimeError(
                "Embedding digest không đồng nhất trong corpus; từ chối thay thế âm thầm."
            )
        if len(digests) == 1:
            only_digest = digests[0]
            if only_digest.startswith("ollama-sha256:"):
                return False
            if sys.platform == "darwin" and only_digest.startswith("bge-m3-mlx-fp16@"):
                return True

        if sys.platform != "darwin":
            return False
        return (
            not target_model
            or target_model in native_embed_models
            or "mlx" in target_model.lower()
        )

    def _resolve_active_embedding_digest(
        self,
        conn: Optional[sqlite3.Connection] = None,
        *,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
    ) -> Optional[str]:
        """Resolve the verified active embedding digest expected by retrieval without loading model weights."""
        target_model = str(embed_model or DEFAULT_EMBED_MODEL or "").strip()
        if not target_model:
            return None
        try:
            reject_retired_model(target_model)
        except ValueError:
            return None

        installed_digests: List[str] = []
        if conn is not None:
            installed_digests, _ = self._corpus_vector_state(conn)

        use_native = self._is_native_embed_target(
            target_model,
            installed_digests=installed_digests,
            for_write=False,
        )
        if use_native:
            if sys.platform != "darwin":
                return None
            try:
                from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient

                if not NativeEmbeddingClient.available():
                    return None
                client = NativeEmbeddingClient()
                ident = str(client.get_status_identity() or "").strip()
                return BGE_NATIVE_IDENTITY if ident == BGE_NATIVE_IDENTITY else None
            except Exception:
                return None

        try:
            ident = self.ollama.embedding_identity(target_model)
        except Exception:
            return None
        ident_str = str(ident or "").strip()
        if not ident_str:
            return None
        try:
            reject_retired_model(ident_str)
        except ValueError:
            return None
        return ident_str

    def _compatible_vectors_summary(
        self,
        conn: sqlite3.Connection,
        *,
        doc_id: Optional[int] = None,
        expected_digest: Optional[str] = None,
        require_1024: bool = False,
    ) -> Tuple[Dict[int, int], Dict[int, int]]:
        """Return ({doc_id: total_chunks}, {doc_id: compatible_chunks}) using strict active-identity validation."""
        total_by_doc: Dict[int, int] = {}
        compat_by_doc: Dict[int, int] = {}
        try:
            where_sql = "WHERE c.doc_id = ?" if doc_id is not None else ""
            params: Tuple[Any, ...] = (doc_id,) if doc_id is not None else ()
            rows = conn.execute(
                f"""
                SELECT c.doc_id, c.text, c.embedding,
                       p.chunk_id AS prov_chunk_id,
                       p.text_sha256, p.model_digest, p.vector_sha256, p.dimensions, p.full_text
                FROM chunks c
                LEFT JOIN corpus_vector_provenance p ON p.chunk_id = c.id
                {where_sql}
                """,
                params,
            ).fetchall()
        except sqlite3.Error:
            where_sql = "WHERE doc_id = ?" if doc_id is not None else ""
            params = (doc_id,) if doc_id is not None else ()
            for r in conn.execute(
                f"SELECT doc_id, COUNT(*) AS total_c FROM chunks {where_sql} GROUP BY doc_id",
                params,
            ).fetchall():
                total_by_doc[int(r["doc_id"])] = int(r["total_c"] or 0)
            return total_by_doc, compat_by_doc

        has_candidate_vectors = any(row["embedding"] is not None for row in rows)
        effective_digest = str(expected_digest or "").strip() or (
            self._resolve_active_embedding_digest(conn) if has_candidate_vectors else None
        )

        for row in rows:
            did = int(row["doc_id"])
            total_by_doc[did] = total_by_doc.get(did, 0) + 1
            prov = row if row["prov_chunk_id"] is not None else None
            if _is_compatible_vector_row(
                row["text"],
                row["embedding"],
                prov,
                expected_digest=effective_digest,
                require_1024=require_1024,
            ):
                compat_by_doc[did] = compat_by_doc.get(did, 0) + 1
        return total_by_doc, compat_by_doc

    def _count_compatible_vectors(
        self,
        conn: sqlite3.Connection,
        *,
        doc_id: Optional[int] = None,
        expected_digest: Optional[str] = None,
        require_1024: bool = False,
    ) -> Tuple[int, int]:
        """Return (total_chunks, compatible_chunks) for a document or whole DB."""
        total_by_doc, compat_by_doc = self._compatible_vectors_summary(
            conn,
            doc_id=doc_id,
            expected_digest=expected_digest,
            require_1024=require_1024,
        )
        if doc_id is not None:
            return total_by_doc.get(int(doc_id), 0), compat_by_doc.get(int(doc_id), 0)
        return sum(total_by_doc.values()), sum(compat_by_doc.values())

    def _load_watcher_sync_flags(self) -> Dict[str, Dict[str, Any]]:
        """Read non-mutating watcher drift annotations (needs_update / missing_source) for this DB."""
        state_path = Path(self.db_path).resolve().parent / "runtime" / "src_watcher_state.json"
        if not state_path.is_file():
            return {}
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, dict):
            return {}
        flags: Dict[str, Dict[str, Any]] = {}
        for rel_name, item in items.items():
            if not isinstance(item, dict):
                continue
            st = item.get("status")
            if st in ("needs_update", "missing_source"):
                flags[str(rel_name)] = {
                    "status": st,
                    "indexed_hash": item.get("indexed_hash"),
                }
        return flags

    def get_status(self) -> Dict[str, Any]:
        """Get summary statistics of the current knowledge base."""
        with self.get_connection() as conn:
            doc_count = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
            scanned_count = conn.execute("SELECT COUNT(*) as c FROM documents WHERE is_scanned = 1").fetchone()["c"]
            chunk_count = conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
            embedded_chunk_count = conn.execute("SELECT COUNT(*) as c FROM chunks WHERE embedding IS NOT NULL").fetchone()["c"]
            total_pages = conn.execute("SELECT SUM(extracted_pages_count) as c FROM documents").fetchone()["c"] or 0

            chunk_stats_by_doc: Dict[int, Tuple[int, int]] = {}
            for c_row in conn.execute(
                "SELECT doc_id, COUNT(*) AS total_c, COUNT(embedding) AS emb_c FROM chunks GROUP BY doc_id"
            ).fetchall():
                chunk_stats_by_doc[c_row["doc_id"]] = (int(c_row["total_c"]), int(c_row["emb_c"]))

            _, compat_by_doc = self._compatible_vectors_summary(conn, require_1024=True)
            compatible_total = sum(compat_by_doc.values())

            docs = conn.execute("""
                SELECT id, filename, file_hash, clean_title, total_pages, extracted_pages_count, is_scanned, status, error_message, pipeline_version, updated_at
                FROM documents ORDER BY filename ASC
            """).fetchall()

            captured: Dict[Tuple[Any, Any], set[int]] = {}
            reviewed: Dict[Tuple[Any, Any], set[int]] = {}
            latest_reviews: Dict[Tuple[Tuple[Any, Any], int], Tuple[Any, Dict[str, Any]]] = {}

            for cache_dir in self._ocr_cache_dirs():
                try:
                    candidates = cache_dir.glob("*.json")
                except Exception:
                    continue
                for cp in candidates:
                    try:
                        entry = json.loads(cp.read_text(encoding="utf-8"))
                        if not isinstance(entry, dict):
                            continue
                        key = (entry.get("source_relpath"), entry.get("source_hash"))
                        page = entry.get("page_num")
                        if type(page) is not int or page < 1:
                            continue
                        if entry.get("status") == "CAPTURED_PENDING_REVIEW" or entry.get("text") or entry.get("is_blank"):
                            captured.setdefault(key, set()).add(page)
                        if entry.get("is_verified") or entry.get("review_schema") or entry.get("verified_by"):
                            identity = (key, page)
                            mtime_ns = cp.stat().st_mtime_ns if isinstance(cp, Path) and cp.exists() else 0
                            order = (entry.get("verified_at") or "", mtime_ns, getattr(cp, "name", str(cp)))
                            previous = latest_reviews.get(identity)
                            if previous is None or order > previous[0]:
                                latest_reviews[identity] = (order, entry)
                    except (OSError, ValueError, TypeError):
                        continue
            for (key, page), (_, entry) in latest_reviews.items():
                is_blank_ver = bool(
                    entry.get("is_verified")
                    and not entry.get("needs_review")
                    and entry.get("is_blank")
                    and (entry.get("status") == "VERIFIED_BLANK" or not entry.get("text"))
                )
                if entry.get("is_verified") and not entry.get("needs_review") and (entry.get("text") or is_blank_ver):
                    reviewed.setdefault(key, set()).add(page)

            try:
                from app.source_page_reviews import latest_page_reviews
                reviews_root = self.src_dir.parent if hasattr(self.src_dir, "parent") else Path.cwd()
                for (r_fn, r_page), (_, r_data) in latest_page_reviews(reviews_root).items():
                    if type(r_page) is not int or r_page < 1 or not r_fn:
                        continue
                    key = (str(Path(r_fn)), r_data.get("source_sha256") or r_data.get("source_hash"))
                    if r_data.get("image_reviewed") is True and r_data.get("completeness") in ("complete", "partial"):
                        captured.setdefault(key, set()).add(r_page)
                        reviewed.setdefault(key, set()).add(r_page)
            except Exception:
                pass

            watcher_flags = self._load_watcher_sync_flags()
            documents = []
            stale_documents = 0
            searchable_documents = 0
            searchable_passages = 0
            ocr_required_documents = 0
            ocr_pending_review_documents = 0
            for row in docs:
                d = dict(row)
                key = (d["filename"], d["file_hash"])
                c = {p for p in captured.get(key, set()) if p <= d["total_pages"]}
                r = {p for p in reviewed.get(key, set()) if p <= d["total_pages"]}
                doc_chunks, doc_embedded = chunk_stats_by_doc.get(d["id"], (0, 0))
                doc_compat = compat_by_doc.get(d["id"], 0)
                vectors_ready = bool(doc_chunks > 0 and doc_compat >= doc_chunks)
                embedding_status = (
                    "vectorized"
                    if vectors_ready
                    else ("partial" if doc_compat > 0 else ("vector_pending" if doc_embedded > 0 or doc_chunks > 0 else "none"))
                )
                d.update(
                    ocr_captured_pages=len(c),
                    ocr_reviewed_pages=len(r),
                    ocr_pending_review_pages=len(c - r),
                    chunk_count=doc_chunks,
                    embedded_chunks=doc_embedded,
                    compatible_embeddings=doc_compat,
                    vectors_ready=vectors_ready,
                    embedding_status=embedding_status,
                )
                flag = watcher_flags.get(d["filename"])
                if flag and flag.get("indexed_hash") in (None, d.get("file_hash")):
                    d["sync_status"] = flag["status"]
                    d["passages_current"] = False
                    stale_documents += 1
                elif d["status"] in ("indexed", "ocr_partial_reviewed"):
                    d["sync_status"] = "current"
                    d["passages_current"] = True
                else:
                    d["sync_status"] = d["status"]
                    d["passages_current"] = False

                is_searchable = bool(
                    doc_chunks > 0
                    and d["status"] in ("indexed", "ocr_partial_reviewed")
                    and d["passages_current"]
                )
                d["searchable"] = is_searchable
                if is_searchable:
                    searchable_documents += 1
                    searchable_passages += doc_chunks
                if d["status"] in ("scanned_unocred", "ocr_partial_reviewed") or d["ocr_pending_review_pages"] > 0:
                    ocr_required_documents += 1
                if d["ocr_pending_review_pages"] > 0 or (d["is_scanned"] and d["status"] == "scanned_unocred"):
                    ocr_pending_review_documents += 1
                documents.append(d)
            return {
                "total_documents": doc_count,
                "registered_documents": doc_count,
                "searchable_documents": searchable_documents,
                "searchable_passages": searchable_passages,
                "scanned_documents": scanned_count,
                "total_indexed_pages": total_pages,
                "total_chunks": chunk_count,
                "embedded_chunks": embedded_chunk_count,
                "embeddings_ready": compatible_total,
                "embeddings_pending": max(0, chunk_count - compatible_total),
                "stale_documents": stale_documents,
                "ocr_required_documents": ocr_required_documents,
                "ocr_pending_review_documents": ocr_pending_review_documents,
                "ocr_captured_pages": sum(d["ocr_captured_pages"] for d in documents),
                "ocr_reviewed_pages": sum(d["ocr_reviewed_pages"] for d in documents),
                "ocr_pending_review_pages": sum(d["ocr_pending_review_pages"] for d in documents),
                "documents": documents,
            }

    def update_page_chunks(
        self,
        filename: str,
        page_num: int,
        text: str,
        *,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
        gpu_timeout: float = 30.0,
        cancel_event: Optional[threading.Event] = None,
        is_blank: bool = False,
        is_partial: bool = False,
    ) -> Dict[str, Any]:
        """Update chunks, compatible embeddings, and FTS for an affected page after human review."""
        if type(page_num) is not int or page_num < 1:
            raise ValueError("Trang hoặc nội dung đã duyệt không hợp lệ")
        if not is_blank and (not isinstance(text, str) or not text.strip()):
            raise ValueError("Trang hoặc nội dung đã duyệt không hợp lệ")
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT id, filename, file_hash, clean_title, total_pages, is_scanned, status FROM documents WHERE filename = ?",
                (filename,)
            ).fetchone()
            if not row:
                logger.warning(f"Không tìm thấy tài liệu {filename} trong documents để cập nhật page chunks.")
                return {"status": "not_found", "chunks_updated": 0}

            doc_id = row["id"]
            clean_title = row["clean_title"]
            rel_filename = row["filename"]
            if page_num > row["total_pages"]:
                raise ValueError("Trang được duyệt nằm ngoài tài liệu")

        chunk_records = (
            []
            if (is_blank and not (text or "").strip())
            else TextChunker.chunk_page(clean_title, rel_filename, page_num, text)
        )

        vector_error: Optional[str] = None
        prepared_rows: List[Tuple[Any, ...]] = []
        prov_rows: List[Tuple[Any, ...]] = []

        if chunk_records:
            can_embed = False
            resolved_embed_model: Optional[str] = None
            explicit_offline_mock = (
                "check_health" in getattr(self.ollama, "__dict__", {})
                and callable(self.ollama.check_health)
                and not self.ollama.check_health()
            )
            if embed_model and not explicit_offline_mock:
                try:
                    can_embed, resolved_embed_model, _ = self._resolve_embedding_config(
                        embed_model,
                        gpu_timeout=gpu_timeout,
                        cancel_event=cancel_event,
                    )
                except IndexingCancelledError:
                    raise
                except Exception as exc:
                    can_embed = False
                    resolved_embed_model = None
                    vector_error = sanitize_error_detail(str(exc)) or "Không thể kết nối mô hình embedding"
            if can_embed and resolved_embed_model:
                try:
                    with _acquire_inference_lock(timeout=gpu_timeout, cancel_event=cancel_event):
                        prepared_rows, prov_rows = self._prepare_chunk_rows(
                            chunk_records, True, resolved_embed_model, cancel_event=cancel_event
                        )
                except IndexingCancelledError:
                    raise
                except Exception as exc:
                    vector_error = sanitize_error_detail(str(exc)) or "Không thể tạo vector nhúng"
                    logger.warning(
                        "Vector generation skipped/deferred in update_page_chunks for '%s' p.%s: %s",
                        rel_filename,
                        page_num,
                        vector_error,
                    )
                    prepared_rows, prov_rows = self._prepare_chunk_rows(
                        chunk_records, False, None, cancel_event=cancel_event
                    )
            else:
                prepared_rows, prov_rows = self._prepare_chunk_rows(
                    chunk_records, False, None, cancel_event=cancel_event
                )

        with corpus_writer_lock(self.db_path, cancel_event=cancel_event), self.get_connection() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ? AND page_num = ?", (doc_id, page_num))
            inserted = self._insert_chunk_rows(conn, doc_id, prepared_rows, prov_rows) if prepared_rows else 0
            pages = conn.execute("SELECT COUNT(DISTINCT page_num) FROM chunks WHERE doc_id=?", (doc_id,)).fetchone()[0]
            if pages == 0:
                state = "scanned_unocred" if row["is_scanned"] else row["status"]
            elif is_partial:
                state = "ocr_partial_reviewed" if row["is_scanned"] else "indexed"
            else:
                state = "indexed" if pages >= row["total_pages"] else ("ocr_partial_reviewed" if row["is_scanned"] else "indexed")
            conn.execute(
                "UPDATE documents SET extracted_pages_count=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (pages, state, doc_id),
            )
            total_doc_chunks, total_doc_embedded = self._count_compatible_vectors(
                conn,
                doc_id=doc_id,
                require_1024=True,
            )
            emb_doc_chunks = int(
                conn.execute(
                    "SELECT COUNT(embedding) FROM chunks WHERE doc_id = ?",
                    (doc_id,),
                ).fetchone()[0]
                or 0
            )

        from app.source_freshness import clear_freshness_cache
        clear_freshness_cache()

        embedded_page_chunks = sum(1 for r in prepared_rows if r[5] is not None)
        vectors_ready = bool(total_doc_chunks > 0 and total_doc_embedded >= total_doc_chunks)
        embedding_status = (
            "vectorized"
            if vectors_ready
            else ("partial" if total_doc_embedded > 0 else ("vector_pending" if (vector_error or emb_doc_chunks > 0) else "fts_only"))
        )
        logger.info(f"Đã cập nhật {inserted} chunks cho tài liệu '{rel_filename}' trang {page_num} sau khi review OCR.")
        result = {
            "status": "success",
            "doc_id": doc_id,
            "filename": rel_filename,
            "page_num": page_num,
            "chunks_updated": inserted,
            "embedded_chunks": embedded_page_chunks,
            "vectors_ready": vectors_ready,
            "embedding_status": embedding_status,
            "document_status": state,
        }
        if vector_error:
            result["vector_error"] = vector_error
        return result
