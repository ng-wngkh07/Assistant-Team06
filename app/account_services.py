"""Account-scoped services, without process-wide environment changes."""
from collections import OrderedDict
from collections.abc import MutableMapping
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from threading import RLock
import time
from typing import Any, Dict, Generator, Iterator, List, Optional
import uuid

from app.accounts import account_id, private_dir, root_dir
from app.db import (
    atomic_transaction,
    open_sqlite,
    secure_dir_permissions,
    secure_file_permissions,
    uploads_migrations,
    validate_path_containment,
)

logger = logging.getLogger("psychology_agent.account_services")


class AccountService:
    def __init__(self, local, factory):
        object.__setattr__(self, '_local', local)
        object.__setattr__(self, '_factory', factory)
        object.__setattr__(self, '_instances', OrderedDict())
        object.__setattr__(self, '_lock', RLock())

    def current(self):
        identity = account_id.get()
        if identity is None:
            return self._local
        key = str(private_dir())
        with self._lock:
            if key not in self._instances:
                self._instances[key] = self._factory()
            self._instances.move_to_end(key)
            while len(self._instances) > 64:
                self._instances.popitem(last=False)
            return self._instances[key]

    def __getattr__(self, name):
        return getattr(self.current(), name)

    def __setattr__(self, name, value):
        setattr(self.current(), name, value)


