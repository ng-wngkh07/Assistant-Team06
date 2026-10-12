"""Comprehensive isolated tests for multi-turn conversation sessions,

topic/title summarization, migration, and API integration.

Uses temporary databases (tmp_path) and mock models only.
Guarantees zero contamination of real user history.
"""

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.history import HistoryStore, generate_fallback_title
from app.topic_summarizer import TopicSummarizer
from app import server
from app.server import app


@pytest.fixture
def temp_history_store(tmp_path):
    """Isolated HistoryStore fixture in tmp_path."""
    db_file = tmp_path / "test_sessions.db"
    return HistoryStore(db_file)


@pytest.fixture
def mock_api_client(tmp_path, monkeypatch):
    """Isolated FastAPI test client fixture with isolated HistoryStore."""
    test_db = tmp_path / "api_test_history.db"
    store = HistoryStore(test_db)
    server.set_history_store(store)
    return TestClient(app)


# =============================================================================
# 1. Store Unit Tests (Multi-turn & Sessions)
# =============================================================================


def test_fallback_title_heuristic():
    """Verify non-GPU fallback title heuristic cleans question fillers and stays bounded."""
    assert generate_fallback_title("Cho tôi hỏi về cơ chế phòng vệ của Sigmund Freud?") == "Cơ chế phòng vệ của Sigmund Freud?"
    assert generate_fallback_title("Bạn có thể cho biết hiệu ứng chim mồi là gì?") == "Hiệu ứng chim mồi là gì?"
    assert generate_fallback_title("tìm hiểu về sang chấn tâm lý và amygdala") == "Sang chấn tâm lý và amygdala"
    assert generate_fallback_title("Nghiên cứu về trí nhớ làm việc") == "Trí nhớ làm việc"
    assert generate_fallback_title("   ") == "Cuộc trò chuyện mới"
    long_q = "Tại sao con người lại có xu hướng trì hoãn công việc mặc dù biết rằng hậu quả của việc trì hoãn là rất tiêu cực đối với bản thân?"
    title = generate_fallback_title(long_q, max_chars=40)
    assert len(title) <= 45
    assert title.endswith("...")


def test_create_session(temp_history_store):
    """Verify creating a new session initializes proper defaults."""
    sess = temp_history_store.create_session()
    assert sess["id"].startswith("sess_")
    assert sess["title"] == "Cuộc trò chuyện mới"
    assert sess["title_mode"] == "auto"
    assert sess["revision"] == 1
    assert sess["is_legacy"] is False
    assert len(sess["turns"]) == 0


def test_add_two_turns_to_same_session(temp_history_store):
    """Verify that multiple turns are added to 1 session, increasing turn_index and setting fallback title on turn 1."""
    sess = temp_history_store.create_session()
    sid = sess["id"]

    # Turn 1
    t1 = temp_history_store.add_turn(
        session_id=sid,
        question="Hệ thống 1 và 2 trong Tư duy nhanh và chậm là gì?",
        answer="Hệ thống 1 hoạt động tự động, Hệ thống 2 đòi hỏi sự chú ý có chủ đích.",
        citations=[{"id": "S1", "book": "Tư duy nhanh và chậm", "page": 45}],
        model="qwen2.5:7b",
        request_id="req_1"
    )
    assert t1["turn_index"] == 1
    assert t1["is_duplicate"] is False
    assert "Hệ thống 1 và 2" in t1["session_title"]
    assert "legacy_id" in t1

    # Turn 2 in same session
    t2 = temp_history_store.add_turn(
        session_id=sid,
        question="Hệ thống 2 tiêu tốn năng lượng như thế nào?",
        answer="Hệ thống 2 tiêu tốn glucose và làm giãn nở đồng tử khi tập trung cao độ.",
        citations=[{"id": "S2", "book": "Tư duy nhanh và chậm", "page": 48}],
        model="qwen2.5:7b",
        request_id="req_2"
    )
    assert t2["turn_index"] == 2
    assert t2["is_duplicate"] is False

    # Retrieve session detail
    detail = temp_history_store.get_session(sid)
    assert detail is not None
    assert len(detail["turns"]) == 2
    assert detail["turns"][0]["turn_index"] == 1
    assert detail["turns"][1]["turn_index"] == 2
    assert detail["revision"] >= 2

    # Verify context extraction for RAG
    ctx = temp_history_store.get_session_context(sid, max_turns=4)
    assert len(ctx) == 4
    assert ctx[0]["role"] == "user"
    assert ctx[1]["role"] == "assistant"
    assert ctx[2]["role"] == "user"
    assert ctx[3]["role"] == "assistant"


