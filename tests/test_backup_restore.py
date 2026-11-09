"""Tests for online backup creation, manifest verification, tamper rejection, and safe destination restore."""
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import pytest

from app.accounts import account_id
from app.account_services import make_material_manager
from app.db import (
    auth_migrations,
    history_migrations,
    managed_sqlite,
    open_sqlite,
    timetable_migrations,
)
from scripts.backup_service import compute_sha256, create_backup, restore_backup


def test_restore_rejects_destination_created_at_publication(tmp_path, monkeypatch):
    import scripts.backup_service as service

    source = tmp_path / "source"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(source))
    _populate_test_accounts(source)
    archive = create_backup(source, tmp_path / "backup.tar.gz")
    destination = tmp_path / "destination"
    publish = service.publish_directory

    def race(staged, target):
        target.mkdir()
        publish(staged, target)

    monkeypatch.setattr(service, "publish_directory", race)
    with pytest.raises(FileExistsError):
        restore_backup(archive, destination)
    assert destination.is_dir() and list(destination.iterdir()) == []


def test_backup_rejects_special_file_in_nested_account_directory(tmp_path, monkeypatch):
    import os
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    source = tmp_path / "source"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(source))
    identity = _populate_test_accounts(source)
    nested = source / identity / "other"
    nested.mkdir()
    os.mkfifo(nested / "pipe")
    with pytest.raises(ValueError, match="Special files"):
        create_backup(source, tmp_path / "backup.tar.gz")


def _populate_test_accounts(root_dir: Path) -> str:
    """Helper to populate realistic accounts, databases, and files."""
    auth_path = root_dir / "accounts.db"
    acc_id = "11" * 16
    with managed_sqlite(auth_path, timeout=5.0) as db:
        auth_migrations.apply(db)
        db.execute(
            "INSERT INTO accounts VALUES (?, 'alice', 'salt1', 'hash1')",
            (acc_id,),
        )
        db.execute(
            "INSERT INTO sessions VALUES ('token_alice', ?, 9999999999.0)",
            (acc_id,),
        )

    acc_dir = root_dir / acc_id
    acc_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # history.db
    with managed_sqlite(acc_dir / "history.db", timeout=5.0) as db:
        history_migrations.apply(db)
        db.execute(
            "INSERT INTO sessions (id, title, title_mode, created_at, updated_at) VALUES ('s1', 'Study Q', 'auto', '2026-11-09T00:00:00Z', '2026-11-09T00:00:00Z')"
        )
        db.execute(
            "INSERT INTO session_turns (session_id, turn_index, question, answer, citations_json, model, created_at) VALUES ('s1', 1, 'Q?', 'A.', '[]', 'm', '2026-11-09T00:00:00Z')"
        )

    # timetable.db
    with managed_sqlite(acc_dir / "timetable.db", timeout=5.0) as db:
        timetable_migrations.apply(db)
        db.execute(
            "INSERT INTO timetable_confirmed (course, weekday, start_time, end_time, created_at) VALUES ('Math', 2, '08:00', '10:00', '2026-11-09T00:00:00Z')"
        )

    # uploads.db and originals
    token = account_id.set(acc_id)
    try:
        mgr = make_material_manager()
        mgr.cache_raw_file("syllabus.pdf", b"%PDF-1.4 SYLLABUS CONTENT FOR ALICE", material_id="mat_syl")
    finally:
        account_id.reset(token)

    return acc_id


