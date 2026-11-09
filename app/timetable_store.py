"""Database storage and isolation for student timetable and vision drafts.

Follows strict test isolation via TIMETABLE_DB_PATH environment variable.
Never writes personal schedule or image data to src/, RAG index, or training datasets.
"""

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from app.config import BASE_DIR, DATA_DIR
from app.db import (
    atomic_transaction,
    open_sqlite,
    secure_dir_permissions,
    secure_file_permissions,
    timetable_migrations,
    validate_path_containment,
)

TIME_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def get_db_path() -> Path:
    """Return the database path, honoring the TIMETABLE_DB_PATH environment variable."""
    from app.accounts import private_dir
    scoped = private_dir()
    if scoped is not None:
        p = scoped / "timetable.db"
        return validate_path_containment(p, scoped)
    env_path = os.environ.get("TIMETABLE_DB_PATH")
    if env_path:
        p = Path(env_path)
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        secure_dir_permissions(p.parent)
        return p
    default_p = DATA_DIR / "timetable.db"
    default_p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_dir_permissions(default_p.parent)
    return default_p


def get_images_dir() -> Path:
    """Return directory for local timetable images outside src/ and RAG."""
    from app.accounts import private_dir
    scoped = private_dir()
    if scoped is not None:
        p = scoped / "timetable_images"
        validated = validate_path_containment(p, scoped)
        validated.mkdir(parents=True, exist_ok=True, mode=0o700)
        secure_dir_permissions(validated)
        return validated
    env_img = os.environ.get("TIMETABLE_IMAGES_DIR")
    if env_img:
        p = Path(env_img)
    else:
        p = DATA_DIR / "timetable_images"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_dir_permissions(p)
    return p


def save_timetable_image(image_bytes: bytes, image_hash: str) -> Path:
    """Save raw image bytes keyed strictly by its sha256 hex digest."""
    if not re.match(r"^[a-f0-9]{64}$", image_hash):
        raise ValueError("Invalid image hash")
    images_dir = get_images_dir()
    target_path = images_dir / f"{image_hash}.png"
    validate_path_containment(target_path, images_dir)
    if not target_path.exists():
        tmp_path = images_dir / f".tmp_{uuid.uuid4().hex}"
        try:
            with open(tmp_path, "wb") as f:
                f.write(image_bytes)
                f.flush()
                os.fsync(f.fileno())
            tmp_path.rename(target_path)
            secure_file_permissions(target_path)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise
    return target_path


def get_image_path(image_hash: str) -> Optional[Path]:
    """Retrieve saved image path by sha256 hex digest."""
    if not re.match(r"^[a-f0-9]{64}$", image_hash):
        return None
    target_path = get_images_dir() / f"{image_hash}.png"
    return target_path if target_path.exists() else None


