"""Tests for idempotent legacy database upgrades and opt-in non-destructive data migration."""
import hashlib
from pathlib import Path
import sqlite3
import pytest

from app.db import (
    auth_migrations,
    history_migrations,
    open_sqlite,
    managed_sqlite,
    timetable_migrations,
    uploads_migrations,
)
from scripts.migrate_legacy_data import compute_sha256, migrate_legacy


def test_idempotent_legacy_upgrade_preserves_records(tmp_path):
    """Upgrading unversioned databases preserves all rows and repeating upgrade is idempotent."""
    # 1. Unversioned accounts.db
    auth_path = tmp_path / "accounts.db"
    conn_raw = sqlite3.connect(str(auth_path))
    conn_raw.executescript("""
        CREATE TABLE accounts(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE feature_usage(account_id TEXT NOT NULL, feature TEXT NOT NULL, guided INTEGER NOT NULL DEFAULT 0, last_used REAL NOT NULL, PRIMARY KEY(account_id,feature));
        CREATE TABLE auth_attempts(identity TEXT NOT NULL, attempted REAL NOT NULL);

        INSERT INTO accounts VALUES ('acc1', 'user1', 'salt1', 'hash1');
        INSERT INTO sessions VALUES ('tok1', 'acc1', 9999999999.0);
        INSERT INTO feature_usage VALUES ('acc1', 'planner', 1, 100.0);
    """)
    conn_raw.commit()
    conn_raw.close()

    # 2. Unversioned history.db
    hist_path = tmp_path / "history.db"
    conn_raw = sqlite3.connect(str(hist_path))
    conn_raw.executescript("""
        CREATE TABLE searches (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, question TEXT, answer TEXT, citations_json TEXT, model TEXT);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, title_mode TEXT, summary TEXT, summary_status TEXT, summarized_revision INT, created_at TEXT, updated_at TEXT, revision INT, is_legacy INT);
        CREATE TABLE session_turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, turn_index INT, question TEXT, answer TEXT, citations_json TEXT, model TEXT, created_at TEXT, request_id TEXT);

        INSERT INTO searches VALUES (1, '2026-11-09', 'What is memory?', 'Memory is...', '[]', 'model1');
        INSERT INTO sessions VALUES ('sess_old', 'Old Chat', 'auto', NULL, 'none', 0, '2026-11-09', '2026-11-09', 1, 1);
        INSERT INTO session_turns VALUES (1, 'sess_old', 1, 'What is memory?', 'Memory is...', '[]', 'model1', '2026-11-09', 'req1');
    """)
    conn_raw.commit()
    conn_raw.close()

    # 3. Unversioned timetable.db
    tt_path = tmp_path / "timetable.db"
    conn_raw = sqlite3.connect(str(tt_path))
    conn_raw.executescript("""
        CREATE TABLE timetable_confirmed (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT, course TEXT, weekday INT, start_time TEXT, end_time TEXT, room TEXT, created_at TEXT);
        INSERT INTO timetable_confirmed VALUES (1, 'd1', 'Physics', 3, '09:00', '11:00', 'A101', '2026-11-09');
    """)
    conn_raw.commit()
    conn_raw.close()

    # 4. Unversioned uploads.db
    up_path = tmp_path / "uploads.db"
    conn_raw = sqlite3.connect(str(up_path))
    conn_raw.executescript("""
        CREATE TABLE records (id TEXT PRIMARY KEY, content TEXT NOT NULL);
        CREATE TABLE files (key TEXT PRIMARY KEY, content BLOB NOT NULL);
        INSERT INTO records VALUES ('mat1', '{"title": "Legacy Notes"}');
        INSERT INTO files VALUES ('notes.txt', X'4C656761637920426C6F62');
    """)
    conn_raw.commit()
    conn_raw.close()

    # Apply migrations first time
    with managed_sqlite(auth_path) as db:
        auth_migrations.apply(db)
    with managed_sqlite(hist_path) as db:
        history_migrations.apply(db)
    with managed_sqlite(tt_path) as db:
        timetable_migrations.apply(db)
    with managed_sqlite(up_path) as db:
        uploads_migrations.apply(db)

    # Verify rows preserved after first upgrade
    with managed_sqlite(auth_path) as db:
        assert db.execute("SELECT username FROM accounts WHERE id='acc1'").fetchone()[0] == "user1"
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    with managed_sqlite(hist_path) as db:
        assert db.execute("SELECT question FROM searches WHERE id=1").fetchone()[0] == "What is memory?"
        assert db.execute("SELECT title FROM sessions WHERE id='sess_old'").fetchone()[0] == "Old Chat"
    with managed_sqlite(tt_path) as db:
        assert db.execute("SELECT course FROM timetable_confirmed WHERE id=1").fetchone()[0] == "Physics"
    with managed_sqlite(up_path) as db:
        assert db.execute("SELECT content FROM records WHERE id='mat1'").fetchone()[0] == '{"title": "Legacy Notes"}'

    # Apply migrations SECOND time (idempotency check)
    with managed_sqlite(auth_path) as db:
        assert auth_migrations.apply(db) == 0
    with managed_sqlite(hist_path) as db:
        assert history_migrations.apply(db) == 0
    with managed_sqlite(tt_path) as db:
        assert timetable_migrations.apply(db) == 0
    with managed_sqlite(up_path) as db:
        assert uploads_migrations.apply(db) == 0

    # Assert row counts remain identical
    with managed_sqlite(auth_path) as db:
        assert db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    with managed_sqlite(hist_path) as db:
        assert db.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM session_turns").fetchone()[0] == 1
    with managed_sqlite(tt_path) as db:
        assert db.execute("SELECT COUNT(*) FROM timetable_confirmed").fetchone()[0] == 1


