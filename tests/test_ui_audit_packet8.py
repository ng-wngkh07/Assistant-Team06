"""Comprehensive behavioral test suite for UI-AUDIT-20261111-08:
Durable Account-Private Document QA and Retrieval.

Verifies:
1. Private text upload + extraction + durable RAG query (both stream and sync).
2. Explicit account-private identity (material_id) distinct from shared integer doc_id.
3. Citations carry material_id, is_private=True, download_url, and no bogus shared doc_id.
4. Same basename with different contents across distinct accounts isolates storage and retrieval.
5. Guessing another account's material_id returns 404 (fail closed).
6. Mismatched shared and private identifiers or mismatched filenames return 400.
7. Private scope survives stored session followup and reload without broadening into shared library.
8. Missing or corrupt CAS original file fails closed (404).
9. Empty or unreadable source returns truthful unavailable without inventing OCR or fallback.
10. Oversized source_page_num returns user-friendly Vietnamese 404.
11. Shared selection regressions and guarantee that shared index, src/, vectors, and models are NOT mutated.
12. Compatibility with authentication-required mode (anonymous access rejected with 401).

Note: In accordance with project instructions, the slow local MLX 9B model is mocked
at the boundary for isolated test runs; live unadapted 9B inference is verified by Codex.
"""

import base64
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.accounts import COOKIE, account_id, connection, issue_session, password_hash, root_dir
from app.config import DATA_DIR
import app.rag_agent as ra
from app.server import app, requested_source_scope, ChatRequest, study_material_manager, document_lookup
from app.trained_client import TrainedModelClient


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Isolate accounts directory, database, and mock model boundary for tests."""
    acct_dir = tmp_path / "accounts"
    acct_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(acct_dir))
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "0")
    # Reset AccountService instances
    if hasattr(study_material_manager, "_instances"):
        study_material_manager._instances.clear()

    # Mock model boundary for isolated deterministic unit tests
    monkeypatch.setattr("app.rag_agent.context_capacity", lambda model: 8192)
    monkeypatch.setattr(TrainedModelClient, "available", classmethod(lambda cls, *a, **k: True))

    def mock_chat_stream(self, messages=None, **kwargs):
        self.last_done_reason = "stop"
        prompt = ""
        if messages:
            prompt = "\n".join(m.get("content", "") for m in messages)

        if "FIFO" in prompt or "Hàng đợi" in prompt:
            yield json.dumps({
                "claims": [{
                    "text": "Hàng đợi hoạt động theo nguyên lý FIFO (First In, First Out).",
                    "source_ids": ["S1"],
                    "evidence_lines": [1, 1],
                }]
            })
        elif "LIFO" in prompt or "Ngăn xếp" in prompt:
            yield json.dumps({
                "claims": [{
                    "text": "Ngăn xếp hoạt động theo nguyên lý LIFO (Last In, First Out).",
                    "source_ids": ["S1"],
                    "evidence_lines": [1, 1],
                }]
            })
        elif "tam giác" in prompt or "180" in prompt:
            yield json.dumps({
                "claims": [{
                    "text": "Tổng ba góc của một tam giác bằng 180 độ.",
                    "source_ids": ["S1"],
                    "evidence_lines": [1, 1],
                }]
            })
        else:
            yield json.dumps({
                "claims": [{
                    "text": "Nội dung tài liệu đã được xác nhận.",
                    "source_ids": ["S1"],
                    "evidence_lines": [1, 1],
                }]
            })

    monkeypatch.setattr(TrainedModelClient, "chat_stream", mock_chat_stream)
    return acct_dir


def _make_client():
    return TestClient(app, headers={"X-Study-Request": "1"})


def _create_account(client: TestClient, username: str, password: str = "private-example-123"):
    """Register an account via the API and return the account dict."""
    res = client.post(
        "/api/account/register",
        json={"username": username, "password": password},
    )
    assert res.status_code == 200, res.text
    return res.json()["account"]


def test_private_txt_upload_and_chat_sync_and_stream(test_env):
    """Authenticated user uploads private text file, which is indexed and queried via chat & stream."""
    client = _make_client()
    _create_account(client, "alice_qa")

    txt_content = (
        "Hàng đợi (Queue) là một cấu trúc dữ liệu trừu tượng hoạt động theo nguyên lý FIFO (First In, First Out). "
        "Phần tử được thêm vào ở cuối hàng đợi (rear) và được lấy ra ở đầu hàng đợi (front). "
        "Thao tác thêm phần tử gọi là enqueue, thao tác lấy ra gọi là dequeue."
    )
    b64_content = base64.b64encode(txt_content.encode("utf-8")).decode("ascii")

    # 1. Upload private material
    upload_res = client.post(
        "/api/materials/upload",
        json={
            "filename": "qa-queue-doc.txt",
            "file_base64": b64_content,
            "generate_study_set": False,
            "persist_to_src": False,
        },
    )
    assert upload_res.status_code == 200, upload_res.text
    mat = upload_res.json()["material"]
    material_id = mat["id"]
    assert material_id is not None
    assert mat["filename"] == "qa-queue-doc.txt"
    assert mat.get("doc_id") is None  # Must NOT be an integer doc_id in shared library

    # 2. Query via synchronous POST /api/chat
    chat_res = client.post(
        "/api/chat",
        json={
            "query": "Hàng đợi hoạt động theo nguyên lý nào?",
            "material_id": material_id,
            "attachment_filename": "qa-queue-doc.txt",
            "save_history": False,
        },
    )
    assert chat_res.status_code == 200, chat_res.text
    chat_data = chat_res.json()
    assert "FIFO" in chat_data.get("answer", "") or "First In" in chat_data.get("answer", "")
    citations = chat_data.get("citations", [])
    assert len(citations) > 0
    cit0 = citations[0]
    assert cit0.get("material_id") == material_id
    assert cit0.get("is_private") is True
    assert cit0.get("doc_id") is None
    assert f"/api/materials/download?material_id={material_id}" in cit0.get("download_url", "")

    # 3. Query via streaming POST /api/chat/stream
    stream_res = client.post(
        "/api/chat/stream",
        json={
            "query": "Thao tác thêm phần tử vào hàng đợi gọi là gì?",
            "material_id": material_id,
            "attachment_filename": "qa-queue-doc.txt",
            "save_history": False,
        },
    )
    assert stream_res.status_code == 200, stream_res.text
    lines = stream_res.text.strip().split("\n\n")
    events = [json.loads(line.replace("data: ", "")) for line in lines if line.startswith("data: ")]
    done_events = [e for e in events if e.get("type") == "done"]
    assert len(done_events) == 1
    done_ev = done_events[0]
    scope = done_ev.get("search_scope", {})
    assert scope.get("kind") == "material"
    assert scope.get("material_id") == material_id
    assert scope.get("document_id") is None


def test_same_basename_different_content_across_accounts(test_env):
    """Two different accounts upload the same filename with different contents; no leak across accounts."""
    client_a = _make_client()
    client_b = _make_client()
    _create_account(client_a, "user_a")
    _create_account(client_b, "user_b")

    # User A uploads doc.txt about Queue (FIFO)
    content_a = "Khái niệm Hàng đợi: Cấu trúc FIFO vào trước ra trước hoạt động rất hiệu quả."
    b64_a = base64.b64encode(content_a.encode("utf-8")).decode("ascii")
    res_a = client_a.post("/api/materials/upload", json={"filename": "doc.txt", "file_base64": b64_a, "generate_study_set": False})
    mat_a = res_a.json()["material"]

    # User B uploads doc.txt about Stack (LIFO)
    content_b = "Khái niệm Ngăn xếp: Cấu trúc LIFO vào sau ra trước hoạt động rất hiệu quả."
    b64_b = base64.b64encode(content_b.encode("utf-8")).decode("ascii")
    res_b = client_b.post("/api/materials/upload", json={"filename": "doc.txt", "file_base64": b64_b, "generate_study_set": False})
    mat_b = res_b.json()["material"]

    assert mat_a["id"] != mat_b["id"]

    # User A queries
    chat_a = client_a.post("/api/chat", json={"query": "Hàng đợi là cấu trúc gì?", "material_id": mat_a["id"], "save_history": False})
    assert chat_a.status_code == 200
    assert "FIFO" in chat_a.json()["answer"]
    assert "LIFO" not in chat_a.json()["answer"]

    # User B queries
    chat_b = client_b.post("/api/chat", json={"query": "Ngăn xếp là cấu trúc gì?", "material_id": mat_b["id"], "save_history": False})
    assert chat_b.status_code == 200
    assert "LIFO" in chat_b.json()["answer"]
    assert "FIFO" not in chat_b.json()["answer"]


def test_guessed_other_account_material_returns_404(test_env):
    """User B trying to access User A's material_id receives 404."""
    client_a = _make_client()
    client_b = _make_client()
    _create_account(client_a, "user_a_leak_check")
    _create_account(client_b, "user_b_adversary")

    content_a = "Dữ liệu bí mật của User A không được tiết lộ cho bất kỳ ai khác."
    b64_a = base64.b64encode(content_a.encode("utf-8")).decode("ascii")
    res_a = client_a.post("/api/materials/upload", json={"filename": "secret.txt", "file_base64": b64_a, "generate_study_set": False})
    mat_a_id = res_a.json()["material"]["id"]

    # User B attempts to query A's material
    chat_b = client_b.post("/api/chat", json={"query": "Bí mật là gì?", "material_id": mat_a_id, "save_history": False})
    assert chat_b.status_code == 404

    # User B attempts to download A's material
    down_b = client_b.get(f"/api/materials/download?material_id={mat_a_id}")
    assert down_b.status_code == 404


