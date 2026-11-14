"""Shared bounded freshness admission contract for indexed shared-corpus sources."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat as stat_mod
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from app.material_storage import SUPPORTED_EXTENSIONS, resolve_safe_source_file

_INDEXED_STATUSES = ("indexed", "ocr_partial_reviewed")
_STALE_WATCHER_STATUSES = {"needs_update", "missing_source", "error"}
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")

_MAX_HASH_CACHE_ENTRIES = 4096
_CACHE_LOCK = threading.Lock()
_FILE_HASH_CACHE: "OrderedDict[Tuple[str, int, int, int, int, int], str]" = OrderedDict()
_WATCHER_STATE_CACHE: "OrderedDict[str, Tuple[int, int, Dict[str, Dict[str, Any]]]]" = OrderedDict()


@dataclass(frozen=True)
class FreshnessResult:
    """Result of verifying indexed document metadata against disk and watcher state."""

    doc_id: Optional[int]
    filename: str
    fresh: bool
    status: str
    reason_code: str
    message: str
    resolved_path: Optional[Path] = None
    has_real_hash: bool = False
    db_file_hash: Optional[str] = None
    disk_sha256: Optional[str] = None
    watcher_status: Optional[str] = None


def normalize_relpath(name: Optional[str]) -> str:
    """Normalize relative path separators without removing parent traversal tokens."""
    if not name:
        return ""
    cleaned = str(name).replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def is_sha256_hex(value: Any) -> bool:
    """Return True if value looks like a canonical 64-char SHA-256 hex digest."""
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(ch in _HEX_CHARS for ch in value)


class FileIdentityChangedError(Exception):
    """Raised when an open source file changes identity, metadata, or bytes during inspection."""


class UnsafeSourcePathError(Exception):
    """Raised when source path containment or symlink safety fails during inspection."""


def clear_freshness_cache() -> None:
    """Clear bounded stat->sha256 and watcher-state caches (used in tests or after re-index)."""
    with _CACHE_LOCK:
        _FILE_HASH_CACHE.clear()
        _WATCHER_STATE_CACHE.clear()


def _stat_identity(st: os.stat_result) -> Tuple[int, int, int, int, int]:
    """Return canonical file identity and metadata version tuple."""
    return (
        int(st.st_dev),
        int(st.st_ino),
        int(st.st_size),
        int(st.st_mtime_ns),
        int(st.st_ctime_ns),
    )


def _evict_hash_cache_key(key: Tuple[str, int, int, int, int, int]) -> None:
    with _CACHE_LOCK:
        _FILE_HASH_CACHE.pop(key, None)


def _evict_hash_cache_path(resolved_path: str) -> None:
    with _CACHE_LOCK:
        stale_keys = [k for k in _FILE_HASH_CACHE if k[0] == resolved_path]
        for k in stale_keys:
            _FILE_HASH_CACHE.pop(k, None)


def _open_pinned_identity(filepath: Path) -> Tuple[Optional[int], str, Tuple[int, int, int, int, int]]:
    """Open filepath read-only to pin its inode on POSIX and capture its resolved path + stat identity."""
    open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    pinned_fd: Optional[int] = None
    try:
        pinned_fd = os.open(str(filepath), open_flags)
        st_open = os.fstat(pinned_fd)
        if not stat_mod.S_ISREG(st_open.st_mode):
            raise FileIdentityChangedError("Tệp nguồn không phải tệp thông thường.")
        resolved_before = str(filepath.resolve(strict=True))
        id_before = _stat_identity(st_open)
        if _stat_identity(filepath.stat()) != id_before:
            raise FileIdentityChangedError("Tệp nguồn thay đổi trước khi đọc chỉ mục băm.")
    except Exception:
        if pinned_fd is not None:
            os.close(pinned_fd)
        raise
    return pinned_fd, resolved_before, id_before


def _verify_pinned_identity(
    filepath: Path,
    pinned_fd: Optional[int],
    resolved_before: str,
    id_before: Tuple[int, int, int, int, int],
) -> None:
    """Ensure an opened descriptor and its filesystem path still refer to the exact same version."""
    if pinned_fd is not None and _stat_identity(os.fstat(pinned_fd)) != id_before:
        _evict_hash_cache_path(resolved_before)
        raise FileIdentityChangedError("Tệp nguồn thay đổi trong lúc kiểm tra.")
    resolved_after = str(filepath.resolve(strict=True))
    if resolved_after != resolved_before or _stat_identity(filepath.stat()) != id_before:
        _evict_hash_cache_path(resolved_before)
        raise FileIdentityChangedError("Tệp nguồn thay đổi sau khi kiểm tra.")


def _compute_file_sha256(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with filepath.open("rb") as stream:
        fd = stream.fileno()
        st_open = os.fstat(fd)
        if not stat_mod.S_ISREG(st_open.st_mode):
            raise FileIdentityChangedError("Tệp nguồn không phải tệp thông thường.")
        before_open = _stat_identity(st_open)
        if _stat_identity(filepath.stat()) != before_open:
            raise FileIdentityChangedError("Tệp nguồn thay đổi danh tính trước khi đọc.")
        while chunk := stream.read(65536):
            hasher.update(chunk)
        if _stat_identity(os.fstat(fd)) != before_open or _stat_identity(filepath.stat()) != before_open:
            raise FileIdentityChangedError("Tệp nguồn thay đổi trong lúc băm kiểm tra.")
    return hasher.hexdigest()


def _get_cached_file_sha256(filepath: Path) -> str:
    pinned_fd, resolved_before, id_before = _open_pinned_identity(filepath)
    key = (resolved_before, *id_before)
    with _CACHE_LOCK:
        cached = _FILE_HASH_CACHE.get(key)
        if cached is not None:
            _FILE_HASH_CACHE.move_to_end(key)

    if cached is not None:
        try:
            _verify_pinned_identity(filepath, pinned_fd, resolved_before, id_before)
            return cached
        finally:
            if pinned_fd is not None:
                os.close(pinned_fd)

    try:
        if os.name == "nt" and pinned_fd is not None:
            # Windows file replace cannot overwrite an open handle; rely on pre/post identity check on nt.
            os.close(pinned_fd)
            pinned_fd = None
        digest = _compute_file_sha256(filepath)
        _verify_pinned_identity(filepath, pinned_fd, resolved_before, id_before)
    except Exception:
        _evict_hash_cache_key(key)
        raise
    finally:
        if pinned_fd is not None:
            os.close(pinned_fd)

    with _CACHE_LOCK:
        _FILE_HASH_CACHE[key] = digest
        _FILE_HASH_CACHE.move_to_end(key)
        while len(_FILE_HASH_CACHE) > _MAX_HASH_CACHE_ENTRIES:
            _FILE_HASH_CACHE.popitem(last=False)
    return digest


def _is_default_db_path(db_path: Optional[Path]) -> bool:
    import app.config as cfg

    if db_path is None:
        return True
    try:
        return Path(db_path).resolve() == Path(cfg.DB_PATH).resolve()
    except OSError:
        return str(db_path) == str(cfg.DB_PATH)


def _resolve_watcher_state_path(data_dir: Optional[Path] = None, db_path: Optional[Path] = None) -> Optional[Path]:
    import app.config as cfg

    if data_dir is not None:
        return Path(data_dir) / "runtime" / "src_watcher_state.json"
    if _is_default_db_path(db_path):
        return Path(cfg.DATA_DIR) / "runtime" / "src_watcher_state.json"
    if db_path is not None:
        return Path(db_path).parent / "runtime" / "src_watcher_state.json"
    return None


def load_watcher_state_items(
    data_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Dict[str, Any]]:
    """Load persisted watcher item statuses keyed by normalized relative filename."""
    state_path = _resolve_watcher_state_path(data_dir=data_dir, db_path=db_path)
    if state_path is None or not state_path.is_file():
        return {}
    try:
        st = state_path.stat()
    except OSError:
        return {}
    cache_key = str(state_path)
    sig = (int(st.st_mtime_ns), int(st.st_size))
    with _CACHE_LOCK:
        entry = _WATCHER_STATE_CACHE.get(cache_key)
        if entry is not None and entry[0] == sig[0] and entry[1] == sig[1]:
            _WATCHER_STATE_CACHE.move_to_end(cache_key)
            return entry[2]

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    mapped: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            rel = normalize_relpath(item.get("filename"))
            if not rel:
                continue
            mapped[rel] = item
            nfc = unicodedata.normalize("NFC", rel)
            if nfc != rel and nfc not in mapped:
                mapped[nfc] = item

    with _CACHE_LOCK:
        _WATCHER_STATE_CACHE[cache_key] = (sig[0], sig[1], mapped)
        _WATCHER_STATE_CACHE.move_to_end(cache_key)
        while len(_WATCHER_STATE_CACHE) > 128:
            _WATCHER_STATE_CACHE.popitem(last=False)
    return mapped


def _lookup_watcher_item(
    watcher_items: Mapping[str, Dict[str, Any]],
    norm_filename: str,
) -> Optional[Dict[str, Any]]:
    item = watcher_items.get(norm_filename)
    if item is not None:
        return item
    return watcher_items.get(unicodedata.normalize("NFC", norm_filename))


def _row_to_dict(doc: Mapping[str, Any]) -> Dict[str, Any]:
    if isinstance(doc, sqlite3.Row):
        return {k: doc[k] for k in doc.keys()}
    return dict(doc)


def infer_src_dir(
    doc: Mapping[str, Any],
    db_path: Optional[Path] = None,
    explicit_src_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Infer the source root for a document row when an explicit src_dir is absent."""
    import app.config as cfg

    if explicit_src_dir is not None:
        return Path(explicit_src_dir)
    if _is_default_db_path(db_path):
        return Path(cfg.SRC_DIR)

    doc_dict = _row_to_dict(doc)
    raw_fp = str(doc_dict.get("filepath") or "").strip()
    norm_fn = normalize_relpath(doc_dict.get("filename"))
    if raw_fp and norm_fn:
        fp = Path(raw_fp.replace("\\", "/"))
        fn_parts = Path(norm_fn).parts
        if fp.is_absolute() and fn_parts and ".." not in fn_parts and len(fp.parts) > len(fn_parts):
            tail = [unicodedata.normalize("NFC", p) for p in fp.parts[-len(fn_parts):]]
            expected = [unicodedata.normalize("NFC", p) for p in fn_parts]
            if tail == expected:
                return Path(*fp.parts[:-len(fn_parts)])

    if db_path is not None:
        db_p = Path(db_path)
        sibling_src = db_p.parent / "src"
        if sibling_src.is_dir():
            return sibling_src
        parent_src = db_p.parent.parent / "src"
        if db_p.parent.name in {"data", "runtime"} and parent_src.is_dir():
            return parent_src
    return None