def test_full_backup_and_safe_restore_to_new_destination(tmp_path, monkeypatch):
    """Test full online backup and safe restoration to a clean new destination."""
    src_accounts = tmp_path / "src_accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    acc_id = _populate_test_accounts(src_accounts)

    archive_path = tmp_path / "backup_archive.tar.gz"
    create_backup(src_accounts, archive_path, quiesced=False)
    assert archive_path.exists()

    # Restore to a NEW destination
    target_dest = tmp_path / "restored_accounts"
    res = restore_backup(archive_path, target_dest)
    assert res["status"] == "success"
    assert res["quiesced"] is False
    assert "cross-database point-in-time consistency across separate SQLite files is not claimed" in res["consistency_guarantee"]

    # Verify restored accounts.db
    with open_sqlite(target_dest / "accounts.db") as db:
        row = db.execute("SELECT username FROM accounts WHERE id = ?", (acc_id,)).fetchone()
        assert row is not None
        assert row["username"] == "alice"

    # Verify restored history.db
    with open_sqlite(target_dest / acc_id / "history.db") as db:
        s_row = db.execute("SELECT title FROM sessions WHERE id = 's1'").fetchone()
        assert s_row is not None
        assert s_row["title"] == "Study Q"

    # Verify restored uploads and originals
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(target_dest))
    token = account_id.set(acc_id)
    try:
        restored_mgr = make_material_manager()
        assert restored_mgr._raw_files["syllabus.pdf"] == b"%PDF-1.4 SYLLABUS CONTENT FOR ALICE"
        assert restored_mgr._raw_files["mat_syl"] == b"%PDF-1.4 SYLLABUS CONTENT FOR ALICE"
    finally:
        account_id.reset(token)


def test_restore_refuses_to_overwrite_live_directory(tmp_path, monkeypatch):
    """Safe restore must refuse to overwrite an existing non-empty directory."""
    src_accounts = tmp_path / "src_accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    _populate_test_accounts(src_accounts)

    archive_path = tmp_path / "backup.tar.gz"
    create_backup(src_accounts, archive_path)

    # Destination exists and has live files
    live_dest = tmp_path / "live_dir"
    live_dest.mkdir()
    (live_dest / "active_file.txt").write_text("live active data")

    with pytest.raises(ValueError, match="Safe restore requires a NEW destination directory"):
        restore_backup(archive_path, live_dest)


def test_restore_rejects_tampered_manifest_or_file(tmp_path, monkeypatch):
    """Restore must detect and abort if any file has been modified or tampered with."""
    src_accounts = tmp_path / "src_accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    _populate_test_accounts(src_accounts)

    archive_path = tmp_path / "clean_backup.tar.gz"
    create_backup(src_accounts, archive_path)

    # Tamper with the archive: unpack, modify a file, repack
    unpack_dir = tmp_path / "tamper_unpack"
    unpack_dir.mkdir()
    with tarfile.open(archive_path, "r:*") as tar:
        tar.extractall(unpack_dir, filter="data")

    # Corrupt accounts.db bytes
    auth_file = unpack_dir / "accounts.db"
    auth_file.write_bytes(auth_file.read_bytes() + b"\x00\xFF")

    tampered_archive = tmp_path / "tampered_backup.tar.gz"
    with tarfile.open(tampered_archive, "w:gz") as tar:
        tar.add(unpack_dir, arcname="")

    target_dest = tmp_path / "dest_tamper"
    with pytest.raises(ValueError, match="Checksum mismatch|Size mismatch|Tampering detected"):
        restore_backup(tampered_archive, target_dest)


def test_restore_rejects_path_traversal_manifest(tmp_path):
    """Restore must reject archives whose manifest attempts directory traversal."""
    malicious_dir = tmp_path / "malicious_src"
    malicious_dir.mkdir()

    manifest = {
        "manifest_version": 1,
        "created_at": "2026-11-09T00:00:00Z",
        "quiesced": True,
        "files": [
            {
                "path": "../../escaped.txt",
                "sha256": "fakehash",
                "size": 10,
                "is_database": False,
            }
        ],
    }
    (malicious_dir / "manifest.json").write_text(json.dumps(manifest))
    (malicious_dir / "file.txt").write_text("hello")

    malicious_archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(malicious_archive, "w:gz") as tar:
        tar.add(malicious_dir, arcname="")

    target_dest = tmp_path / "dest_traversal"
    with pytest.raises(ValueError, match="Path traversal"):
        restore_backup(malicious_archive, target_dest)


