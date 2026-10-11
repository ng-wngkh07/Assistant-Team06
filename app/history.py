"""Private, local storage for multi-turn conversation sessions and search history.

Supports:
- Multi-turn conversation sessions with ordered completed turns.
- Atomic turn insertion with idempotency (request_id).
- Safe title generation (fast fallback + auto/manual mode).
- Topic summary storage and revision tracking.
- Parameterized session search and pagination.
- Legacy searches table preservation and non-destructive migration.
"""

import json
import logging
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import DATA_DIR
from app.answer_format import clean_answer

logger = logging.getLogger("psychology_agent.history")


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def generate_fallback_title(question: str, max_chars: int = 45) -> str:
    """Generate a clean, concise fallback title from the first question with zero GPU cost."""
    q = question.strip()
    # Remove leading question fillers
    q = re.sub(
        r"^(cho\s+tôi\s+hỏi\s+(về\s+)?|bạn\s+có\s+thể\s+cho\s+biết\s+(về\s+)?|làm\s+sao\s+để\s+|xin\s+hỏi\s+(về\s+)?|tìm\s+hiểu\s+(về\s+)?|nghiên\s+cứu\s+(về\s+)?|giải\s+thích\s+(giúp\s+tôi\s+)?(về\s+)?|hãy\s+giải\s+thích\s+(về\s+)?|về\s+)",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    if not q:
        return "Cuộc trò chuyện mới"

    # Truncate at word boundary up to max_chars
    if len(q) > max_chars:
        truncated = q[:max_chars]
        last_space = truncated.rfind(" ")
        if last_space > 15:
            q = truncated[:last_space].rstrip(",;:- ") + "..."
        else:
            q = truncated.rstrip(",;:- ") + "..."

    # Capitalize first character
    return q[0].upper() + q[1:]



class HistoryStore:
    def __init__(self, path: Path = DATA_DIR / "history.db", auto_init: bool = True):
        self.path = Path(path)
        if auto_init:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def _init_db(self) -> None:
        """Initialize database schema idempotently."""
        with self._connect() as db:
            # 1. Legacy searches table (preserved exactly)
            db.execute("""CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                citations_json TEXT NOT NULL,
                model TEXT NOT NULL
            )""")

            # 2. Multi-turn conversation sessions table
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
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
            )""")

            # 3. Ordered session turns table
            db.execute("""CREATE TABLE IF NOT EXISTS session_turns (
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
            )""")

            # 4. Metadata table for idempotent migrations and settings
            db.execute("""CREATE TABLE IF NOT EXISTS history_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")

            # Ensure columns exist in case table was created with an older schema
            turn_cols = [c[1] for c in db.execute("PRAGMA table_info(session_turns)").fetchall()]
            if "legacy_search_id" not in turn_cols and "id" in turn_cols:
                db.execute("ALTER TABLE session_turns ADD COLUMN legacy_search_id INTEGER")
            if "answer_mode" not in turn_cols and "id" in turn_cols:
                db.execute("ALTER TABLE session_turns ADD COLUMN answer_mode TEXT NOT NULL DEFAULT 'quick'")
            if "search_scope_json" not in turn_cols and "id" in turn_cols:
                db.execute("ALTER TABLE session_turns ADD COLUMN search_scope_json TEXT NOT NULL DEFAULT '{}'")

            sess_cols = [c[1] for c in db.execute("PRAGMA table_info(sessions)").fetchall()]
            if "summarized_revision" not in sess_cols and "id" in sess_cols:
                db.execute("ALTER TABLE sessions ADD COLUMN summarized_revision INTEGER NOT NULL DEFAULT 0")

            # Indices
            db.execute("CREATE INDEX IF NOT EXISTS idx_session_turns_sid ON session_turns(session_id, turn_index)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_is_legacy ON sessions(is_legacy)")


    # =========================================================================
    # Legacy Compatibility Methods (preserved for existing callers and tests)
    # =========================================================================

    def save(
        self,
        question: str,
        answer: str,
        citations: list,
        model: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> int:
        """Save a completed turn. If session_id is provided, links to session."""
        cleaned = clean_answer(answer)
        cit_json = json.dumps(citations, ensure_ascii=False)
        q_clean = question.strip()

        with self._connect() as db:
            # Check idempotency before inserting into ANY table
            if session_id and request_id:
                existing = db.execute(
                    "SELECT id, legacy_search_id FROM session_turns WHERE session_id=? AND request_id=?",
                    (session_id, request_id),
                ).fetchone()
                if existing:
                    return existing["legacy_search_id"] if existing["legacy_search_id"] is not None else existing["id"]

            # Insert into legacy searches
            cursor = db.execute(
                "INSERT INTO searches(question, answer, citations_json, model) VALUES (?, ?, ?, ?)",
                (q_clean, cleaned, cit_json, model),
            )
            legacy_id = cursor.lastrowid

            # If session_id provided, also save turn into session
            if session_id:
                now = _now_iso()
                sess = db.execute("SELECT id, title_mode, revision FROM sessions WHERE id=?", (session_id,)).fetchone()
                if sess:
                    max_idx = db.execute(
                        "SELECT COALESCE(MAX(turn_index), 0) FROM session_turns WHERE session_id=?", (session_id,)
                    ).fetchone()[0]
                    turn_idx = max_idx + 1

                    db.execute(
                        """INSERT INTO session_turns(session_id, turn_index, question, answer, citations_json, model, created_at, request_id, legacy_search_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (session_id, turn_idx, q_clean, cleaned, cit_json, model, now, request_id, legacy_id),
                    )

                    new_title = None
                    if sess["title_mode"] == "auto" and turn_idx == 1:
                        new_title = generate_fallback_title(q_clean)
                        db.execute(
                            "UPDATE sessions SET title=?, updated_at=?, revision=revision+1 WHERE id=?",
                            (new_title, now, session_id),
                        )
                    else:
                        db.execute(
                            "UPDATE sessions SET updated_at=?, revision=revision+1 WHERE id=?",
                            (now, session_id),
                        )

            return legacy_id

    def list(self, limit: int = 100) -> list[dict]:
        """List legacy searches (ORDER BY id DESC)."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, created_at, question, model FROM searches ORDER BY id DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, item_id: int) -> dict | None:
        """Get a legacy search item by ID."""
        with self._connect() as db:
            row = db.execute("SELECT * FROM searches WHERE id=?", (item_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["answer"] = clean_answer(result["answer"])
        result["citations"] = json.loads(result.pop("citations_json"))
        return result

    def delete(self, item_id: int) -> bool:
        """Delete a legacy search item and any mirrored session/turn."""
        with self._connect() as db:
            db.execute("DELETE FROM session_turns WHERE legacy_search_id=?", (item_id,))
            db.execute("DELETE FROM sessions WHERE id=?", (f"legacy_{item_id}",))
            return db.execute("DELETE FROM searches WHERE id=?", (item_id,)).rowcount > 0

    def clear(self) -> None:
        """Clear all searches, sessions, and turns."""
        with self._connect() as db:
            db.execute("DELETE FROM session_turns")
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM searches")

    # =========================================================================
    # Multi-Turn Session Methods
    # =========================================================================

    def create_session(
        self,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
        is_legacy: bool = False,
    ) -> dict:
        """Create a new conversation session."""
        sid = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        stitle = title.strip() if title and title.strip() else "Cuộc trò chuyện mới"
        mode = "manual" if title and title.strip() else "auto"
        now = _now_iso()

        with self._connect() as db:
            db.execute(
                """INSERT INTO sessions(id, title, title_mode, summary_status, created_at, updated_at, revision, is_legacy)
                   VALUES (?, ?, ?, 'none', ?, ?, 1, ?)""",
                (sid, stitle, mode, now, now, 1 if is_legacy else 0),
            )

        return {
            "id": sid,
            "title": stitle,
            "title_mode": mode,
            "summary": None,
            "summary_status": "none",
            "created_at": now,
            "updated_at": now,
            "revision": 1,
            "is_legacy": is_legacy,
            "turns": [],
        }

    def get_session(self, session_id: str) -> Optional[dict]:
        """Get session detail with all ordered turns."""
        with self._connect() as db:
            s_row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not s_row:
                return None

            t_rows = db.execute(
                "SELECT * FROM session_turns WHERE session_id=? ORDER BY turn_index ASC",
                (session_id,),
            ).fetchall()

        turns = []
        for t in t_rows:
            turns.append({
                "id": t["id"],
                "turn_index": t["turn_index"],
                "question": t["question"],
                "answer": clean_answer(t["answer"]),
                "citations": json.loads(t["citations_json"]),
                "answer_mode": t["answer_mode"],
                "search_scope": json.loads(t["search_scope_json"] or "{}"),
                "model": t["model"],
                "created_at": t["created_at"],
                "request_id": t["request_id"],
            })

        sess = dict(s_row)
        sess["is_legacy"] = bool(sess["is_legacy"])
        sess["turns"] = turns
        return sess

    def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        include_legacy: bool = True,
        filter_empty: bool = True,
    ) -> dict:
        """List sessions ordered by updated_at DESC with pagination."""
        lim = min(max(limit, 1), 200)
        off = max(offset, 0)

        where_clauses = []
        where_params: List[Any] = []
        having_clauses = []
        having_params: List[Any] = []

        if not include_legacy:
            where_clauses.append("s.is_legacy = 0")

        if filter_empty:
            # Exclude empty sessions completely from sidebar/listing
            having_clauses.append("COUNT(t.id) > 0")

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        having_sql = ("HAVING " + " AND ".join(having_clauses)) if having_clauses else ""

        with self._connect() as db:
            query = f"""
                SELECT s.*, COUNT(t.id) as turn_count,
                       (SELECT question FROM session_turns WHERE session_id = s.id ORDER BY turn_index ASC LIMIT 1) as first_question
                FROM sessions s
                LEFT JOIN session_turns t ON s.id = t.session_id
                {where_sql}
                GROUP BY s.id
                {having_sql}
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
            """
            exec_params = where_params + having_params + [lim, off]
            rows = db.execute(query, exec_params).fetchall()

            # Count total
            count_query = f"""
                SELECT COUNT(*) FROM (
                    SELECT s.id FROM sessions s
                    LEFT JOIN session_turns t ON s.id = t.session_id
                    {where_sql}
                    GROUP BY s.id
                    {having_sql}
                )
            """
            count_params = where_params + having_params
            total = db.execute(count_query, count_params).fetchone()[0]

        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "title": r["title"],
                "title_mode": r["title_mode"],
                "summary": r["summary"],
                "summary_status": r["summary_status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "revision": r["revision"],
                "is_legacy": bool(r["is_legacy"]),
                "turn_count": r["turn_count"],
                "first_question": r["first_question"],
            })

        return {
            "items": items,
            "total": total,
            "limit": lim,
            "offset": off,
        }

    def get_turn_by_request_id(self, request_id: str) -> Optional[dict]:
        """Lookup turn by request_id across all sessions."""
        if not request_id:
            return None
        with self._connect() as db:
            row = db.execute(
                """SELECT t.*, s.title as session_title
                   FROM session_turns t
                   JOIN sessions s ON t.session_id = s.id
                   WHERE t.request_id = ?""",
                (request_id,),
            ).fetchone()
            if not row:
                return None
            try:
                cits = json.loads(row["citations_json"])
            except Exception:
                cits = []
            return {
                "id": row["id"],
                "session_id": row["session_id"],
                "turn_index": row["turn_index"],
                "question": row["question"],
                "answer": row["answer"],
                "citations": cits,
                "answer_mode": row["answer_mode"],
                "search_scope": json.loads(row["search_scope_json"] or "{}"),
                "model": row["model"],
                "created_at": row["created_at"],
                "session_title": row["session_title"],
                "legacy_id": row["legacy_search_id"] if row["legacy_search_id"] is not None else row["id"],
            }

    def add_turn(
        self,
        session_id: str,
        question: str,
        answer: str,
        citations: list,
        model: str,
        request_id: Optional[str] = None,
        answer_mode: str = "quick",
        search_scope: Optional[dict] = None,
    ) -> dict:
        """Atomically add a completed turn to a session. Supports idempotency via request_id."""
        q_clean = question.strip()
        ans_clean = clean_answer(answer)
        cit_json = json.dumps(citations, ensure_ascii=False)
        scope_json = json.dumps(search_scope or {}, ensure_ascii=False)
        mode = {
            "quick": "quick", "summary": "quick",
            "steps": "steps", "step_by_step": "steps",
            "compare": "compare", "comparison": "compare",
        }.get(answer_mode, "quick")
        now = _now_iso()

        with self._connect() as db:
            # Verify session exists
            sess = db.execute("SELECT id, title, title_mode, revision FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not sess:
                raise KeyError(f"Session '{session_id}' not found")

            # Idempotency check before inserting into ANY table
            if request_id:
                existing = db.execute(
                    "SELECT id, turn_index, created_at, legacy_search_id FROM session_turns WHERE session_id=? AND request_id=?",
                    (session_id, request_id),
                ).fetchone()
                if existing:
                    return {
                        "id": existing["id"],
                        "session_id": session_id,
                        "turn_index": existing["turn_index"],
                        "session_title": sess["title"],
                        "created_at": existing["created_at"],
                        "legacy_id": existing["legacy_search_id"] if existing["legacy_search_id"] is not None else existing["id"],
                        "is_duplicate": True,
                    }

            # Next turn index
            max_idx = db.execute(
                "SELECT COALESCE(MAX(turn_index), 0) FROM session_turns WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            turn_idx = max_idx + 1

            # Insert into legacy searches first for backward compatibility and ID mapping
            leg_cur = db.execute(
                "INSERT INTO searches(question, answer, citations_json, model) VALUES (?, ?, ?, ?)",
                (q_clean, ans_clean, cit_json, model),
            )
            legacy_id = leg_cur.lastrowid

            # Insert turn with legacy_search_id reference
            cursor = db.execute(
                """INSERT INTO session_turns(session_id, turn_index, question, answer, citations_json, model, created_at, request_id, answer_mode, search_scope_json, legacy_search_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, turn_idx, q_clean, ans_clean, cit_json, model, now, request_id, mode, scope_json, legacy_id),
            )
            turn_id = cursor.lastrowid

            # Update session
            current_title = sess["title"]
            if sess["title_mode"] == "auto" and turn_idx == 1:
                current_title = generate_fallback_title(q_clean)
                db.execute(
                    "UPDATE sessions SET title=?, updated_at=?, revision=revision+1 WHERE id=?",
                    (current_title, now, session_id),
                )
            else:
                db.execute(
                    "UPDATE sessions SET updated_at=?, revision=revision+1 WHERE id=?",
                    (now, session_id),
                )

        return {
            "id": turn_id,
            "session_id": session_id,
            "turn_index": turn_idx,
            "session_title": current_title,
            "created_at": now,
            "answer_mode": mode,
            "search_scope": search_scope or {},
            "legacy_id": legacy_id,
            "is_duplicate": False,
        }

    def get_session_context(self, session_id: str, max_turns: int = 4) -> List[Dict[str, str]]:
        """Retrieve the last max_turns completed turns from the session formatted for rag_agent chat_history."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT question, answer FROM session_turns WHERE session_id=? ORDER BY turn_index DESC LIMIT ?",
                (session_id, max_turns),
            ).fetchall()

        messages = []
        for r in reversed(rows):
            messages.append({"role": "user", "content": r["question"]})
            messages.append({"role": "assistant", "content": clean_answer(r["answer"])})
        return messages

    def rename_session(self, session_id: str, title: str) -> bool:
        """Manually rename a session. Sets title_mode='manual' to protect against auto-summary overwrite."""
        t = title.strip()
        if not t:
            return False
        # Limit title length
        t = t[:120]
        now = _now_iso()

        with self._connect() as db:
            res = db.execute(
                "UPDATE sessions SET title=?, title_mode='manual', updated_at=?, revision=revision+1 WHERE id=?",
                (t, now, session_id),
            )
            return res.rowcount > 0

    def update_session_summary(
        self,
        session_id: str,
        summary: str,
        auto_title: Optional[str] = None,
        expected_revision: Optional[int] = None,
    ) -> bool:
        """Atomically update session summary with compare-and-set protection on expected_revision."""
        now = _now_iso()
        clean_sum = summary.strip()
        clean_title = auto_title.strip()[:120] if auto_title else ""

        with self._connect() as db:
            # Atomic compare-and-set update:
            # Does NOT increment revision (preserves content revision so summary doesn't immediately become stale)
            # Updates title ONLY if title_mode == 'auto' and auto_title is non-empty
            if expected_revision is not None:
                res = db.execute(
                    """UPDATE sessions
                       SET summary=?,
                           summary_status='completed',
                           summarized_revision=?,
                           updated_at=?,
                           title = CASE WHEN title_mode = 'auto' AND ? != '' THEN ? ELSE title END
                       WHERE id=? AND revision=?""",
                    (clean_sum, expected_revision, now, clean_title, clean_title, session_id, expected_revision),
                )
            else:
                res = db.execute(
                    """UPDATE sessions
                       SET summary=?,
                           summary_status='completed',
                           updated_at=?,
                           title = CASE WHEN title_mode = 'auto' AND ? != '' THEN ? ELSE title END
                       WHERE id=?""",
                    (clean_sum, now, clean_title, clean_title, session_id),
                )
            return res.rowcount > 0

    def set_summary_status(self, session_id: str, status: str) -> bool:
        """Set summary status ('none', 'pending', 'completed', 'failed')."""
        with self._connect() as db:
            res = db.execute("UPDATE sessions SET summary_status=? WHERE id=?", (status, session_id))
            return res.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        """Delete a session, all its turns, and any mirrored legacy searches atomically."""
        with self._connect() as db:
            # Find associated legacy search IDs
            rows = db.execute(
                "SELECT legacy_search_id FROM session_turns WHERE session_id=? AND legacy_search_id IS NOT NULL",
                (session_id,),
            ).fetchall()
            legacy_ids = [r[0] for r in rows if r[0] is not None]

            # Check if this session was a legacy migrated session: legacy_<search_id>
            if session_id.startswith("legacy_"):
                try:
                    s_id = int(session_id.replace("legacy_", ""))
                    legacy_ids.append(s_id)
                except ValueError:
                    pass

            if legacy_ids:
                placeholders = ",".join("?" for _ in legacy_ids)
                db.execute(f"DELETE FROM searches WHERE id IN ({placeholders})", legacy_ids)

            db.execute("DELETE FROM session_turns WHERE session_id=?", (session_id,))
            res = db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            return res.rowcount > 0

    def search_sessions(self, query: str, limit: int = 20, offset: int = 0) -> dict:
        """Search sessions by keyword across title, summary, questions, and answers with SQL bound parameters."""
        q = query.strip()
        if not q:
            return self.list_sessions(limit=limit, offset=offset)

        param = f"%{q}%"
        lim = min(max(limit, 1), 100)
        off = max(offset, 0)

        with self._connect() as db:
            search_sql = """
                SELECT s.id, s.title, s.title_mode, s.summary, s.summary_status,
                       s.created_at, s.updated_at, s.revision, s.is_legacy,
                       (SELECT COUNT(*) FROM session_turns WHERE session_id = s.id) as turn_count,
                       (SELECT question FROM session_turns WHERE session_id = s.id ORDER BY turn_index ASC LIMIT 1) as first_question
                FROM sessions s
                WHERE (
                    s.title LIKE ?
                    OR s.summary LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM session_turns t
                        WHERE t.session_id = s.id AND (t.question LIKE ? OR t.answer LIKE ?)
                    )
                )
                AND (SELECT COUNT(*) FROM session_turns WHERE session_id = s.id) > 0
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
            """
            rows = db.execute(search_sql, (param, param, param, param, lim, off)).fetchall()

            count_sql = """
                SELECT COUNT(*) FROM sessions s
                WHERE (
                    s.title LIKE ?
                    OR s.summary LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM session_turns t
                        WHERE t.session_id = s.id AND (t.question LIKE ? OR t.answer LIKE ?)
                    )
                )
                AND (SELECT COUNT(*) FROM session_turns WHERE session_id = s.id) > 0
            """
            total = db.execute(count_sql, (param, param, param, param)).fetchone()[0]

        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "title": r["title"],
                "title_mode": r["title_mode"],
                "summary": r["summary"],
                "summary_status": r["summary_status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "revision": r["revision"],
                "is_legacy": bool(r["is_legacy"]),
                "turn_count": r["turn_count"],
                "first_question": r["first_question"],
            })

        return {
            "items": items,
            "total": total,
            "limit": lim,
            "offset": off,
            "query": q,
        }

    # =========================================================================
    # Safe, Idempotent Legacy Migration
    # =========================================================================

    def migrate_legacy_searches(self, backup: bool = True) -> dict:
        """Migrate legacy searches to sessions idempotently.

        Preserves existing searches table and creates a SQLite backup before mutation.
        Fail-closed: aborts if backup fails. Transactional: rolls back if error occurs.
        Does not re-create deleted sessions.
        """
        backup_path = None
        if backup and self.path.exists() and self.path.stat().st_size > 0:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            backup_path = self.path.parent / f"{self.path.name}.bak_sessions_{ts}"
            try:
                with self._connect() as src:
                    with sqlite3.connect(backup_path) as dst:
                        src.backup(dst)
                backup_path.chmod(0o600)
            except Exception as e:
                logger.error("Could not create SQLite backup: %s", e)
                raise RuntimeError(f"Sao lưu CSDL thất bại ({e}), hủy bỏ quá trình di trú để bảo vệ dữ liệu.") from e

        # CLI callers use auto_init=False so the backup contains the original
        # schema as well as the original rows before initialization adds tables.
        self._init_db()
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                # Check if migration already marked done
                meta = db.execute("SELECT value FROM history_meta WHERE key='legacy_searches_migrated'").fetchone()
                if meta and meta["value"] == "1":
                    return {
                        "status": "already_migrated",
                        "migrated_count": 0,
                        "backup_path": str(backup_path) if backup_path else None,
                    }

                # Fetch all searches NOT already mapped to any session turn
                searches = db.execute("""
                    SELECT * FROM searches
                    WHERE id NOT IN (SELECT legacy_search_id FROM session_turns WHERE legacy_search_id IS NOT NULL)
                    ORDER BY id ASC
                """).fetchall()
                migrated_count = 0

                for s in searches:
                    legacy_sid = f"legacy_{s['id']}"
                    # Check if session already exists
                    exists = db.execute("SELECT id FROM sessions WHERE id=?", (legacy_sid,)).fetchone()
                    if exists:
                        continue

                    q = s["question"]
                    ans = s["answer"]
                    cits = s["citations_json"]
                    model = s["model"]
                    created_at = s["created_at"]
                    title = generate_fallback_title(q)

                    db.execute(
                        """INSERT INTO sessions(id, title, title_mode, summary_status, created_at, updated_at, revision, is_legacy)
                           VALUES (?, ?, 'auto', 'none', ?, ?, 1, 1)""",
                        (legacy_sid, title, created_at, created_at),
                    )

                    db.execute(
                        """INSERT INTO session_turns(session_id, turn_index, question, answer, citations_json, model, created_at, request_id, legacy_search_id)
                           VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)""",
                        (legacy_sid, q, ans, cits, model, created_at, f"legacy_{s['id']}", s["id"]),
                    )
                    migrated_count += 1

                # Mark migration complete in metadata
                db.execute(
                    "INSERT OR REPLACE INTO history_meta(key, value) VALUES ('legacy_searches_migrated', '1')"
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

        return {
            "status": "success",
            "migrated_count": migrated_count,
            "backup_path": str(backup_path) if backup_path else None,
        }