def _is_lexical_unsafe_filename(norm_fn: str) -> bool:
    if not norm_fn or "\x00" in norm_fn:
        return True
    p = Path(norm_fn)
    if p.is_absolute() or ".." in p.parts:
        return True
    return False


def _resolve_disk_candidate(
    norm_fn: str,
    target_src: Optional[Path],
    raw_filepath: str,
) -> Tuple[Optional[Path], bool]:
    """Return (safe_disk_path_or_None, unsafe_path_violation)."""
    if target_src is not None:
        try:
            resolved_root = target_src.resolve()
        except OSError:
            return None, True
        raw_cand = target_src / norm_fn
        try:
            if raw_cand.is_symlink() and not raw_cand.resolve(strict=False).is_relative_to(resolved_root):
                return None, True
            resolved_cand = raw_cand.resolve(strict=False)
            if not resolved_cand.is_relative_to(resolved_root):
                return None, True
        except OSError:
            return None, True
        if Path(norm_fn).suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None, True
        safe_path = resolve_safe_source_file(norm_fn, search_dirs=[target_src])
        return safe_path, False

    if raw_filepath:
        fp = Path(raw_filepath)
        if fp.is_absolute():
            if fp.is_symlink():
                try:
                    resolved = fp.resolve(strict=False)
                    if resolved != fp and not resolved.is_relative_to(fp.parent.resolve()):
                        return None, True
                except OSError:
                    return None, True
            if fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
                return None, True
            return (fp if fp.is_file() else None), False
    return None, False