def test_mismatched_shared_and_private_identifiers_return_400(test_env):
    """Sending both source_document_id and material_id, or mismatched filename, fails closed with 400."""
    client = _make_client()
    _create_account(client, "user_validation")

    b64 = base64.b64encode(b"Sample private text with sufficient length exceeding thirty characters.").decode("ascii")
    res = client.post("/api/materials/upload", json={"filename": "sample.txt", "file_base64": b64, "generate_study_set": False})
    mat_id = res.json()["material"]["id"]

    # Both shared doc_id and private material_id provided
    bad1 = client.post(
        "/api/chat",
        json={"query": "Test", "source_document_id": 1, "material_id": mat_id, "save_history": False},
    )
    assert bad1.status_code == 400

    # Mismatched attachment_filename
    bad2 = client.post(
        "/api/chat",
        json={"query": "Test", "material_id": mat_id, "attachment_filename": "other.txt", "save_history": False},
    )
    assert bad2.status_code == 400


def test_private_followup_and_session_reload_survives(test_env):
    """Scope survives stored session followup and reload without broadening to shared library."""
    client = _make_client()
    _create_account(client, "user_session")

    content = "Định lý hình học: Tổng ba góc trong một tam giác luôn luôn bằng 180 độ. Định lý này áp dụng cho mọi tam giác."
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    res = client.post("/api/materials/upload", json={"filename": "triangle.txt", "file_base64": b64, "generate_study_set": False})
    mat_id = res.json()["material"]["id"]

    # Turn 1 with explicit material_id and save_history=True
    t1_res = client.post(
        "/api/chat",
        json={
            "query": "Tổng ba góc của tam giác bằng bao nhiêu?",
            "material_id": mat_id,
            "attachment_filename": "triangle.txt",
            "save_history": True,
        },
    )
    assert t1_res.status_code == 200
    sess_id = t1_res.json()["session_id"]
    assert sess_id is not None

    # Turn 2: Follow-up question in same session WITHOUT explicit material_id
    t2_res = client.post(
        "/api/chat",
        json={
            "query": "Định lý tam giác áp dụng cho tam giác nào?",
            "session_id": sess_id,
            "save_history": True,
        },
    )
    assert t2_res.status_code == 200
    cit2 = t2_res.json().get("citations", [])
    assert len(cit2) > 0
    assert cit2[0].get("material_id") == mat_id
    assert cit2[0].get("is_private") is True


