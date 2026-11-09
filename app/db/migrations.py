"""Atomic, versioned SQLite migrations using BEGIN IMMEDIATE."""
from datetime import datetime, timezone
import logging
import sqlite3
from typing import Callable, List, Optional

from app.db.connection import atomic_transaction

logger = logging.getLogger("psychology_agent.db.migrations")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Migration:
    def __init__(self, version: int, name: str, up: Callable[[sqlite3.Connection], None]):
        self.version = version
        self.name = name
        self.up = up

    def __repr__(self) -> str:
        return f"<Migration v{self.version}: {self.name}>"


class MigrationManager:
    def __init__(self, store_name: str, migrations: List[Migration]):
        self.store_name = store_name
        self.migrations = sorted(migrations, key=lambda m: m.version)

    def current_version(self, conn: sqlite3.Connection) -> int:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        if not cursor.fetchone():
            return 0
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return row[0] if (row and row[0] is not None) else 0

    def apply(self, conn: sqlite3.Connection) -> int:
        """Apply all pending migrations in order using atomic BEGIN IMMEDIATE transactions."""
        # Ensure schema_migrations table exists
        with atomic_transaction(conn):
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
            """)

        cur_v = self.current_version(conn)
        applied = 0
        for m in self.migrations:
            if m.version > cur_v:
                with atomic_transaction(conn):
                    # Check inside transaction in case another connection applied it
                    row = conn.execute(
                        "SELECT version FROM schema_migrations WHERE version = ?", (m.version,)
                    ).fetchone()
                    if row:
                        continue
                    logger.info("Applying migration %s v%d: %s", self.store_name, m.version, m.name)
                    m.up(conn)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                        (m.version, m.name, _now_iso()),
                    )
                    applied += 1
        return applied
