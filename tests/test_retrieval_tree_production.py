"""Production tests for source-bound sidecar retrieval tree and embedding optimization."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import pytest

from app.indexer import KnowledgeIndexer
from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
from app.searcher import HybridSearcher
from app.retrieval_tree import (
    TreeSearcher,
    build_tree,
    get_source_binding_fingerprint,
    read_source_bound_tree_memberships,
    TAXONOMY_VERSION,
)
from app.document_lookup import DocumentLookup
from app.learning import LearningService


@pytest.fixture
def synthetic_kb(tmp_path: Path):
    """Create a synthetic knowledge base with multi-domain documents and chunk provenance."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)
    source_db = tmp_path / "knowledge_base.db"
    KnowledgeIndexer(source_db, src_dir)

    docs = [
        (
            1,
            "doc1_linear.pdf",
            "Đại số tuyến tính",
            "Đại số tuyến tính: Không gian vectơ và ma trận vuông. Ánh xạ tuyến tính giữa các không gian.",
        ),
        (
            2,
            "doc2_calculus.docx",
            "Vi tích phân",
            "Giải tích và vi tích phân: Đạo hàm của hàm số liên tục và tích phân xác định theo Riemann.",
        ),
        (
            3,
            "doc3_unclassified.pptx",
            "Ghi chú chung",
            "Ghi chú thường ngày: Hôm nay thời tiết đẹp và trời nhiều mây.",
        ),
        (
            4,
            "doc4_cs.txt",
            "Cấu trúc dữ liệu",
            "Cấu trúc dữ liệu và giải thuật: Ngăn xếp LIFO và hàng đợi FIFO.",
        ),
    ]

    with sqlite3.connect(source_db) as conn:
        for did, fname, title, text in docs:
            (src_dir / fname).write_text(text, encoding="utf-8")
            fhash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            conn.execute(
                """INSERT INTO documents(id, filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, status, pipeline_version)
                   VALUES(?, ?, ?, ?, ?, ?, 1, 1, 'indexed', 'v1')""",
                (did, fname, title, str(src_dir / fname), len(text), fhash),
            )
            blob = struct.pack("ff", 1.0, 0.0)
            conn.execute(
                """INSERT INTO chunks(id, doc_id, book_title, filename, page_num, chunk_index, text, embedding)
                   VALUES(?, ?, ?, ?, 1, 0, ?, ?)""",
                (did, did, title, fname, text, blob),
            )
            conn.execute(
                """INSERT INTO chunks_fts(rowid, book_title, text) VALUES(?, ?, ?)""",
                (did, title, text),
            )
            conn.execute(
                """INSERT INTO corpus_vector_provenance VALUES(?, ?, ?, ?, 2, 1, 'synthetic')""",
                (
                    did,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    BGE_NATIVE_IDENTITY,
                    hashlib.sha256(blob).hexdigest(),
                ),
            )
        conn.commit()

    return source_db, tmp_path