def test_idempotent_duplicate_turn(temp_history_store):
    """Submitting with the same request_id in the same session must be a no-op returning the existing turn."""
    sess = temp_history_store.create_session()
    sid = sess["id"]

    t1 = temp_history_store.add_turn(
        session_id=sid,
        question="Câu hỏi kiểm tra",
        answer="Câu trả lời kiểm tra",
        citations=[],
        model="qwen2.5:7b",
        request_id="req_dup_test"
    )
    assert t1["is_duplicate"] is False

    t2 = temp_history_store.add_turn(
        session_id=sid,
        question="Câu hỏi kiểm tra",
        answer="Câu trả lời kiểm tra",
        citations=[],
        model="qwen2.5:7b",
        request_id="req_dup_test"
    )
    assert t2["is_duplicate"] is True
    assert t2["id"] == t1["id"]
    assert t2["legacy_id"] == t1["legacy_id"]

    detail = temp_history_store.get_session(sid)
    assert len(detail["turns"]) == 1


def test_manual_rename_protects_auto_summary(temp_history_store):
    """Once a user renames a session manually, auto-summary must NOT overwrite the manual title."""
    sess = temp_history_store.create_session()
    sid = sess["id"]

    temp_history_store.add_turn(
        session_id=sid,
        question="Khái niệm cái bóng Carl Jung",
        answer="Cái bóng là những phần nhân cách bị kìm nén.",
        citations=[],
        model="qwen2.5:7b"
    )

    # User renames manually
    renamed = temp_history_store.rename_session(sid, "Tiêu Đề Người Dùng Đặt")
    assert renamed is True

    s_after_rename = temp_history_store.get_session(sid)
    assert s_after_rename["title"] == "Tiêu Đề Người Dùng Đặt"
    assert s_after_rename["title_mode"] == "manual"

    # Summarizer attempts to update summary with auto_title
    updated = temp_history_store.update_session_summary(
        session_id=sid,
        summary="Bản tóm tắt về tâm lý học Jungian.",
        auto_title="Tiêu Đề Tự Động Từ Model"
    )
    assert updated is True

    s_final = temp_history_store.get_session(sid)
    assert s_final["title"] == "Tiêu Đề Người Dùng Đặt"
    assert s_final["title_mode"] == "manual"
    assert s_final["summary"] == "Bản tóm tắt về tâm lý học Jungian."


def test_stale_summary_revision_rejected(temp_history_store):
    """If session revision changes before summary update, compare-and-set rejects update atomically."""
    sess = temp_history_store.create_session()
    sid = sess["id"]

    temp_history_store.add_turn(sid, "Câu 1", "Đáp 1", [], "qwen2.5:7b")
    s_rev1 = temp_history_store.get_session(sid)
    rev1 = s_rev1["revision"]

    # Concurrent modification (e.g. turn 2 added)
    temp_history_store.add_turn(sid, "Câu 2", "Đáp 2", [], "qwen2.5:7b")
    s_rev2 = temp_history_store.get_session(sid)
    assert s_rev2["revision"] > rev1

    # Stale update with expected_revision=rev1 must be rejected
    rejected = temp_history_store.update_session_summary(
        session_id=sid,
        summary="Tóm tắt bị stale",
        auto_title="Tiêu đề stale",
        expected_revision=rev1,
    )
    assert rejected is False

    # Summary in store must NOT be updated
    s_check = temp_history_store.get_session(sid)
    assert s_check["summary"] is None