def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize database tables for drafts, confirmed timetable, and VLM cache."""
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
            options_json TEXT
        )
    """)
    # Migration check for existing tables without the provenance columns
    cursor = conn.execute("PRAGMA table_info(timetable_drafts)")
    cols = {row[1] for row in cursor.fetchall()}
    if "model_digest" not in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN model_digest TEXT")
    if "prompt_version" not in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN prompt_version TEXT")
    if "options_json" not in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN options_json TEXT")
    if "confirmed_at" not in cols:
        conn.execute("ALTER TABLE timetable_drafts ADD COLUMN confirmed_at TEXT")
    if "confirmed_entries_json" not in cols:
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

    # Personal Study Schedule Assistant tables (stored in same isolated DB)
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


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection with guaranteed cleanup, versioned migrations, and transaction safety."""
    db_path = get_db_path()
    conn = open_sqlite(
        db_path,
        timeout=10.0,
        wal=True,
        synchronous_full=True,
        foreign_keys=True,
    )
    try:
        timetable_migrations.apply(conn)
        init_schema(conn)
        yield conn
        if conn.in_transaction:
            conn.commit()
    except Exception:
        if conn.in_transaction:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        conn.close()


def validate_entry_values(
    course: str, weekday: int, start_time: str, end_time: str
) -> Tuple[str, int, str, str]:
    c = str(course or "").strip()
    if not c:
        raise ValueError("Tên môn học không được để trống")
    try:
        w = int(weekday)
    except (TypeError, ValueError):
        raise ValueError("Thứ trong tuần không hợp lệ")
    if w < 1 or w > 7:
        raise ValueError("Thứ trong tuần phải từ 1 đến 7")

    st = str(start_time or "").strip()
    et = str(end_time or "").strip()
    if not TIME_REGEX.match(st):
        raise ValueError(f"Giờ bắt đầu không hợp lệ ({st}). Định dạng HH:MM (00:00 - 23:59)")
    if not TIME_REGEX.match(et):
        raise ValueError(f"Giờ kết thúc không hợp lệ ({et}). Định dạng HH:MM (00:00 - 23:59)")
    if et <= st:
        raise ValueError("Giờ kết thúc phải lớn hơn giờ bắt đầu")

    return c, w, st, et


def get_confirmed_entries() -> List[Dict[str, Any]]:
    """Retrieve all confirmed schedule entries sorted by weekday and start_time."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, draft_id, course, weekday, start_time, end_time, room, created_at
            FROM timetable_confirmed
            ORDER BY weekday ASC, start_time ASC, course ASC
        """).fetchall()
        return [dict(r) for r in rows]


def save_draft(
    draft_id: str,
    image_hash: str,
    filename: str,
    entries: List[Dict[str, Any]],
    uncertainties: List[str],
    raw_model_output: Optional[str] = None,
    status: str = "draft",
    model_digest: Optional[str] = None,
    prompt_version: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> None:
    """Save an extracted timetable draft for user review before confirmation."""
    now = datetime.now(timezone.utc).isoformat()
    options_json = json.dumps(options or {}, ensure_ascii=False) if options is not None else None
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO timetable_drafts
            (draft_id, image_hash, filename, created_at, status, raw_model_output, entries_json, uncertainties_json, model_digest, prompt_version, options_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                image_hash,
                filename,
                now,
                status,
                raw_model_output or "",
                json.dumps(entries, ensure_ascii=False),
                json.dumps(uncertainties, ensure_ascii=False),
                model_digest,
                prompt_version,
                options_json,
            ),
        )


def get_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    """Get a draft by its ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM timetable_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["entries"] = json.loads(data["entries_json"])
        data["uncertainties"] = json.loads(data["uncertainties_json"])
        opts_raw = data.get("options_json")
        data["options"] = json.loads(opts_raw) if opts_raw else {}
        data["model_digest"] = data.get("model_digest")
        data["prompt_version"] = data.get("prompt_version")
        data["modeldigest"] = data.get("model_digest")
        data["promptversion"] = data.get("prompt_version")
        data["imagehash"] = data.get("image_hash")
        data["confirmed_entries"] = json.loads(data.get("confirmed_entries_json") or "[]")
        events = conn.execute(
            "SELECT confirmed_at, entries_json, replaced_schedule_json FROM timetable_confirmation_events WHERE draft_id = ? ORDER BY id",
            (draft_id,),
        ).fetchall()
        data["confirmation_history"] = [{
            "confirmed_at": event["confirmed_at"],
            "entries": json.loads(event["entries_json"]),
            "replaced_schedule": json.loads(event["replaced_schedule_json"] or "[]"),
        } for event in events]
        return data


def confirm_draft(
    draft_id: str,
    entries: List[Dict[str, Any]],
    clear_existing: bool = False,
) -> int:
    """Confirm a draft and persist validated entries into timetable_confirmed.

    Validates schema and rejects empty lists or conflicting entries.
    """
    if not entries:
        raise ValueError("Danh sách môn học xác nhận không được để trống")

    with get_connection() as conn:
        # Serialize conflict checks with writes, including concurrent confirmations.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT draft_id FROM timetable_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Draft not found: {draft_id}")

        existing = [dict(r) for r in conn.execute(
            "SELECT id, course, weekday, start_time, end_time, room FROM timetable_confirmed"
        ).fetchall()]

        now = datetime.now(timezone.utc).isoformat()
        to_insert = []
        for e in entries:
            c, w, st, et = validate_entry_values(
                course=e.get("course"),
                weekday=e.get("weekday"),
                start_time=e.get("start_time"),
                end_time=e.get("end_time"),
            )
            room = str(e.get("room", "")).strip() if e.get("room") else None
            to_insert.append((draft_id, c, w, st, et, room, now))

        # Check intra-batch conflicts
        for i in range(len(to_insert)):
            for j in range(i + 1, len(to_insert)):
                _, c1, w1, s1, e1, _, _ = to_insert[i]
                _, c2, w2, s2, e2, _, _ = to_insert[j]
                if w1 == w2 and s1 < e2 and s2 < e1:
                    raise ValueError(f"Xung đột lịch trong bản xác nhận: '{c1}' và '{c2}' trùng giờ thứ {w1}")

        # Check against existing entries if not replacing all
        if not clear_existing:
            for _, c1, w1, s1, e1, _, _ in to_insert:
                for ex in existing:
                    if ex["weekday"] == w1 and s1 < ex["end_time"] and ex["start_time"] < e1:
                        raise ValueError(
                            f"Trùng lịch học với môn {ex['course']} ({ex['start_time']} - {ex['end_time']}) vào thứ {w1}"
                        )

        if clear_existing:
            conn.execute("DELETE FROM timetable_confirmed")

        for item in to_insert:
            conn.execute(
                """
                INSERT INTO timetable_confirmed
                (draft_id, course, weekday, start_time, end_time, room, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                item,
            )

        reviewed_json = json.dumps([
            {"course": c, "weekday": w, "start_time": st, "end_time": et, "room": room,
             "period": entries[i].get("period")}
            for i, (_, c, w, st, et, room, _) in enumerate(to_insert)
        ], ensure_ascii=False)
        conn.execute(
            "UPDATE timetable_drafts SET status = 'confirmed', confirmed_at = ?, confirmed_entries_json = ? WHERE draft_id = ?",
            (now, reviewed_json, draft_id),
        )
        conn.execute(
            "INSERT INTO timetable_confirmation_events (draft_id, confirmed_at, entries_json, replaced_schedule_json) VALUES (?, ?, ?, ?)",
            (draft_id, now, reviewed_json, json.dumps(existing, ensure_ascii=False) if clear_existing else None),
        )
        return len(to_insert)


def add_manual_entry(
    course: str,
    weekday: int,
    start_time: str,
    end_time: str,
    room: Optional[str] = None,
) -> int:
    """Insert a manually added timetable entry after validation and conflict checking."""
    c, w, st, et = validate_entry_values(course, weekday, start_time, end_time)

    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT course, start_time, end_time FROM timetable_confirmed WHERE weekday = ?",
            (w,)
        ).fetchall()
        for ex in existing:
            if st < ex["end_time"] and ex["start_time"] < et:
                raise ValueError(
                    f"Trùng lịch học với môn {ex['course']} ({ex['start_time']} - {ex['end_time']}) vào thứ {w}"
                )

        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO timetable_confirmed
            (draft_id, course, weekday, start_time, end_time, room, created_at)
            VALUES (NULL, ?, ?, ?, ?, ?, ?)
            """,
            (c, w, st, et, room.strip() if room else None, now),
        )
        return cursor.lastrowid or 0