def test_duplicate_parent_hash_removal_regression(tmp_path, monkeypatch):
    """Demonstrate removal of duplicate parent hash while preserving byte verification."""
    from app import native_embedding as mod

    client = mod.NativeEmbeddingClient(tmp_path)
    monkeypatch.setattr(client, "available", lambda *a: True)

    checks, workers = [], []

    def identity():
        checks.append("actual-byte-hash")
        return "fixture-basis"

    monkeypatch.setattr(client, "get_identity", identity)
    monkeypatch.setattr(mod.gpu_coordinator, "acquire_for_inference", lambda: nullcontext())

    def run(*args, **kwargs):
        workers.append(json.loads(kwargs["input"]))
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({"vectors": [[1.0] + [0.0] * 1023], "model_identity": "fixture-basis"}),
        )

    monkeypatch.setattr(mod.subprocess, "run", run)
    with client._query_cache_lock:
        client._query_cache.clear()

    # Cache miss: initial basis check (1) + post-generation batch check (2) = 2 checks, no 3rd duplicate check
    assert client.get_embedding("distinct query", expected_identity="fixture-basis") == [1.0] + [0.0] * 1023
    assert len(workers) == 1
    assert len(checks) == 2, "Must perform exactly 2 actual-byte checks on miss: initial basis and post-batch"

    # Cache hit: verifies actual model bytes exactly once (1)
    checks.clear()
    assert client.get_embedding("distinct query", expected_identity="fixture-basis") == [1.0] + [0.0] * 1023
    assert len(checks) == 1, "Cache hit must verify actual model bytes"
    assert len(workers) == 1

    # Overridden get_batch_embeddings returning plain list must retain post-generation check
    def custom_plain_list(texts, **kw):
        return [[1.0] + [0.0] * 1023]

    monkeypatch.setattr(client, "get_batch_embeddings", custom_plain_list)
    checks.clear()
    assert client.get_embedding("another query", expected_identity="fixture-basis") == [1.0] + [0.0] * 1023
    assert len(checks) == 2, "Plain list fallback must execute final actual-byte check"


def test_valid_source_bound_sidecar_hybrid_search(synthetic_kb):
    """HybridSearcher uses adjacent sidecar tree by default, routing candidates accurately."""
    source_db, tmp_path = synthetic_kb
    sidecar_db = tmp_path / "retrieval_tree.db"

    build_res = build_tree(source_db, sidecar_db)
    assert build_res["status"] == "success"
    assert build_res["classified_documents"] == 3

    searcher = HybridSearcher(db_path=source_db)
    results = searcher.search_hybrid("ma trận vuông và ánh xạ tuyến tính", top_k=5, embed_model=None)
    assert len(results) > 0
    assert results[0]["doc_id"] == 1

    routing = searcher.last_topic_routing
    assert routing is not None
    assert routing["applied"] is True
    assert routing["explicit_scope"] is False
    assert routing["candidate_chunk_count"] >= 1
    assert routing["candidate_doc_count"] >= 1
    assert 1 in routing["candidate_doc_ids"]
    assert routing["fallback_to_global"] is False