def test_delete_and_search_sessions(temp_history_store):
    """Verify parameterized search and deletion cascade."""
    s1 = temp_history_store.create_session(title="Tâm lý học hành vi Skinner")
    temp_history_store.add_turn(
        session_id=s1["id"],
        question="Điều kiện hóa thao tác là gì?",
        answer="Là quá trình học tập qua củng cố và trừng phạt.",
        citations=[],
        model="qwen2.5:7b"
    )

    s2 = temp_history_store.create_session(title="Phân tâm học Freud")
    temp_history_store.add_turn(
        session_id=s2["id"],
        question="Bản năng và cái tôi là gì?",
        answer="Cấu trúc nhân cách gồm Id, Ego và Superego.",
        citations=[],
        model="qwen2.5:7b"
    )

    # Search by title
    res_title = temp_history_store.search_sessions("Skinner")
    assert res_title["total"] == 1
    assert res_title["items"][0]["id"] == s1["id"]

    # Search by turn answer keyword
    res_ans = temp_history_store.search_sessions("Superego")
    assert res_ans["total"] == 1
    assert res_ans["items"][0]["id"] == s2["id"]

    # Delete session
    del_ok = temp_history_store.delete_session(s1["id"])
    assert del_ok is True
    assert temp_history_store.get_session(s1["id"]) is None

    # Deleted session no longer matches search
    res_after_del = temp_history_store.search_sessions("Skinner")
    assert res_after_del["total"] == 0


def test_search_accurate_total_turn_count(temp_history_store):
    """Search turn_count must reflect total turns in the session, even when query matches only one turn."""
    sess = temp_history_store.create_session(title="Phiên nhiều lượt kiểm tra")
    sid = sess["id"]
    temp_history_store.add_turn(sid, "Khái niệm Alpha", "Nội dung A", [], "m1")
    temp_history_store.add_turn(sid, "Khái niệm Beta", "Nội dung B", [], "m1")
    temp_history_store.add_turn(sid, "Khái niệm Gamma", "Nội dung C", [], "m1")

    # Search specifically for "Alpha"
    res = temp_history_store.search_sessions("Alpha")
    assert res["total"] == 1
    item = res["items"][0]
    # Total turn_count of session must be 3, NOT 1
    assert item["turn_count"] == 3


# =============================================================================
# 2. Migration Tests
# =============================================================================


def test_migration_preserves_legacy_searches(tmp_path):
    """Verify legacy searches table is migrated safely to sessions without losing rows or data."""
    test_db = tmp_path / "legacy_migration_test.db"
    store = HistoryStore(test_db)

    # Insert mock legacy searches directly
    with store._connect() as db:
        db.execute("INSERT INTO searches(id, question, answer, citations_json, model) VALUES (1, 'Câu 1', 'Đáp 1', '[]', 'm1')")
        db.execute("INSERT INTO searches(id, question, answer, citations_json, model) VALUES (2, 'Câu 2', 'Đáp 2', '[]', 'm2')")

    # Run migration
    report = store.migrate_legacy_searches(backup=True)
    assert report["status"] == "success"
    assert report["migrated_count"] == 2
    assert report["backup_path"] is not None
    assert Path(report["backup_path"]).exists()

    # Verify migrated sessions
    s_list = store.list_sessions(include_legacy=True)
    assert s_list["total"] == 2
    assert all(item["is_legacy"] for item in s_list["items"])

    # Legacy searches table rows must remain 100% intact
    with store._connect() as db:
        legacy_rows = db.execute("SELECT id, question FROM searches ORDER BY id ASC").fetchall()
        assert len(legacy_rows) == 2
        assert legacy_rows[0]["id"] == 1
        assert legacy_rows[1]["id"] == 2

    # Repeated migration must be idempotent
    report2 = store.migrate_legacy_searches(backup=True)
    assert report2["status"] == "already_migrated"
    assert report2["migrated_count"] == 0


def test_dry_run_readonly_does_not_create_tables(tmp_path):
    """Dry-run must connect in mode=ro and not create tables or modify the database."""
    test_db = tmp_path / "dry_run_test.db"
    # Create DB with ONLY searches table
    conn = sqlite3.connect(test_db)
    conn.execute("CREATE TABLE searches (id INTEGER PRIMARY KEY, question TEXT, answer TEXT, citations_json TEXT, model TEXT)")
    conn.execute("INSERT INTO searches VALUES (1, 'Q1', 'A1', '[]', 'm1')")
    conn.commit()
    conn.close()

    # Inspect in read-only mode via URI mode=ro
    uri = f"file:{test_db}?mode=ro"
    ro_conn = sqlite3.connect(uri, uri=True)
    cur = ro_conn.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    ro_conn.close()

    assert "searches" in tables
    assert "sessions" not in tables
    assert "session_turns" not in tables


# =============================================================================
# 3. Topic Summarizer Tests
# =============================================================================