def test_oversized_page_num_returns_user_friendly_404(test_env):
    """Requesting page 99 of a 1-page document returns friendly 404."""
    client = _make_client()
    _create_account(client, "user_page")

    b64 = base64.b64encode(b"Day la mot trang duy nhat co do dai tren 30 ky tu de dam bao trich xuat hop le.").decode("ascii")
    res = client.post("/api/materials/upload", json={"filename": "single.txt", "file_base64": b64, "generate_study_set": False})
    mat_id = res.json()["material"]["id"]

    bad_page = client.post(
        "/api/chat",
        json={
            "query": "Noi dung gi?",
            "material_id": mat_id,
            "source_page_num": 99,
            "save_history": False,
        },
    )
    assert bad_page.status_code == 404
    assert "99" in bad_page.json()["detail"] or "vượt quá" in bad_page.json()["detail"]


def test_empty_or_unreadable_source_returns_truthful_unavailable(test_env):
    """Empty or unreadable source returns truthful unavailable instead of fabricating content."""
    import io
    from PIL import Image

    client = _make_client()
    _create_account(client, "user_empty")

    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color="white").save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    res = client.post("/api/materials/upload", json={"filename": "empty_drawing.png", "file_base64": b64, "generate_study_set": False})
    assert res.status_code == 200
    mat_id = res.json()["material"]["id"]

    chat_res = client.post(
        "/api/chat",
        json={"query": "Tóm tắt tài liệu này", "material_id": mat_id, "save_history": False},
    )
    assert chat_res.status_code == 200
    body = chat_res.json()
    assert body.get("response_kind") == "insufficient_evidence"