def test_migrate_legacy_data_opt_in_validation(tmp_path):
    """Test target account validation: required, registered, non-guest."""
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = tmp_path / "source_legacy"
    source_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Setup accounts.db with registered account
    valid_acc = "12" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'registered_user', 'salt', 'hash')", (valid_acc,))

    # 1. Missing target account
    with pytest.raises(ValueError, match="Opt-in target account is required"):
        migrate_legacy(source_dir, accounts_dir, "")

    # 2. Implicit guest or anonymous
    with pytest.raises(ValueError, match="Implicit guest or anonymous migration is strictly forbidden"):
        migrate_legacy(source_dir, accounts_dir, "guest")
    with pytest.raises(ValueError, match="Implicit guest or anonymous migration is strictly forbidden"):
        migrate_legacy(source_dir, accounts_dir, "anonymous")

    # 3. Unregistered target account
    with pytest.raises(ValueError, match="does not exist in accounts.db"):
        migrate_legacy(source_dir, accounts_dir, "99" * 16)


def test_migrate_legacy_non_destructive_and_knowledge_preservation(tmp_path):
    """Test legacy migration copies data without modifying source and preserves knowledge assets."""
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = tmp_path / "source_legacy"
    source_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Register target account
    target_acc = "34" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'bob', 'salt', 'hash')", (target_acc,))

    # Create source legacy databases
    hist_src = source_dir / "history.db"
    with managed_sqlite(hist_src) as db:
        history_migrations.apply(db)
        db.execute("INSERT INTO searches (question, answer, citations_json, model) VALUES ('Legacy Q?', 'Legacy A.', '[]', 'm1')")
        db.execute("INSERT INTO sessions (id, title, title_mode) VALUES ('sess_leg', 'Legacy Chat', 'auto')")
        db.execute("INSERT INTO session_turns (session_id, turn_index, question, answer, citations_json, model) VALUES ('sess_leg', 1, 'Q?', 'A.', '[]', 'm1')")

    tt_src = source_dir / "timetable.db"
    with managed_sqlite(tt_src) as db:
        timetable_migrations.apply(db)
        db.execute("INSERT INTO timetable_confirmed (course, weekday, start_time, end_time, created_at) VALUES ('Chemistry', 1, '08:00', '10:00', '2026-11-09')")

    sha_hist_before = compute_sha256(hist_src)
    sha_tt_before = compute_sha256(tt_src)

    # Execute migration
    res = migrate_legacy(source_dir, accounts_dir, target_acc)
    assert res["status"] == "success"
    assert res["source_preservation_verified"] is True
    assert res["migrated_history"]["searches"] == 1
    assert res["migrated_history"]["sessions"] == 1
    assert res["migrated_timetable"]["confirmed"] == 1

    # Verify source file hashes are 100% identical (zero bytes modified)
    assert compute_sha256(hist_src) == sha_hist_before
    assert compute_sha256(tt_src) == sha_tt_before

    # Verify target account has imported data
    target_hist = accounts_dir / target_acc / "history.db"
    with managed_sqlite(target_hist) as db:
        assert db.execute("SELECT question FROM searches").fetchone()[0] == "Legacy Q?"
        assert db.execute("SELECT title FROM sessions").fetchone()[0] == "Legacy Chat"

    target_tt = accounts_dir / target_acc / "timetable.db"
    with managed_sqlite(target_tt) as db:
        assert db.execute("SELECT course FROM timetable_confirmed").fetchone()[0] == "Chemistry"

    # Re-running is idempotent (0 newly inserted duplicates)
    res2 = migrate_legacy(source_dir, accounts_dir, target_acc)
    assert res2["migrated_history"]["searches"] == 0
    assert res2["migrated_history"]["sessions"] == 0
    assert res2["migrated_timetable"]["confirmed"] == 0

    # Knowledge assets protection: passing protected source path fails
    with pytest.raises(ValueError, match="Protected source path"):
        migrate_legacy(Path("/data/knowledge_base.db"), accounts_dir, target_acc)