def test_original_changed_wal_fallback(synthetic_kb):
    """Mutations to original source DB (including WAL commit) trigger safe global fallback."""
    source_db, tmp_path = synthetic_kb
    sidecar_db = tmp_path / "retrieval_tree.db"
    build_tree(source_db, sidecar_db)

    searcher = HybridSearcher(db_path=source_db)

    # Mutate source in WAL mode
    with sqlite3.connect(source_db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("UPDATE chunks SET text='Nội dung đã thay đổi hoàn toàn' WHERE id=1")
        conn.commit()

    # Search should safely fall back to global without throwing an exception or hiding data
    results = searcher.search_hybrid("ánh xạ tuyến tính", top_k=5, embed_model=None)
    routing = searcher.last_topic_routing
    assert routing is not None
    assert routing["fallback_to_global"] is True
    assert routing["fallback_reason"] == "stale_or_invalid_tree"
    assert routing["applied"] is False


def test_explicit_scopes_authoritative_with_sidecar(synthetic_kb):
    """Explicit document scopes bypass candidate filtering even when valid sidecar exists."""
    source_db, tmp_path = synthetic_kb
    sidecar_db = tmp_path / "retrieval_tree.db"
    build_tree(source_db, sidecar_db)

    searcher = HybridSearcher(db_path=source_db)
    # Query matches linear algebra (doc 1), but user explicitly restricts to doc 4
    results = searcher.search_hybrid("ma trận vuông", document_id=4, top_k=5, embed_model=None)
    routing = searcher.last_topic_routing

    assert routing is not None
    assert routing["explicit_scope"] is True
    assert routing["applied"] is False
    assert routing["document_id"] == 4
    assert all(r["doc_id"] == 4 for r in results)


def test_missing_and_wrong_binding_rejected(synthetic_kb, tmp_path):
    """Sidecar with missing or mismatched source binding is rejected closed."""
    source_db, _ = synthetic_kb
    sidecar_db = tmp_path / "retrieval_tree.db"
    build_tree(source_db, sidecar_db)

    # Test wrong source path
    other_db = tmp_path / "other.db"
    KnowledgeIndexer(other_db)
    tree_searcher_wrong = TreeSearcher(sidecar_db, source_db=other_db)
    with tree_searcher_wrong.get_connection() as conn:
        assert not tree_searcher_wrong._verify_tree_integrity(conn)

    # Test missing binding in metadata
    with sqlite3.connect(sidecar_db) as conn:
        conn.execute("DELETE FROM tree_metadata WHERE key='source_db_path'")
        conn.commit()

    assert read_source_bound_tree_memberships(source_db, sidecar_db) is None
    tree_searcher_missing = TreeSearcher(sidecar_db, source_db=source_db)
    with tree_searcher_missing.get_connection() as conn:
        assert not tree_searcher_missing._verify_tree_integrity(conn)


def test_shared_learning_membership_and_cache_validation(synthetic_kb):
    """LearningService uses shared source-bound memberships and invalidates cache if sidecar modified/removed."""
    source_db, tmp_path = synthetic_kb
    sidecar_db = tmp_path / "retrieval_tree.db"
    build_tree(source_db, sidecar_db)

    lookup = DocumentLookup(source_db, src_dir=tmp_path / "src")
    service = LearningService(lookup, practice_service=None, searcher=None)

    # 1. Reads memberships from sidecar
    available_docs = lookup.documents()
    members = service._catalog_members(available_docs)
    assert 1 in members.get("Đại số tuyến tính", set())

    # 2. Permitted available document ID filtering
    # Restrict available docs to exclude doc 1
    restricted_docs = [d for d in available_docs if d["id"] != 1]
    restricted_members = service._catalog_members(restricted_docs)
    assert 1 not in restricted_members.get("Đại số tuyến tính", set())

    # 3. Sidecar removed: cache cannot retain sidecar data
    sidecar_db.unlink()
    # Cache must be bypassed and fall back to catalog path
    uncached_members = service._catalog_members(available_docs)
    assert 1 in uncached_members.get("Đại số tuyến tính", set())


def test_query_time_no_source_or_sidecar_writes(synthetic_kb, tmp_path):
    """Executing hybrid and tree searches performs zero writes to source and sidecar DBs."""
    source_db, _ = synthetic_kb
    sidecar_db = tmp_path / "retrieval_tree.db"
    build_tree(source_db, sidecar_db)

    fp_src_before = get_source_binding_fingerprint(source_db)
    fp_sidecar_before = get_source_binding_fingerprint(sidecar_db)

    searcher = HybridSearcher(db_path=source_db)
    for q in ["ma trận", "đạo hàm", "không gian vectơ", "ngăn xếp"]:
        searcher.search_hybrid(q, top_k=3, embed_model=None)

    fp_src_after = get_source_binding_fingerprint(source_db)
    fp_sidecar_after = get_source_binding_fingerprint(sidecar_db)

    assert fp_src_after == fp_src_before, "Source DB must not mutate during queries"
    assert fp_sidecar_after == fp_sidecar_before, "Sidecar DB must not mutate during queries"


def test_build_retrieval_tree_cli(synthetic_kb, tmp_path):
    """CLI builds target sidecar and prints status JSON cleanly."""
    source_db, _ = synthetic_kb
    output_db = tmp_path / "cli_retrieval_tree.db"

    cmd = [
        sys.executable,
        "scripts/build_retrieval_tree.py",
        "--source-db",
        str(source_db),
        "--output",
        str(output_db),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"CLI build failed: {proc.stderr}"
    data = json.loads(proc.stdout)
    assert data["status"] == "success"
    assert output_db.is_file()