def test_quiesced_backup_manifest_flag(tmp_path, monkeypatch):
    """Quiesced backup records operator assertion without claiming unverified host write freeze."""
    src_accounts = tmp_path / "src_accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    _populate_test_accounts(src_accounts)

    archive_path = tmp_path / "quiesced_backup.tar.gz"
    create_backup(src_accounts, archive_path, quiesced=True)

    target_dest = tmp_path / "dest_quiesced"
    res = restore_backup(archive_path, target_dest)
    assert res["quiesced"] is True
    assert res["quiescence_type"] == "operator_asserted"
    assert "Operator-asserted quiesced snapshot" in res["consistency_guarantee"]
    assert "the backup tool cannot independently verify or enforce caller quiescence assertions" in res["consistency_guarantee"]


def test_backup_fails_on_corrupt_database(tmp_path, monkeypatch):
    """Backup must abort immediately if a database fails integrity check."""
    src_accounts = tmp_path / "src_accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    _populate_test_accounts(src_accounts)

    # Corrupt accounts.db by overwriting header bytes with garbage
    auth_db = src_accounts / "accounts.db"
    with open(auth_db, "r+b") as f:
        f.seek(16)
        f.write(b"CORRUPTED_GARBAGE_HEADER")

    archive_path = tmp_path / "corrupt_backup.tar.gz"
    with pytest.raises(Exception):
        create_backup(src_accounts, archive_path)


def test_backup_fails_on_broken_content_reference(tmp_path, monkeypatch):
    """Backup must abort if uploads.db refers to an original file missing from originals/."""
    src_accounts = tmp_path / "src_accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    acc_id = _populate_test_accounts(src_accounts)

    # Delete the original .bin file
    orig_dir = src_accounts / acc_id / "originals"
    for f in orig_dir.glob("*.bin"):
        f.unlink()

    archive_path = tmp_path / "broken_ref_backup.tar.gz"
    with pytest.raises(RuntimeError, match="Backup failed: missing referenced original"):
        create_backup(src_accounts, archive_path)


def test_restore_fails_on_untracked_extra_file_in_archive(tmp_path, monkeypatch):
    """Restore must abort if archive contains files not declared in manifest.json."""
    src_accounts = tmp_path / "src_accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    _populate_test_accounts(src_accounts)

    archive_path = tmp_path / "clean_backup.tar.gz"
    create_backup(src_accounts, archive_path)

    # Unpack, add untracked file, repack
    unpack_dir = tmp_path / "unpack_extra"
    unpack_dir.mkdir()
    with tarfile.open(archive_path, "r:*") as tar:
        tar.extractall(unpack_dir, filter="data")

    (unpack_dir / "untracked_spy.txt").write_text("untracked payload")

    tampered_archive = tmp_path / "tampered_extra.tar.gz"
    with tarfile.open(tampered_archive, "w:gz") as tar:
        tar.add(unpack_dir, arcname="")

    target_dest = tmp_path / "dest_extra"
    with pytest.raises(ValueError, match="untracked files not in manifest"):
        restore_backup(tampered_archive, target_dest)


def test_restore_fails_on_duplicate_manifest_entry(tmp_path):
    """Restore must reject manifests with duplicate path entries."""
    malicious_dir = tmp_path / "dup_manifest_src"
    malicious_dir.mkdir()

    manifest = {
        "manifest_version": 1,
        "created_at": "2026-11-09T00:00:00Z",
        "quiesced": False,
        "files": [
            {
                "path": "file1.txt",
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "size": 0,
                "is_database": False,
            },
            {
                "path": "file1.txt",
                "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "size": 0,
                "is_database": False,
            },
        ],
    }
    (malicious_dir / "manifest.json").write_text(json.dumps(manifest))
    (malicious_dir / "file1.txt").write_bytes(b"")

    archive = tmp_path / "dup_manifest.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(malicious_dir, arcname="")

    target_dest = tmp_path / "dest_dup"
    with pytest.raises(ValueError, match="Duplicate path in manifest"):
        restore_backup(archive, target_dest)