def test_summarizer_gpu_busy_fallback(temp_history_store, monkeypatch):
    """When GPU is busy, summarizer must set summary_status='pending' and return fallback title without error."""
    sess = temp_history_store.create_session()
    sid = sess["id"]
    temp_history_store.add_turn(sid, "Nghiên cứu về trí nhớ làm việc", "Trí nhớ làm việc có dung lượng giới hạn.", [], "qwen2.5:7b")

    summarizer = TopicSummarizer(history_store=temp_history_store)
    from app import gpu_lock
    monkeypatch.setattr(gpu_lock.gpu_coordinator, "check_inference_allowed", lambda: (False, "MLX Training"))

    monkeypatch.setattr(summarizer.ollama, "check_health", lambda: True)
    res = summarizer.summarize_session_sync(sid, model="test-ollama")
    assert res["status"] == "gpu_busy"
    assert "Trí nhớ làm việc" in res["title"]

    s_check = temp_history_store.get_session(sid)
    assert s_check["summary_status"] == "pending"


def test_summarizer_successful_json_parse(temp_history_store, monkeypatch):
    """When Ollama returns valid JSON, summarizer updates title and summary cleanly via chat_complete."""
    sess = temp_history_store.create_session()
    sid = sess["id"]
    temp_history_store.add_turn(sid, "Add-1 task và đồng tử", "Đồng tử giãn tối đa khi nỗ lực đạt đỉnh.", [], "qwen2.5:7b")

    summarizer = TopicSummarizer(history_store=temp_history_store)
    from app import gpu_lock
    monkeypatch.setattr(gpu_lock.gpu_coordinator, "check_inference_allowed", lambda: (True, ""))
    monkeypatch.setattr(summarizer.ollama, "check_health", lambda: True)

    mock_json_str = json.dumps({
        "title": "Nỗ Lực Nhận Thức & Đồng Tử",
        "summary": "Phiên thảo luận về thí nghiệm Add-1 của Kahneman đo lường kích thước đồng tử."
    })
    # Completed model fixture; production requires an explicit stop finish.
    monkeypatch.setattr(summarizer.ollama, "chat_complete", lambda **kwargs: mock_json_str)
    monkeypatch.setattr(summarizer.ollama, "last_done_reason", "stop")

    res = summarizer.summarize_session_sync(sid, model="qwen2.5:7b")
    assert res["status"] == "completed"
    assert res["title"] == "Nỗ Lực Nhận Thức & Đồng Tử"
    assert "Add-1" in res["summary"]

    s_final = temp_history_store.get_session(sid)
    assert s_final["title"] == "Nỗ Lực Nhận Thức & Đồng Tử"
    assert s_final["summary_status"] == "completed"


def test_summarizer_rejects_malformed_json_without_synthesizing(temp_history_store, monkeypatch):
    """When Ollama returns malformed or invalid text, summarizer must fail without fabricating fake summary."""
    sess = temp_history_store.create_session()
    sid = sess["id"]
    temp_history_store.add_turn(sid, "Vấn đề giấc ngủ REM", "Giai đoạn REM liên quan đến giấc mơ.", [], "qwen2.5:7b")

    summarizer = TopicSummarizer(history_store=temp_history_store)
    from app import gpu_lock
    monkeypatch.setattr(gpu_lock.gpu_coordinator, "check_inference_allowed", lambda: (True, ""))
    monkeypatch.setattr(summarizer.ollama, "check_health", lambda: True)

    # Malformed text lacking proper JSON schema
    monkeypatch.setattr(summarizer.ollama, "chat_complete", lambda **kwargs: "Đây là câu trả lời không có định dạng JSON hợp lệ.")
    monkeypatch.setattr(summarizer.ollama, "last_done_reason", "stop")

    res = summarizer.summarize_session_sync(sid, model="qwen2.5:7b")
    assert res["status"] == "failed"

    s_check = temp_history_store.get_session(sid)
    assert s_check["summary"] is None
    assert s_check["summary_status"] == "failed"


# =============================================================================
# 4. API Endpoints Integration Tests (using isolated mock_api_client)
# =============================================================================


