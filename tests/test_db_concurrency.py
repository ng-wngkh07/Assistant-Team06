"""Tests for database concurrency, WAL mode under load, and commit failure handling."""
import concurrent.futures
from pathlib import Path
import sqlite3
import threading
import time
import pytest

from app.accounts import account_id
from app.account_services import make_material_manager
from app.db import (
    atomic_transaction,
    open_sqlite,
    history_migrations,
)
from app.history import HistoryStore


def test_concurrent_connections_begin_immediate_no_locked_error(tmp_path):
    """Test 8 concurrent threads writing to history.db using BEGIN IMMEDIATE without lock errors."""
    db_path = tmp_path / "concurrent_history.db"
    store = HistoryStore(db_path)

    # Pre-create session
    session = store.create_session("Concurrent Test Session")
    session_id = session["id"]

    num_threads = 8
    turns_per_thread = 10
    errors = []

    def worker(worker_id: int):
        try:
            for i in range(turns_per_thread):
                req_id = f"worker_{worker_id}_turn_{i}"
                store.add_turn(
                    session_id=session_id,
                    question=f"Question from worker {worker_id} iter {i}",
                    answer=f"Answer from worker {worker_id} iter {i}",
                    citations=[],
                    model="test_model",
                    request_id=req_id,
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Encountered concurrency errors: {errors}"

    # Verify all turns were inserted without any loss
    sess = store.get_session(session_id)
    assert sess is not None
    assert len(sess["turns"]) == num_threads * turns_per_thread


def test_concurrent_readers_and_writers_in_wal_mode(tmp_path):
    """Verify that concurrent readers are not blocked by writers in WAL mode."""
    db_path = tmp_path / "wal_concurrency.db"
    conn_setup = open_sqlite(db_path, timeout=10.0, wal=True, synchronous_full=True)
    with atomic_transaction(conn_setup):
        conn_setup.execute("CREATE TABLE counter (id INT PRIMARY KEY, val INT)")
        conn_setup.execute("INSERT INTO counter VALUES (1, 0)")
    conn_setup.close()

    stop_event = threading.Event()
    read_counts = []
    write_counts = []
    errors = []

    def reader_worker():
        conn = open_sqlite(db_path, timeout=10.0, wal=True, synchronous_full=True)
        count = 0
        try:
            while not stop_event.is_set():
                row = conn.execute("SELECT val FROM counter WHERE id = 1").fetchone()
                assert row is not None
                count += 1
                time.sleep(0.001)
            read_counts.append(count)
        except Exception as exc:
            errors.append(("reader", exc))
        finally:
            conn.close()

    def writer_worker():
        conn = open_sqlite(db_path, timeout=10.0, wal=True, synchronous_full=True)
        count = 0
        try:
            for _ in range(30):
                with atomic_transaction(conn):
                    conn.execute("UPDATE counter SET val = val + 1 WHERE id = 1")
                count += 1
                time.sleep(0.002)
            write_counts.append(count)
        except Exception as exc:
            errors.append(("writer", exc))
        finally:
            conn.close()

    r_threads = [threading.Thread(target=reader_worker) for _ in range(4)]
    w_threads = [threading.Thread(target=writer_worker) for _ in range(2)]

    for t in r_threads + w_threads:
        t.start()

    for t in w_threads:
        t.join()

    stop_event.set()
    for t in r_threads:
        t.join()

    assert not errors, f"Errors during WAL reader/writer test: {errors}"
    assert sum(write_counts) == 60
    assert sum(read_counts) > 0


def test_concurrent_uploads_and_metadata_updates(tmp_path, monkeypatch):
    """Test concurrent uploads to PrivateMaterials under same account."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    acc_id = "a1" * 16
    token = account_id.set(acc_id)

    try:
        manager = make_material_manager()
        num_uploads = 12
        errors = []

        def upload_worker(idx: int):
            try:
                fn = f"file_{idx}.txt"
                content = f"Content for file {idx} with unique payload {idx * 12345}".encode()
                mat_id = f"mat_{idx}"
                manager.cache_raw_file(fn, content, material_id=mat_id)
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(upload_worker, i) for i in range(num_uploads)]
            concurrent.futures.wait(futures)

        assert not errors, f"Concurrent upload errors: {errors}"

        # Verify all files exist in originals/ and can be retrieved
        restored = make_material_manager()
        for idx in range(num_uploads):
            fn = f"file_{idx}.txt"
            expected = f"Content for file {idx} with unique payload {idx * 12345}".encode()
            assert restored._raw_files[fn] == expected
            assert restored._raw_files[f"mat_{idx}"] == expected
    finally:
        account_id.reset(token)


def test_database_commit_failure_preserves_consistency(tmp_path, monkeypatch):
    """Test that a database failure during upload rolls back without leaving corrupted references."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    acc_id = "b2" * 16
    token = account_id.set(acc_id)

    try:
        manager = make_material_manager()
        # Save a valid initial file
        manager.cache_raw_file("initial.txt", b"initial content", material_id="mat_init")
        assert manager._raw_files["initial.txt"] == b"initial content"

        # Simulate DB commit failure by sabotaging a table or query
        with manager.connect() as db:
            # Add a trigger that rejects any insert for file with key 'failing.txt'
            db.execute("""
                CREATE TRIGGER reject_failing BEFORE INSERT ON files
                FOR EACH ROW WHEN NEW.key = 'failing.txt'
                BEGIN
                    SELECT RAISE(FAIL, 'Simulated commit failure');
                END;
            """)

        with pytest.raises(sqlite3.IntegrityError, match="Simulated commit failure"):
            manager.cache_raw_file("failing.txt", b"failing content", material_id="mat_failing")

        # Verify same-process manager mapping does NOT expose uncommitted bytes
        assert "failing.txt" not in manager._raw_files
        assert "mat_failing" not in manager._raw_files

        # Verify fresh manager does not see failed upload
        restored = make_material_manager()
        assert restored._raw_files["initial.txt"] == b"initial content"
        assert "failing.txt" not in restored._raw_files
        assert "mat_failing" not in restored._raw_files
    finally:
        account_id.reset(token)


def test_atomic_transaction_propagates_busy_and_does_not_yield_unlocked(tmp_path):
    """BEGIN IMMEDIATE must propagate OperationalError on busy timeout without executing the body."""
    db_path = tmp_path / "busy_test.db"
    conn_writer = open_sqlite(db_path, timeout=5.0)
    conn_writer.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    conn_writer.execute("BEGIN EXCLUSIVE")
    conn_writer.execute("INSERT INTO t VALUES (1)")

    conn_contender = open_sqlite(db_path, timeout=0.1)

    body_entered = False
    with pytest.raises(sqlite3.OperationalError):
        with atomic_transaction(conn_contender):
            body_entered = True

    assert body_entered is False, "Critical defect: atomic_transaction executed body unlocked after failed BEGIN IMMEDIATE"

    conn_contender.close()
    conn_writer.rollback()
    conn_writer.close()


def test_atomic_transaction_rollback_on_body_exception(tmp_path):
    """atomic_transaction rolls back any writes made inside the block when an exception occurs."""
    db_path = tmp_path / "rollback_test.db"
    conn = open_sqlite(db_path, timeout=5.0)
    conn.execute("CREATE TABLE items (id INT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO items VALUES (1, 'original')")
    conn.commit()

    with pytest.raises(RuntimeError, match="crash during write"):
        with atomic_transaction(conn):
            conn.execute("UPDATE items SET name = 'mutated' WHERE id = 1")
            conn.execute("INSERT INTO items VALUES (2, 'uncommitted')")
            raise RuntimeError("crash during write")

    # Verify rollback: row 1 retained original value, row 2 was not inserted
    row = conn.execute("SELECT name FROM items WHERE id = 1").fetchone()
    assert row["name"] == "original"
    assert conn.execute("SELECT COUNT(*) FROM items WHERE id = 2").fetchone()[0] == 0
    conn.close()


def test_overwriting_same_alias_with_two_manager_instances(tmp_path, monkeypatch):
    """Overwriting an alias with a second manager must update DB and evict stale in-memory bytes in first manager."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    acc_id = "c1" * 16
    token = account_id.set(acc_id)

    try:
        mgr1 = make_material_manager()
        mgr1.cache_raw_file("document.txt", b"Initial Content Version 1", material_id="mat_doc")
        # mgr1 caches in memory
        assert mgr1._raw_files["document.txt"] == b"Initial Content Version 1"

        # mgr2 writes newer version to the same alias
        mgr2 = make_material_manager()
        mgr2.cache_raw_file("document.txt", b"Updated Content Version 2", material_id="mat_doc")

        # mgr1 should reflect the updated content, evicting the stale in-memory cached bytes
        assert mgr1._raw_files["document.txt"] == b"Updated Content Version 2"
        assert mgr1._raw_files["mat_doc"] == b"Updated Content Version 2"
    finally:
        account_id.reset(token)


def test_material_aliases_preserve_basename_and_material_id(tmp_path, monkeypatch):
    """Caching a file with relative directory path preserves full filename, basename, and material_id aliases."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    acc_id = "c2" * 16
    token = account_id.set(acc_id)

    try:
        mgr = make_material_manager()
        payload = b"Deeply nested syllabus content 2026"
        mgr.cache_raw_file("course_materials/unit1/syllabus.pdf", payload, material_id="mat_unit1_syl")

        # All 3 aliases in _raw_files must point to the identical content
        assert mgr._raw_files["course_materials/unit1/syllabus.pdf"] == payload
        assert mgr._raw_files["syllabus.pdf"] == payload
        assert mgr._raw_files["mat_unit1_syl"] == payload

        # DB must have all 3 alias keys
        with mgr.connect() as db:
            keys = {r[0] for r in db.execute("SELECT key FROM files").fetchall()}
            assert "course_materials/unit1/syllabus.pdf" in keys
            assert "syllabus.pdf" in keys
            assert "mat_unit1_syl" in keys
    finally:
        account_id.reset(token)


def test_corrupted_original_access_fails_closed(tmp_path, monkeypatch):
    """Accessing or writing to a corrupted immutable CAS file must fail closed and never rewrite."""
    import hashlib
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    acc_id = "c3" * 16
    token = account_id.set(acc_id)

    try:
        mgr = make_material_manager()
        original_bytes = b"Authentic document payload"
        file_hash = hashlib.sha256(original_bytes).hexdigest()
        mgr.cache_raw_file("authentic.pdf", original_bytes, material_id="mat_auth")

        # Corrupt the on-disk CAS file
        cas_file = accounts_dir / acc_id / "originals" / f"{file_hash}.bin"
        assert cas_file.exists()
        cas_file.write_bytes(b"TAMPERED_CONTENT_CORRUPTED")

        with pytest.raises((KeyError, RuntimeError)):
            _ = mgr._raw_files["authentic.pdf"]

        # Fresh manager reading the corrupted file must fail closed (raises KeyError/RuntimeError)
        mgr_fresh = make_material_manager()
        with pytest.raises((KeyError, RuntimeError)):
            _ = mgr_fresh._raw_files["authentic.pdf"]

        # Attempting to write a file whose hash already exists on disk as corrupt must fail closed without rewriting
        with pytest.raises(RuntimeError, match="[Cc]orrupt.* detected"):
            mgr.cache_raw_file("another_name.pdf", original_bytes, material_id="mat_auth2")

        # Ensure corrupted file was NOT silently overwritten
        assert cas_file.read_bytes() == b"TAMPERED_CONTENT_CORRUPTED"
    finally:
        account_id.reset(token)


def test_concurrent_same_hash_writes(tmp_path, monkeypatch):
    """Multiple concurrent writers uploading identical file content succeed without errors or corrupting CAS."""
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    acc_id = "c4" * 16
    token = account_id.set(acc_id)

    try:
        shared_payload = b"IDENTICAL SHARED SLIDE DATA " * 50
        errors = []

        def worker(idx: int):
            t = account_id.set(acc_id)
            try:
                # Each worker instantiates a manager on its thread
                worker_mgr = make_material_manager()
                fn = f"shared_file_{idx}.pdf"
                worker_mgr.cache_raw_file(fn, shared_payload, material_id=f"mat_shared_{idx}")
            except Exception as exc:
                errors.append(exc)
            finally:
                account_id.reset(t)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(16)]
            concurrent.futures.wait(futures)

        assert not errors, f"Errors during concurrent same-hash writes: {errors}"

        # Verify exactly one original CAS file was created
        originals = list((accounts_dir / acc_id / "originals").glob("*.bin"))
        assert len(originals) == 1

        # Verify all filenames can retrieve the data cleanly
        verify_mgr = make_material_manager()
        for idx in range(16):
            assert verify_mgr._raw_files[f"shared_file_{idx}.pdf"] == shared_payload
            assert verify_mgr._raw_files[f"mat_shared_{idx}"] == shared_payload
    finally:
        account_id.reset(token)
