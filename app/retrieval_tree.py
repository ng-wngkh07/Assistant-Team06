"""Persistent hierarchical retrieval tree on SQLite sidecar database (Production).

Preserves all original source rows, embeddings, and provenance in the active DB.
Builds versioned taxonomy, doc_topics, chunk_topics, and retrieval_tree_nodes
with content, source, and taxonomy hashes in an adjacent sidecar SQLite database.
Provides TreeSearcher with read-only query connections, persistent scoped
candidate filtering, source-bound fingerprint validation, and guaranteed global
fallback on missing, stale, invalid, or inadequate evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, Dict, List, Optional, Set, Tuple, Union

from app.config import DEFAULT_EMBED_MODEL, DEFAULT_TOP_K
from app.rag_evidence_validator import has_relevant_evidence
from app.searcher import HybridSearcher
from app.text_cleaner import normalize_vietnamese_text
from app.topic_tree import (
    CHILD_TO_PARENT,
    PATTERNS,
    TOPIC_TREE,
    infer_query_topic_hierarchy,
    normalized,
)

logger = logging.getLogger(__name__)

TAXONOMY_VERSION = "v5-20261105"


def _detect_locator_kind(filename: str = "", filepath: str = "") -> Tuple[str, str]:
    """Detect truthful locator kind and path segment based on document source.

    Returns:
        (locator_kind, path_segment) where:
        - PDF: ('page', 'page')
        - DOCX: ('section', 'section')
        - PPTX: ('slide', 'slide')
        - Unknown: ('unknown', 'unknown')
    """
    target = (filename or filepath or "").strip().lower()
    if target.endswith(".pdf"):
        return "page", "page"
    if target.endswith(".docx") or target.endswith(".doc"):
        return "section", "section"
    if target.endswith(".pptx") or target.endswith(".ppt"):
        return "slide", "slide"
    return "unknown", "unknown"


def compute_taxonomy_hash() -> str:
    """Compute canonical deterministic hash of the compiled taxonomy tree."""
    data = []
    for node in TOPIC_TREE:
        data.append({
            "parent_id": node["parent_id"],
            "parent_label": node["parent_label"],
            "parent_aliases": sorted(list(node.get("parent_aliases", ()))),
            "children": [
                {
                    "topic": c_label,
                    "aliases": sorted(list(c_aliases)),
                }
                for c_label, c_aliases in sorted(node["children"], key=lambda x: x[0])
            ],
        })
    canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_taxonomy_hash_from_db(conn: sqlite3.Connection) -> str:
    """Compute taxonomy hash directly from the database taxonomy_nodes table."""
    parents_rows = conn.execute("SELECT node_id, label, aliases_json FROM taxonomy_nodes WHERE level=1").fetchall()
    children_rows = conn.execute("SELECT node_id, parent_id, label, aliases_json FROM taxonomy_nodes WHERE level=2").fetchall()

    children_by_parent: Dict[str, list] = {}
    for cr in children_rows:
        pid = cr[1]
        children_by_parent.setdefault(pid, []).append({
            "topic": cr[2],
            "aliases": sorted(json.loads(cr[3])),
        })

    parents_map = {pr[0]: pr for pr in parents_rows}

    data = []
    for node in TOPIC_TREE:
        pid = node["parent_id"]
        if pid not in parents_map:
            return ""
        pr = parents_map[pid]
        data.append({
            "parent_id": pid,
            "parent_label": pr[1],
            "parent_aliases": sorted(json.loads(pr[2])),
            "children": sorted(children_by_parent.get(pid, []), key=lambda x: x["topic"]),
        })
    canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _normalize_fp(fp: Any) -> Any:
    """Normalize nested tuple/list structures for consistent JSON comparison."""
    if isinstance(fp, (tuple, list)):
        return [_normalize_fp(x) for x in fp]
    return fp


def get_source_binding_fingerprint(db_path: Union[Path, str]) -> tuple:
    """Extract canonical filesystem and SQLite header fingerprint of the source database."""
    p = Path(db_path).resolve()
    if not p.is_file():
        return ()

    try:
        st = p.stat()
        db_stat = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return ()

    wal_path = p.with_name(p.name + "-wal")
    try:
        wst = wal_path.stat()
        wal_stat = (wst.st_mtime_ns, wst.st_size)
    except OSError:
        wal_stat = None

    change_counter = None
    try:
        with open(p, "rb") as f:
            f.seek(24)
            data = f.read(4)
            if len(data) == 4:
                change_counter = int.from_bytes(data, "big")
    except OSError:
        pass

    return (str(p), db_stat, wal_stat, change_counter)


def get_sqlite_fingerprint(db_path: Path, conn: Optional[sqlite3.Connection] = None) -> tuple:
    """Extract filesystem and SQLite header fingerprint to detect any modification across connections."""
    p = Path(db_path).resolve()
    if not p.is_file():
        return ()

    try:
        st = p.stat()
        db_stat = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return ()

    wal_path = p.with_name(p.name + "-wal")
    try:
        wst = wal_path.stat()
        wal_stat = (wst.st_mtime_ns, wst.st_size)
    except OSError:
        wal_stat = None

    change_counter = None
    try:
        with open(p, "rb") as f:
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
            sv = conn.execute("PRAGMA schema_version").fetchone()[0]
            conn_info = (dv, sv, conn.total_changes)
        except Exception:
            pass

    return (str(p), db_stat, wal_stat, change_counter, conn_info)


def build_tree(source_db: Union[Path, str], shadow_db: Union[Path, str]) -> Dict[str, Any]:
    """Atomically build persistent retrieval tree in a new shadow/sidecar SQLite database.

    Args:
        source_db: Existing knowledge base opened in read-only mode.
        shadow_db: New target SQLite database path. Must not exist beforehand.

    Returns:
        dict with metadata, build statistics, and integrity hashes.
    """
    src_path = Path(source_db).resolve()
    dest_path = Path(shadow_db).resolve()

    if not src_path.is_file():
        raise FileNotFoundError(f"Source database not found: {src_path}")

    if src_path == dest_path:
        raise ValueError("Refuse source == destination: shadow_db must be a distinct path.")

    if dest_path.exists():
        raise ValueError(f"shadow_db must be a new destination (file already exists: {dest_path})")

    # Capture initial source fingerprint before backup
    fp_before = get_source_binding_fingerprint(src_path)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest_path.with_name(f".tmp.{dest_path.name}.{os.getpid()}.{uuid.uuid4().hex}")
    if temp_dest.exists():
        temp_dest.unlink()

    # 1. Open source SQLite with URI mode=ro
    src_uri = f"file:{src_path}?mode=ro"
    src_conn = sqlite3.connect(src_uri, uri=True)
    dest_conn = sqlite3.connect(str(temp_dest))

    try:
        # 2. SQLite online backup into temp shadow DB
        src_conn.backup(dest_conn)
        src_conn.close()
        src_conn = None

        dest_conn.row_factory = sqlite3.Row

        # 3. Create persistent hierarchical tree tables
        dest_conn.executescript("""
        CREATE TABLE IF NOT EXISTS taxonomy_nodes (
            node_id TEXT PRIMARY KEY,
            parent_id TEXT,
            level INTEGER NOT NULL,
            label TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            taxonomy_version TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_taxonomy_parent ON taxonomy_nodes(parent_id);

        CREATE TABLE IF NOT EXISTS doc_topics (
            doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            topic_id TEXT NOT NULL,
            topic_label TEXT NOT NULL,
            parent_id TEXT NOT NULL,
            match_count INTEGER NOT NULL,
            PRIMARY KEY (doc_id, topic_id)
        );
        CREATE INDEX IF NOT EXISTS idx_doc_topics_topic ON doc_topics(topic_id);
        CREATE INDEX IF NOT EXISTS idx_doc_topics_parent ON doc_topics(parent_id);

        CREATE TABLE IF NOT EXISTS chunk_topics (
            chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
            doc_id INTEGER NOT NULL,
            topic_id TEXT NOT NULL,
            topic_label TEXT NOT NULL,
            parent_id TEXT NOT NULL,
            PRIMARY KEY (chunk_id, topic_id)
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_topics_topic ON chunk_topics(topic_id, chunk_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_topics_parent ON chunk_topics(parent_id, chunk_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_topics_doc ON chunk_topics(doc_id);

        CREATE TABLE IF NOT EXISTS retrieval_tree_nodes (
            path_id TEXT PRIMARY KEY,
            doc_id INTEGER NOT NULL,
            page_num INTEGER NOT NULL,
            locator_kind TEXT NOT NULL DEFAULT 'page',
            chunk_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            taxonomy_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_tree_doc_page ON retrieval_tree_nodes(doc_id, page_num);
        CREATE INDEX IF NOT EXISTS idx_tree_chunk ON retrieval_tree_nodes(chunk_id);

        CREATE TABLE IF NOT EXISTS tree_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        # 4. Insert versioned taxonomy nodes
        tax_hash = compute_taxonomy_hash()
        for node in TOPIC_TREE:
            p_id = node["parent_id"]
            p_label = node["parent_label"]
            p_aliases = list(node.get("parent_aliases", ()))
            dest_conn.execute(
                "INSERT INTO taxonomy_nodes(node_id, parent_id, level, label, aliases_json, taxonomy_version) VALUES (?, NULL, 1, ?, ?, ?)",
                (p_id, p_label, json.dumps(p_aliases, ensure_ascii=False), TAXONOMY_VERSION),
            )
            for c_label, c_aliases in node["children"]:
                c_id = normalized(c_label).replace(" ", "_")
                dest_conn.execute(
                    "INSERT INTO taxonomy_nodes(node_id, parent_id, level, label, aliases_json, taxonomy_version) VALUES (?, ?, 2, ?, ?, ?)",
                    (c_id, p_id, c_label, json.dumps(list(c_aliases), ensure_ascii=False), TAXONOMY_VERSION),
                )

        # 5. Populate document -> physical page / section -> chunk paths and taxonomy memberships
        docs_rows = dest_conn.execute("SELECT * FROM documents").fetchall()
        docs_map = {row["id"]: row for row in docs_rows}

        chunks_rows = dest_conn.execute("SELECT * FROM chunks ORDER BY doc_id, page_num, chunk_index").fetchall()

        doc_topic_counts: Dict[Tuple[int, str, str, str], int] = {}
        classified_chunks: Set[int] = set()
        classified_docs: Set[int] = set()

        for crow in chunks_rows:
            cid = crow["id"]
            did = crow["doc_id"]
            pnum = crow["page_num"]
            cidx = crow["chunk_index"]
            text = crow["text"] or ""

            drow = docs_map.get(did)
            dtitle = drow["clean_title"] if (drow and "clean_title" in drow.keys()) else ""
            dhash = drow["file_hash"] if (drow and "file_hash" in drow.keys() and drow["file_hash"]) else ""

            content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            source_sha = dhash if (dhash and dhash.strip()) else "unknown"

            fname = crow["filename"] if ("filename" in crow.keys() and crow["filename"]) else (drow["filename"] if (drow and "filename" in drow.keys()) else "")
            fpath = drow["filepath"] if (drow and "filepath" in drow.keys() and drow["filepath"]) else ""
            locator_kind, path_segment = _detect_locator_kind(fname, fpath)
            path_id = f"doc:{did}/{path_segment}:{pnum}/chunk:{cid}"

            dest_conn.execute(
                """INSERT INTO retrieval_tree_nodes(path_id, doc_id, page_num, locator_kind, chunk_id, chunk_index, content_sha256, source_sha256, taxonomy_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (path_id, did, pnum, locator_kind, cid, cidx, content_sha, source_sha, tax_hash),
            )

            # Match topics
            norm_text = normalized(text)
            norm_title = normalized(dtitle)
            chunk_matched = False
            for t_label, pat in PATTERNS.items():
                if pat.search(norm_text) or pat.search(norm_title):
                    chunk_matched = True
                    p_info = CHILD_TO_PARENT.get(t_label, {})
                    p_id = p_info.get("parent_id", "general")
                    t_id = normalized(t_label).replace(" ", "_")
                    dest_conn.execute(
                        """INSERT OR IGNORE INTO chunk_topics(chunk_id, doc_id, topic_id, topic_label, parent_id)
                           VALUES (?, ?, ?, ?, ?)""",
                        (cid, did, t_id, t_label, p_id),
                    )
                    key = (did, t_id, t_label, p_id)
                    doc_topic_counts[key] = doc_topic_counts.get(key, 0) + 1

            if chunk_matched:
                classified_chunks.add(cid)
                classified_docs.add(did)

        # Insert doc_topics
        for (did, t_id, t_label, p_id), count in doc_topic_counts.items():
            dest_conn.execute(
                """INSERT INTO doc_topics(doc_id, topic_id, topic_label, parent_id, match_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (did, t_id, t_label, p_id, count),
            )

        total_docs = len(docs_map)
        total_chunks = len(chunks_rows)
        unclass_docs = total_docs - len(classified_docs)
        unclass_chunks = total_chunks - len(classified_chunks)

        meta = {
            "is_valid": "1",
            "taxonomy_version": TAXONOMY_VERSION,
            "taxonomy_hash": tax_hash,
            "tree_schema_version": "1.0",
            "source_db_path": str(src_path),
            "source_db_fingerprint": json.dumps(fp_before),
            "doc_count": str(total_docs),
            "chunk_count": str(total_chunks),
            "classified_docs_count": str(len(classified_docs)),
            "unclassified_docs_count": str(unclass_docs),
            "classified_chunks_count": str(len(classified_chunks)),
            "unclassified_chunks_count": str(unclass_chunks),
            "built_at": datetime.now(timezone.utc).isoformat(),
        }

        for k, v in meta.items():
            dest_conn.execute("INSERT OR REPLACE INTO tree_metadata(key, value) VALUES (?, ?)", (k, v))

        # 6. Install transactional invalidation triggers on mutation targets
        dest_conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS trg_chunks_dirty_ins AFTER INSERT ON chunks
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_chunks_dirty_upd AFTER UPDATE ON chunks
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_chunks_dirty_del AFTER DELETE ON chunks
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_docs_dirty_ins AFTER INSERT ON documents
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_docs_dirty_upd AFTER UPDATE ON documents
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_docs_dirty_del AFTER DELETE ON documents
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_tax_dirty_ins AFTER INSERT ON taxonomy_nodes
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_tax_dirty_upd AFTER UPDATE ON taxonomy_nodes
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_tax_dirty_del AFTER DELETE ON taxonomy_nodes
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_chunk_topics_dirty_ins AFTER INSERT ON chunk_topics
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_chunk_topics_dirty_upd AFTER UPDATE ON chunk_topics
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_chunk_topics_dirty_del AFTER DELETE ON chunk_topics
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_doc_topics_dirty_ins AFTER INSERT ON doc_topics
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_doc_topics_dirty_upd AFTER UPDATE ON doc_topics
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_doc_topics_dirty_del AFTER DELETE ON doc_topics
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;

        CREATE TRIGGER IF NOT EXISTS trg_tree_nodes_dirty_ins AFTER INSERT ON retrieval_tree_nodes
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_tree_nodes_dirty_upd AFTER UPDATE ON retrieval_tree_nodes
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        CREATE TRIGGER IF NOT EXISTS trg_tree_nodes_dirty_del AFTER DELETE ON retrieval_tree_nodes
        BEGIN
            UPDATE tree_metadata SET value = '0' WHERE key = 'is_valid';
        END;
        """)

        dest_conn.commit()
        dest_conn.close()
        dest_conn = None

        # Verify source database did not mutate during build
        fp_after = get_source_binding_fingerprint(src_path)
        if fp_after != fp_before:
            if temp_dest.exists():
                try:
                    temp_dest.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Source database modified during tree build; refusing publication ({src_path})")

        # Atomic publication: refuse replacement at publication operation (no-clobber).
        try:
            os.link(str(temp_dest), str(dest_path))
            temp_dest.unlink()
        except FileExistsError:
            if temp_dest.exists():
                try:
                    temp_dest.unlink()
                except OSError:
                    pass
            raise FileExistsError(f"shadow_db must be a new destination (file appeared concurrently: {dest_path})")

        result = {
            "status": "success",
            "shadow_db": str(dest_path),
            "source_db": str(src_path),
            "taxonomy_version": TAXONOMY_VERSION,
            "taxonomy_hash": tax_hash,
            "total_documents": total_docs,
            "total_chunks": total_chunks,
            "classified_documents": len(classified_docs),
            "unclassified_documents": unclass_docs,
            "classified_chunks": len(classified_chunks),
            "unclassified_chunks": unclass_chunks,
            "doc_topic_links": len(doc_topic_counts),
        }
        return result

    except Exception:
        if dest_conn is not None:
            try:
                dest_conn.rollback()
                dest_conn.close()
            except Exception:
                pass
        if temp_dest.exists():
            try:
                temp_dest.unlink()
            except OSError:
                pass
        raise
    finally:
        if src_conn is not None:
            src_conn.close()


class TreeSearcher:
    """Retrieval searcher on persistent tree with read-only query connections.

    Supports candidate filtering using persisted taxonomy and document/chunk
    memberships, while preserving explicit document scopes and global fallback.
    In sidecar mode, queries original source database for chunks/vectors and
    validates the source binding fingerprint on each request.
    """

    def __init__(
        self,
        db_path: Union[Path, str],
        source_db: Optional[Union[Path, str]] = None,
        src_dir: Optional[Path] = None,
        data_dir: Optional[Path] = None,
    ):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(f"Tree shadow database not found: {self.db_path}")

        if source_db is not None:
            self.source_db_path = Path(source_db).resolve()
            self.is_sidecar = True
        else:
            self.source_db_path = self.db_path
            self.is_sidecar = False

        # Initialize underlying hybrid searcher pointing to the source database
        self._searcher = HybridSearcher(self.source_db_path, src_dir=src_dir, data_dir=data_dir)
        self._lock = threading.Lock()
        self._local = threading.local()
        self.last_topic_routing: Optional[Dict[str, Any]] = None
        self._canonical_tax_hash = compute_taxonomy_hash()

        # Enforce read-only connection factory on hybrid searcher with connection-local scoping
        self._bind_readonly_searcher_connections()

    def _bind_readonly_searcher_connections(self) -> None:
        """Bind searcher connections to read-only URI mode=ro with connection-local scoping."""
        def get_ro_connection() -> sqlite3.Connection:
            uri = f"file:{self.source_db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row

            scoped = getattr(self._local, "scoped_chunk_ids", None)
            if scoped:
                in_clause = ",".join(str(int(x)) for x in scoped)
                conn.execute(
                    f"CREATE TEMP VIEW chunks AS SELECT * FROM main.chunks WHERE id IN ({in_clause})"
                )
            return conn

        self._searcher.get_connection = get_ro_connection
        self._searcher.connect = get_ro_connection

    def get_connection(self) -> sqlite3.Connection:
        """Open a read-only query connection to the sidecar tree database."""
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _verify_tree_integrity(self, conn: sqlite3.Connection) -> bool:
        """Fast integrity, binding and staleness check without scanning entire corpus."""
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            required_tables = {"taxonomy_nodes", "doc_topics", "chunk_topics", "retrieval_tree_nodes", "tree_metadata"}
            if not required_tables.issubset(tables):
                return False

            meta_rows = dict(conn.execute("SELECT key, value FROM tree_metadata").fetchall())
            if meta_rows.get("is_valid") != "1":
                return False
            if meta_rows.get("taxonomy_version") != TAXONOMY_VERSION:
                return False
            if meta_rows.get("taxonomy_hash") != self._canonical_tax_hash:
                return False

            # Validate canonical taxonomy from actual database nodes
            if compute_taxonomy_hash_from_db(conn) != self._canonical_tax_hash:
                return False

            # Validate source binding if in sidecar mode or if source binding was recorded
            if self.is_sidecar or ("source_db_path" in meta_rows):
                stored_src = meta_rows.get("source_db_path")
                stored_fp = meta_rows.get("source_db_fingerprint")
                if not stored_src or not stored_fp:
                    return False
                if Path(stored_src).resolve() != self.source_db_path:
                    return False

                current_fp = get_source_binding_fingerprint(self.source_db_path)
                try:
                    expected_fp = json.loads(stored_fp)
                    if _normalize_fp(current_fp) != _normalize_fp(expected_fp):
                        return False
                except Exception:
                    return False

            return True
        except Exception:
            return False

    def search_hybrid(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
        is_broad: bool = False,
        max_per_book: Optional[int] = None,
        document_id: Optional[int] = None,
        document_ids: Optional[Collection[int]] = None,
        enable_topic_routing: bool = True,
    ) -> List[Dict[str, Any]]:
        """Search hybrid with topic routing and candidate filtering.

        Preserves existing HybridSearcher result fields, explicit scope authority,
        and observable last_topic_routing state with candidate chunk counts.
        """
        query_clean = normalize_vietnamese_text(query)
        if not query_clean:
            return []

        # 1. Explicit document scope takes priority
        if document_id is not None or document_ids is not None or not enable_topic_routing:
            self._local.scoped_chunk_ids = None
            candidate_docs = (
                [document_id]
                if document_id is not None
                else (sorted(list(set(document_ids))) if document_ids else [])
            )
            self.last_topic_routing = {
                "applied": False,
                "explicit_scope": (document_id is not None or document_ids is not None),
                "document_id": document_id,
                "candidate_doc_ids": candidate_docs,
                "candidate_chunk_count": 0,
                "candidate_doc_count": 1 if document_id is not None else len(candidate_docs),
                "matched_topics": [],
                "parent_ids": [],
                "parent_labels": [],
                "fallback_to_global": False,
                "fallback_reason": None,
                "reason": "explicit_scope" if (document_id is not None or document_ids is not None) else "routing_disabled",
            }
            self._searcher.last_topic_routing = self.last_topic_routing
            return self._searcher._execute_hybrid_search(
                query_clean=query_clean,
                top_k=top_k,
                embed_model=embed_model,
                is_broad=is_broad,
                max_per_book=max_per_book,
                document_id=document_id,
                document_ids=document_ids,
            )

        # 2. Check tree integrity and freshness on read-only connection
        with closing(self.get_connection()) as conn:
            if not self._verify_tree_integrity(conn):
                logger.warning("Tree integrity check failed, unbound or stale; falling back to global search.")
                self._local.scoped_chunk_ids = None
                self.last_topic_routing = {
                    "applied": False,
                    "explicit_scope": False,
                    "document_id": None,
                    "candidate_doc_ids": [],
                    "candidate_chunk_count": 0,
                    "candidate_doc_count": 0,
                    "matched_topics": [],
                    "parent_ids": [],
                    "parent_labels": [],
                    "fallback_to_global": True,
                    "fallback_reason": "stale_or_invalid_tree",
                    "reason": "stale_or_invalid_tree",
                }
                self._searcher.last_topic_routing = self.last_topic_routing
                return self._searcher._execute_hybrid_search(
                    query_clean=query_clean,
                    top_k=top_k,
                    embed_model=embed_model,
                    is_broad=is_broad,
                    max_per_book=max_per_book,
                    document_id=None,
                    document_ids=None,
                )

            # 3. Match topics from query
            hierarchy = infer_query_topic_hierarchy(query_clean)
            matched_children = hierarchy.get("child_topics", [])
            matched_parents = hierarchy.get("parent_ids", [])
            matched_parent_labels = hierarchy.get("parent_labels", [])

            if not matched_children and not matched_parents:
                # Unrecognized or general query -> global search
                self._local.scoped_chunk_ids = None
                self.last_topic_routing = {
                    "applied": False,
                    "explicit_scope": False,
                    "document_id": None,
                    "candidate_doc_ids": [],
                    "candidate_chunk_count": 0,
                    "candidate_doc_count": 0,
                    "matched_topics": [],
                    "parent_ids": [],
                    "parent_labels": [],
                    "fallback_to_global": True,
                    "fallback_reason": "unrecognized_topic",
                    "reason": "unrecognized_topic",
                }
                self._searcher.last_topic_routing = self.last_topic_routing
                return self._searcher._execute_hybrid_search(
                    query_clean=query_clean,
                    top_k=top_k,
                    embed_model=embed_model,
                    is_broad=is_broad,
                    max_per_book=max_per_book,
                    document_id=None,
                    document_ids=None,
                )

            # 4. Resolve candidate chunk IDs from persisted chunk_topics precisely
            if matched_children:
                topic_ids = [normalized(c).replace(" ", "_") for c in matched_children]
                placeholders = ",".join("?" for _ in topic_ids)
                cand_rows = conn.execute(
                    f"SELECT DISTINCT chunk_id, doc_id FROM chunk_topics WHERE topic_id IN ({placeholders})",
                    topic_ids,
                ).fetchall()
            else:
                placeholders = ",".join("?" for _ in matched_parents)
                cand_rows = conn.execute(
                    f"SELECT DISTINCT chunk_id, doc_id FROM chunk_topics WHERE parent_id IN ({placeholders})",
                    matched_parents,
                ).fetchall()

            candidate_chunk_ids = [r["chunk_id"] for r in cand_rows]
            candidate_doc_ids = sorted(list({r["doc_id"] for r in cand_rows}))
            if candidate_doc_ids:
                with closing(self._searcher.get_connection()) as src_conn:
                    fresh_set = self._searcher._fresh_doc_id_set(src_conn, candidate_doc_ids)
                if fresh_set is not None:
                    candidate_chunk_ids = [r["chunk_id"] for r in cand_rows if r["doc_id"] in fresh_set]
                    candidate_doc_ids = sorted(list({r["doc_id"] for r in cand_rows if r["doc_id"] in fresh_set}))

        matched_topics_list = list(matched_children) if matched_children else list(matched_parents)

        if not candidate_chunk_ids:
            # Topic recognized but no candidate chunks found -> global search fallback
            self._local.scoped_chunk_ids = None
            self.last_topic_routing = {
                "applied": False,
                "explicit_scope": False,
                "document_id": None,
                "candidate_doc_ids": [],
                "candidate_chunk_count": 0,
                "candidate_doc_count": 0,
                "matched_topics": matched_topics_list,
                "parent_ids": list(matched_parents),
                "parent_labels": list(matched_parent_labels),
                "fallback_to_global": True,
                "fallback_reason": "empty_topic_candidates",
                "reason": "empty_candidates",
            }
            self._searcher.last_topic_routing = self.last_topic_routing
            return self._searcher._execute_hybrid_search(
                query_clean=query_clean,
                top_k=top_k,
                embed_model=embed_model,
                is_broad=is_broad,
                max_per_book=max_per_book,
                document_id=None,
                document_ids=None,
            )

        # 5. Scoped search across candidate chunks using connection-local scope adapter
        self._local.scoped_chunk_ids = set(candidate_chunk_ids)
        try:
            scoped_results = self._searcher._execute_hybrid_search(
                query_clean=query_clean,
                top_k=top_k,
                embed_model=embed_model,
                is_broad=is_broad,
                max_per_book=max_per_book,
                document_id=None,
                document_ids=candidate_doc_ids,
            )
        finally:
            self._local.scoped_chunk_ids = None

        # 6. Verify evidence adequacy
        if has_relevant_evidence(query_clean, scoped_results):
            self.last_topic_routing = {
                "applied": True,
                "explicit_scope": False,
                "document_id": None,
                "candidate_doc_ids": candidate_doc_ids,
                "candidate_chunk_count": len(candidate_chunk_ids),
                "candidate_doc_count": len(candidate_doc_ids),
                "matched_topics": matched_topics_list,
                "parent_ids": list(matched_parents),
                "parent_labels": list(matched_parent_labels),
                "fallback_to_global": False,
                "fallback_reason": None,
                "reason": "scoped_evidence_sufficient",
            }
            self._searcher.last_topic_routing = self.last_topic_routing
            return scoped_results

        # 7. Fallback to global search if scoped evidence is inadequate
        self._local.scoped_chunk_ids = None
        self.last_topic_routing = {
            "applied": False,
            "explicit_scope": False,
            "document_id": None,
            "candidate_doc_ids": candidate_doc_ids,
            "candidate_chunk_count": len(candidate_chunk_ids),
            "candidate_doc_count": len(candidate_doc_ids),
            "matched_topics": matched_topics_list,
            "parent_ids": list(matched_parents),
            "parent_labels": list(matched_parent_labels),
            "fallback_to_global": True,
            "fallback_reason": "scoped_evidence_inadequate",
            "reason": "insufficient_evidence",
        }
        self._searcher.last_topic_routing = self.last_topic_routing
        return self._searcher._execute_hybrid_search(
            query_clean=query_clean,
            top_k=top_k,
            embed_model=embed_model,
            is_broad=is_broad,
            max_per_book=max_per_book,
            document_id=None,
            document_ids=None,
        )

    def catalog(self) -> Dict[str, Any]:
        """Return hierarchical catalog with persisted document and chunk counts."""
        with closing(self.get_connection()) as conn:
            if not self._verify_tree_integrity(conn):
                raise ValueError("Tree metadata is stale or corrupted.")

            meta_rows = conn.execute("SELECT key, value FROM tree_metadata").fetchall()
            meta = {r["key"]: r["value"] for r in meta_rows}

            nodes_rows = conn.execute("SELECT node_id, parent_id, level, label, aliases_json FROM taxonomy_nodes ORDER BY level, node_id").fetchall()

            doc_counts = dict(conn.execute("SELECT topic_id, COUNT(DISTINCT doc_id) FROM doc_topics GROUP BY topic_id").fetchall())
            chunk_counts = dict(conn.execute("SELECT topic_id, COUNT(DISTINCT chunk_id) FROM chunk_topics GROUP BY topic_id").fetchall())

            p_doc_counts = dict(conn.execute("SELECT parent_id, COUNT(DISTINCT doc_id) FROM doc_topics GROUP BY parent_id").fetchall())
            p_chunk_counts = dict(conn.execute("SELECT parent_id, COUNT(DISTINCT chunk_id) FROM chunk_topics GROUP BY parent_id").fetchall())

            parents: Dict[str, Dict[str, Any]] = {}
            for row in nodes_rows:
                if row["level"] == 1:
                    parents[row["node_id"]] = {
                        "parent_id": row["node_id"],
                        "parent_label": row["label"],
                        "aliases": json.loads(row["aliases_json"]),
                        "document_count": p_doc_counts.get(row["node_id"], 0),
                        "chunk_count": p_chunk_counts.get(row["node_id"], 0),
                        "children": [],
                    }

            for row in nodes_rows:
                if row["level"] == 2:
                    p_id = row["parent_id"]
                    t_id = row["node_id"]
                    if p_id in parents:
                        parents[p_id]["children"].append({
                            "topic_id": t_id,
                            "topic": row["label"],
                            "aliases": json.loads(row["aliases_json"]),
                            "document_count": doc_counts.get(t_id, 0),
                            "chunk_count": chunk_counts.get(t_id, 0),
                        })

            return {
                "taxonomy_version": meta.get("taxonomy_version", TAXONOMY_VERSION),
                "taxonomy_hash": meta.get("taxonomy_hash", self._canonical_tax_hash),
                "built_at": meta.get("built_at"),
                "total_documents": int(meta.get("doc_count", 0)),
                "total_chunks": int(meta.get("chunk_count", 0)),
                "classified_documents": int(meta.get("classified_docs_count", 0)),
                "unclassified_documents": int(meta.get("unclassified_docs_count", 0)),
                "classified_chunks": int(meta.get("classified_chunks_count", 0)),
                "unclassified_chunks": int(meta.get("unclassified_chunks_count", 0)),
                "domains": list(parents.values()),
            }


def read_source_bound_tree_memberships(
    source_db_path: Union[Path, str],
    sidecar_path: Optional[Union[Path, str]] = None,
) -> Optional[Dict[str, Set[int]]]:
    """Read topic-to-document memberships from a verified source-bound sidecar tree database.

    Returns None if sidecar is absent, invalid, unbound, or if the source database has been mutated.
    """
    src_p = Path(source_db_path).resolve()
    if not src_p.is_file():
        return None

    if sidecar_path is None:
        sc_p = src_p.with_name("retrieval_tree.db")
    else:
        sc_p = Path(sidecar_path).resolve()

    if not sc_p.is_file():
        return None

    try:
        uri = f"file:{sc_p}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if not {"doc_topics", "tree_metadata", "taxonomy_nodes"}.issubset(tables):
                return None

            meta = dict(conn.execute("SELECT key, value FROM tree_metadata").fetchall())
            if meta.get("is_valid") != "1" or meta.get("taxonomy_version") != TAXONOMY_VERSION:
                return None

            # Verify binding to source_db_path
            stored_src = meta.get("source_db_path")
            stored_fp = meta.get("source_db_fingerprint")
            if not stored_src or not stored_fp:
                return None
            if Path(stored_src).resolve() != src_p:
                return None

            current_fp = get_source_binding_fingerprint(src_p)
            expected_fp = json.loads(stored_fp)
            if _normalize_fp(current_fp) != _normalize_fp(expected_fp):
                return None

            # Read memberships: topic_label -> set(doc_ids)
            rows = conn.execute("SELECT topic_label, doc_id FROM doc_topics").fetchall()
            members: Dict[str, Set[int]] = {}
            for r in rows:
                lbl = r["topic_label"]
                did = r["doc_id"]
                members.setdefault(lbl, set()).add(did)

            return members
    except Exception as exc:
        logger.debug("Failed to read source-bound tree memberships: %s", exc)
        return None


get_source_bound_tree_memberships = read_source_bound_tree_memberships


def get_tree_catalog(db_path: Union[Path, str], source_db: Optional[Union[Path, str]] = None) -> Dict[str, Any]:
    """Standalone helper to retrieve catalog from a shadow database."""
    searcher = TreeSearcher(db_path, source_db=source_db)
    return searcher.catalog()
