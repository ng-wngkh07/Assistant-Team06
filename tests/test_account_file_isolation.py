"""Tests for account file isolation, path containment, symlink rejection, and permissions."""
import hashlib
import os
from pathlib import Path
import stat
import pytest

from app.accounts import account_id, root_dir
from app.account_services import make_material_manager
from app.db import validate_path_containment
from app.study_materials import StudyMaterialError


def test_private_byte_cache_has_total_memory_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(tmp_path / "accounts"))
    token = account_id.set("de" * 16)
    try:
        manager = make_material_manager()
        for i in range(8):
            manager.cache_raw_file(str(i), bytes([i]) * 1024 * 1024)
        manager.cache_raw_file("large", b"L" * (5 * 1024 * 1024))
        assert manager._raw_files["0"] == bytes(1024 * 1024)
        assert sum(map(len, manager._raw_files._mem_cache.values())) <= 4 * 1024 * 1024
        assert "large" not in manager._raw_files._mem_cache
    finally:
        account_id.reset(token)


def test_failed_extracted_metadata_preserves_existing_alias(tmp_path, monkeypatch):
    import sqlite3
    from types import SimpleNamespace

    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(tmp_path / "accounts"))
    token = account_id.set("df" * 16)
    try:
        manager = make_material_manager()
        manager.cache_raw_file("existing.pdf", b"original")
        with manager.connect() as db:
            db.execute("CREATE TRIGGER reject_record BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT, 'rejected metadata'); END")
            db.commit()
        extraction = SimpleNamespace(filename="existing.pdf", clean_title="Test", total_pages=1,
            total_chars=4, extracted_pages=[{"page_num": 1, "text": "Test"}], _raw_bytes=b"new payload")
        with pytest.raises(sqlite3.IntegrityError, match="rejected metadata"):
            manager.store_extracted_document(extraction)
        for current in (manager, make_material_manager()):
            assert current._raw_files["existing.pdf"] == b"original"
            assert current.list_materials() == []
    finally:
        account_id.reset(token)


