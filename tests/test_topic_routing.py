"""Tests for Part 4 Step 3 Topic Routing in Study Agent Q&A retrieval.

Verifies:
1. Confident topic queries (both accented and unaccented) route to relevant document candidates.
2. Ambiguous or unrecognized queries safely fall back to global library search.
3. Explicit document scope (document_id) remains authoritative and bypasses topic candidate filtering.
4. Empty topic candidates fall back safely to global search without returning empty results.
5. Inadequate scoped evidence automatically falls back to global search (avoiding silent evidence loss).
6. Multi-topic and cross-topic queries union candidates from all matched topics.
7. Topic membership cache automatically invalidates on SQLite DB commits/updates.
8. Diagnostic metadata and auditable plan are exposed on searcher and agent responses.
"""

import sqlite3
import unicodedata
import pytest
from pathlib import Path

from app.indexer import KnowledgeIndexer
from app.searcher import HybridSearcher
from app.topic_router import TopicRouter


@pytest.mark.parametrize("on_disk", [False, True])
def test_membership_cache_does_not_survive_rolled_back_updates(tmp_path, on_disk):
    conn = sqlite3.connect(tmp_path / "rollback.db" if on_disk else ":memory:")
    try:
        conn.executescript("CREATE TABLE documents(id INTEGER PRIMARY KEY);"
                           "CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, text TEXT);"
                           "INSERT INTO documents VALUES(1);"
                           "INSERT INTO chunks VALUES(1,1,'đạo hàm');")
        router = TopicRouter()
        before = router.get_topic_document_members(conn)
        conn.execute("UPDATE chunks SET text='ma trận' WHERE id=1")
        during = router.get_topic_document_members(conn)
        assert during != before
        conn.rollback()
        assert router.get_topic_document_members(conn) == before
    finally:
        conn.close()


def _setup_test_kb(tmp_path: Path):
    """Create a test knowledge base with representative documents from multiple domains."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    db = tmp_path / "kb.db"
    KnowledgeIndexer(db, src)

    docs = [
        # Doc 1: Linear algebra (Math & Logic)
        (
            1,
            "linear_algebra.txt",
            "Đại số tuyến tính",
            "Đại số tuyến tính: Ma trận vuông và định thức của ma trận. Ánh xạ tuyến tính giữa các không gian vectơ.",
        ),
        # Doc 2: Calculus (Math & Logic)
        (
            2,
            "calculus.txt",
            "Vi tích phân",
            "Giải tích và vi tích phân: Đạo hàm của hàm số liên tục và tích phân xác định theo Riemann.",
        ),
        # Doc 3: Discrete math & Logic (Math & Logic)
        (
            3,
            "discrete_math.txt",
            "Toán rời rạc",
            "Toán rời rạc và logic mệnh đề: Đại số Boole, phép đếm và hệ thức đệ quy.",
        ),
        # Doc 4: Mechanics (Natural Sciences)
        (
            4,
            "mechanics.txt",
            "Cơ học đại cương",
            "Cơ học cổ điển: Định luật Newton về chuyển động của chất điểm và bảo toàn động lượng.",
        ),
        # Doc 5: Information Theory (Computer Science)
        (
            5,
            "information_theory.txt",
            "Lý thuyết thông tin",
            "Lý thuyết thông tin: Entropy Shannon và tốc độ truyền dữ liệu qua kênh nhiễu.",
        ),
        # Doc 6: Cross-domain cryptography note (Quantum Cryptography)
        (
            6,
            "quantum_crypto.txt",
            "Mật mã học lượng tử",
            "Ứng dụng toán tử Heisenberg trong giao thức mật mã học lượng tử để phân phối khóa bảo mật.",
        ),
    ]

    with sqlite3.connect(db) as conn:
        for did, fname, title, text in docs:
            p = src / fname
            p.write_text(text, encoding="utf-8")
            conn.execute(
                "INSERT INTO documents(id, filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, status) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (did, fname, title, str(p), len(text), f"hash_{did}", 1, 1, "indexed"),
            )
            conn.execute(
                "INSERT INTO chunks(doc_id, filename, book_title, page_num, chunk_index, text) VALUES(?,?,?,?,?,?)",
                (did, fname, title, 1, 0, text),
            )

    return db, src


def test_topic_routing_accented_and_unaccented_queries(tmp_path):
    """Accented and unaccented topic queries must route to the same candidate documents."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    # 1. Accented query
    accented_q = "định thức của ma trận vuông là gì?"
    results_acc = searcher.search_hybrid(accented_q, top_k=5, embed_model=None)
    routing_acc = getattr(searcher, "last_topic_routing", None)

    assert routing_acc is not None, "searcher should expose last_topic_routing diagnostic metadata"
    assert routing_acc["applied"] is True
    assert "Đại số tuyến tính" in routing_acc["matched_topics"]
    assert 1 in routing_acc["candidate_doc_ids"]
    assert len(results_acc) > 0
    # The top result should come from candidate doc 1
    assert results_acc[0]["doc_id"] == 1
    assert routing_acc["fallback_to_global"] is False

    # 2. Unaccented query
    unaccented_q = "dinh thuc cua ma tran vuong la gi?"
    results_unacc = searcher.search_hybrid(unaccented_q, top_k=5, embed_model=None)
    routing_unacc = getattr(searcher, "last_topic_routing", None)

    assert routing_unacc is not None
    assert routing_unacc["applied"] is True
    assert "Đại số tuyến tính" in routing_unacc["matched_topics"]
    assert 1 in routing_unacc["candidate_doc_ids"]
    assert len(results_unacc) > 0
    assert results_unacc[0]["doc_id"] == 1
    assert routing_unacc["fallback_to_global"] is False