def _invalid_freshness(
    doc_id: Optional[int],
    filename: str,
    status: str,
    reason_code: str,
    message: str,
    *,
    resolved_path: Optional[Path] = None,
    has_real_hash: bool = False,
    db_file_hash: Optional[str] = None,
    disk_sha256: Optional[str] = None,
    watcher_status: Optional[str] = None,
) -> FreshnessResult:
    return FreshnessResult(
        doc_id=doc_id,
        filename=filename,
        fresh=False,
        status=status,
        reason_code=reason_code,
        message=message,
        resolved_path=resolved_path,
        has_real_hash=has_real_hash,
        db_file_hash=db_file_hash,
        disk_sha256=disk_sha256,
        watcher_status=watcher_status,
    )


def assess_document_freshness(
    doc: Mapping[str, Any],
    *,
    db_path: Optional[Path] = None,
    src_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    watcher_items: Optional[Mapping[str, Dict[str, Any]]] = None,
    require_disk_if_unhashed: bool = True,
) -> FreshnessResult:
    """Assess whether an indexed document row strictly matches a safe, unchanged original on disk."""
    doc_dict = _row_to_dict(doc)
    doc_id = doc_dict.get("id")
    raw_filename = doc_dict.get("filename")
    status = doc_dict.get("status")
    file_hash = doc_dict.get("file_hash")
    raw_filepath = str(doc_dict.get("filepath") or "").strip()

    if status is not None and status not in _INDEXED_STATUSES:
        return _invalid_freshness(
            doc_id,
            str(raw_filename or ""),
            "not_indexed",
            "source_not_indexed",
            "Tài liệu đã chọn chưa hoàn tất lập chỉ mục hoặc chưa sẵn sàng sử dụng.",
            has_real_hash=is_sha256_hex(file_hash),
            db_file_hash=file_hash,
        )

    if not raw_filename:
        return FreshnessResult(
            doc_id=doc_id,
            filename="",
            fresh=True,
            status="fresh",
            reason_code="fresh",
            message="",
            db_file_hash=file_hash,
        )

    norm_fn = normalize_relpath(str(raw_filename))
    if _is_lexical_unsafe_filename(norm_fn):
        return _invalid_freshness(
            doc_id,
            norm_fn,
            "unsafe_source_path",
            "source_identity_mismatch",
            "Định danh tệp tài liệu nguồn không hợp lệ hoặc vượt ngoài phạm vi cho phép.",
            has_real_hash=is_sha256_hex(file_hash),
            db_file_hash=file_hash,
        )

    if watcher_items is None:
        watcher_items = load_watcher_state_items(data_dir=data_dir, db_path=db_path)
    w_item = _lookup_watcher_item(watcher_items, norm_fn)
    w_status = w_item.get("status") if w_item else None
    w_stale = bool(w_item and (w_item.get("stale") or w_status in _STALE_WATCHER_STATUSES))

    has_real_hash = is_sha256_hex(file_hash)
    target_src = infer_src_dir(doc_dict, db_path=db_path, explicit_src_dir=src_dir)
    disk_path, unsafe_path = _resolve_disk_candidate(norm_fn, target_src, raw_filepath)

    if unsafe_path:
        return _invalid_freshness(
            doc_id,
            norm_fn,
            "unsafe_source_path",
            "source_identity_mismatch",
            "Đường dẫn tài liệu nguồn không hợp lệ hoặc nằm ngoài thư mục nguồn cho phép.",
            has_real_hash=has_real_hash,
            db_file_hash=file_hash,
            watcher_status=w_status,
        )

    if disk_path is not None and disk_path.is_file():
        admit_fd: Optional[int] = None
        try:
            admit_fd, admit_resolved, admit_id = _open_pinned_identity(disk_path)
            if os.name == "nt" and admit_fd is not None:
                os.close(admit_fd)
                admit_fd = None

            if has_real_hash:
                disk_sha256 = _get_cached_file_sha256(disk_path)
                if disk_sha256.casefold() != str(file_hash).casefold():
                    return _invalid_freshness(
                        doc_id,
                        norm_fn,
                        "needs_update",
                        "source_stale",
                        "Tài liệu gốc đã thay đổi trên đĩa sau khi lập chỉ mục. Vui lòng cập nhật chỉ mục trước khi sử dụng hoặc đối chiếu.",
                        resolved_path=disk_path,
                        has_real_hash=True,
                        db_file_hash=file_hash,
                        disk_sha256=disk_sha256,
                        watcher_status=w_status,
                    )
            elif w_stale:
                return _invalid_freshness(
                    doc_id,
                    norm_fn,
                    w_status if w_status in _STALE_WATCHER_STATUSES else "needs_update",
                    "source_stale",
                    "Tài liệu gốc đang ở trạng thái cần cập nhật chỉ mục trước khi sử dụng.",
                    resolved_path=disk_path,
                    has_real_hash=False,
                    db_file_hash=file_hash,
                    watcher_status=w_status,
                )

            rechecked_path, unsafe_now = _resolve_disk_candidate(norm_fn, target_src, raw_filepath)
            if unsafe_now:
                _evict_hash_cache_path(admit_resolved)
                raise UnsafeSourcePathError("Đường dẫn tài liệu nguồn vi phạm ranh giới an toàn.")
            if rechecked_path is None:
                _evict_hash_cache_path(admit_resolved)
                raise FileNotFoundError(str(disk_path))
            _verify_pinned_identity(rechecked_path, admit_fd, admit_resolved, admit_id)

            return FreshnessResult(
                doc_id=doc_id,
                filename=norm_fn,
                fresh=True,
                status="fresh",
                reason_code="fresh",
                message="",
                resolved_path=rechecked_path,
                has_real_hash=has_real_hash,
                db_file_hash=file_hash,
                disk_sha256=disk_sha256 if has_real_hash else None,
                watcher_status=w_status,
            )
        except (FileIdentityChangedError, UnsafeSourcePathError, OSError) as exc:
            rechecked_path, unsafe_now = _resolve_disk_candidate(norm_fn, target_src, raw_filepath)
            if isinstance(exc, UnsafeSourcePathError) or unsafe_now:
                return _invalid_freshness(
                    doc_id,
                    norm_fn,
                    "unsafe_source_path",
                    "source_identity_mismatch",
                    "Đường dẫn tài liệu nguồn không hợp lệ hoặc nằm ngoài thư mục nguồn cho phép.",
                    has_real_hash=has_real_hash,
                    db_file_hash=file_hash,
                    watcher_status=w_status,
                )
            if rechecked_path is None or isinstance(exc, FileNotFoundError):
                return _invalid_freshness(
                    doc_id,
                    norm_fn,
                    "missing_source",
                    "source_missing",
                    "Không thể đọc tệp gốc trên đĩa khớp với chỉ mục hiện tại.",
                    has_real_hash=has_real_hash,
                    db_file_hash=file_hash,
                    watcher_status=w_status,
                )
            return _invalid_freshness(
                doc_id,
                norm_fn,
                "needs_update",
                "source_stale",
                "Tài liệu gốc đã thay đổi trên đĩa trong lúc kiểm tra. Vui lòng cập nhật chỉ mục trước khi sử dụng hoặc đối chiếu.",
                resolved_path=rechecked_path,
                has_real_hash=has_real_hash,
                db_file_hash=file_hash,
                watcher_status=w_status,
            )
        finally:
            if admit_fd is not None:
                os.close(admit_fd)

    if w_stale or has_real_hash or require_disk_if_unhashed:
        return _invalid_freshness(
            doc_id,
            norm_fn,
            "missing_source",
            "source_missing",
            "Không tìm thấy tệp gốc khớp với chỉ mục (tệp có thể đã bị xóa hoặc di chuyển). Vui lòng kiểm tra thư mục nguồn và cập nhật chỉ mục.",
            has_real_hash=has_real_hash,
            db_file_hash=file_hash,
            watcher_status=w_status,
        )

    return FreshnessResult(
        doc_id=doc_id,
        filename=norm_fn,
        fresh=True,
        status="fresh",
        reason_code="fresh",
        message="",
        has_real_hash=False,
        db_file_hash=file_hash,
        watcher_status=w_status,
    )