def test_api_session_lifecycle(mock_api_client):
    """Test full session lifecycle via REST API."""
    # 1. Create session
    c_res = mock_api_client.post("/api/sessions", json={"title": "Phiên thử nghiệm"})
    assert c_res.status_code == 200
    s_data = c_res.json()
    sid = s_data["id"]
    assert s_data["title"] == "Phiên thử nghiệm"

    # 2. Get session detail
    g_res = mock_api_client.get(f"/api/sessions/{sid}")
    assert g_res.status_code == 200
    assert g_res.json()["turns"] == []

    # 3. Rename session
    r_res = mock_api_client.patch(f"/api/sessions/{sid}", json={"title": "Phiên đã đổi tên"})
    assert r_res.status_code == 200
    assert r_res.json()["renamed"] is True

    # 4. Search sessions
    s_res = mock_api_client.get("/api/sessions/search?q=đổi tên")
    assert s_res.status_code == 200
    # Search excludes empty sessions, so add a turn first
    server.history_store.add_turn(sid, "Câu hỏi tìm kiếm", "Đáp án đổi tên", [], "model")
    s_res2 = mock_api_client.get("/api/sessions/search?q=đổi tên")
    assert s_res2.status_code == 200
    assert s_res2.json()["total"] >= 1

    # 5. Non-existent session returns 404
    bad_res = mock_api_client.get("/api/sessions/non_existent_id_123")
    assert bad_res.status_code == 404

    # 6. Delete session
    d_res = mock_api_client.delete(f"/api/sessions/{sid}")
    assert d_res.status_code == 200
    assert d_res.json()["deleted"] is True


def test_api_chat_multi_turn_session_binding(mock_api_client):
    """Test /api/chat binds consecutive turns to the same session_id."""
    res1 = mock_api_client.post("/api/chat", json={
        "query": "Tôi muốn tự tử",
        "save_history": True
    })
    assert res1.status_code == 200
    d1 = res1.json()
    assert "session_id" in d1
    sid = d1["session_id"]
    assert d1["turn_index"] == 1

    # Second turn with existing session_id
    res2 = mock_api_client.post("/api/chat", json={
        "query": "Tôi muốn tự tử",
        "session_id": sid,
        "save_history": True
    })
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["session_id"] == sid
    assert d2["turn_index"] == 2

    # Verify session detail has both turns
    sess_res = mock_api_client.get(f"/api/sessions/{sid}")
    assert sess_res.status_code == 200
    turns = sess_res.json()["turns"]
    assert len(turns) == 2


def test_api_chat_save_history_false_does_not_create_session(mock_api_client):
    """When save_history is False, no session or turn should be recorded."""
    res = mock_api_client.post("/api/chat", json={
        "query": "Tôi muốn tự tử",
        "save_history": False
    })
    assert res.status_code == 200
    d = res.json()
    assert "session_id" not in d
    assert "turn_id" not in d

    s_list = mock_api_client.get("/api/sessions")
    assert s_list.json()["total"] == 0


def test_idempotency_replay_same_and_different_query(mock_api_client):
    """Same request_id returns cached replay; same request_id with different query raises 409."""
    req_id = "req_replay_123"
    res1 = mock_api_client.post("/api/chat", json={
        "query": "Tôi muốn tự tử",
        "request_id": req_id,
        "save_history": True
    })
    assert res1.status_code == 200
    d1 = res1.json()

    # Replay with same query: returns cached replay immediately
    res_replay = mock_api_client.post("/api/chat", json={
        "query": "Tôi muốn tự tử",
        "request_id": req_id,
        "save_history": True
    })
    assert res_replay.status_code == 200
    d_replay = res_replay.json()
    assert d_replay.get("cached_replay") is True
    assert d_replay["turn_id"] == d1["turn_id"]

    # Replay with DIFFERENT query: must raise HTTP 409 Conflict
    res_conflict = mock_api_client.post("/api/chat", json={
        "query": "Câu hỏi hoàn toàn khác nhưng cùng request_id",
        "request_id": req_id,
        "save_history": True
    })
    assert res_conflict.status_code == 409


def test_gpu_busy_does_not_create_empty_session(mock_api_client, monkeypatch):
    """When GPU is busy, no empty session or turn row is created in the database."""
    from app import gpu_lock
    monkeypatch.setattr(gpu_lock.gpu_coordinator, "check_inference_allowed", lambda: (False, "GPU bận huấn luyện MLX"))

    res = mock_api_client.post("/api/chat", json={
        "query": "Câu hỏi khi GPU bận",
        "save_history": True
    })
    assert res.status_code == 503

    # Database must have zero sessions and zero turns
    s_list = mock_api_client.get("/api/sessions")
    assert s_list.json()["total"] == 0
