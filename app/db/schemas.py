"""Database schemas and versioned migrations for global auth and per-account stores."""
import sqlite3
from typing import List

from app.db.migrations import Migration, MigrationManager


# ============================================================================
# Auth Store (accounts.db) Migrations
# ============================================================================

def _migrate_auth_v1(conn: sqlite3.Connection) -> None:
    # 1. Accounts table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    # 2. Audit orphan sessions and recreate with foreign keys if needed
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    if cursor.fetchone():
        # Remove orphan records
        conn.execute("DELETE FROM sessions WHERE account_id NOT IN (SELECT id FROM accounts)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions_migrated (
                token_hash TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                expires REAL NOT NULL
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO sessions_migrated (token_hash, account_id, expires)
            SELECT token_hash, account_id, expires FROM sessions
            WHERE account_id IN (SELECT id FROM accounts)
        """)
        conn.execute("DROP TABLE sessions")
        conn.execute("ALTER TABLE sessions_migrated RENAME TO sessions")
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                expires REAL NOT NULL
            )
        """)

    # 3. Audit orphan feature_usage and recreate with foreign keys
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='feature_usage'")
    if cursor.fetchone():
        conn.execute("DELETE FROM feature_usage WHERE account_id NOT IN (SELECT id FROM accounts)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feature_usage_migrated (
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                feature TEXT NOT NULL,
                guided INTEGER NOT NULL DEFAULT 0,
                last_used REAL NOT NULL,
                PRIMARY KEY(account_id, feature)
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO feature_usage_migrated (account_id, feature, guided, last_used)
            SELECT account_id, feature, guided, last_used FROM feature_usage
            WHERE account_id IN (SELECT id FROM accounts)
        """)
        conn.execute("DROP TABLE feature_usage")
        conn.execute("ALTER TABLE feature_usage_migrated RENAME TO feature_usage")
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feature_usage (
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                feature TEXT NOT NULL,
                guided INTEGER NOT NULL DEFAULT 0,
                last_used REAL NOT NULL,
                PRIMARY KEY(account_id, feature)
            )
        """)

    # 4. Auth attempts
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_attempts (
            identity TEXT NOT NULL,
            attempted REAL NOT NULL
        )
    """)


def _migrate_auth_v2(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS auth_attempt_lookup ON auth_attempts(identity, attempted)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_account_id ON sessions(account_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_lookup ON sessions(token_hash, expires)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_usage_account ON feature_usage(account_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)


auth_migrations = MigrationManager(
    store_name="auth",
    migrations=[
        Migration(1, "001_baseline_auth", _migrate_auth_v1),
        Migration(2, "002_auth_indexes_and_meta", _migrate_auth_v2),
    ],
)


# ============================================================================
# History Store (history.db) Migrations
# ============================================================================

def _migrate_history_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            citations_json TEXT NOT NULL,
            model TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            title_mode TEXT NOT NULL DEFAULT 'auto',
            summary TEXT,
            summary_status TEXT NOT NULL DEFAULT 'none',
            summarized_revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            revision INTEGER NOT NULL DEFAULT 1,
            is_legacy INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            turn_index INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            citations_json TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            request_id TEXT,
            answer_mode TEXT NOT NULL DEFAULT 'quick',
            search_scope_json TEXT NOT NULL DEFAULT '{}',
            legacy_search_id INTEGER,
            UNIQUE(session_id, turn_index),
            UNIQUE(session_id, request_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cols = [c[1] for c in conn.execute("PRAGMA table_info(session_turns)").fetchall()]
    if "legacy_search_id" not in cols and "id" in cols:
        conn.execute("ALTER TABLE session_turns ADD COLUMN legacy_search_id INTEGER")
    if "answer_mode" not in cols and "id" in cols:
        conn.execute("ALTER TABLE session_turns ADD COLUMN answer_mode TEXT NOT NULL DEFAULT 'quick'")
    if "search_scope_json" not in cols and "id" in cols:
        conn.execute("ALTER TABLE session_turns ADD COLUMN search_scope_json TEXT NOT NULL DEFAULT '{}'")

    sess_cols = [c[1] for c in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    if "summarized_revision" not in sess_cols and "id" in sess_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN summarized_revision INTEGER NOT NULL DEFAULT 0")


def _migrate_history_v2(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_turns_legacy_search ON session_turns(legacy_search_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_turns_request_id ON session_turns(request_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_searches_created_at ON searches(created_at DESC)")


history_migrations = MigrationManager(
    store_name="history",
    migrations=[
        Migration(1, "001_baseline_history", _migrate_history_v1),
        Migration(2, "002_history_indexes", _migrate_history_v2),
    ],
)


# ============================================================================
# Timetable Store (timetable.db) Migrations
# ============================================================================

def _migrate_timetable_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timetable_drafts (
            draft_id TEXT PRIMARY KEY,
            image_hash TEXT NOT NULL,
            filename TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            raw_model_output TEXT,
            entries_json TEXT NOT NULL,
            uncertainties_json TEXT NOT NULL,
            model_digest TEXT,
            prompt_version TEXT,
            options_json TEXT,
            confirmed_at TEXT,
            confirmed_entries_json TEXT
        )
    """)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(timetable_drafts)").fetchall()}
    if "model_digest" not in cols and "draft_id" in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN model_digest TEXT")
    if "prompt_version" not in cols and "draft_id" in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN prompt_version TEXT")
    if "options_json" not in cols and "draft_id" in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN options_json TEXT")
    if "confirmed_at" not in cols and "draft_id" in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN confirmed_at TEXT")
    if "confirmed_entries_json" not in cols and "draft_id" in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN confirmed_entries_json TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS timetable_confirmation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            entries_json TEXT NOT NULL,
            replaced_schedule_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timetable_confirmed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id TEXT,
            course TEXT NOT NULL,
            weekday INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            room TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timetable_vlm_cache (
            cache_key TEXT PRIMARY KEY,
            image_hash TEXT NOT NULL,
            model_digest TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS personal_routines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weekday INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            title TEXT NOT NULL,
            activity_type TEXT NOT NULL,
            flexibility TEXT NOT NULL,
            is_recurring INTEGER NOT NULL DEFAULT 1,
            note TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_preferences (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            earliest_study_time TEXT NOT NULL DEFAULT '07:00',
            latest_study_time TEXT NOT NULL DEFAULT '22:30',
            max_daily_study_minutes INTEGER NOT NULL DEFAULT 240,
            preferred_session_minutes INTEGER NOT NULL DEFAULT 60,
            min_session_minutes INTEGER NOT NULL DEFAULT 30,
            buffer_before_deadline_hours INTEGER NOT NULL DEFAULT 12,
            ai_notes TEXT DEFAULT '',
            week_start_date TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            course TEXT NOT NULL,
            deadline_weekday INTEGER NOT NULL,
            deadline_time TEXT NOT NULL DEFAULT '23:59',
            deadline_date TEXT,
            estimated_minutes INTEGER,
            priority TEXT NOT NULL DEFAULT 'medium',
            completed INTEGER NOT NULL DEFAULT 0,
            completed_minutes INTEGER NOT NULL DEFAULT 0,
            splittable INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_plan_drafts (
            plan_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            sessions_json TEXT NOT NULL,
            unscheduled_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL,
            suggestions_json TEXT NOT NULL,
            explanation TEXT,
            preserved_sessions_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT,
            task_id INTEGER,
            title TEXT NOT NULL,
            course TEXT NOT NULL,
            weekday INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            priority TEXT DEFAULT 'medium',
            source TEXT NOT NULL DEFAULT 'ai_assistant',
            completed INTEGER NOT NULL DEFAULT 0,
            locked INTEGER NOT NULL DEFAULT 0,
            session_index INTEGER DEFAULT 1,
            total_sessions INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """)


def _migrate_timetable_v2(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_sessions_plan_id ON study_sessions(plan_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_sessions_task_id ON study_sessions(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_tasks_deadline ON study_tasks(deadline_weekday, deadline_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timetable_confirmed_weekday ON timetable_confirmed(weekday, start_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_personal_routines_weekday ON personal_routines(weekday, start_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timetable_drafts_image_hash ON timetable_drafts(image_hash)")


def _migrate_timetable_v3(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course TEXT NOT NULL,
            lesson_title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'upcoming',
            classified_topic TEXT NOT NULL,
            domain_label TEXT DEFAULT '',
            question_count INTEGER NOT NULL DEFAULT 4,
            worksheet_id TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS planner_worksheets (
            worksheet_id TEXT PRIMARY KEY,
            course TEXT NOT NULL,
            topic TEXT NOT NULL,
            lesson_title TEXT DEFAULT '',
            questions_json TEXT NOT NULL,
            pdf_blob BLOB NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
    if "classified_topic" not in session_cols and "id" in session_cols:
        conn.execute("ALTER TABLE study_sessions ADD COLUMN classified_topic TEXT DEFAULT ''")
    if "worksheet_id" not in session_cols and "id" in session_cols:
        conn.execute("ALTER TABLE study_sessions ADD COLUMN worksheet_id TEXT DEFAULT ''")


def _migrate_timetable_v4(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(planner_worksheets)").fetchall()}
    if "generator_version" not in cols and "worksheet_id" in cols:
        conn.execute("ALTER TABLE planner_worksheets ADD COLUMN generator_version TEXT DEFAULT ''")


def _migrate_timetable_v5(conn: sqlite3.Connection) -> None:
    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
    if "practice_id" not in session_cols and "id" in session_cols:
        conn.execute("ALTER TABLE study_sessions ADD COLUMN practice_id TEXT DEFAULT ''")
    if "exercise_status" not in session_cols and "id" in session_cols:
        conn.execute("ALTER TABLE study_sessions ADD COLUMN exercise_status TEXT DEFAULT ''")
    lesson_cols = {row[1] for row in conn.execute("PRAGMA table_info(weekly_lessons)").fetchall()}
    if "practice_id" not in lesson_cols and "id" in lesson_cols:
        conn.execute("ALTER TABLE weekly_lessons ADD COLUMN practice_id TEXT DEFAULT ''")
    if "exercise_status" not in lesson_cols and "id" in lesson_cols:
        conn.execute("ALTER TABLE weekly_lessons ADD COLUMN exercise_status TEXT DEFAULT ''")


def _migrate_timetable_v6(conn: sqlite3.Connection) -> None:
    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
    if "weekly_lesson_id" not in session_cols and "id" in session_cols:
        conn.execute("ALTER TABLE study_sessions ADD COLUMN weekly_lesson_id INTEGER")


timetable_migrations = MigrationManager(
    store_name="timetable",
    migrations=[
        Migration(1, "001_baseline_timetable", _migrate_timetable_v1),
        Migration(2, "002_timetable_indexes", _migrate_timetable_v2),
        Migration(3, "003_weekly_lessons_and_worksheets", _migrate_timetable_v3),
        Migration(4, "004_worksheet_generator_version", _migrate_timetable_v4),
        Migration(5, "005_session_practice_linkage", _migrate_timetable_v5),
        Migration(6, "006_session_weekly_lesson_linkage", _migrate_timetable_v6),
    ],
)


# ============================================================================
# Uploads / Materials Store (uploads.db) Migrations
# ============================================================================

def _migrate_uploads_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            key TEXT PRIMARY KEY,
            content BLOB
        )
    """)


def _rebuild_files_nullable(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='files'").fetchone()
    if not cur:
        return
    table_info = conn.execute("PRAGMA table_info(files)").fetchall()
    content_not_null = False
    has_file_hash = False
    has_file_size = False
    has_created_at = False
    for col in table_info:
        col_name = col[1]
        col_notnull = col[3]
        if col_name == "content" and col_notnull:
            content_not_null = True
        if col_name == "file_hash":
            has_file_hash = True
        if col_name == "file_size":
            has_file_size = True
        if col_name == "created_at":
            has_created_at = True

    if content_not_null or not (has_file_hash and has_file_size and has_created_at):
        conn.execute("""
            CREATE TABLE files__new (
                key TEXT PRIMARY KEY,
                file_hash TEXT,
                file_size INTEGER,
                created_at REAL,
                content BLOB
            )
        """)
        cols_present = {c[1] for c in table_info}
        select_hash = "file_hash" if "file_hash" in cols_present else "NULL"
        select_size = "file_size" if "file_size" in cols_present else "NULL"
        select_created = "created_at" if "created_at" in cols_present else "NULL"
        select_content = "content" if "content" in cols_present else "NULL"
        conn.execute(f"""
            INSERT INTO files__new (key, file_hash, file_size, created_at, content)
            SELECT key, {select_hash}, {select_size}, {select_created}, {select_content}
            FROM files
        """)
        conn.execute("DROP TABLE files")
        conn.execute("ALTER TABLE files__new RENAME TO files")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_objects (
            hash TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            created_at REAL NOT NULL,
            storage_path TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files(file_hash)")


def _migrate_uploads_v2(conn: sqlite3.Connection) -> None:
    _rebuild_files_nullable(conn)


def _migrate_uploads_v4(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS private_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id TEXT NOT NULL,
            file_hash TEXT,
            filename TEXT NOT NULL,
            clean_title TEXT NOT NULL,
            page_num INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_private_chunks_mat ON private_chunks(material_id, page_num)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_private_chunks_hash ON private_chunks(file_hash)")
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS private_chunks_fts USING fts5(
            text,
            content='private_chunks',
            content_rowid='id'
        )
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_private_chunks_ai AFTER INSERT ON private_chunks BEGIN
            INSERT INTO private_chunks_fts(rowid, text) VALUES (new.id, new.text);
        END;
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_private_chunks_ad AFTER DELETE ON private_chunks BEGIN
            INSERT INTO private_chunks_fts(private_chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END;
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_private_chunks_au AFTER UPDATE ON private_chunks BEGIN
            INSERT INTO private_chunks_fts(private_chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
            INSERT INTO private_chunks_fts(rowid, text) VALUES (new.id, new.text);
        END;
    """)


uploads_migrations = MigrationManager(
    store_name="uploads",
    migrations=[
        Migration(1, "001_baseline_uploads", _migrate_uploads_v1),
        Migration(2, "002_content_addressed_files", _migrate_uploads_v2),
        Migration(3, "003_nullable_content_rebuild", _rebuild_files_nullable),
        Migration(4, "004_private_chunks_fts", _migrate_uploads_v4),
    ],
)


# ============================================================================
# Practice Store (practice.db) Migrations
# ============================================================================

def _migrate_practice_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS practice_sessions (
            practice_id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            count INTEGER NOT NULL,
            requested_count INTEGER NOT NULL,
            difficulty TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT 'practice',
            topic TEXT DEFAULT '',
            warning TEXT,
            processing_level TEXT NOT NULL DEFAULT 'careful',
            questions_json TEXT NOT NULL,
            answers_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_practice_sessions_created ON practice_sessions(created_at DESC)")


practice_migrations = MigrationManager(
    store_name="practice",
    migrations=[
        Migration(1, "001_baseline_practice", _migrate_practice_v1),
    ],
)
