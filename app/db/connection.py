"""Robust SQLite connection management with WAL, busy timeout, and containment validation."""
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional, Union

PRAGMA_BUSY_TIMEOUT_MS = 10000


def validate_path_containment(
    path: Union[str, Path],
    base_dir: Union[str, Path],
    allow_symlinks: bool = False,
) -> Path:
    """Ensure path is strictly contained within base_dir, resolving configured root once and inspecting lexical descendants under root."""
    base_path = Path(base_dir).absolute()
    base_resolved = base_path.resolve()
    raw = Path(path)
    if ".." in raw.parts:
        raise ValueError(f"Path containment violation: '{path}' escapes the root by parent traversal")

    def relative_under_root(candidate):
        for root in (base_path, base_resolved):
            try:
                return candidate.relative_to(root)
            except ValueError:
                pass
        # Only normalize the configured root and ancestors, never its descendants.
        for ancestor in candidate.parents:
            if ancestor.resolve() == base_resolved:
                return candidate.relative_to(ancestor)
        return None

    candidate = raw if raw.is_absolute() else Path.cwd() / raw
    rel = relative_under_root(candidate)
    if rel is None and not raw.is_absolute():
        rel = raw
    if rel is None:
        raise ValueError(f"Path containment violation: '{path}' escapes '{base_resolved}'")
    target = base_resolved
    for part in rel.parts:
        target = target / part
        if not allow_symlinks and target.is_symlink():
            raise ValueError(f"Symlink rejected under account root: '{target}' is a symlink")
    resolved = target.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise ValueError(f"Path containment violation: '{path}' escapes '{base_resolved}'")
    return resolved


def secure_file_permissions(path: Union[str, Path]) -> None:
    """Set strict 0o600 file permissions."""
    try:
        Path(path).chmod(0o600)
    except OSError:
        pass


def secure_dir_permissions(path: Union[str, Path]) -> None:
    """Set strict 0o700 directory permissions."""
    try:
        Path(path).chmod(0o700)
    except OSError:
        pass


def open_sqlite(
    path: Union[str, Path],
    timeout: float = 10.0,
    wal: bool = True,
    synchronous_full: bool = True,
    foreign_keys: bool = True,
) -> sqlite3.Connection:
    """Open SQLite connection with WAL, busy timeout, synchronous FULL and foreign keys."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_dir_permissions(target_path.parent)

    conn = sqlite3.connect(str(target_path), timeout=timeout)
    conn.row_factory = sqlite3.Row

    try:
        # Pragma configurations
        conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        if wal:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                        conn.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if getattr(exc, "sqlite_errorcode", 0) & 255 not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                        raise
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        if synchronous_full:
            conn.execute("PRAGMA synchronous = FULL")
        else:
            conn.execute("PRAGMA synchronous = NORMAL")
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        conn.close()
        raise

    if target_path.exists():
        secure_file_permissions(target_path)
    return conn


@contextmanager
def managed_sqlite(
    path: Union[str, Path],
    timeout: float = 10.0,
    wal: bool = True,
    synchronous_full: bool = True,
    foreign_keys: bool = True,
) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for deterministic SQLite connection lifecycle (opens and closes on exit)."""
    conn = open_sqlite(
        path,
        timeout=timeout,
        wal=wal,
        synchronous_full=synchronous_full,
        foreign_keys=foreign_keys,
    )
    try:
        yield conn
        if conn.in_transaction:
            conn.commit()
    except Exception:
        if conn.in_transaction:
            try:
                conn.rollback()
            except sqlite3.OperationalError:
                pass
        raise
    finally:
        conn.close()


@contextmanager
def atomic_transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Connection, None, None]:
    """Execute block within an immediate transaction (BEGIN IMMEDIATE) for write safety.

    Never swallows OperationalError from BEGIN IMMEDIATE: if lock acquisition fails,
    the exception propagates and the caller body is never entered unlocked.
    """
    started = False
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
        started = True
    try:
        yield conn
        if started and conn.in_transaction:
            conn.commit()
    except Exception:
        if started and conn.in_transaction:
            try:
                conn.rollback()
            except sqlite3.OperationalError:
                pass
        raise