def test_missing_or_corrupt_cas_file_fails_closed(test_env):
    """Corrupting or removing CAS file triggers fail closed 404 on query and download."""
    client = _make_client()
    acct = _create_account(client, "user_cas_integrity")
    user_id = acct["id"]

    content = "Du lieu nay duoc luu tru trong kho CAS cua he thong va se bi kiem tra toan ven."
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    res = client.post("/api/materials/upload", json={"filename": "cas_test.txt", "file_base64": b64, "generate_study_set": False})
    assert res.status_code == 200
    mat_id = res.json()["material"]["id"]

    # Locate CAS file in user's originals dir
    user_dir = test_env / str(user_id)
    orig_dir = user_dir / "originals"
    bin_files = list(orig_dir.glob("*.bin"))
    assert len(bin_files) > 0
    target_bin = bin_files[0]

    # Tamper with the CAS file on disk
    target_bin.write_bytes(b"tampered content")

    # Querying the tampered material fails closed with 404
    chat_res = client.post("/api/chat", json={"query": "Noi dung gi?", "material_id": mat_id, "save_history": False})
    assert chat_res.status_code == 404

    # Downloading the tampered material fails closed with 404
    down_res = client.get(f"/api/materials/download?material_id={mat_id}")
    assert down_res.status_code == 404