def test_restore_rejects_duplicate_tar_members(tmp_path):
    """Restore must reject archives that contain duplicate tar member entries."""
    archive_path = tmp_path / "dup_tar_members.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        # Add manifest
        manifest_bytes = json.dumps({
            "manifest_version": 1,
            "created_at": "2026-11-09T00:00:00Z",
            "quiesced": False,
            "files": [],
        }).encode("utf-8")
        ti1 = tarfile.TarInfo(name="manifest.json")
        ti1.size = len(manifest_bytes)
        tar.addfile(ti1, io.BytesIO(manifest_bytes))

        # Add duplicate member for manifest.json
        ti2 = tarfile.TarInfo(name="manifest.json")
        ti2.size = len(manifest_bytes)
        tar.addfile(ti2, io.BytesIO(manifest_bytes))

    target_dest = tmp_path / "dest_dup_tar"
    with pytest.raises(ValueError, match="Duplicate tar member"):
        restore_backup(archive_path, target_dest)


def test_restore_rejects_malformed_manifest_types_and_sizes(tmp_path):
    """Restore must strictly enforce manifest schema types, versions, sizes, and database flags."""
    # 1. quiesced is int (1) instead of bool
    m_dir1 = tmp_path / "m1"
    m_dir1.mkdir()
    (m_dir1 / "manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "created_at": "2026-11-09T00:00:00Z",
        "quiesced": 1,  # invalid type
        "files": [],
    }))
    arc1 = tmp_path / "arc1.tar.gz"
    with tarfile.open(arc1, "w:gz") as tar:
        tar.add(m_dir1, arcname="")
    with pytest.raises(ValueError, match="must be a boolean"):
        restore_backup(arc1, tmp_path / "d1")

    # 2. manifest_version is not integer 1
    m_dir2 = tmp_path / "m2"
    m_dir2.mkdir()
    (m_dir2 / "manifest.json").write_text(json.dumps({
        "manifest_version": "1",  # string instead of int
        "created_at": "2026-11-09T00:00:00Z",
        "quiesced": True,
        "files": [],
    }))
    arc2 = tmp_path / "arc2.tar.gz"
    with tarfile.open(arc2, "w:gz") as tar:
        tar.add(m_dir2, arcname="")
    with pytest.raises(ValueError, match="manifest_version.*1"):
        restore_backup(arc2, tmp_path / "d2")

    # 3. .db file declared with is_database=False
    m_dir3 = tmp_path / "m3"
    m_dir3.mkdir()
    (m_dir3 / "test.db").write_bytes(b"dummy")
    (m_dir3 / "manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "created_at": "2026-11-09T00:00:00Z",
        "quiesced": True,
        "files": [{
            "path": "test.db",
            "sha256": compute_sha256(m_dir3 / "test.db"),
            "size": 5,
            "is_database": False,  # invalid for .db file
        }],
    }))
    arc3 = tmp_path / "arc3.tar.gz"
    with tarfile.open(arc3, "w:gz") as tar:
        tar.add(m_dir3, arcname="")
    with pytest.raises(ValueError, match="cannot set is_database=false"):
        restore_backup(arc3, tmp_path / "d3")

    # 4. size declared does not match actual extracted file size
    m_dir4 = tmp_path / "m4"
    m_dir4.mkdir()
    (m_dir4 / "payload.txt").write_bytes(b"actual content")
    (m_dir4 / "manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "created_at": "2026-11-09T00:00:00Z",
        "quiesced": False,
        "files": [{
            "path": "payload.txt",
            "sha256": compute_sha256(m_dir4 / "payload.txt"),
            "size": 9999,  # size mismatch
            "is_database": False,
        }],
    }))
    arc4 = tmp_path / "arc4.tar.gz"
    with tarfile.open(arc4, "w:gz") as tar:
        tar.add(m_dir4, arcname="")
    with pytest.raises(ValueError, match="Size mismatch"):
        restore_backup(arc4, tmp_path / "d4")