def table_columns(conn: sqlite3.Connection, table_name: str) -> Set[str]:
    """Return existing lowercase column names for table_name, or empty set if missing."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.Error:
        return set()
    cols: Set[str] = set()
    for row in rows:
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        cols.add(str(name).casefold())
    return cols


def fetch_document_row(
    conn: sqlite3.Connection,
    *,
    doc_id: Optional[int] = None,
    filename: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a single documents row safely across legacy and full schemas."""
    cols = table_columns(conn, "documents")
    if not cols:
        return None
    wanted = [c for c in ("id", "filename", "clean_title", "filepath", "file_size", "file_hash", "total_pages", "is_scanned", "status") if c in cols]
    if "id" not in wanted:
        return None
    select_sql = ", ".join(wanted)
    if doc_id is not None:
        row = conn.execute(f"SELECT {select_sql} FROM documents WHERE id = ?", (doc_id,)).fetchone()
    elif filename is not None and "filename" in cols:
        norm = normalize_relpath(filename)
        row = conn.execute(f"SELECT {select_sql} FROM documents WHERE filename = ?", (filename,)).fetchone()
        if row is None and norm != filename:
            row = conn.execute(f"SELECT {select_sql} FROM documents WHERE filename = ?", (norm,)).fetchone()
    else:
        return None
    return _row_to_dict(row) if row is not None else None


