"""Tests for atomic versioned SQLite schema migrations, rollback, retry, and foreign key enforcement."""
from pathlib import Path
import sqlite3
import pytest

from app.db import (
    Migration,
    MigrationManager,
    auth_migrations,
    history_migrations,
    open_sqlite,
    timetable_migrations,
    uploads_migrations,
)


def test_migration_versioning_and_order(tmp_path):
    db_path = tmp_path / "test_migration.db"
    conn = open_sqlite(db_path, timeout=5.0)

    mgr = MigrationManager(
        store_name="custom_test",
        migrations=[
            Migration(1, "create_t1", lambda c: c.execute("CREATE TABLE t1 (id INT PRIMARY KEY, name TEXT)")),
            Migration(2, "create_t2", lambda c: c.execute("CREATE TABLE t2 (id INT PRIMARY KEY, val TEXT)")),
        ],
    )

    assert mgr.current_version(conn) == 0
    applied = mgr.apply(conn)
    assert applied == 2
    assert mgr.current_version(conn) == 2

    # Re-apply is idempotent (no-op)
    applied_again = mgr.apply(conn)
    assert applied_again == 0
    assert mgr.current_version(conn) == 2

    # Verify tables exist
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "t1" in tables
    assert "t2" in tables
    assert "schema_migrations" in tables
    conn.close()


def test_failing_migration_rollback_and_retry(tmp_path):
    db_path = tmp_path / "test_rollback.db"
    conn = open_sqlite(db_path, timeout=5.0)

    # Step 1: Migration 1 succeeds
    m1 = Migration(1, "v1_ok", lambda c: c.execute("CREATE TABLE initial_data (id INT PRIMARY KEY)"))
    mgr1 = MigrationManager("rollback_test", [m1])
    mgr1.apply(conn)
    assert mgr1.current_version(conn) == 1

    # Step 2: Migration 2 fails halfway
    def bad_migration(c: sqlite3.Connection):
        c.execute("CREATE TABLE table_that_should_rollback (id INT PRIMARY KEY)")
        raise RuntimeError("Intentional migration crash for rollback testing")

    m2_failing = Migration(2, "v2_fail", bad_migration)
    mgr_failing = MigrationManager("rollback_test", [m1, m2_failing])

    with pytest.raises(RuntimeError, match="Intentional migration crash"):
        mgr_failing.apply(conn)

    # Assert version remains at 1
    assert mgr_failing.current_version(conn) == 1

    # Assert table created in bad migration was rolled back
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "table_that_should_rollback" not in tables
    assert "initial_data" in tables

    # Step 3: Retry with corrected Migration 2
    m2_fixed = Migration(2, "v2_fixed", lambda c: c.execute("CREATE TABLE table_fixed (id INT PRIMARY KEY, val TEXT)"))
    mgr_fixed = MigrationManager("rollback_test", [m1, m2_fixed])
    applied = mgr_fixed.apply(conn)
    assert applied == 1
    assert mgr_fixed.current_version(conn) == 2

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "table_fixed" in tables
    conn.close()


def test_legacy_unversioned_auth_upgrade_and_orphan_cleanup(tmp_path):
    db_path = tmp_path / "accounts.db"
    # Create unversioned legacy database without schema_migrations
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.executescript("""
        CREATE TABLE accounts(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE feature_usage(account_id TEXT NOT NULL, feature TEXT NOT NULL, guided INTEGER NOT NULL DEFAULT 0, last_used REAL NOT NULL, PRIMARY KEY(account_id,feature));
        CREATE TABLE auth_attempts(identity TEXT NOT NULL, attempted REAL NOT NULL);

        -- Insert valid account
        INSERT INTO accounts VALUES('acc11111111111111111111111111111', 'valid_user', 'salt1', 'hash1');

        -- Insert valid session and feature usage
        INSERT INTO sessions VALUES('token_valid', 'acc11111111111111111111111111111', 9999999999.0);
        INSERT INTO feature_usage VALUES('acc11111111111111111111111111111', 'planner', 1, 1000.0);

        -- Insert ORPHAN session and feature usage (referencing non-existent account)
        INSERT INTO sessions VALUES('token_orphan', 'orphan_account_does_not_exist', 9999999999.0);
        INSERT INTO feature_usage VALUES('orphan_account_does_not_exist', 'planner', 1, 1000.0);
    """)
    raw_conn.commit()
    raw_conn.close()

    # Now upgrade using auth_migrations
    conn = open_sqlite(db_path, timeout=5.0, wal=True, synchronous_full=True, foreign_keys=True)
    applied = auth_migrations.apply(conn)
    assert applied >= 2

    # Verify orphan records were audited and cleaned
    sessions = [r[0] for r in conn.execute("SELECT token_hash FROM sessions").fetchall()]
    assert "token_valid" in sessions
    assert "token_orphan" not in sessions

    features = [r[0] for r in conn.execute("SELECT account_id FROM feature_usage").fetchall()]
    assert "acc11111111111111111111111111111" in features
    assert "orphan_account_does_not_exist" not in features

    # Verify foreign key cascading enforcement: deleting account cascades to sessions and feature_usage
    conn.execute("DELETE FROM accounts WHERE id = 'acc11111111111111111111111111111'")
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE account_id = 'acc11111111111111111111111111111'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM feature_usage WHERE account_id = 'acc11111111111111111111111111111'").fetchone()[0] == 0

    # Verify foreign keys reject inserting session with invalid account_id
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sessions VALUES('token_new', 'invalid_acc', 9999999999.0)")

    # Verify indices exist
    indices = {r[1] for r in conn.execute("SELECT type, name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "auth_attempt_lookup" in indices
    assert "idx_sessions_account_id" in indices
    assert "idx_feature_usage_account" in indices
    conn.close()


def test_wal_busy_timeout_and_synchronous_pragmas(tmp_path):
    db_path = tmp_path / "pragmas.db"
    conn = open_sqlite(db_path, timeout=10.0, wal=True, synchronous_full=True, foreign_keys=True)

    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    assert journal_mode == "wal"

    busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    assert busy_timeout >= 10000

    # PRAGMA synchronous returns 2 for FULL
    synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
    assert synchronous in (2, "2", "FULL")

    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk in (1, "1", "ON")

    conn.close()
