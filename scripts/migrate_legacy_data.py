#!/usr/bin/env python3
"""Opt-in legacy data migration into an explicit target account with non-destructive source verification."""
import argparse
import hashlib
import json
import logging
from pathlib import Path
from contextlib import closing
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import (
    atomic_transaction,
    history_migrations,
    open_sqlite,
    secure_dir_permissions,
    secure_file_permissions,
    timetable_migrations,
    validate_path_containment,
)

logger = logging.getLogger("psychology_agent.migrate_legacy")

ACCOUNT_ID_REGEX = re.compile(r"^[a-f0-9]{32}$")
PROTECTED_NAMES = {"knowledge_base.db", "vectors.db", "vectors.npy", "chunks_fts", "weights"}


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def validate_target_account(accounts_dir: Path, target_account_id: str) -> str:
    """Validate target account exists and is not guest/anonymous."""
    if not target_account_id or target_account_id.lower() in {"guest", "anonymous", "none", "null"}:
        raise ValueError("Opt-in target account is required. Implicit guest or anonymous migration is strictly forbidden.")
    if not ACCOUNT_ID_REGEX.match(target_account_id):
        raise ValueError(f"Invalid target account ID: '{target_account_id}'. Must be a 32-character hex identity.")

    auth_db = accounts_dir / "accounts.db"
    if not auth_db.exists():
        raise FileNotFoundError(f"Accounts database not found at '{auth_db}'. Cannot verify target account.")

    with closing(sqlite3.connect(auth_db.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT id, username FROM accounts WHERE id = ?", (target_account_id,)).fetchone()
        if not row:
            raise ValueError(f"Target account '{target_account_id}' does not exist in accounts.db. Please register first.")
    return target_account_id


def _rows_equal(row1: sqlite3.Row, row2: sqlite3.Row, defaults: Optional[Dict[str, Any]] = None) -> bool:
    """Compare two SQLite rows column-by-column.

    If a column exists in row1 (source), compare values directly (do not ignore!).
    If an optional column is absent in row1 but present in row2, verify row2 has the explicit default.
    """
    defaults = defaults or {}
    keys1 = set(row1.keys())
    keys2 = set(row2.keys())

    for k in keys1.intersection(keys2):
        if row1[k] != row2[k]:
            return False

    for k in keys2 - keys1:
        if k in defaults:
            if row2[k] != defaults[k]:
                return False

    return True


def migrate_legacy_history(source_db_path: Path, target_db_path: Path) -> Dict[str, int]:
    """Migrate legacy searches and conversation sessions into target history.db."""
    counts = {"searches": 0, "sessions": 0, "turns": 0}
    if not source_db_path.exists():
        return counts

    # Ensure target DB has tables, migrations and all columns
    dst_init = open_sqlite(target_db_path, timeout=10.0, wal=True, synchronous_full=True, foreign_keys=True)
    try:
        history_migrations.apply(dst_init)
        cols = [c[1] for c in dst_init.execute("PRAGMA table_info(session_turns)").fetchall()]
        if "legacy_search_id" not in cols and "id" in cols:
            dst_init.execute("ALTER TABLE session_turns ADD COLUMN legacy_search_id INTEGER")
        if "answer_mode" not in cols and "id" in cols:
            dst_init.execute("ALTER TABLE session_turns ADD COLUMN answer_mode TEXT NOT NULL DEFAULT 'quick'")
        if "search_scope_json" not in cols and "id" in cols:
            dst_init.execute("ALTER TABLE session_turns ADD COLUMN search_scope_json TEXT NOT NULL DEFAULT '{}'")

        sess_cols = [c[1] for c in dst_init.execute("PRAGMA table_info(sessions)").fetchall()]
        if "summarized_revision" not in sess_cols and "id" in sess_cols:
            dst_init.execute("ALTER TABLE sessions ADD COLUMN summarized_revision INTEGER NOT NULL DEFAULT 0")
    finally:
        dst_init.close()

    # Read from source in read-only URI mode and write to target within the SAME transaction
    src = sqlite3.connect(f"file:{source_db_path.resolve()}?mode=ro", uri=True, timeout=10.0)
    dst = open_sqlite(target_db_path, timeout=10.0, wal=True, synchronous_full=True, foreign_keys=True)
    try:
        src.row_factory = sqlite3.Row
        dst.row_factory = sqlite3.Row
        src.execute("BEGIN")

        with atomic_transaction(dst):
            # 1. Searches
            cursor = src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='searches'")
            if cursor.fetchone():
                src_searches = src.execute("SELECT id, created_at, question, answer, citations_json, model FROM searches").fetchall()
                existing_count = 0
                for r in src_searches:
                    existing = dst.execute("SELECT * FROM searches WHERE id = ?", (r["id"],)).fetchone()
                    if existing:
                        if not _rows_equal(r, existing):
                            raise ValueError(
                                f"ID collision detected in 'searches' table for id={r['id']}: target contains conflicting data. Migration aborted to prevent data loss."
                            )
                        existing_count += 1
                    else:
                        cur = dst.execute(
                            "INSERT INTO searches (id, created_at, question, answer, citations_json, model) VALUES (?, ?, ?, ?, ?, ?)",
                            (r["id"], r["created_at"], r["question"], r["answer"], r["citations_json"], r["model"]),
                        )
                        if cur.rowcount > 0:
                            counts["searches"] += 1
                if counts["searches"] + existing_count != len(src_searches):
                    raise RuntimeError(f"Unexpected row drop in searches: inserted {counts['searches']}, existing {existing_count}, total {len(src_searches)}")

            # 2. Sessions
            cursor = src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
            if cursor.fetchone():
                src_cols = {c[1] for c in src.execute("PRAGMA table_info(sessions)").fetchall()}
                src_sessions = src.execute("SELECT * FROM sessions").fetchall()
                existing_count = 0
                for r in src_sessions:
                    existing = dst.execute("SELECT * FROM sessions WHERE id = ?", (r["id"],)).fetchone()
                    if existing:
                        if not _rows_equal(r, existing, defaults={
                            "summary": None,
                            "summary_status": "none",
                            "summarized_revision": 0,
                            "revision": 1,
                            "is_legacy": 1,
                        }):
                            raise ValueError(
                                f"ID collision detected in 'sessions' table for id='{r['id']}': target contains conflicting data. Migration aborted to prevent data loss."
                            )
                        existing_count += 1
                    else:
                        summary = r["summary"] if "summary" in src_cols else None
                        summary_status = r["summary_status"] if "summary_status" in src_cols else "none"
                        summarized_revision = r["summarized_revision"] if "summarized_revision" in src_cols else 0
                        revision = r["revision"] if "revision" in src_cols else 1
                        is_legacy = r["is_legacy"] if "is_legacy" in src_cols else 1
                        cur = dst.execute(
                            """INSERT INTO sessions (
                                id, title, title_mode, summary, summary_status,
                                summarized_revision, created_at, updated_at, revision, is_legacy
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                r["id"], r["title"], r["title_mode"], summary, summary_status,
                                summarized_revision, r["created_at"], r["updated_at"], revision, is_legacy,
                            ),
                        )
                        if cur.rowcount > 0:
                            counts["sessions"] += 1
                if counts["sessions"] + existing_count != len(src_sessions):
                    raise RuntimeError(f"Unexpected row drop in sessions: inserted {counts['sessions']}, existing {existing_count}, total {len(src_sessions)}")

            # 3. Session turns (preflight all unique identities: id, (session_id, turn_index), (session_id, request_id))
            cursor = src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='session_turns'")
            if cursor.fetchone():
                cols = {c[1] for c in src.execute("PRAGMA table_info(session_turns)").fetchall()}
                has_legacy = "legacy_search_id" in cols
                has_answer_mode = "answer_mode" in cols
                has_search_scope = "search_scope_json" in cols

                src_turns = src.execute("SELECT * FROM session_turns").fetchall()
                existing_count = 0
                turn_defaults = {"answer_mode": "quick", "search_scope_json": "{}", "legacy_search_id": None}

                for r in src_turns:
                    # Check 1: Primary ID collision
                    existing_by_id = dst.execute("SELECT * FROM session_turns WHERE id = ?", (r["id"],)).fetchone()
                    if existing_by_id:
                        if not _rows_equal(r, existing_by_id, defaults=turn_defaults):
                            raise ValueError(
                                f"ID collision detected in 'session_turns' table for id={r['id']}: target contains conflicting data. Migration aborted to prevent data loss."
                            )

                    # Check 2: UNIQUE(session_id, turn_index)
                    existing_by_turn = dst.execute(
                        "SELECT * FROM session_turns WHERE session_id = ? AND turn_index = ?",
                        (r["session_id"], r["turn_index"]),
                    ).fetchone()
                    if existing_by_turn:
                        if existing_by_turn["id"] != r["id"] or not _rows_equal(r, existing_by_turn, defaults=turn_defaults):
                            raise ValueError(
                                f"Unique constraint collision detected in 'session_turns' for (session_id='{r['session_id']}', turn_index={r['turn_index']}): target contains conflicting data."
                            )

                    # Check 3: UNIQUE(session_id, request_id)
                    if r["request_id"]:
                        existing_by_req = dst.execute(
                            "SELECT * FROM session_turns WHERE session_id = ? AND request_id = ?",
                            (r["session_id"], r["request_id"]),
                        ).fetchone()
                        if existing_by_req:
                            if existing_by_req["id"] != r["id"] or not _rows_equal(r, existing_by_req, defaults=turn_defaults):
                                raise ValueError(
                                    f"Unique constraint collision detected in 'session_turns' for (session_id='{r['session_id']}', request_id='{r['request_id']}'): target contains conflicting data."
                                )

                    if existing_by_id or existing_by_turn:
                        existing_count += 1
                    else:
                        legacy_sid = r["legacy_search_id"] if has_legacy else None
                        amode = r["answer_mode"] if has_answer_mode else "quick"
                        sscope = r["search_scope_json"] if has_search_scope else "{}"
                        cur = dst.execute(
                            """INSERT INTO session_turns (
                                id, session_id, turn_index, question, answer, citations_json,
                                model, created_at, request_id, answer_mode, search_scope_json, legacy_search_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                r["id"], r["session_id"], r["turn_index"], r["question"], r["answer"],
                                r["citations_json"], r["model"], r["created_at"], r["request_id"],
                                amode, sscope, legacy_sid,
                            ),
                        )
                        if cur.rowcount > 0:
                            counts["turns"] += 1
                if counts["turns"] + existing_count != len(src_turns):
                    raise RuntimeError(f"Unexpected row drop in session_turns: inserted {counts['turns']}, existing {existing_count}, total {len(src_turns)}")

            # Foreign key verification on target
            fk_violations = dst.execute("PRAGMA foreign_key_check").fetchall()
            if fk_violations:
                raise RuntimeError(f"Foreign key violations in history.db after migration: {fk_violations}")
    finally:
        dst.close()
        src.close()

    return counts


def migrate_legacy_timetable(source_db_path: Path, target_db_path: Path) -> Dict[str, int]:
    """Migrate legacy timetable drafts, routines, tasks, plans and preferences into target timetable.db."""
    counts = {
        "drafts": 0,
        "confirmation_events": 0,
        "confirmed": 0,
        "vlm_cache": 0,
        "routines": 0,
        "preferences": 0,
        "tasks": 0,
        "plans": 0,
        "sessions": 0,
    }
    if not source_db_path.exists():
        return counts

    dst_init = open_sqlite(target_db_path, timeout=10.0, wal=True, synchronous_full=True, foreign_keys=True)
    try:
        timetable_migrations.apply(dst_init)
    finally:
        dst_init.close()

    src = sqlite3.connect(f"file:{source_db_path.resolve()}?mode=ro", uri=True, timeout=10.0)
    dst = open_sqlite(target_db_path, timeout=10.0, wal=True, synchronous_full=True, foreign_keys=True)
    try:
        src.row_factory = sqlite3.Row
        dst.row_factory = sqlite3.Row
        src.execute("BEGIN")

        check_specs = [
            ("timetable_drafts", "draft_id", "drafts"),
            ("timetable_confirmation_events", "id", "confirmation_events"),
            ("timetable_confirmed", "id", "confirmed"),
            ("timetable_vlm_cache", "cache_key", "vlm_cache"),
            ("personal_routines", "id", "routines"),
            ("study_preferences", "id", "preferences"),
            ("study_tasks", "id", "tasks"),
            ("study_plan_drafts", "plan_id", "plans"),
            ("study_sessions", "id", "sessions"),
        ]

        with atomic_transaction(dst):
            for tbl, pk_col, count_key in check_specs:
                cur = src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
                if cur.fetchone():
                    src_rows = src.execute(f"SELECT * FROM {tbl}").fetchall()
                    existing_count = 0
                    for r in src_rows:
                        existing = dst.execute(f"SELECT * FROM {tbl} WHERE {pk_col} = ?", (r[pk_col],)).fetchone()
                        if existing:
                            if not _rows_equal(r, existing):
                                raise ValueError(
                                    f"ID collision detected in '{tbl}' for {pk_col}='{r[pk_col]}': target contains conflicting data. Migration aborted to prevent data loss."
                                )
                            existing_count += 1
                        else:
                            # Construct dynamic INSERT for all present columns
                            cols = [c[1] for c in src.execute(f"PRAGMA table_info({tbl})").fetchall()]
                            placeholders = ", ".join(["?"] * len(cols))
                            col_names = ", ".join(cols)
                            values = [r[c] for c in cols]
                            ins_cur = dst.execute(
                                f"INSERT INTO {tbl} ({col_names}) VALUES ({placeholders})",
                                values,
                            )
                            if ins_cur.rowcount > 0:
                                counts[count_key] += 1
                    if counts[count_key] + existing_count != len(src_rows):
                        raise RuntimeError(f"Unexpected row drop in {tbl}: inserted {counts[count_key]}, existing {existing_count}, total {len(src_rows)}")

            # Foreign key verification on target
            fk_violations = dst.execute("PRAGMA foreign_key_check").fetchall()
            if fk_violations:
                raise RuntimeError(f"Foreign key violations in timetable.db after migration: {fk_violations}")
    finally:
        dst.close()
        src.close()

    return counts


def migrate_legacy(
    source_dir: Union[str, Path],
    accounts_dir: Union[str, Path],
    target_account_id: str,
) -> Dict[str, Any]:
    """Execute opt-in legacy migration non-destructively into verified target account."""
    src_p = Path(source_dir)
    if src_p.is_symlink():
        raise ValueError(f"Source directory cannot be a symlink: '{src_p}'")
    src_res = src_p.resolve()

    acc_p = Path(accounts_dir)
    if acc_p.is_symlink():
        raise ValueError(f"Accounts directory cannot be a symlink: '{acc_p}'")
    acc_res = acc_p.resolve()

    # Security check: Never allow migration from or to protected files
    for protected in PROTECTED_NAMES:
        if protected == src_res.name or (src_res.is_file() and src_res.name == protected) or protected in src_res.parts:
            raise ValueError(f"Protected source path '{protected}' cannot be migrated or altered.")

    # 1. Validate target account
    target_account_id = validate_target_account(acc_res, target_account_id)
    target_account_dir = acc_res / target_account_id

    # Security check: Reject symlink target directory
    if target_account_dir.is_symlink():
        raise ValueError(f"Target account directory cannot be a symlink: '{target_account_dir}'")
    if target_account_dir.exists():
        for item in target_account_dir.iterdir():
            if item.is_symlink():
                raise ValueError(f"Target directory contains symlink '{item.name}', which is strictly forbidden.")

    # Security check: Reject same source and target directory or file
    if src_res == target_account_dir.resolve() or src_p == target_account_dir:
        raise ValueError(f"Source directory and target account directory cannot be identical: '{src_p}'")
    if (src_res / "history.db").resolve() == (target_account_dir / "history.db").resolve():
        raise ValueError("Source history.db and target history.db cannot be the same file.")
    if (src_res / "timetable.db").resolve() == (target_account_dir / "timetable.db").resolve():
        raise ValueError("Source timetable.db and target timetable.db cannot be the same file.")

    target_account_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_dir_permissions(target_account_dir)

    # 2. Record source hashes before migration to prove zero modification
    source_hashes_before: Dict[str, str] = {}
    src_history = src_res / "history.db"
    src_timetable = src_res / "timetable.db"
    if src_history.exists():
        if src_history.is_symlink():
            raise ValueError(f"Source history.db cannot be a symlink: '{src_history}'")
        source_hashes_before["history.db"] = compute_sha256(src_history)
    if src_timetable.exists():
        if src_timetable.is_symlink():
            raise ValueError(f"Source timetable.db cannot be a symlink: '{src_timetable}'")
        source_hashes_before["timetable.db"] = compute_sha256(src_timetable)

    # 3. Migrate data
    h_counts = migrate_legacy_history(src_history, target_account_dir / "history.db")
    t_counts = migrate_legacy_timetable(src_timetable, target_account_dir / "timetable.db")

    # 4. Verify source hashes after migration (assert zero byte modification)
    for name, expected_sha in source_hashes_before.items():
        actual_sha = compute_sha256(src_res / name)
        if actual_sha != expected_sha:
            raise RuntimeError(f"Non-destructive invariant violated! Source '{name}' was altered during migration.")

    return {
        "status": "success",
        "target_account": target_account_id,
        "migrated_history": h_counts,
        "migrated_timetable": t_counts,
        "source_preservation_verified": True,
        "cross_db_atomicity": "history.db and timetable.db are migrated as independent atomic SQLite transactions. True cross-database atomicity across multiple SQLite files is not supported by single-file WAL transactions.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy history and timetable data to an explicit account")
    parser.add_argument("--source-dir", required=True, help="Directory containing legacy history.db/timetable.db")
    parser.add_argument("--accounts-dir", required=True, help="Accounts root directory")
    parser.add_argument("--target-account", required=True, help="Target 32-hex account ID")

    args = parser.parse_args()
    try:
        res = migrate_legacy(args.source_dir, args.accounts_dir, args.target_account)
        print(f"Migration completed successfully: {json.dumps(res, indent=2)}")
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