def test_topic_routing_ambiguous_and_unrecognized_terms_fallback(tmp_path):
    """Unrecognized, vague or ambiguous queries must safely fall back to global search."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    # Query with generic words not matching any topic alias in the taxonomy
    vague_q = "tổng quan về các khái niệm cơ bản và phương pháp học tập"
    results = searcher.search_hybrid(vague_q, top_k=5, embed_model=None)
    routing = getattr(searcher, "last_topic_routing", None)

    assert routing is not None
    assert routing["applied"] is False
    assert routing["fallback_to_global"] is True
    assert routing["fallback_reason"] in ("unrecognized_topic", "no_confident_topic")
    # All documents remain eligible for search; no restrictive candidate filtering was applied
    assert routing["candidate_doc_ids"] == [] or routing["candidate_doc_ids"] is None


def test_topic_routing_explicit_document_scope_is_authoritative(tmp_path):
    """When document_id is explicitly provided, topic routing candidate filtering must NOT override it."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    # User asks a linear algebra question ("ma trận"), but explicitly restricts scope to doc 6
    results = searcher.search_hybrid("ma trận", top_k=5, embed_model=None, document_id=6)
    routing = getattr(searcher, "last_topic_routing", None)

    assert routing is not None
    assert routing["explicit_scope"] is True
    assert routing["applied"] is False  # Not overridden by topic routing
    assert routing["document_id"] == 6
    # Results must only contain doc 6
    assert all(r["doc_id"] == 6 for r in results)


def test_topic_routing_empty_topic_candidates_falls_back_to_global(tmp_path):
    """When a topic is recognized but has 0 matching documents in the DB, it must fall back to global search."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    # "thiên văn học" matches child topic "Thiên văn học và quang học", but test KB has no astronomy docs
    astro_q = "khoảng cách tính theo năm ánh sáng trong thiên văn học"
    results = searcher.search_hybrid(astro_q, top_k=5, embed_model=None)
    routing = getattr(searcher, "last_topic_routing", None)

    assert routing is not None
    assert "Thiên văn học và quang học" in routing["matched_topics"]
    assert routing["fallback_to_global"] is True
    assert routing["fallback_reason"] in ("empty_topic_candidates", "no_candidates_for_topic")
    # Must not crash or raise an error due to empty candidates


def test_topic_routing_scoped_evidence_inadequate_falls_back_to_global(tmp_path):
    """When candidate documents lack matching evidence, search falls back heuristically to global to reduce risk of evidence loss (heuristic adequacy does not guarantee no evidence loss)."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    # Query mentions "ma trận" (triggers topic "Đại số tuyến tính" -> Doc 1),
    # but specifically asks for "Heisenberg" and "giao thức mật mã" which is ONLY in Doc 6!
    cross_q = "ma trận Heisenberg trong giao thức mật mã học lượng tử"
    results = searcher.search_hybrid(cross_q, top_k=5, embed_model=None)
    routing = getattr(searcher, "last_topic_routing", None)

    assert routing is not None
    assert "Đại số tuyến tính" in routing["matched_topics"]
    # Because candidate doc 1 has zero mention of Heisenberg or cryptography,
    # the searcher must detect inadequate scoped evidence and fall back to global search!
    assert routing["fallback_to_global"] is True
    assert routing["fallback_reason"] == "scoped_evidence_inadequate"
    # And Doc 6 must be retrieved via global fallback!
    assert any(r["doc_id"] == 6 for r in results)