def update_entry(
    entry_id: int,
    course: str,
    weekday: int,
    start_time: str,
    end_time: str,
    room: Optional[str] = None,
) -> bool:
    """Update an existing confirmed timetable entry after validation and conflict checking."""
    c, w, st, et = validate_entry_values(course, weekday, start_time, end_time)
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT id FROM timetable_confirmed WHERE id = ?", (entry_id,)
        ).fetchone()
        if not exists:
            return False

        existing = conn.execute(
            "SELECT id, course, start_time, end_time FROM timetable_confirmed WHERE weekday = ? AND id != ?",
            (w, entry_id),
        ).fetchall()
        for ex in existing:
            if st < ex["end_time"] and ex["start_time"] < et:
                raise ValueError(
                    f"Trùng lịch học với môn {ex['course']} ({ex['start_time']} - {ex['end_time']}) vào thứ {w}"
                )

        conn.execute(
            """
            UPDATE timetable_confirmed
            SET course = ?, weekday = ?, start_time = ?, end_time = ?, room = ?
            WHERE id = ?
            """,
            (c, w, st, et, room.strip() if room else None, entry_id),
        )
        return True



def delete_entry(entry_id: int) -> bool:
    """Delete a single confirmed entry."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM timetable_confirmed WHERE id = ?", (entry_id,)
        )
        return cursor.rowcount > 0


def clear_all_entries() -> None:
    """Delete all confirmed entries."""
    with get_connection() as conn:
        conn.execute("DELETE FROM timetable_confirmed")


def get_vlm_cache(
    image_hash: str, model_digest: str, prompt_version: str
) -> Optional[Dict[str, Any]]:
    """Retrieve cached VLM response for an exact image hash + model digest + prompt."""
    cache_key = f"{image_hash}:{model_digest}:{prompt_version}"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT response_json FROM timetable_vlm_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row:
            try:
                return json.loads(row["response_json"])
            except Exception:
                return None
    return None


def get_latest_image_cache(image_hash: str, prompt_version: str) -> Optional[Dict[str, Any]]:
    """Candidate cache; caller must verify engine/model fingerprints before reuse."""
    with get_connection() as conn:
        row=conn.execute('SELECT response_json FROM timetable_vlm_cache WHERE image_hash=? AND prompt_version=? ORDER BY created_at DESC LIMIT 1',
            (image_hash,prompt_version)).fetchone()
        if row:
            try: return json.loads(row['response_json'])
            except (ValueError,TypeError): return None
    return None


def set_vlm_cache(
    image_hash: str,
    model_digest: str,
    prompt_version: str,
    response_data: Dict[str, Any],
) -> None:
    """Store VLM response in timetable_vlm_cache."""
    cache_key = f"{image_hash}:{model_digest}:{prompt_version}"
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO timetable_vlm_cache
            (cache_key, image_hash, model_digest, prompt_version, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                image_hash,
                model_digest,
                prompt_version,
                json.dumps(response_data, ensure_ascii=False),
                now,
            ),
        )
