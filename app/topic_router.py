"""Topic Router for Study Agent Q&A retrieval (Part 4 Step 3).

Uses the shared 5-parent / 26-child taxonomy from `app.topic_tree` to:
1. Identify confidently recognized topic queries (accented and unaccented).
2. Map recognized topics to relevant document candidates using cached membership.
3. Keep explicit document scope authoritative.
4. Safely fall back to global library search for uncertain, ambiguous, unrecognized,
   or empty-candidate queries.
5. Invalidate the document membership cache whenever the SQLite database changes.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Set, Tuple, Union

from app.topic_tree import (
    CHILD_TO_PARENT,
    PATTERNS,
    TOPIC_TREE,
    TOPICS,
    normalized,
    resolve_topic_hierarchy,
)

logger = logging.getLogger(__name__)

# Compile regex patterns for parent domains to recognize queries like
# "khoa học máy tính", "toán học", "vật lý đại cương", etc.
PARENT_PATTERNS: Dict[str, re.Pattern[str]] = {}
PARENT_CHILDREN: Dict[str, Tuple[str, ...]] = {}
for _node in TOPIC_TREE:
    _pid = _node["parent_id"]
    _aliases = [_node["parent_label"], _pid, *(_node.get("parent_aliases", ()))]
    PARENT_PATTERNS[_pid] = re.compile(
        "|".join(r"(?<!\w)" + re.escape(normalized(_a)) + r"(?!\w)" for _a in _aliases)
    )
    PARENT_CHILDREN[_pid] = tuple(c_label for c_label, _ in _node["children"])


def get_sqlite_fingerprint(db_path: Any, conn: Optional[sqlite3.Connection] = None) -> tuple:
    """Extract filesystem and SQLite header fingerprint to detect any modification across connections."""
    resolved_path: Optional[Path] = None
    if db_path:
        try:
            p = Path(db_path)
            if p.is_file():
                resolved_path = p
        except Exception:
            pass

    if resolved_path is None and conn is not None:
        try:
            rows = conn.execute("PRAGMA database_list").fetchall()
            for row in rows:
                if row[1] == "main" and row[2]:
                    p = Path(row[2])
                    if p.is_file():
                        resolved_path = p
                        break
        except Exception:
            pass

    if resolved_path is not None:
        try:
            st = resolved_path.stat()
            db_info = (st.st_mtime_ns, st.st_size, st.st_ino)
            wal = resolved_path.with_name(resolved_path.name + "-wal")
            try:
                wst = wal.stat()
                wal_info = (wst.st_mtime_ns, wst.st_size)
            except OSError:
                wal_info = None

            change_counter = None
            try:
                with open(resolved_path, "rb") as f:
                    f.seek(24)
                    data = f.read(4)
                    if len(data) == 4:
                        change_counter = int.from_bytes(data, "big")
            except OSError:
                pass

            conn_info = None
            if conn is not None:
                try:
                    dv = conn.execute("PRAGMA data_version").fetchone()[0]
                    conn_info = (dv, conn.total_changes)
                except Exception:
                    pass

            return (str(resolved_path.resolve()), db_info, wal_info, change_counter, conn_info)
        except Exception:
            pass

    if conn is not None:
        try:
            dv = conn.execute("PRAGMA data_version").fetchone()[0]
            sv = conn.execute("PRAGMA schema_version").fetchone()[0]
            return ("in_memory", id(conn), conn.total_changes, dv, sv)
        except Exception:
            pass

    return ()


class TopicRouter:
    """Routes Q&A queries to relevant document candidates using the shared taxonomy."""

    def __init__(self, db_path: Optional[Union[Path, str]] = None):
        self._db_path = Path(db_path) if db_path is not None else None
        self._cache: Optional[Dict[str, Set[int]]] = None
        self._cache_fp: Optional[tuple] = None
        self._lock = threading.Lock()

    @property
    def db_path(self) -> Optional[Path]:
        return self._db_path

    @db_path.setter
    def db_path(self, val: Optional[Union[Path, str]]):
        with self._lock:
            self._db_path = Path(val) if val is not None else None
            self._cache = None
            self._cache_fp = None

    def invalidate_cache(self) -> None:
        """Explicitly clear the cached document topic memberships."""
        with self._lock:
            self._cache = None
            self._cache_fp = None

    def infer_query_topics(self, query: str) -> Dict[str, Any]:
        """Identify matching child topics and parent domains from a query string.

        Supports accented and unaccented Vietnamese, parent domain aliases,
        and multiple topic references.
        """
        norm_q = normalized(query)
        if not norm_q:
            return {
                "confident": False,
                "child_topics": [],
                "parent_ids": [],
                "parent_labels": [],
            }

        matched_children: List[str] = []
        matched_parent_ids: List[str] = []
        matched_parent_labels: List[str] = []

        # 1. Match child topics
        for label, pattern in PATTERNS.items():
            if pattern.search(norm_q):
                matched_children.append(label)
                p_info = CHILD_TO_PARENT.get(label)
                if p_info:
                    if p_info["parent_id"] not in matched_parent_ids:
                        matched_parent_ids.append(p_info["parent_id"])
                    if p_info["parent_label"] not in matched_parent_labels:
                        matched_parent_labels.append(p_info["parent_label"])

        # 2. Match parent domains directly
        for pid, pattern in PARENT_PATTERNS.items():
            if pattern.search(norm_q):
                if pid not in matched_parent_ids:
                    matched_parent_ids.append(pid)
                    # Find label
                    for node in TOPIC_TREE:
                        if node["parent_id"] == pid and node["parent_label"] not in matched_parent_labels:
                            matched_parent_labels.append(node["parent_label"])
                # If no child topics matched under this parent, include its children
                existing_under_parent = [
                    c for c in matched_children
                    if CHILD_TO_PARENT.get(c, {}).get("parent_id") == pid
                ]
                if not existing_under_parent:
                    for c_label in PARENT_CHILDREN.get(pid, ()):
                        if c_label not in matched_children:
                            matched_children.append(c_label)

        confident = bool(matched_children or matched_parent_ids)
        return {
            "confident": confident,
            "child_topics": matched_children,
            "parent_ids": matched_parent_ids,
            "parent_labels": matched_parent_labels,
        }

    def get_topic_document_members(
        self,
        conn: sqlite3.Connection,
        db_path: Optional[Union[Path, str]] = None,
    ) -> Dict[str, Set[int]]:
        """Return a mapping of topic label -> set of document IDs matching its aliases.

        Automatically validates and refreshes using the SQLite database fingerprint.
        """
        path = Path(db_path) if db_path is not None else self._db_path
        fp = get_sqlite_fingerprint(path, conn)
        # Uncommitted contents can disappear without changing total_changes.
        # Anonymous connections also have no durable identity across lifetimes.
        cacheable = bool(fp and fp[0] != "in_memory" and not conn.in_transaction)

        with self._lock:
            if cacheable and self._cache is not None and self._cache_fp == fp:
                return self._cache

        members: Dict[str, Set[int]] = {label: set() for label, _ in TOPICS}

        # Check existing valid document IDs if documents table exists
        valid_doc_ids: Optional[Set[int]] = None
        try:
            d_rows = conn.execute("SELECT id FROM documents").fetchall()
            if d_rows:
                valid_doc_ids = {r[0] for r in d_rows}
        except Exception:
            valid_doc_ids = None

        has_fts = False
        try:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
            ).fetchone()
            if table_exists:
                has_fts = conn.execute("SELECT 1 FROM chunks_fts LIMIT 1").fetchone() is not None
        except Exception:
            has_fts = False

        if has_fts:
            for label, aliases in TOPICS:
                pattern = PATTERNS[label]
                terms = []
                for a in aliases:
                    for var in {unicodedata.normalize("NFC", a), unicodedata.normalize("NFD", a)}:
                        safe = var.replace('"', '""')
                        terms.append(f'text: "{safe}"')
                fts_query = " OR ".join(terms)
                try:
                    rows = conn.execute(
                        "SELECT c.doc_id, c.text FROM chunks c "
                        "JOIN chunks_fts f ON f.rowid = c.id WHERE chunks_fts MATCH ?",
                        (fts_query,),
                    ).fetchall()
                    for row in rows:
                        doc_id = row[0]
                        if valid_doc_ids is not None and doc_id not in valid_doc_ids:
                            continue
                        if doc_id not in members[label]:
                            if pattern.search(normalized(row[1])):
                                members[label].add(doc_id)
                except Exception as exc:
                    logger.warning("FTS query failed for topic '%s': %s; scanning chunks", label, exc)
                    for row in conn.execute("SELECT doc_id, text FROM chunks").fetchall():
                        doc_id = row[0]
                        if valid_doc_ids is not None and doc_id not in valid_doc_ids:
                            continue
                        if doc_id not in members[label]:
                            if pattern.search(normalized(row[1])):
                                members[label].add(doc_id)
        else:
            try:
                for row in conn.execute("SELECT doc_id, text FROM chunks").fetchall():
                    doc_id = row[0]
                    if valid_doc_ids is not None and doc_id not in valid_doc_ids:
                        continue
                    text_norm = normalized(row[1])
                    for label, _ in TOPICS:
                        if doc_id not in members[label] and PATTERNS[label].search(text_norm):
                            members[label].add(doc_id)
            except Exception as exc:
                logger.warning("Chunk scan failed during topic membership computation: %s", exc)

        if cacheable:
            with self._lock:
                self._cache = members
                self._cache_fp = fp

        return members

    def plan_routing(
        self,
        query: str,
        conn: Optional[sqlite3.Connection] = None,
        db_path: Optional[Union[Path, str]] = None,
        document_id: Optional[int] = None,
        document_ids: Optional[Collection[int]] = None,
    ) -> Dict[str, Any]:
        """Compute an auditable topic routing plan for a query.

        Returns metadata indicating whether topic candidate routing was applied,
        which topics/parents matched, candidate document IDs, and whether global
        fallback was chosen.
        """
        # 1. Explicit document scope takes complete authority
        if document_id is not None:
            return {
                "applied": False,
                "explicit_scope": True,
                "document_id": document_id,
                "candidate_doc_ids": [document_id],
                "matched_topics": [],
                "parent_ids": [],
                "parent_labels": [],
                "fallback_to_global": False,
                "fallback_reason": None,
            }

        if document_ids is not None:
            return {
                "applied": False,
                "explicit_scope": True,
                "document_id": None,
                "candidate_doc_ids": sorted(list(set(document_ids))),
                "matched_topics": [],
                "parent_ids": [],
                "parent_labels": [],
                "fallback_to_global": False,
                "fallback_reason": None,
            }

        # 2. Infer topics from query
        inferred = self.infer_query_topics(query)
        if not inferred["confident"]:
            return {
                "applied": False,
                "explicit_scope": False,
                "document_id": None,
                "candidate_doc_ids": [],
                "matched_topics": [],
                "parent_ids": [],
                "parent_labels": [],
                "fallback_to_global": True,
                "fallback_reason": "unrecognized_topic",
            }

        # 3. Retrieve topic candidate documents
        created_conn = False
        if conn is None:
            path = Path(db_path) if db_path is not None else self._db_path
            if path and path.exists():
                conn = sqlite3.connect(str(path))
                created_conn = True
            else:
                return {
                    "applied": False,
                    "explicit_scope": False,
                    "document_id": None,
                    "candidate_doc_ids": [],
                    "matched_topics": inferred["child_topics"],
                    "parent_ids": inferred["parent_ids"],
                    "parent_labels": inferred["parent_labels"],
                    "fallback_to_global": True,
                    "fallback_reason": "db_unavailable",
                }

        try:
            members = self.get_topic_document_members(conn, db_path)
            candidate_doc_ids: Set[int] = set()
            for topic in inferred["child_topics"]:
                candidate_doc_ids.update(members.get(topic, set()))
        finally:
            if created_conn and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        # 4. Handle empty candidates -> fallback to global
        if not candidate_doc_ids:
            return {
                "applied": False,
                "explicit_scope": False,
                "document_id": None,
                "candidate_doc_ids": [],
                "matched_topics": inferred["child_topics"],
                "parent_ids": inferred["parent_ids"],
                "parent_labels": inferred["parent_labels"],
                "fallback_to_global": True,
                "fallback_reason": "empty_topic_candidates",
            }

        # 5. Confidently routed to candidates
        return {
            "applied": True,
            "explicit_scope": False,
            "document_id": None,
            "candidate_doc_ids": sorted(candidate_doc_ids),
            "matched_topics": inferred["child_topics"],
            "parent_ids": inferred["parent_ids"],
            "parent_labels": inferred["parent_labels"],
            "fallback_to_global": False,
            "fallback_reason": None,
        }