def test_configured_root_alias_and_relative_paths(tmp_path, monkeypatch):
    root = tmp_path / "accounts"
    root.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable")
    expected = root.resolve() / "account" / "history.db"
    assert validate_path_containment(alias / "account/history.db", alias) == expected
    assert validate_path_containment(root / "account/history.db", alias) == expected
    monkeypatch.chdir(tmp_path)
    assert validate_path_containment(Path("accounts/account/history.db"), Path("accounts")) == expected
    assert validate_path_containment(Path("account/history.db"), Path("accounts")) == expected
    (root / "linked").symlink_to(root / "account", target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        validate_path_containment(alias / "linked/history.db", alias)


def test_account_ab_durable_original_isolation(tmp_path, monkeypatch):
    """Test Account A and B store originals strictly in isolated directories."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))

    acc_a = "aa" * 16
    acc_b = "bb" * 16

    content_a = b"SECRET DATA FOR ACCOUNT A ONLY"
    content_b = b"SECRET DATA FOR ACCOUNT B ONLY"
    hash_a = hashlib.sha256(content_a).hexdigest()
    hash_b = hashlib.sha256(content_b).hexdigest()

    # Upload for Account A
    token_a = account_id.set(acc_a)
    try:
        mgr_a = make_material_manager()
        mgr_a.cache_raw_file("a_doc.pdf", content_a, material_id="mat_a")
    finally:
        account_id.reset(token_a)

    # Upload for Account B
    token_b = account_id.set(acc_b)
    try:
        mgr_b = make_material_manager()
        mgr_b.cache_raw_file("b_doc.pdf", content_b, material_id="mat_b")
    finally:
        account_id.reset(token_b)

    # Verify physical disk isolation
    dir_a = accounts_dir / acc_a / "originals"
    dir_b = accounts_dir / acc_b / "originals"

    assert (dir_a / f"{hash_a}.bin").exists()
    assert not (dir_a / f"{hash_b}.bin").exists()

    assert (dir_b / f"{hash_b}.bin").exists()
    assert not (dir_b / f"{hash_a}.bin").exists()

    # Verify cross-account read isolation
    token_a = account_id.set(acc_a)
    try:
        mgr_a = make_material_manager()
        assert mgr_a._raw_files["a_doc.pdf"] == content_a
        assert "b_doc.pdf" not in mgr_a._raw_files
        assert "mat_b" not in mgr_a._raw_files
    finally:
        account_id.reset(token_a)

    token_b = account_id.set(acc_b)
    try:
        mgr_b = make_material_manager()
        assert mgr_b._raw_files["b_doc.pdf"] == content_b
        assert "a_doc.pdf" not in mgr_b._raw_files
        assert "mat_a" not in mgr_b._raw_files
    finally:
        account_id.reset(token_b)


def test_path_containment_and_symlink_rejection(tmp_path):
    """Test validate_path_containment rejects path traversal and symlinks escaping root."""
    base_dir = tmp_path / "safe_zone"
    base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    outside_dir = tmp_path / "outside_zone"
    outside_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("classified")

    # 1. Direct traversal with ../
    traversal_path = base_dir / "../outside_zone/secret.txt"
    with pytest.raises(ValueError, match="escapes"):
        validate_path_containment(traversal_path, base_dir)

    # 2. Symlink inside safe_zone pointing outside
    symlink_file = base_dir / "link_to_outside.txt"
    try:
        symlink_file.symlink_to(outside_file)
        with pytest.raises(ValueError, match="Symlink rejected under account root"):
            validate_path_containment(symlink_file, base_dir)
        with pytest.raises(ValueError, match="escapes"):
            validate_path_containment(symlink_file, base_dir, allow_symlinks=True)
    except OSError:
        pass  # On systems where symlinks require elevated permissions, skip gracefully


def test_strict_file_and_directory_permissions(tmp_path, monkeypatch):
    """Verify directories are 0o700 and database/original files are 0o600."""
    if os.name == "nt":
        pytest.skip("POSIX mode bits do not verify Windows ACLs")
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))

    acc_id = "cc" * 16
    token = account_id.set(acc_id)
    try:
        mgr = make_material_manager()
        mgr.cache_raw_file("perm_test.bin", b"test permission content", material_id="mat_perm")

        acc_dir = accounts_dir / acc_id
        orig_dir = acc_dir / "originals"
        db_file = acc_dir / "uploads.db"

        # Check directory permissions (0o700)
        acc_dir_mode = stat.S_IMODE(acc_dir.stat().st_mode)
        assert acc_dir_mode & 0o777 == 0o700

        orig_dir_mode = stat.S_IMODE(orig_dir.stat().st_mode)
        assert orig_dir_mode & 0o777 == 0o700

        # Check file permissions (0o600)
        db_mode = stat.S_IMODE(db_file.stat().st_mode)
        assert db_mode & 0o777 == 0o600

        for f in orig_dir.iterdir():
            if f.is_file():
                f_mode = stat.S_IMODE(f.stat().st_mode)
                assert f_mode & 0o777 in (0o600, 0o400)
    finally:
        account_id.reset(token)


def test_content_addressed_deduplication(tmp_path, monkeypatch):
    """Uploading identical content under different keys shares same content-addressed file."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))

    acc_id = "dd" * 16
    token = account_id.set(acc_id)
    try:
        mgr = make_material_manager()
        identical_content = b"REPEATED IDENTICAL DATA 12345"
        expected_hash = hashlib.sha256(identical_content).hexdigest()

        mgr.cache_raw_file("doc1.pdf", identical_content, material_id="m1")
        mgr.cache_raw_file("doc2.pdf", identical_content, material_id="m2")

        orig_files = list((accounts_dir / acc_id / "originals").glob("*.bin"))
        # Only one physical file stored for identical content
        assert len(orig_files) == 1
        assert orig_files[0].name == f"{expected_hash}.bin"

        # Both keys retrieve identical data
        assert mgr._raw_files["doc1.pdf"] == identical_content
        assert mgr._raw_files["doc2.pdf"] == identical_content
    finally:
        account_id.reset(token)


def test_no_all_blob_in_memory_loading_on_startup(tmp_path, monkeypatch):
    """Ensure opening an account with large/many files does not read BLOBs into memory on init."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))

    acc_id = "ee" * 16
    token = account_id.set(acc_id)
    try:
        mgr = make_material_manager()
        mgr.cache_raw_file("large1.bin", b"X" * 100000, material_id="mat_l1")
        mgr.cache_raw_file("large2.bin", b"Y" * 100000, material_id="mat_l2")

        # Create fresh manager instance
        fresh_mgr = make_material_manager()
        # Verify in-memory cache is empty
        assert len(fresh_mgr._raw_files._mem_cache) == 0

        # Only loads into memory when explicitly accessed
        data = fresh_mgr._raw_files["large1.bin"]
        assert data == b"X" * 100000
        assert "large1.bin" in fresh_mgr._raw_files._mem_cache
        assert "large2.bin" not in fresh_mgr._raw_files._mem_cache
    finally:
        account_id.reset(token)


def test_private_materials_refuses_shared_index_ingest(tmp_path, monkeypatch):
    """Ensure private account uploads strictly preserve no-shared-index boundary."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))

    acc_id = "ff" * 16
    token = account_id.set(acc_id)
    try:
        mgr = make_material_manager()
        with pytest.raises(StudyMaterialError, match="Tệp tài khoản được lưu riêng"):
            mgr.ingest_to_src_and_index("any_file.pdf", b"test")
    finally:
        account_id.reset(token)


def test_symlink_under_account_root_rejected_for_account_dir(tmp_path, monkeypatch):
    """Account A created as a symlink to Account B under the same account root must be rejected."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))

    acc_b = "22" * 16
    acc_a = "11" * 16

    # Setup Account B
    token_b = account_id.set(acc_b)
    try:
        mgr_b = make_material_manager()
        mgr_b.cache_raw_file("b_secret.txt", b"B confidential data", material_id="mat_b")
    finally:
        account_id.reset(token_b)

    dir_b = accounts_dir / acc_b
    dir_a = accounts_dir / acc_a

    # Create Account A as a symlink to Account B
    try:
        dir_a.symlink_to(dir_b)
    except OSError:
        pytest.skip("Symlink creation not supported or privileged on this platform")

    # Attempting to access Account A must fail closed
    token_a = account_id.set(acc_a)
    try:
        with pytest.raises(ValueError, match="Symlink rejected under account root|Account directory cannot be a symlink"):
            make_material_manager()
    finally:
        account_id.reset(token_a)


def test_symlink_under_account_root_rejected_for_child_originals(tmp_path, monkeypatch):
    """A file or directory inside an account linked to another account's data must be rejected."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))

    acc_a = "33" * 16
    acc_b = "44" * 16

    token_b = account_id.set(acc_b)
    try:
        mgr_b = make_material_manager()
        mgr_b.cache_raw_file("b_file.bin", b"b payload", material_id="m_b")
    finally:
        account_id.reset(token_b)

    token_a = account_id.set(acc_a)
    try:
        mgr_a = make_material_manager()
        # Create a symlink inside A's originals pointing to B's originals
        b_orig = accounts_dir / acc_b / "originals"
        a_symlink = accounts_dir / acc_a / "originals" / "evil_link.bin"
        try:
            a_symlink.symlink_to(list(b_orig.glob("*.bin"))[0])
        except OSError:
            pytest.skip("Symlink creation not supported or privileged on this platform")

        with pytest.raises(ValueError, match="Symlink rejected under account root"):
            validate_path_containment(a_symlink, accounts_dir / acc_a)
    finally:
        account_id.reset(token_a)


def test_fresh_manager_sees_updated_aliases(tmp_path, monkeypatch):
    """Updating an alias in one manager must be immediately visible to fresh managers."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    acc_id = "55" * 16
    token = account_id.set(acc_id)

    try:
        mgr1 = make_material_manager()
        mgr1.cache_raw_file("document.pdf", b"VERSION 1 BYTES", material_id="mat_v1")
        assert mgr1._raw_files["document.pdf"] == b"VERSION 1 BYTES"

        # Update alias with new content
        mgr1.cache_raw_file("document.pdf", b"VERSION 2 UPDATED BYTES", material_id="mat_v2")

        # Fresh manager must see updated content for alias
        fresh_mgr = make_material_manager()
        assert fresh_mgr._raw_files["document.pdf"] == b"VERSION 2 UPDATED BYTES"
    finally:
        account_id.reset(token)


def test_legacy_blob_migration_preserves_empty_blob_and_streams(tmp_path, monkeypatch):
    """Legacy migration of files table preserves empty raw BLOBs and migrates them safely."""
    import sqlite3
    accounts_dir = tmp_path / "accounts"
    acc_id = "66" * 16
    acc_dir = accounts_dir / acc_id
    acc_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path = acc_dir / "uploads.db"

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE records (id TEXT PRIMARY KEY, content TEXT NOT NULL);
        CREATE TABLE files (key TEXT PRIMARY KEY, content BLOB);
        INSERT INTO files VALUES ('empty.bin', X'');
        INSERT INTO files VALUES ('data.bin', X'68656C6C6F');
    """)
    conn.commit()
    conn.close()

    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    token = account_id.set(acc_id)
    try:
        mgr = make_material_manager()
        # Verify both empty and non-empty raw bytes are preserved and accessible
        assert mgr._raw_files["empty.bin"] == b""
        assert mgr._raw_files["data.bin"] == b"hello"

        # Verify files table was migrated to content-addressed references (content = NULL)
        with mgr.connect() as db:
            rows = db.execute("SELECT key, file_hash, content FROM files").fetchall()
            for r in rows:
                assert r["content"] is None
                assert r["file_hash"] is not None
    finally:
        account_id.reset(token)


def test_content_addressed_byte_verification_on_read(tmp_path, monkeypatch):
    """If an on-disk original file is corrupted, LazyRawFilesMapping must detect hash mismatch and refuse to return corrupted bytes."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    acc_id = "77" * 16
    token = account_id.set(acc_id)

    try:
        mgr = make_material_manager()
        valid_bytes = b"IMMUTABLE VALID DATA 123"
        mgr.cache_raw_file("sample.bin", valid_bytes, material_id="mat_samp")
        h = hashlib.sha256(valid_bytes).hexdigest()

        # Corrupt the underlying file on disk
        target_file = accounts_dir / acc_id / "originals" / f"{h}.bin"
        target_file.write_bytes(b"CORRUPTED ON-DISK PAYLOAD")

        # Fresh manager must detect corruption
        fresh = make_material_manager()
        with pytest.raises(KeyError):
            _ = fresh._raw_files["sample.bin"]
    finally:
        account_id.reset(token)