def test_topic_routing_multi_topic_and_cross_topic_queries(tmp_path):
    """Multi-topic and cross-topic queries must union candidate documents across matched topics."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    # 1. Multi-topic query within same parent (Math & Logic: Linear Algebra + Calculus)
    multi_q = "kết hợp giữa ma trận và đạo hàm của hàm số"
    results_multi = searcher.search_hybrid(multi_q, top_k=5, embed_model=None)
    routing_multi = getattr(searcher, "last_topic_routing", None)

    assert routing_multi is not None
    assert routing_multi["applied"] is True
    assert "Đại số tuyến tính" in routing_multi["matched_topics"]
    assert "Giải tích và vi tích phân" in routing_multi["matched_topics"]
    # Candidate documents should include both doc 1 and doc 2
    assert 1 in routing_multi["candidate_doc_ids"]
    assert 2 in routing_multi["candidate_doc_ids"]

    # 2. Cross-domain query (Math & Logic vs Computer Science: Discrete Math + Information Theory)
    cross_domain_q = "quan hệ giữa đại số Boole và entropy Shannon"
    results_cross = searcher.search_hybrid(cross_domain_q, top_k=5, embed_model=None)
    routing_cross = getattr(searcher, "last_topic_routing", None)

    assert routing_cross is not None
    assert routing_cross["applied"] is True
    assert "Logic và toán rời rạc" in routing_cross["matched_topics"]
    assert "Lý thuyết thông tin" in routing_cross["matched_topics"]
    # Candidate documents should union candidates from both domains (doc 3 and doc 5)
    assert 3 in routing_cross["candidate_doc_ids"]
    assert 5 in routing_cross["candidate_doc_ids"]


def test_topic_routing_cache_invalidation_on_db_change(tmp_path):
    """Topic membership cache must invalidate on actual database changes."""
    db, src = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    # Initial check: doc 4 has Mechanics, does NOT have Chemistry
    chem_q = "nồng độ mol và dung môi trong hóa học"
    searcher.search_hybrid(chem_q, top_k=5, embed_model=None)
    routing_before = searcher.last_topic_routing
    assert "Hóa học" in routing_before["matched_topics"]
    assert routing_before["fallback_to_global"] is True
    assert routing_before["fallback_reason"] in ("empty_topic_candidates", "no_candidates_for_topic")

    # Now update doc 4 chunks to add Chemistry concepts and commit to SQLite
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE chunks SET text = 'Hóa học đại cương: Xác định nồng độ mol của chất tan trong dung dịch và dung môi.' WHERE doc_id = 4"
        )
        conn.commit()

    # Search again: the cache must automatically detect the DB change and discover doc 4 under Chemistry!
    searcher.search_hybrid(chem_q, top_k=5, embed_model=None)
    routing_after = searcher.last_topic_routing
    assert routing_after["applied"] is True
    assert "Hóa học" in routing_after["matched_topics"]
    assert 4 in routing_after["candidate_doc_ids"]
    assert routing_after["fallback_to_global"] is False

    # Also verify explicit invalidation works
    searcher.topic_router.invalidate_cache()
    members = searcher.topic_router.get_topic_document_members(searcher.get_connection(), searcher.db_path)
    assert 4 in members["Hóa học"]


def test_topic_routing_rag_agent_integration(tmp_path):
    """PsychologyAgent/StudyAgent must expose topic routing diagnostics while preserving search_scope."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    from app.rag_agent import PsychologyAgent

    agent = PsychologyAgent(searcher=searcher)
    # Perform a query through process_query_sync
    res = agent.process_query_sync("định thức ma trận vuông")

    # Diagnostic metadata must be present on searcher and in the result
    assert getattr(searcher, "last_topic_routing", None) is not None
    assert "topic_routing" in res
    assert res["topic_routing"]["applied"] is True
    assert "Đại số tuyến tính" in res["topic_routing"]["matched_topics"]

    # search_scope contract must be strictly preserved
    assert res["search_scope"] == {"kind": "library"}


import json
from unittest.mock import patch