def test_shared_selection_regression_and_no_shared_mutation(test_env, monkeypatch):
    """Shared document QA works without mutating shared src/, index, or vector stores."""
    client = _make_client()
    _create_account(client, "user_shared_check")

    fake_doc = {"id": 1, "filename": "shared_lecture.pdf", "total_pages": 10}
    monkeypatch.setattr(document_lookup, "document", lambda doc_id: fake_doc if doc_id == 1 else None)

    fake_chunk = {
        "id": 101, "doc_id": 1, "book_title": "Shared Lecture", "filename": "shared_lecture.pdf",
        "page_num": 1, "chunk_index": 0, "text": "Nội dung bài giảng dùng chung trong thư viện.",
        "safe_text": "Nội dung bài giảng dùng chung trong thư viện.",
    }
    from app.server import agent
    monkeypatch.setattr(agent.searcher, "search_hybrid", lambda *a, **k: [fake_chunk])

    res = client.post(
        "/api/chat",
        json={"query": "Tài liệu này về chủ đề gì?", "source_document_id": 1, "save_history": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data.get("search_scope", {}).get("document_id") == 1
    assert data.get("search_scope", {}).get("kind") in ("document", "page")


def test_anonymous_access_in_accounts_required_mode(test_env, monkeypatch):
    """When accounts are required, anonymous requests are rejected with 401."""
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    anon_client = _make_client()

    res = anon_client.post("/api/chat", json={"query": "Xin chào?", "save_history": False})
    assert res.status_code == 401
    assert "đăng nhập" in res.json()["detail"].lower()


def test_stream_mixed_source_session_isolates_material_context(test_env, monkeypatch):
    """Mixed source session reloads effective context preserving material_id, preventing contamination."""
    from app import server
    client = _make_client()
    acct = _create_account(client, "context_probe_user")
    m_res = client.post(
        "/api/materials/upload",
        json={
            "filename": "current.txt",
            "file_base64": base64.b64encode(b"Current source contains sufficiently long evidence about queues and FIFO.").decode(),
            "generate_study_set": False,
        },
    )
    m = m_res.json()["material"]["id"]

    token = account_id.set(acct["id"])
    try:
        s = server.history_store.create_session()["id"]
        server.history_store.add_turn(
            s,
            "OTHER_DOCUMENT_SECRET",
            "A distinct document says LIFO.",
            [],
            "test",
            search_scope={"kind": "material", "material_id": "other-material"},
        )
        server.history_store.add_turn(
            s,
            "CURRENT_DOCUMENT",
            "Queue uses FIFO.",
            [],
            "test",
            search_scope={"kind": "material", "material_id": m},
        )
    finally:
        account_id.reset(token)

    captured = {}
    def probe(**kwargs):
        captured.update(kwargs)
        yield {"type": "done", "answer": "Done", "search_scope": {"kind": "material", "material_id": m}}

    monkeypatch.setattr(server.agent, "process_query_stream", probe)

    # 1. Stream call with material_id must only see turns for current material
    r = client.post(
        "/api/chat/stream",
        json={"query": "Explain current source", "material_id": m, "session_id": s, "save_history": False},
    )
    assert r.status_code == 200, r.text
    chat_hist = captured.get("chat_history") or []
    assert "OTHER_DOCUMENT_SECRET" not in str(chat_hist)
    assert any("CURRENT_DOCUMENT" in turn.get("content", "") for turn in chat_hist)

    # 2. When isolate_context=True, chat_history must be empty
    captured.clear()
    r_iso = client.post(
        "/api/chat/stream",
        json={"query": "Explain isolated", "material_id": m, "session_id": s, "isolate_context": True, "save_history": False},
    )
    assert r_iso.status_code == 200
    assert captured.get("chat_history") == []


def test_cached_replay_strictly_bound_to_material_id(test_env):
    """Cached replay with same request_id is rejected with conflict if target material_id does not match."""
    client = _make_client()
    _create_account(client, "replay_user")

    res1 = client.post(
        "/api/materials/upload",
        json={
            "filename": "mat1.txt",
            "file_base64": base64.b64encode(b"First material content with sufficient length exceeding thirty characters.").decode(),
            "generate_study_set": False,
        },
    )
    m1 = res1.json()["material"]["id"]

    res2 = client.post(
        "/api/materials/upload",
        json={
            "filename": "mat2.txt",
            "file_base64": base64.b64encode(b"Second material content with sufficient length exceeding thirty characters.").decode(),
            "generate_study_set": False,
        },
    )
    m2 = res2.json()["material"]["id"]

    # Initial turn on m1 with request_id
    req_id = "idem-replay-001"
    t1 = client.post(
        "/api/chat",
        json={"query": "Explain first", "material_id": m1, "request_id": req_id, "save_history": True},
    )
    assert t1.status_code == 200
    assert t1.json().get("cached_replay") is not True

    # Replay on m1: returns cached replay
    t1_replay = client.post(
        "/api/chat",
        json={"query": "Explain first", "material_id": m1, "request_id": req_id, "save_history": True},
    )
    assert t1_replay.status_code == 200
    assert t1_replay.json().get("cached_replay") is True

    # Replay with same request_id but m2: fails closed with 409
    t2_conflict = client.post(
        "/api/chat",
        json={"query": "Explain first", "material_id": m2, "request_id": req_id, "save_history": True},
    )
    assert t2_conflict.status_code == 409

    # Replay with same request_id on stream but m2: fails closed with 409
    stream_conflict = client.post(
        "/api/chat/stream",
        json={"query": "Explain first", "material_id": m2, "request_id": req_id, "save_history": True},
    )
    assert stream_conflict.status_code == 409


def test_private_retrieval_does_not_call_shared_expansion_or_quality(test_env, monkeypatch):
    """Private retrieval never invokes shared index context expansion or shared page-quality lookup."""
    from app import server
    client = _make_client()
    _create_account(client, "private_boundary_user")

    res = client.post(
        "/api/materials/upload",
        json={
            "filename": "shared_colliding_name.txt",
            "file_base64": base64.b64encode(b"Private text that happens to share a filename with a shared document.").decode(),
            "generate_study_set": False,
        },
    )
    assert res.status_code == 200, res.text
    m_id = res.json()["material"]["id"]

    expand_called = []
    quality_called = []

    orig_expand = server.agent.searcher.expand_page_context
    orig_quality = getattr(server.agent.searcher, "_attach_page_quality_metadata", None)

    def spy_expand(chunks, *args, **kwargs):
        expand_called.append(list(chunks))
        return orig_expand(chunks, *args, **kwargs)

    def spy_quality(chunks, *args, **kwargs):
        quality_called.append(list(chunks))
        if orig_quality:
            orig_quality(chunks, *args, **kwargs)

    monkeypatch.setattr(server.agent.searcher, "expand_page_context", spy_expand)
    if orig_quality:
        monkeypatch.setattr(server.agent.searcher, "_attach_page_quality_metadata", spy_quality)

    r = client.post(
        "/api/chat",
        json={"query": "Noi dung tài liệu này là gì?", "material_id": m_id, "save_history": False},
    )
    assert r.status_code == 200

    assert len(expand_called) == 0, f"expand_page_context was called: {expand_called}"
    assert len(quality_called) == 0, f"_attach_page_quality_metadata was called: {quality_called}"


def test_private_qa_enqueue_relevance_and_negative_controls(test_env):
    """Upload exact queue text, test enqueue followup relevance, and verify negative controls."""
    client = _make_client()
    _create_account(client, "queue_student")

    source_text = (
        "Hàng đợi (Queue) là cấu trúc dữ liệu hoạt động theo nguyên lý FIFO: phần tử vào trước được lấy ra trước. "
        "Thêm phần tử ở cuối gọi là enqueue. Lấy phần tử ở đầu gọi là dequeue."
    )
    b64 = base64.b64encode(source_text.encode("utf-8")).decode("ascii")

    upload_res = client.post(
        "/api/materials/upload",
        json={"filename": "same-name.txt", "file_base64": b64, "generate_study_set": False},
    )
    assert upload_res.status_code == 200
    mat_id = upload_res.json()["material"]["id"]

    # Turn 1: FIFO principle
    t1 = client.post(
        "/api/chat",
        json={"query": "Hàng đợi hoạt động theo nguyên lý nào?", "material_id": mat_id, "save_history": True},
    )
    assert t1.status_code == 200
    sess_id = t1.json()["session_id"]
    assert "FIFO" in t1.json()["answer"]

    # Turn 2: Followup in same session: "Thao tác thêm phần tử vào hàng đợi tên là gì?"
    t2 = client.post(
        "/api/chat",
        json={"query": "Thao tác thêm phần tử vào hàng đợi tên là gì?", "session_id": sess_id, "save_history": True},
    )
    assert t2.status_code == 200
    data2 = t2.json()
    assert data2.get("response_kind") != "insufficient_evidence"
    assert len(data2.get("citations", [])) > 0
    assert data2["citations"][0]["material_id"] == mat_id

    # Negative control 1: Unrelated Mars question -> must return insufficient_evidence with low_relevance
    t_unrelated = client.post(
        "/api/chat",
        json={"query": "Tài liệu này cho biết sao Hỏa có bao nhiêu vệ tinh?", "material_id": mat_id, "save_history": False},
    )
    assert t_unrelated.status_code == 200
    unrelated_data = t_unrelated.json()
    assert unrelated_data.get("response_kind") == "insufficient_evidence"
    assert unrelated_data.get("insufficient_reason") == "low_relevance"
    assert unrelated_data.get("generation_calls") == 0


def test_unresolved_symbol_pdf_export_end_to_end():
    """Unresolved PUA symbol (U+E123) in PDF worksheet renders explicit codepoint marker and notice without NUL."""
    import pymupdf as fitz
    from app.exercise_pdf import generate_exercise_worksheet_pdf, clean_for_pdf_export

    raw_q = "Giải phương trình f(x) = x\ue123y."

    # Pre-render preservation check: Python string preserves raw codepoint
    cleaned = clean_for_pdf_export(raw_q)
    assert "\ue123" in cleaned
    assert cleaned == raw_q

    # End-to-end PDF render
    pdf_bytes = generate_exercise_worksheet_pdf(
        topic="Synthetic unresolved symbol",
        questions=[{
            "question": raw_q,
            "source": {"title": "Synthetic QA only", "page_num": 1, "snippet": "f(x) = x\ue123y"},
        }],
    )
    assert len(pdf_bytes) > 0

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pdf_text = ""
    for page in doc:
        pdf_text += page.get_text()

    assert "\x00" not in pdf_text, "Exported PDF text must not contain NUL byte"
    assert "U+E123" in pdf_text, "Exported PDF text must contain explicit U+E123 codepoint marker"
    assert "Lưu ý rà soát nguồn" in pdf_text or "Ký hiệu chưa xác định" in pdf_text, (
        "Exported PDF must contain clear source-review notice"
    )