def check_doc_freshness(
    conn: sqlite3.Connection,
    doc_id: int,
    *,
    db_path: Optional[Path] = None,
    src_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    require_disk_if_unhashed: bool = True,
) -> Optional[FreshnessResult]:
    """Verify freshness of doc_id. Returns None only when the DB has no documents table."""
    cols = table_columns(conn, "documents")
    if not cols:
        return None
    row = fetch_document_row(conn, doc_id=doc_id)
    if row is None:
        return _invalid_freshness(
            doc_id,
            "",
            "missing_source",
            "source_missing",
            "Không tìm thấy tài liệu đã chọn trong chỉ mục thư viện.",
        )
    return assess_document_freshness(
        row,
        db_path=db_path,
        src_dir=src_dir,
        data_dir=data_dir,
        require_disk_if_unhashed=require_disk_if_unhashed,
    )


def get_fresh_doc_ids(
    conn: sqlite3.Connection,
    *,
    db_path: Optional[Path] = None,
    src_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    candidate_doc_ids: Optional[Iterable[int]] = None,
    require_disk_if_unhashed: bool = True,
) -> Optional[List[int]]:
    """Return sorted fresh indexed doc_ids, or None when DB has no documents table."""
    cols = table_columns(conn, "documents")
    if not cols or "id" not in cols:
        return None
    wanted = [c for c in ("id", "filename", "clean_title", "filepath", "file_size", "file_hash", "total_pages", "status") if c in cols]
    sql = f"SELECT {', '.join(wanted)} FROM documents"
    clauses: List[str] = []
    params: List[Any] = []
    if "status" in cols:
        clauses.append("status IN ('indexed', 'ocr_partial_reviewed')")
    if candidate_doc_ids is not None:
        ids_list = [int(i) for i in candidate_doc_ids]
        if not ids_list:
            return []
        placeholders = ",".join("?" for _ in ids_list)
        clauses.append(f"id IN ({placeholders})")
        params.extend(ids_list)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"

    rows = conn.execute(sql, params).fetchall()
    watcher_items = load_watcher_state_items(data_dir=data_dir, db_path=db_path)
    fresh_ids: List[int] = []
    for row in rows:
        row_dict = _row_to_dict(row)
        res = assess_document_freshness(
            row_dict,
            db_path=db_path,
            src_dir=src_dir,
            data_dir=data_dir,
            watcher_items=watcher_items,
            require_disk_if_unhashed=require_disk_if_unhashed,
        )
        if res.fresh and res.doc_id is not None:
            fresh_ids.append(int(res.doc_id))
    return fresh_ids