class FakeModelClient:
    """Isolated fake model for testing streaming and diagnostics without GPU or network."""

    def check_health(self):
        return True

    def chat_stream(self, messages, model=None, **kwargs):
        yield "Định thức ma trận là giá trị đặc trưng của ma trận vuông."

    def get_embedding(self, *args, **kwargs):
        return None

    def get_batch_embeddings(self, texts, *args, **kwargs):
        return [None] * len(texts)


def test_topic_routing_json_serialization_and_streaming_trace(tmp_path):
    """All exposed diagnostics and actual streaming events must be JSON-safe without sets."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    from app.rag_agent import PsychologyAgent

    fake_model = FakeModelClient()
    agent = PsychologyAgent(searcher=searcher, ollama=fake_model)

    # 1. Routed topic query
    routed_events = list(
        agent.process_query_stream(
            query="định thức ma trận vuông",
            model="qwen2.5:7b-instruct",
            embed_model=None,
        )
    )
    assert len(routed_events) > 0
    trace_events = [e for e in routed_events if e.get("type") == "retrieval_trace"]
    done_events = [e for e in routed_events if e.get("type") == "done"]
    assert len(trace_events) == 1
    assert len(done_events) == 1

    # Every event must be serializable with standard json.dumps (as in app/server.py)
    for evt in routed_events:
        serialized = json.dumps(evt, ensure_ascii=False)
        assert serialized is not None

    trace_routing = trace_events[0]["topic_routing"]
    assert isinstance(trace_routing["candidate_doc_ids"], list)
    assert trace_routing["candidate_doc_ids"] == [1]
    assert trace_routing["applied"] is True

    done_routing = done_events[0]["topic_routing"]
    assert isinstance(done_routing["candidate_doc_ids"], list)
    assert done_routing["candidate_doc_ids"] == [1]

    # 2. Unrecognized query (fallback to global)
    unrec_events = list(
        agent.process_query_stream(
            query="phân phối khóa bảo mật trong giao thức",
            model="qwen2.5:7b-instruct",
            embed_model=None,
        )
    )
    for evt in unrec_events:
        json.dumps(evt, ensure_ascii=False)
    unrec_trace = next(e for e in unrec_events if e.get("type") == "retrieval_trace")
    assert isinstance(unrec_trace["topic_routing"]["candidate_doc_ids"], list)
    assert unrec_trace["topic_routing"]["candidate_doc_ids"] == []
    assert unrec_trace["topic_routing"]["fallback_to_global"] is True
    assert unrec_trace["topic_routing"]["fallback_reason"] == "unrecognized_topic"

    # 3. Explicit document query
    doc_events = list(
        agent.process_query_stream(
            query="định thức ma trận",
            source_document_id=1,
            model="qwen2.5:7b-instruct",
            embed_model=None,
        )
    )
    for evt in doc_events:
        json.dumps(evt, ensure_ascii=False)
    doc_trace = next(e for e in doc_events if e.get("type") == "retrieval_trace")
    assert isinstance(doc_trace["topic_routing"]["candidate_doc_ids"], list)
    assert doc_trace["topic_routing"]["candidate_doc_ids"] == [1]
    assert doc_trace["topic_routing"]["explicit_scope"] is True

    # 4. Searcher direct bypass metadata must be JSON-safe list
    searcher.search_hybrid("định thức", document_id=1, embed_model=None)
    assert isinstance(searcher.last_topic_routing["candidate_doc_ids"], list)
    assert searcher.last_topic_routing["candidate_doc_ids"] == [1]
    json.dumps(searcher.last_topic_routing, ensure_ascii=False)


def test_rag_agent_topic_routing_lifecycle_and_stale_prevention(tmp_path):
    """rag_agent must not leak stale topic routing into explicit page or early/empty requests."""
    db, _ = _setup_test_kb(tmp_path)
    searcher = HybridSearcher(db_path=db)

    from app.rag_agent import PsychologyAgent

    fake_model = FakeModelClient()
    agent = PsychologyAgent(searcher=searcher, ollama=fake_model)

    # Step 1: Routed library query
    events_1 = list(
        agent.process_query_stream(
            query="định thức ma trận vuông",
            model="qwen2.5:7b-instruct",
            embed_model=None,
        )
    )
    trace_1 = next(e for e in events_1 if e.get("type") == "retrieval_trace")
    assert trace_1["topic_routing"] is not None
    assert trace_1["topic_routing"]["applied"] is True
    assert agent.last_topic_routing is not None
    assert agent.last_topic_routing["applied"] is True
    assert searcher.last_topic_routing is not None

    # Step 2: Explicit page request (bypasses searcher hybrid search)
    events_2 = list(
        agent.process_query_stream(
            query="ma trận vuông",
            source_page=(1, 1),
            model="qwen2.5:7b-instruct",
            embed_model=None,
        )
    )
    trace_2 = next(e for e in events_2 if e.get("type") == "retrieval_trace")
    # Must NOT have stale topic routing from Step 1
    assert trace_2["topic_routing"] is None
    assert agent.last_topic_routing is None
    assert searcher.last_topic_routing is None

    # Step 3: Early/empty query
    events_3 = list(agent.process_query_stream(query="", model="qwen2.5:7b-instruct", embed_model=None))
    assert any(e.get("type") == "done" for e in events_3)
    assert agent.last_topic_routing is None
    assert searcher.last_topic_routing is None


def test_in_memory_fingerprint_detects_inplace_updates_without_count_change():
    """In-memory membership tracking must detect in-place updates where row-count and max(id) are unchanged."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, text TEXT)")
    conn.execute("INSERT INTO chunks VALUES (1, 1, 'ma trận đại số tuyến tính')")
    conn.commit()

    router = TopicRouter()
    p1 = router.plan_routing("ma trận", conn=conn)
    assert p1["candidate_doc_ids"] == [1]

    # In-place UPDATE: id remains 1, count remains 1, max(id) remains 1
    conn.execute("UPDATE chunks SET text = 'Cơ học cổ điển và định luật Newton' WHERE id = 1")
    conn.commit()

    # Without calling invalidate_cache(), the router must detect the update
    p2 = router.plan_routing("ma trận", conn=conn)
    assert p2["candidate_doc_ids"] == []

    p3 = router.plan_routing("Cơ học", conn=conn)
    assert p3["candidate_doc_ids"] == [1]

    # Different in-memory connection must have distinct DB identity and not share stale cache
    conn2 = sqlite3.connect(":memory:")
    conn2.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, text TEXT)")
    conn2.execute("INSERT INTO chunks VALUES (1, 2, 'Lý thuyết thông tin và entropy Shannon')")
    conn2.commit()

    p_conn2 = router.plan_routing("lý thuyết thông tin", conn=conn2)
    assert p_conn2["candidate_doc_ids"] == [2]

    # Original connection still reports its own state
    p_conn1 = router.plan_routing("Cơ học", conn=conn)
    assert p_conn1["candidate_doc_ids"] == [1]