def test_migrate_legacy_fails_closed_on_id_collision(tmp_path):
    """When target database already has records with same ID but different data, migration must fail closed before modifying target."""
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = tmp_path / "source_collision"
    source_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    target_acc = "88" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'user88', 'salt', 'hash')", (target_acc,))

    # Target already has searches id=1 with target question
    target_hist = accounts_dir / target_acc / "history.db"
    with managed_sqlite(target_hist) as db:
        history_migrations.apply(db)
        db.execute("INSERT INTO searches (id, question, answer, citations_json, model) VALUES (1, 'TARGET QUESTION', 'TARGET ANSWER', '[]', 'm_target')")

    # Source has searches id=1 with different legacy question
    src_hist = source_dir / "history.db"
    with managed_sqlite(src_hist) as db:
        history_migrations.apply(db)
        db.execute("INSERT INTO searches (id, question, answer, citations_json, model) VALUES (1, 'LEGACY QUESTION', 'LEGACY ANSWER', '[]', 'm_legacy')")

    with pytest.raises(ValueError, match="ID collision detected in 'searches' table for id=1"):
        migrate_legacy(source_dir, accounts_dir, target_acc)

    # Verify target was NOT modified
    with managed_sqlite(target_hist) as db:
        row = db.execute("SELECT question, answer FROM searches WHERE id = 1").fetchone()
        assert row["question"] == "TARGET QUESTION"


def test_migrate_legacy_copies_all_planner_tables(tmp_path):
    """Legacy migration must copy all 9 planner tables including plans, sessions, preferences, confirmation events, and VLM cache."""
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = tmp_path / "source_all_planner"
    source_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    target_acc = "99" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'planner_user', 'salt', 'hash')", (target_acc,))

    src_tt = source_dir / "timetable.db"
    with managed_sqlite(src_tt) as db:
        timetable_migrations.apply(db)
        # 1. Draft
        db.execute("""INSERT INTO timetable_drafts (
            draft_id, image_hash, filename, created_at, status, raw_model_output,
            entries_json, uncertainties_json, model_digest, prompt_version, options_json,
            confirmed_at, confirmed_entries_json
        ) VALUES ('d_full', 'img_h', 'sched.png', '2026-11-09', 'confirmed', 'raw', '[]', '[]', 'dig1', 'v1', '{}', '2026-11-09', '[]')""")

        # 2. Confirmation events
        db.execute("INSERT INTO timetable_confirmation_events (id, draft_id, confirmed_at, entries_json, replaced_schedule_json) VALUES (1, 'd_full', '2026-11-09', '[]', '[]')")

        # 3. Confirmed
        db.execute("INSERT INTO timetable_confirmed (id, draft_id, course, weekday, start_time, end_time, room, created_at) VALUES (1, 'd_full', 'Bio', 2, '10:00', '12:00', 'B201', '2026-11-09')")

        # 4. VLM cache
        db.execute("INSERT INTO timetable_vlm_cache (cache_key, image_hash, model_digest, prompt_version, response_json, created_at) VALUES ('k1', 'img_h', 'dig1', 'v1', '{}', '2026-11-09')")

        # 5. Routines
        db.execute("INSERT INTO personal_routines (id, weekday, start_time, end_time, title, activity_type, flexibility, is_recurring, note, created_at) VALUES (1, 1, '07:00', '08:00', 'Jog', 'exercise', 'fixed', 1, '', '2026-11-09')")

        # 6. Preferences
        db.execute("INSERT OR REPLACE INTO study_preferences (id, earliest_study_time, latest_study_time, max_daily_study_minutes, preferred_session_minutes, min_session_minutes, buffer_before_deadline_hours, ai_notes, week_start_date, updated_at) VALUES (1, '08:00', '22:00', 300, 45, 20, 24, 'prefer morning', '2026-11-09', '2026-11-09')")

        # 7. Tasks
        db.execute("INSERT INTO study_tasks (id, title, course, deadline_weekday, deadline_time, deadline_date, estimated_minutes, priority, completed, completed_minutes, splittable, notes, created_at) VALUES (1, 'Essay', 'Literature', 4, '23:59', '2026-11-12', 120, 'high', 0, 0, 1, '', '2026-11-09')")

        # 8. Plan drafts
        db.execute("INSERT INTO study_plan_drafts (plan_id, created_at, status, sessions_json, unscheduled_json, warnings_json, suggestions_json, explanation, preserved_sessions_json) VALUES ('p1', '2026-11-09', 'draft', '[]', '[]', '[]', '[]', 'Plan exp', '[]')")

        # 9. Study sessions
        db.execute("INSERT INTO study_sessions (id, plan_id, task_id, title, course, weekday, start_time, end_time, duration_minutes, priority, source, completed, locked, session_index, total_sessions, notes, created_at) VALUES (1, 'p1', 1, 'Write section 1', 'Literature', 3, '14:00', '15:00', 60, 'high', 'ai_assistant', 0, 0, 1, 2, '', '2026-11-09')")

    res = migrate_legacy(source_dir, accounts_dir, target_acc)
    assert res["status"] == "success"
    counts = res["migrated_timetable"]
    assert counts["drafts"] == 1
    assert counts["confirmation_events"] == 1
    assert counts["confirmed"] == 1
    assert counts["vlm_cache"] == 1
    assert counts["routines"] == 1
    assert counts["preferences"] == 1
    assert counts["tasks"] == 1
    assert counts["plans"] == 1
    assert counts["sessions"] == 1