def test_restore_rejects_existing_empty_destination(tmp_path, monkeypatch):
    """Restore strictly requires a NEW destination directory that does not yet exist, even if empty."""
    src_accounts = tmp_path / "src_acc"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    _populate_test_accounts(src_accounts)

    archive_path = tmp_path / "backup.tar.gz"
    create_backup(src_accounts, archive_path)

    # Empty destination directory already exists
    empty_dest = tmp_path / "empty_existing_dir"
    empty_dest.mkdir()

    with pytest.raises(ValueError, match="Safe restore requires a NEW destination directory"):
        restore_backup(archive_path, empty_dest)


def test_restore_invalid_archive_does_not_partially_publish(tmp_path, monkeypatch):
    """If an archive fails validation during restore, no partial directory or file is left at the destination."""
    src_accounts = tmp_path / "src_acc"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    _populate_test_accounts(src_accounts)

    archive_path = tmp_path / "clean_backup.tar.gz"
    create_backup(src_accounts, archive_path)

    # Tamper with archive
    unpack_dir = tmp_path / "unpack_corrupt"
    unpack_dir.mkdir()
    with tarfile.open(archive_path, "r:*") as tar:
        tar.extractall(unpack_dir, filter="data")

    # Corrupt a file
    (unpack_dir / "accounts.db").write_bytes(b"CORRUPTED_DB_HEADER")

    corrupt_archive = tmp_path / "corrupt_arc.tar.gz"
    with tarfile.open(corrupt_archive, "w:gz") as tar:
        tar.add(unpack_dir, arcname="")

    target_dest = tmp_path / "never_published_dest"
    with pytest.raises(Exception):
        restore_backup(corrupt_archive, target_dest)

    # Destination directory MUST NOT exist
    assert not target_dest.exists(), "Defect: target destination directory was published despite corrupt archive"


def test_backup_and_restore_legacy_blob_only_uploads(tmp_path):
    """Backup service supports genuine legacy BLOB-only uploads schema before versioned upgrade."""
    src_dir = tmp_path / "legacy_src"
    acc_id = "f0" * 16
    acc_dir = src_dir / acc_id
    acc_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Setup legacy accounts.db
    with open_sqlite(src_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'legacy_user', 's', 'h')", (acc_id,))

    # Setup legacy uploads.db with raw BLOBs and NO originals/ directory
    legacy_uploads = acc_dir / "uploads.db"
    conn = sqlite3.connect(str(legacy_uploads))
    conn.executescript("""
        CREATE TABLE records (id TEXT PRIMARY KEY, content TEXT NOT NULL);
        CREATE TABLE files (key TEXT PRIMARY KEY, content BLOB NOT NULL);
        INSERT INTO records VALUES ('rec1', '{"title": "Legacy Notes"}');
        INSERT INTO files VALUES ('blob1.bin', X'0102030405');
    """)
    conn.commit()
    conn.close()

    archive_path = tmp_path / "legacy_backup.tar.gz"
    res_backup = create_backup(src_dir, archive_path)
    assert res_backup.exists()

    # Restore to a new destination
    restored_dest = tmp_path / "restored_legacy"
    res_restore = restore_backup(archive_path, restored_dest)
    assert res_restore["status"] == "success"

    # Verify restored uploads.db preserves raw BLOBs
    with open_sqlite(restored_dest / acc_id / "uploads.db") as db:
        row = db.execute("SELECT content FROM files WHERE key = 'blob1.bin'").fetchone()
        assert row is not None
        assert row[0] == b"\x01\x02\x03\x04\x05"


def test_backup_archive_posix_permissions(tmp_path, monkeypatch):
    """Archive file created on POSIX platforms has mode 0o600."""
    import os
    if os.name == "nt":
        pytest.skip("POSIX permissions not supported on Windows")

    src_accounts = tmp_path / "src_acc_perm"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(src_accounts))
    _populate_test_accounts(src_accounts)

    archive_path = tmp_path / "perm_archive.tar.gz"
    create_backup(src_accounts, archive_path)

    import stat
    mode = stat.S_IMODE(archive_path.stat().st_mode)
    assert mode & 0o777 == 0o600
