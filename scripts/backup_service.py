#!/usr/bin/env python3
"""Online SQLite backup and safe restore service with manifest validation and reference verification."""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

logger = logging.getLogger("psychology_agent.backup_service")


def publish_directory(source: Path, destination: Path) -> None:
    """Publish on one filesystem without replacing a concurrently created destination."""
    if sys.platform == "win32":
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        result = rename(os.fsencode(source), os.fsencode(destination), 4)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise OSError("Atomic non-overwriting directory publication is unsupported on this platform")
    if result:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(destination))


def compute_sha256(path: Path) -> str:
    """Compute sha256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def check_source_path_safety(p: Path) -> None:
    """Reject symlinks, hard links, and special files (FIFOs/sockets/devices)."""
    if p.is_symlink():
        raise ValueError(f"Symlinks are rejected in backup source: '{p}'")
    st = p.lstat()
    if stat.S_ISREG(st.st_mode):
        if st.st_nlink > 1:
            raise ValueError(f"Hard links are rejected in backup source: '{p}'")
    elif stat.S_ISDIR(st.st_mode):
        pass
    else:
        raise ValueError(f"Special files (FIFOs/sockets/devices) are rejected in backup source: '{p}'")


def validate_relative_path(rel_path: str, base_dir: Path) -> Path:
    """Ensure relative path does not escape base_dir via .. or absolute path."""
    clean_p = rel_path.strip().replace("\\", "/")
    if clean_p.startswith("/") or ".." in clean_p.split("/"):
        raise ValueError(f"Path traversal detected in path: '{rel_path}'")
    target = (base_dir / clean_p).resolve()
    base_res = base_dir.resolve()
    try:
        target.relative_to(base_res)
    except ValueError as exc:
        raise ValueError(f"Path traversal: '{rel_path}' escapes '{base_dir}'") from exc
    return target


def backup_sqlite_online(src_path: Path, dst_path: Path) -> Tuple[str, List[str]]:
    """Perform online SQLite backup with integrity check and foreign key check using read-only URI."""
    check_source_path_safety(src_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Ensure read-only source SQLite connection
    src_conn = sqlite3.connect(f"file:{src_path.resolve()}?mode=ro", uri=True, timeout=30.0)
    dst_conn = sqlite3.connect(str(dst_path), timeout=30.0)
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
        dst_conn.execute("PRAGMA journal_mode = DELETE")
        dst_conn.execute("PRAGMA foreign_keys = ON")
        ic = dst_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            raise RuntimeError(f"Backup failed: integrity check failed for '{src_path.name}': {ic}")
        fk_violations = [list(r) for r in dst_conn.execute("PRAGMA foreign_key_check").fetchall()]
        if fk_violations:
            raise RuntimeError(f"Backup failed: foreign key check failed for '{src_path.name}': {fk_violations}")
        return ic, fk_violations
    finally:
        dst_conn.close()
        src_conn.close()


def create_backup(
    accounts_dir: Union[str, Path],
    output_archive: Union[str, Path],
    quiesced: bool = False,
) -> Path:
    """Create online backup archive with manifest, hashes, and integrity checks."""
    src_root = Path(accounts_dir)
    check_source_path_safety(src_root)
    src_root = src_root.resolve()
    if not src_root.exists():
        raise FileNotFoundError(f"Accounts directory does not exist: {accounts_dir}")

    out_p = Path(output_archive).resolve()
    if out_p.is_relative_to(src_root):
        raise ValueError("Backup output must be outside the source accounts directory")
    if out_p.exists():
        raise FileExistsError(f"Backup destination '{out_p}' already exists. Refusing to overwrite.")
    out_p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source_entry in src_root.rglob("*"):
        check_source_path_safety(source_entry)

    with tempfile.TemporaryDirectory() as staging_dir:
        staging_root = Path(staging_dir)
        files_manifest: List[Dict[str, Any]] = []
        seen_paths: Set[str] = set()

        def add_manifest_entry(entry: Dict[str, Any]) -> None:
            p = entry["path"]
            if p in seen_paths:
                raise ValueError(f"Duplicate entry in manifest: '{p}'")
            if p.startswith("/") or ".." in p.split("/"):
                raise ValueError(f"Invalid path traversal in manifest entry: '{p}'")
            seen_paths.add(p)
            files_manifest.append(entry)

        # 1. Back up accounts.db if present
        auth_db = src_root / "accounts.db"
        if auth_db.exists():
            check_source_path_safety(auth_db)
            dst_auth = staging_root / "accounts.db"
            ic, fk = backup_sqlite_online(auth_db, dst_auth)
            add_manifest_entry({
                "path": "accounts.db",
                "sha256": compute_sha256(dst_auth),
                "size": int(dst_auth.stat().st_size),
                "is_database": True,
                "integrity_check": ic,
                "foreign_key_violations": fk,
            })

        # 2. Iterate account subdirectories (32-character hex account IDs)
        account_pattern = re.compile(r"^[a-f0-9]{32}$")
        for item in sorted(src_root.iterdir()):
            check_source_path_safety(item)
            if item.is_dir() and account_pattern.match(item.name):
                acc_name = item.name
                acc_staging = staging_root / acc_name
                acc_staging.mkdir(parents=True, exist_ok=True, mode=0o700)

                # Content-addressed originals first so DB reference check can verify them
                originals_dir = item / "originals"
                if originals_dir.exists() and originals_dir.is_dir():
                    check_source_path_safety(originals_dir)
                    dst_orig = acc_staging / "originals"
                    dst_orig.mkdir(parents=True, exist_ok=True, mode=0o700)
                    for orig_file in sorted(originals_dir.iterdir()):
                        check_source_path_safety(orig_file)
                        if orig_file.is_file() and not orig_file.name.startswith("."):
                            dst_file = dst_orig / orig_file.name
                            shutil.copy2(orig_file, dst_file)
                            rel_path = f"{acc_name}/originals/{orig_file.name}"
                            add_manifest_entry({
                                "path": rel_path,
                                "sha256": compute_sha256(dst_file),
                                "size": int(dst_file.stat().st_size),
                                "is_database": False,
                            })

                # Timetable images
                images_dir = item / "timetable_images"
                if images_dir.exists() and images_dir.is_dir():
                    check_source_path_safety(images_dir)
                    dst_images = acc_staging / "timetable_images"
                    dst_images.mkdir(parents=True, exist_ok=True, mode=0o700)
                    for img_file in sorted(images_dir.iterdir()):
                        check_source_path_safety(img_file)
                        if img_file.is_file() and not img_file.name.startswith("."):
                            dst_file = dst_images / img_file.name
                            shutil.copy2(img_file, dst_file)
                            rel_path = f"{acc_name}/timetable_images/{img_file.name}"
                            add_manifest_entry({
                                "path": rel_path,
                                "sha256": compute_sha256(dst_file),
                                "size": int(dst_file.stat().st_size),
                                "is_database": False,
                            })

                # Databases
                for db_name in ("history.db", "timetable.db", "uploads.db"):
                    db_file = item / db_name
                    if db_file.exists():
                        check_source_path_safety(db_file)
                        dst_db = acc_staging / db_name
                        ic, fk = backup_sqlite_online(db_file, dst_db)

                        # If uploads.db, verify references against originals (support legacy BLOB-only schema)
                        if db_name == "uploads.db":
                            conn = sqlite3.connect(f"file:{dst_db.resolve()}?mode=ro", uri=True)
                            conn.row_factory = sqlite3.Row
                            try:
                                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
                                if cur.fetchone():
                                    cols = [c[1] for c in conn.execute("PRAGMA table_info(files)").fetchall()]
                                    if "file_hash" in cols:
                                        rows = conn.execute("SELECT key, file_hash, file_size FROM files WHERE file_hash IS NOT NULL").fetchall()
                                        for r in rows:
                                            fh = r["file_hash"]
                                            if not re.fullmatch(r"^[a-f0-9]{64}$", fh):
                                                raise ValueError(f"Invalid file_hash format in uploads.db: '{fh}'")
                                            f_path = acc_staging / "originals" / f"{fh}.bin"
                                            if not f_path.exists():
                                                raise RuntimeError(f"Backup failed: missing referenced original '{fh}.bin' for key '{r['key']}' in {acc_name}/uploads.db")
                                            if compute_sha256(f_path) != fh:
                                                raise RuntimeError(f"Backup failed: corrupted referenced original '{fh}.bin' in {acc_name}/uploads.db")
                                            if r["file_size"] is not None and f_path.stat().st_size != r["file_size"]:
                                                raise RuntimeError(f"Backup failed: size mismatch for '{fh}.bin' in {acc_name}/uploads.db")

                                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_objects'")
                                if cur.fetchone():
                                    rows = conn.execute("SELECT hash, size, storage_path FROM file_objects").fetchall()
                                    for r in rows:
                                        fh = r["hash"]
                                        if not re.fullmatch(r"^[a-f0-9]{64}$", fh):
                                            raise ValueError(f"Invalid hash in file_objects: '{fh}'")
                                        sp = r["storage_path"]
                                        if not isinstance(sp, str) or sp.startswith("/") or ".." in sp.split("/"):
                                            raise ValueError(f"Invalid storage_path in file_objects: '{sp}'")
                                        f_path = acc_staging / sp
                                        if not f_path.exists():
                                            raise RuntimeError(f"Backup failed: missing file_objects file '{fh}.bin' in {acc_name}/uploads.db")
                                        if compute_sha256(f_path) != fh:
                                            raise RuntimeError(f"Backup failed: corrupted file_objects file '{fh}.bin' in {acc_name}/uploads.db")
                                        if f_path.stat().st_size != r["size"]:
                                            raise RuntimeError(f"Backup failed: size mismatch for file_objects '{sp}' in {acc_name}/uploads.db")
                            finally:
                                conn.close()

                        rel_path = f"{acc_name}/{db_name}"
                        add_manifest_entry({
                            "path": rel_path,
                            "sha256": compute_sha256(dst_db),
                            "size": int(dst_db.stat().st_size),
                            "is_database": True,
                            "integrity_check": ic,
                            "foreign_key_violations": fk,
                        })

        # 3. Create manifest.json with explicit operator assertion notice
        manifest = {
            "manifest_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "quiesced": bool(quiesced),
            "quiescence_type": "operator_asserted" if quiesced else "none",
            "consistency_guarantee": (
                "Operator-asserted quiesced snapshot: caller asserts writes were halted externally; the backup tool cannot independently verify or enforce caller quiescence assertions. Individual SQLite databases are verified internally consistent via SQLite backup API."
                if quiesced
                else "Online live backup: individual databases are internally consistent via SQLite backup API; cross-database point-in-time consistency across separate SQLite files is not claimed."
            ),
            "security_notice": "SHA-256 hashes detect accidental corruption or tampering, but do not provide cryptographic authentication or signatures against an active adversary who can rewrite both payload files and manifest.json.",
            "files": files_manifest,
        }
        manifest_path = staging_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest_path.chmod(0o600)

        # 4. Create archive with private 0o600 permissions at creation (never world-readable)
        if out_p.suffix in {".tar", ".gz", ".tgz"}:
            staging_archive = out_p.parent / f".tmp_archive_{uuid.uuid4().hex}"
            fd = os.open(str(staging_archive), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with open(fd, "wb", closefd=True) as fileobj:
                    with tarfile.open(fileobj=fileobj, mode="w:gz") as tar:
                        tar.add(staging_root, arcname="")
                    fileobj.flush()
                    os.fsync(fileobj.fileno())

                os.link(str(staging_archive), str(out_p))
                try:
                    out_p.chmod(0o600)
                except OSError:
                    pass
            finally:
                if staging_archive.exists():
                    try:
                        staging_archive.unlink()
                    except OSError:
                        pass
        else:
            with tempfile.TemporaryDirectory(dir=out_p.parent) as publish_root:
                staged = Path(publish_root) / "backup"
                shutil.copytree(staging_root, staged)
                staged.chmod(0o700)
                publish_directory(staged, out_p)

    return out_p


def restore_backup(
    archive_path: Union[str, Path],
    destination_dir: Union[str, Path],
) -> Dict[str, Any]:
    """Safely restore a backup archive strictly to a NEW destination directory with strict checks."""
    requested = Path(destination_dir).absolute()
    if os.path.lexists(requested):
        raise ValueError(f"Restore destination '{requested}' already exists. Safe restore requires a NEW destination directory.")
    dest = requested.parent.resolve() / requested.name
    # Safety Check: Strictly require a NEW non-existent destination directory; reject existing even empty
    if dest.exists():
        raise ValueError(
            f"Restore destination '{dest}' already exists. Safe restore requires a NEW destination directory."
        )

    arc_path = Path(archive_path).resolve()
    if not arc_path.exists():
        raise FileNotFoundError(f"Backup archive does not exist: {archive_path}")

    with tempfile.TemporaryDirectory() as extract_dir:
        staging = Path(extract_dir)

        # 1. Extract archive and reject duplicate canonical tar members, symlinks/hardlinks/special files
        if arc_path.is_file():
            seen_members: Set[str] = set()
            with tarfile.open(arc_path, "r:*") as tar:
                for member in tar.getmembers():
                    norm_name = os.path.normpath(member.name).replace("\\", "/")
                    if norm_name in seen_members:
                        raise ValueError(f"Duplicate tar member in archive: '{member.name}'")
                    seen_members.add(norm_name)

                    if member.issym() or member.islnk():
                        raise ValueError(f"Archive contains symlink or hardlink '{member.name}', which is strictly forbidden.")
                    if not (member.isreg() or member.isdir()):
                        raise ValueError(f"Archive contains special file '{member.name}', which is strictly forbidden.")
                    validate_relative_path(member.name, staging)
                tar.extractall(staging, filter="data")
        elif arc_path.is_dir():
            for root, dirs, files in os.walk(arc_path):
                for d in dirs:
                    p = Path(root) / d
                    if p.is_symlink():
                        raise ValueError(f"Input directory contains symlink '{p}', which is strictly forbidden.")
                for f in files:
                    p = Path(root) / f
                    check_source_path_safety(p)
            shutil.copytree(arc_path, staging, dirs_exist_ok=True)
        else:
            raise ValueError(f"Invalid archive path: {arc_path}")

        # 2. Read and validate manifest.json
        manifest_file = staging / "manifest.json"
        if not manifest_file.exists():
            raise ValueError("Invalid backup archive: missing manifest.json")

        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Corrupted manifest.json: {exc}") from exc

        # Validate manifest version and structure (types: bool is not int, version1 exactly)
        if not isinstance(manifest, dict):
            raise ValueError("Invalid manifest.json: root must be a JSON object")
        mv = manifest.get("manifest_version")
        if type(mv) is not int or isinstance(mv, bool) or mv != 1:
            raise ValueError(f"Unsupported manifest_version: {mv}. Exactly 1 is required.")

        quiesced_val = manifest.get("quiesced")
        if not isinstance(quiesced_val, bool):
            raise ValueError(f"Invalid 'quiesced' in manifest: must be a boolean, got {type(quiesced_val)}")

        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("Invalid manifest.json: 'files' must be a list")

        # Check for duplicate manifest entries and validate exact types
        seen_manifest_paths: Set[str] = set()
        for entry in files:
            if not isinstance(entry, dict):
                raise ValueError("Manifest entry must be a dictionary")
            rel = entry.get("path")
            if not isinstance(rel, str) or not rel:
                raise ValueError("Manifest entry missing valid 'path' string")
            norm_rel = os.path.normpath(rel).replace("\\", "/")
            if norm_rel != rel:
                raise ValueError(f"Manifest path must be exact canonical path: '{rel}' vs '{norm_rel}'")
            if rel in seen_manifest_paths:
                raise ValueError(f"Duplicate path in manifest: '{rel}'")
            if rel.startswith("/") or ".." in rel.split("/"):
                raise ValueError(f"Path traversal detected in manifest: '{rel}'")
            seen_manifest_paths.add(rel)

            sha = entry.get("sha256")
            if not isinstance(sha, str) or len(sha) != 64 or not re.fullmatch(r"^[a-f0-9]{64}$", sha):
                raise ValueError(f"Invalid sha256 checksum for '{rel}': '{sha}'")

            size = entry.get("size")
            if type(size) is not int or isinstance(size, bool) or size < 0:
                raise ValueError(f"Invalid size for '{rel}': '{size}'. Must be a non-negative integer.")

            is_db = entry.get("is_database")
            if type(is_db) is not bool:
                raise ValueError(f"Invalid is_database for '{rel}': must be boolean, got {type(is_db)}")

            # Cannot suppress DB integrity by is_database=false
            if rel.endswith(".db") and not is_db:
                raise ValueError(f"Database file '{rel}' cannot set is_database=false to suppress integrity checks")

        # 3. Exact file inventory check (no untracked files in staging)
        staged_files = {
            p.relative_to(staging).as_posix()
            for p in staging.rglob("*")
            if p.is_file() and p.name != "manifest.json"
        }
        if staged_files != seen_manifest_paths:
            extra = staged_files - seen_manifest_paths
            missing = seen_manifest_paths - staged_files
            err_msg = []
            if extra:
                err_msg.append(f"untracked files not in manifest: {sorted(extra)}")
            if missing:
                err_msg.append(f"files missing from archive: {sorted(missing)}")
            raise ValueError(f"Archive file inventory mismatch: {'; '.join(err_msg)}")

        # 4. Checksum verification and exact size verification
        for entry in files:
            rel = entry["path"]
            staged_file = validate_relative_path(rel, staging)
            actual_size = staged_file.stat().st_size
            if actual_size != entry["size"]:
                raise ValueError(f"Size mismatch for '{rel}': expected {entry['size']}, got {actual_size}")
            actual_sha = compute_sha256(staged_file)
            if actual_sha != entry["sha256"]:
                raise ValueError(
                    f"Checksum mismatch for '{rel}': expected {entry['sha256']}, got {actual_sha}. Tampering detected."
                )

        # 5. Database integrity and foreign key checks
        for entry in files:
            if entry.get("is_database") or entry["path"].endswith(".db"):
                rel = entry["path"]
                db_path = validate_relative_path(rel, staging)
                conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
                try:
                    conn.execute("PRAGMA foreign_keys = ON")
                    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
                    if ic != "ok":
                        raise ValueError(f"Integrity check failed for restored database '{rel}': {ic}")
                    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
                    if fk:
                        raise ValueError(f"Foreign key check failed for restored database '{rel}': {fk}")
                finally:
                    conn.close()

        # 6. Verify content-addressed references in uploads.db (both files and file_objects; supports legacy BLOB-only)
        for entry in files:
            if entry["path"].endswith("uploads.db"):
                db_path = validate_relative_path(entry["path"], staging)
                acc_dir = db_path.parent
                orig_dir = acc_dir / "originals"
                conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
                    if cur.fetchone():
                        cols = [c[1] for c in conn.execute("PRAGMA table_info(files)").fetchall()]
                        if "file_hash" in cols:
                            rows = conn.execute("SELECT key, file_hash, file_size FROM files WHERE file_hash IS NOT NULL").fetchall()
                            for r in rows:
                                f_hash = r["file_hash"]
                                if not re.fullmatch(r"^[a-f0-9]{64}$", f_hash):
                                    raise ValueError(f"Invalid file_hash format in '{entry['path']}': '{f_hash}'")
                                orig_file = orig_dir / f"{f_hash}.bin"
                                validate_relative_path(f"originals/{f_hash}.bin", acc_dir)
                                if not orig_file.exists():
                                    raise ValueError(
                                        f"Broken original reference in '{entry['path']}': file hash '{f_hash}' not found in originals/"
                                    )
                                if compute_sha256(orig_file) != f_hash:
                                    raise ValueError(
                                        f"Original file '{orig_file.name}' in '{entry['path']}' does not match expected sha256 hash '{f_hash}'"
                                    )
                                if r["file_size"] is not None and orig_file.stat().st_size != r["file_size"]:
                                    raise ValueError(
                                        f"Original file '{orig_file.name}' size mismatch in '{entry['path']}'"
                                    )
                    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_objects'")
                    if cur.fetchone():
                        rows = conn.execute("SELECT hash, size, storage_path FROM file_objects").fetchall()
                        for r in rows:
                            f_hash = r["hash"]
                            if not re.fullmatch(r"^[a-f0-9]{64}$", f_hash):
                                raise ValueError(f"Invalid hash in file_objects: '{f_hash}'")
                            sp = r["storage_path"]
                            if not isinstance(sp, str) or sp.startswith("/") or ".." in sp.split("/"):
                                raise ValueError(f"Invalid storage_path in file_objects: '{sp}'")
                            orig_file = acc_dir / sp
                            validate_relative_path(sp, acc_dir)
                            if not orig_file.exists():
                                raise ValueError(
                                    f"Broken file_objects reference in '{entry['path']}': file hash '{f_hash}' not found in {sp}"
                                )
                            if compute_sha256(orig_file) != f_hash:
                                raise ValueError(
                                    f"Original file '{orig_file.name}' in '{entry['path']}' does not match expected sha256 hash '{f_hash}'"
                                )
                            if orig_file.stat().st_size != r["size"]:
                                raise ValueError(
                                    f"Size mismatch for file_objects '{sp}' in '{entry['path']}'"
                                )
                finally:
                    conn.close()

        # 7. Safe atomic publication: stage on destination filesystem, atomically publish without overwriting
        tmp_publish = dest.parent / f".tmp_restore_{uuid.uuid4().hex}"
        tmp_publish.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            tmp_publish.chmod(0o700)
        except OSError:
            pass
        try:
            for entry in files:
                rel = entry["path"]
                src_f = staging / rel
                dst_f = tmp_publish / rel
                dst_f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    dst_f.parent.chmod(0o700)
                except OSError:
                    pass
                shutil.copy2(src_f, dst_f)
                try:
                    dst_f.chmod(0o600)
                except OSError:
                    pass

            dst_manifest = tmp_publish / "manifest.json"
            shutil.copy2(manifest_file, dst_manifest)
            try:
                dst_manifest.chmod(0o600)
            except OSError:
                pass

            # Atomic publication: reject if dest was created in the meantime
            if dest.exists():
                raise FileExistsError(f"Destination '{dest}' was created concurrently. Aborting without overwriting.")
            publish_directory(tmp_publish, dest)
        finally:
            if tmp_publish.exists():
                shutil.rmtree(tmp_publish, ignore_errors=True)

    return {
        "status": "success",
        "destination": str(dest),
        "files_restored": len(files),
        "quiesced": manifest.get("quiesced", False),
        "quiescence_type": manifest.get("quiescence_type", "none"),
        "consistency_guarantee": manifest.get("consistency_guarantee", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Online SQLite backup and safe restore service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Create online backup")
    backup_parser.add_argument("--accounts-dir", required=True, help="Path to accounts directory")
    backup_parser.add_argument("--out", required=True, help="Output archive path (.tar.gz)")
    backup_parser.add_argument("--quiesced", action="store_true", help="Mark backup as quiesced snapshot")

    restore_parser = subparsers.add_parser("restore", help="Restore backup to a NEW destination")
    restore_parser.add_argument("--archive", required=True, help="Backup archive file")
    restore_parser.add_argument("--destination", required=True, help="Target NEW destination directory")

    args = parser.parse_args()
    if args.command == "backup":
        out = create_backup(args.accounts_dir, args.out, quiesced=args.quiesced)
        print(f"Backup created successfully: {out}")
    elif args.command == "restore":
        res = restore_backup(args.archive, args.destination)
        print(f"Restore completed successfully: {json.dumps(res, indent=2)}")


if __name__ == "__main__":
    main()