def test_migrate_legacy_handles_older_session_turns_missing_optional_columns(tmp_path):
    """Source database with older schema lacking answer_mode and search_scope_json must migrate cleanly."""
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = tmp_path / "source_older"
    source_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    target_acc = "aa" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'older_schema_user', 'salt', 'hash')", (target_acc,))

    # Create older source history.db without answer_mode or search_scope_json
    src_hist = source_dir / "history.db"
    conn = sqlite3.connect(str(src_hist))
    conn.executescript("""
        CREATE TABLE searches (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, question TEXT, answer TEXT, citations_json TEXT, model TEXT);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, title_mode TEXT, summary TEXT, summary_status TEXT, summarized_revision INT, created_at TEXT, updated_at TEXT, revision INT, is_legacy INT);
        CREATE TABLE session_turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, turn_index INT, question TEXT, answer TEXT, citations_json TEXT, model TEXT, created_at TEXT, request_id TEXT);

        INSERT INTO sessions VALUES ('sess_v0', 'V0 Session', 'auto', NULL, 'none', 0, '2026-11-09', '2026-11-09', 1, 0);
        INSERT INTO session_turns VALUES (1, 'sess_v0', 1, 'Question 1', 'Answer 1', '[]', 'm1', '2026-11-09', 'req_v0');
    """)
    conn.commit()
    conn.close()

    res = migrate_legacy(source_dir, accounts_dir, target_acc)
    assert res["status"] == "success"
    assert res["migrated_history"]["turns"] == 1

    # Verify target session_turns has default answer_mode and search_scope_json
    target_hist = accounts_dir / target_acc / "history.db"
    with managed_sqlite(target_hist) as db:
        row = db.execute("SELECT answer_mode, search_scope_json FROM session_turns WHERE id = 1").fetchone()
        assert row["answer_mode"] == "quick"
        assert row["search_scope_json"] == "{}"


def test_migrate_legacy_rejects_same_source_target_and_symlinks(tmp_path):
    """Migration must reject identical source and target directory, and reject symlink target directory."""
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    target_acc = "bb" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'user_sym', 'salt', 'hash')", (target_acc,))

    target_dir = accounts_dir / target_acc
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    # 1. Same source and target
    with pytest.raises(ValueError, match="Source directory and target account directory cannot be identical"):
        migrate_legacy(target_dir, accounts_dir, target_acc)

    # 2. Target directory is a symlink
    sym_acc = "cc" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        db.execute("INSERT INTO accounts VALUES (?, 'user_sym2', 'salt', 'hash')", (sym_acc,))

    sym_dir = accounts_dir / sym_acc
    try:
        sym_dir.symlink_to(target_dir)
        with pytest.raises(ValueError, match="Target account directory cannot be a symlink"):
            migrate_legacy(tmp_path, accounts_dir, sym_acc)
    except OSError:
        pass  # skip if OS symlinks not permitted