def compute_sha256_file(path: Path) -> str:
    """Compute sha256 checksum of a file using chunked streaming."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class CorruptedOriginalError(KeyError, RuntimeError):
    """Raised when an on-disk original file fails cryptographic hash verification."""
    pass


class LazyRawFilesMapping(MutableMapping):
    """Dictionary-like mapping that reads content-addressed files on demand without all-BLOB RAM loading."""

    def __init__(self, manager: "PrivateMaterials"):
        self._manager = manager
        self._mem_cache: Dict[str, bytes] = {}
        self._lock = RLock()

    MAX_CACHE_BYTES = 4 * 1024 * 1024

    def _remember(self, key: str, data: bytes) -> None:
        self._mem_cache.pop(key, None)
        if len(data) > self.MAX_CACHE_BYTES:
            return
        while self._mem_cache and (
            len(self._mem_cache) >= 50
            or sum(map(len, self._mem_cache.values())) + len(data) > self.MAX_CACHE_BYTES
        ):
            self._mem_cache.pop(next(iter(self._mem_cache)))
        self._mem_cache[key] = data

    def _read_from_storage(self, file_hash: str) -> Optional[bytes]:
        if not file_hash:
            return None
        orig_path = self._manager.originals_dir / f"{file_hash}.bin"
        validate_path_containment(orig_path, self._manager.originals_dir)
        if orig_path.exists():
            data = orig_path.read_bytes()
            if hashlib.sha256(data).hexdigest() == file_hash:
                return data
            logger.error("Corrupted original file detected: %s", file_hash)
            raise CorruptedOriginalError(
                f"Corrupted original file detected for hash {file_hash}: hash mismatch on disk"
            )
        return None

    def __getitem__(self, key: str) -> bytes:
        with self._lock:
            with self._manager.connect() as db:
                row = db.execute(
                    "SELECT file_hash, content FROM files WHERE key = ?", (key,)
                ).fetchone()
            if not row:
                self._mem_cache.pop(key, None)
                raise KeyError(key)

            file_hash = row["file_hash"]
            content = row["content"]

            if file_hash:
                if key in self._mem_cache:
                    cached = self._mem_cache[key]
                    if hashlib.sha256(cached).hexdigest() == file_hash:
                        orig_path = self._manager.originals_dir / f"{file_hash}.bin"
                        validate_path_containment(orig_path, self._manager.originals_dir)
                        if not orig_path.is_file() or compute_sha256_file(orig_path) != file_hash:
                            self._mem_cache.pop(key, None)
                            raise CorruptedOriginalError(f"Corrupted or missing original detected for hash {file_hash}")
                        self._mem_cache.pop(key)
                        self._mem_cache[key] = cached
                        return cached
                    self._mem_cache.pop(key, None)

                data = self._read_from_storage(file_hash)
                if data is not None:
                    self._remember(key, data)
                    return data
                raise KeyError(f"Missing original for hash {file_hash}")

            if content is not None:
                self._remember(key, content)
                return content

            raise KeyError(key)

    def __setitem__(self, key: str, value: bytes) -> None:
        self._manager.cache_raw_file(key, value)

    def __delitem__(self, key: str) -> None:
        with self._lock:
            self._mem_cache.pop(key, None)
            with self._manager.connect() as db:
                with atomic_transaction(db):
                    db.execute("DELETE FROM files WHERE key = ?", (key,))

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        with self._lock:
            with self._manager.connect() as db:
                row = db.execute("SELECT 1 FROM files WHERE key = ?", (key,)).fetchone()
                return row is not None

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            cached = self._mem_cache.pop(key, None)
            with self._manager.connect() as db:
                row = db.execute("SELECT file_hash, content FROM files WHERE key = ?", (key,)).fetchone()
                if row:
                    with atomic_transaction(db):
                        db.execute("DELETE FROM files WHERE key = ?", (key,))
                    if cached is not None:
                        return cached
                    if row["file_hash"]:
                        data = self._read_from_storage(row["file_hash"])
                        if data is not None:
                            return data
                    return row["content"]
            return cached if cached is not None else default

    def __iter__(self) -> Iterator[str]:
        with self._manager.connect() as db:
            keys = [r[0] for r in db.execute("SELECT key FROM files").fetchall()]
        return iter(keys)

    def __len__(self) -> int:
        with self._manager.connect() as db:
            row = db.execute("SELECT COUNT(*) FROM files").fetchone()
            return row[0] if row else 0


def make_material_manager():
    from app.study_materials import StudyMaterialManager, StudyMaterialError

    class PrivateMaterials(StudyMaterialManager):
        def __init__(self):
            super().__init__()
            self._lock = RLock()
            p_dir = private_dir()
            if p_dir is None:
                raise RuntimeError("Account private directory is required for private materials")
            self.account_dir = validate_path_containment(p_dir, root_dir())
            self.path = self.account_dir / "uploads.db"
            self.originals_dir = self.account_dir / "originals"
            self.originals_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            secure_dir_permissions(self.originals_dir)

            # Lazy content-addressed file mapping (no all-BLOB loading into RAM)
            self._raw_files = LazyRawFilesMapping(self)

            # Initialize DB and run versioned migrations
            with self.connect() as db:
                uploads_migrations.apply(db)
                self._migrate_legacy_blobs(db)
                # Load metadata records only (JSON text, not BLOBs)
                rows = db.execute("SELECT id, content FROM records").fetchall()
                self._materials = {r[0]: json.loads(r[1]) for r in rows}

        @contextmanager
        def connect(self) -> Generator[sqlite3.Connection, None, None]:
            conn = open_sqlite(
                self.path,
                timeout=10.0,
                wal=True,
                synchronous_full=True,
                foreign_keys=True,
            )
            try:
                yield conn
            finally:
                conn.close()

        def _migrate_legacy_blobs(self, db: sqlite3.Connection) -> None:
            """Migrate any existing BLOBs in legacy uploads.db into content-addressed originals using streaming."""
            count_row = db.execute("SELECT COUNT(*) FROM files WHERE content IS NOT NULL").fetchone()
            if not count_row or count_row[0] == 0:
                return

            batch_size = 50
            while True:
                rows = db.execute("SELECT rowid, key FROM files WHERE content IS NOT NULL LIMIT ?", (batch_size,)).fetchall()
                if not rows:
                    break

                with atomic_transaction(db):
                    now = time.time()
                    for row in rows:
                        rowid = row[0]
                        key = row[1]
                        hasher = hashlib.sha256()
                        size = 0
                        tmp_file = self.originals_dir / f".tmp_{uuid.uuid4().hex}"
                        fd = os.open(str(tmp_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        try:
                            with open(fd, "wb", closefd=True) as out_f:
                                blob = db.blobopen("files", "content", rowid, readonly=True)
                                try:
                                    while True:
                                        chunk = blob.read(65536)
                                        if not chunk:
                                            break
                                        hasher.update(chunk)
                                        size += len(chunk)
                                        out_f.write(chunk)
                                finally:
                                    blob.close()
                                out_f.flush()
                                os.fsync(out_f.fileno())

                            file_hash = hasher.hexdigest()
                            target_file = self.originals_dir / f"{file_hash}.bin"
                            validate_path_containment(target_file, self.originals_dir)

                            if target_file.exists():
                                existing_hash = compute_sha256_file(target_file)
                                if existing_hash != file_hash:
                                    raise RuntimeError(f"Corrupted immutable object detected at {target_file}")
                                try:
                                    tmp_file.unlink()
                                except OSError:
                                    pass
                            else:
                                try:
                                    os.link(str(tmp_file), str(target_file))
                                    secure_file_permissions(target_file)
                                    try:
                                        dir_fd = os.open(str(self.originals_dir), os.O_RDONLY)
                                        try:
                                            os.fsync(dir_fd)
                                        finally:
                                            os.close(dir_fd)
                                    except OSError:
                                        pass
                                except FileExistsError:
                                    if compute_sha256_file(target_file) != file_hash:
                                        raise RuntimeError(f"Corrupted immutable object detected at {target_file}")
                                finally:
                                    if tmp_file.exists():
                                        try:
                                            tmp_file.unlink()
                                        except OSError:
                                            pass

                            db.execute(
                                "INSERT OR IGNORE INTO file_objects(hash, size, created_at, storage_path) VALUES(?, ?, ?, ?)",
                                (file_hash, size, now, f"originals/{file_hash}.bin"),
                            )
                            db.execute(
                                "UPDATE files SET file_hash = ?, file_size = ?, content = NULL, created_at = COALESCE(created_at, ?) WHERE rowid = ?",
                                (file_hash, size, now, rowid),
                            )
                        except Exception:
                            if tmp_file.exists():
                                try:
                                    tmp_file.unlink()
                                except OSError:
                                    pass
                            raise

        def cache_raw_file(
            self,
            filename: Optional[str],
            file_bytes: bytes,
            material_id: Optional[str] = None,
            _record: Optional[Dict[str, Any]] = None,
        ) -> None:
            """Save raw file to immutable content-addressed storage and record metadata without full catalog rewrite."""
            if file_bytes is None:
                return

            file_hash = hashlib.sha256(file_bytes).hexdigest()
            target_file = self.originals_dir / f"{file_hash}.bin"
            validate_path_containment(target_file, self.originals_dir)

            # 1. CAS publishing with no-overwrite exclusive link + fsync
            if target_file.exists():
                existing_hash = compute_sha256_file(target_file)
                if existing_hash != file_hash:
                    raise RuntimeError(f"Corrupted immutable object detected at {target_file}")
            else:
                tmp_file = self.originals_dir / f".tmp_{uuid.uuid4().hex}"
                try:
                    fd = os.open(str(tmp_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with open(fd, "wb", closefd=True) as f:
                        f.write(file_bytes)
                        f.flush()
                        os.fsync(f.fileno())

                    try:
                        os.link(str(tmp_file), str(target_file))
                        secure_file_permissions(target_file)
                        try:
                            dir_fd = os.open(str(self.originals_dir), os.O_RDONLY)
                            try:
                                os.fsync(dir_fd)
                            finally:
                                os.close(dir_fd)
                        except OSError:
                            pass
                    except FileExistsError:
                        if compute_sha256_file(target_file) != file_hash:
                            raise RuntimeError(f"Corrupted immutable object detected at {target_file}")
                    finally:
                        if tmp_file.exists():
                            try:
                                tmp_file.unlink()
                            except OSError:
                                pass
                except Exception:
                    if tmp_file.exists():
                        try:
                            tmp_file.unlink()
                        except OSError:
                            pass
                    raise

            # Collect exact aliases: full filename, basename, material_id
            aliases = set()
            if filename:
                aliases.add(filename)
                aliases.add(Path(filename).name)
            if material_id:
                aliases.add(material_id)

            # 2. Atomic database update with BEGIN IMMEDIATE
            now = time.time()
            with self._lock, self.connect() as db:
                with atomic_transaction(db):
                    db.execute(
                        "INSERT OR IGNORE INTO file_objects(hash, size, created_at, storage_path) VALUES(?, ?, ?, ?)",
                        (file_hash, len(file_bytes), now, f"originals/{file_hash}.bin"),
                    )
                    for key in sorted(aliases):
                        db.execute(
                            "INSERT OR REPLACE INTO files(key, file_hash, file_size, created_at, content) VALUES(?, ?, ?, ?, NULL)",
                            (key, file_hash, len(file_bytes), now),
                        )
                    if _record is not None:
                        db.execute(
                            "INSERT OR REPLACE INTO records(id, content) VALUES(?, ?)",
                            (_record["id"], json.dumps(_record, ensure_ascii=False)),
                        )
                    elif material_id and material_id in self._materials:
                        db.execute(
                            "INSERT OR REPLACE INTO records(id, content) VALUES(?, ?)",
                            (material_id, json.dumps(self._materials[material_id], ensure_ascii=False)),
                        )

            # 3. ONLY AFTER SUCCESSFUL COMMIT: update in-memory caches and aliases (no recursive super call)
            if hasattr(self._raw_files, "_mem_cache"):
                with self._raw_files._lock:
                    for alias in aliases:
                        self._raw_files._remember(alias, file_bytes)

        def store_extracted_document(
            self,
            extracted_result,
            ingestion_info: Optional[Dict[str, Any]] = None,
            **kwargs,
        ) -> Dict[str, Any]:
            """Store extracted document and persist only this record to database."""
            record = self.build_extracted_document_record(extracted_result, ingestion_info)
            mid = record["id"]
            raw_b = getattr(extracted_result, "_raw_bytes", None)
            if raw_b is not None:
                self.cache_raw_file(extracted_result.filename, raw_b, material_id=mid, _record=record)
            else:
                with self._lock, self.connect() as db:
                    with atomic_transaction(db):
                        db.execute(
                            "INSERT OR REPLACE INTO records(id, content) VALUES(?, ?)",
                            (mid, json.dumps(record, ensure_ascii=False)),
                        )
            with self._lock:
                self._materials[mid] = record
            return record

        def get_material(self, material_id: str) -> Optional[Dict[str, Any]]:
            """Get material record by ID, checking database for latest committed updates."""
            if not material_id:
                return None
            with self.connect() as db:
                row = db.execute("SELECT content FROM records WHERE id = ?", (material_id,)).fetchone()
                if row:
                    record = json.loads(row[0])
                    with self._lock:
                        self._materials[material_id] = record
                    return record
            with self._lock:
                return self._materials.get(material_id)

        def list_materials(self) -> List[Dict[str, Any]]:
            """List all material records, reflecting latest committed database records."""
            with self.connect() as db:
                rows = db.execute("SELECT id, content FROM records").fetchall()
                records = [json.loads(r[1]) for r in rows]
                with self._lock:
                    self._materials = {r[0]: json.loads(r[1]) for r in rows}
                return records

        def evict_raw_file(self, *filenames: Optional[str]) -> None:
            super().evict_raw_file(*filenames)
            if hasattr(self._raw_files, "_mem_cache"):
                with self._raw_files._lock:
                    for fn in filenames:
                        if fn:
                            self._raw_files._mem_cache.pop(fn, None)
                            self._raw_files._mem_cache.pop(Path(fn).name, None)

        def ingest_to_src_and_index(self, *args, **kwargs):
            raise StudyMaterialError(
                "Tệp tài khoản được lưu riêng. Chưa hỗ trợ đưa tệp riêng vào chỉ mục thư viện dùng chung; hãy bỏ tùy chọn thêm vào thư viện."
            )

    return PrivateMaterials()