def test_topic_router_connection_lifecycle(tmp_path):
    """plan_routing must close internally created connections and preserve caller connections."""
    db, _ = _setup_test_kb(tmp_path)
    router = TopicRouter(db_path=db)

    # 1. When conn=None, plan_routing opens and closes its own connection
    real_connect = sqlite3.connect
    opened_conns = []

    def tracking_connect(*args, **kwargs):
        c = real_connect(*args, **kwargs)
        opened_conns.append(c)
        return c

    with patch("sqlite3.connect", side_effect=tracking_connect):
        plan = router.plan_routing("định thức ma trận")
        assert plan["applied"] is True

    assert len(opened_conns) == 1
    # Verify the internally-created connection was closed (cannot execute queries on closed connection)
    with pytest.raises(sqlite3.ProgrammingError):
        opened_conns[0].execute("SELECT 1")

    # 2. When caller passes an existing connection, it MUST NOT be closed
    caller_conn = sqlite3.connect(str(db))
    plan_caller = router.plan_routing("định thức ma trận", conn=caller_conn)
    assert plan_caller["applied"] is True
    # Caller connection is still valid and open
    cur = caller_conn.execute("SELECT 1")
    assert cur.fetchone()[0] == 1
    caller_conn.close()

    # 3. On error paths with internally created connection, connection is closed
    err_conns = []

    def err_tracking_connect(*args, **kwargs):
        c = real_connect(*args, **kwargs)
        err_conns.append(c)
        return c

    with patch("sqlite3.connect", side_effect=err_tracking_connect):
        with patch.object(router, "get_topic_document_members", side_effect=RuntimeError("simulated error")):
            with pytest.raises(RuntimeError, match="simulated error"):
                router.plan_routing("định thức ma trận")

    assert len(err_conns) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        err_conns[0].execute("SELECT 1")