def test_migrate_legacy_fails_closed_on_session_turn_unique_collision_different_id(tmp_path):
    """When target already has a session turn with same (session_id, turn_index) but different id, migration fails closed."""
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = tmp_path / "source_turn_collision"
    source_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    target_acc = "dd" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'user_turn_col', 's', 'h')", (target_acc,))

    # Target has turn with id=1, session_id='sess_col', turn_index=1
    target_hist = accounts_dir / target_acc / "history.db"
    with managed_sqlite(target_hist) as db:
        history_migrations.apply(db)
        db.execute("INSERT INTO sessions (id, title, title_mode, created_at, updated_at) VALUES ('sess_col', 'Title', 'auto', '2026-11-09T00:00:00Z', '2026-11-09T00:00:00Z')")
        db.execute("INSERT INTO session_turns (id, session_id, turn_index, question, answer, citations_json, model, request_id) VALUES (1, 'sess_col', 1, 'Target Q', 'Target A', '[]', 'm1', 'req1')")

    # Source has turn with id=2, session_id='sess_col', turn_index=1 (colliding turn_index, different id)
    src_hist = source_dir / "history.db"
    with managed_sqlite(src_hist) as db:
        history_migrations.apply(db)
        db.execute("INSERT INTO sessions (id, title, title_mode, created_at, updated_at) VALUES ('sess_col', 'Title', 'auto', '2026-11-09T00:00:00Z', '2026-11-09T00:00:00Z')")
        db.execute("INSERT INTO session_turns (id, session_id, turn_index, question, answer, citations_json, model, request_id) VALUES (2, 'sess_col', 1, 'Source Colliding Q', 'Source Colliding A', '[]', 'm1', 'req2')")

    with pytest.raises(ValueError, match="Unique constraint collision detected in 'session_turns'"):
        migrate_legacy(source_dir, accounts_dir, target_acc)

    # Verify target was untouched
    with managed_sqlite(target_hist) as db:
        row = db.execute("SELECT question, answer FROM session_turns WHERE id = 1").fetchone()
        assert row["question"] == "Target Q"
        assert db.execute("SELECT COUNT(*) FROM session_turns WHERE id = 2").fetchone()[0] == 0


def test_migrate_legacy_preserves_planner_and_session_turn_metadata_fields(tmp_path):
    """Source session_turns containing answer_mode, search_scope_json, and legacy_search_id are preserved in target."""
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_dir = tmp_path / "source_metadata_fields"
    source_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    target_acc = "ee" * 16
    with managed_sqlite(accounts_dir / "accounts.db") as db:
        auth_migrations.apply(db)
        db.execute("INSERT INTO accounts VALUES (?, 'meta_user', 's', 'h')", (target_acc,))

    src_hist = source_dir / "history.db"
    with managed_sqlite(src_hist) as db:
        history_migrations.apply(db)
        db.execute("INSERT INTO sessions (id, title, title_mode) VALUES ('sess_meta', 'Meta Session', 'auto')")
        db.execute("""INSERT INTO session_turns (
            id, session_id, turn_index, question, answer, citations_json, model, created_at, request_id,
            answer_mode, search_scope_json, legacy_search_id
        ) VALUES (
            10, 'sess_meta', 1, 'Question with metadata?', 'Answer with metadata.', '[]', 'm_meta', '2026-11-09T00:00:00Z', 'req_meta',
            'detailed', '{"course": "physics"}', 42
        )""")

    res = migrate_legacy(source_dir, accounts_dir, target_acc)
    assert res["status"] == "success"
    assert res["migrated_history"]["turns"] == 1

    # Verify target has the metadata fields preserved
    target_hist = accounts_dir / target_acc / "history.db"
    with managed_sqlite(target_hist) as db:
        row = db.execute("SELECT answer_mode, search_scope_json, legacy_search_id FROM session_turns WHERE id = 10").fetchone()
        assert row is not None
        assert row["answer_mode"] == "detailed"
        assert row["search_scope_json"] == '{"course": "physics"}'
        assert row["legacy_search_id"] == 42
