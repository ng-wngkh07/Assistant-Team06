"""Regression tests for FIX-API-READINESS-01: degraded 503 mapping and API path projection.

All tests run against isolated temporary storage with synthetic fixtures only;
no real course sources, serving databases, or local model weights are used.
"""

import base64
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import server
from app.history import HistoryStore
from app.model_resolver import resolve_mlx_model_dir
from app.native_embedding import NativeEmbeddingClient
from app.ollama_client import OllamaClient
from app.study_materials import StudyMaterialError
from app.trained_client import TrainedModelClient


def _find_keys(obj, target_keys: set[str], prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if str(k).lower() in target_keys:
                hits.append(path)
            hits.extend(_find_keys(v, target_keys, path))
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            hits.extend(_find_keys(item, target_keys, f"{prefix}[{idx}]"))
    return hits


@pytest.fixture(autouse=True)
def isolated_readiness_env(tmp_path, monkeypatch):
    """Isolate filesystem, SQLite DBs, and local model paths under tmp_path."""
    import app.config as cfg
    import app.model_resolver as mr
    import app.study_materials as sm

    data_dir = tmp_path / "data"
    src_dir = tmp_path / "src"
    accounts_dir = tmp_path / "accounts"
    missing_model_dir = data_dir / "models" / "qwen3.5-9b-4bit"
    missing_bge_dir = data_dir / "models" / "bge-m3-mlx-fp16"
    for d in (data_dir, src_dir):
        d.mkdir(parents=True, exist_ok=True)
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    monkeypatch.setenv("AGENT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AGENT_SRC_DIR", str(src_dir))
    monkeypatch.setenv("MLX_MODEL_DIR", str(missing_model_dir))
    monkeypatch.setenv("MLX_9B_MODEL_DIR", str(missing_model_dir))
    monkeypatch.setenv("MLX_BGE_MODEL_DIR", str(missing_bge_dir))
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "0")
    monkeypatch.setenv("HISTORY_DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(tmp_path / "timetable.db"))
    monkeypatch.setenv("TIMETABLE_IMAGES_DIR", str(tmp_path / "timetable_images"))
    monkeypatch.setenv("PRACTICE_DB_PATH", str(tmp_path / "practice.db"))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CHAT_BACKEND", raising=False)

    monkeypatch.setattr(cfg, "DATA_DIR", data_dir)
    monkeypatch.setattr(cfg, "SRC_DIR", src_dir)
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(cfg, "MLX_9B_MODEL_DIR", missing_model_dir)
    monkeypatch.setattr(sm, "DATA_DIR", data_dir)
    monkeypatch.setattr(mr, "DATA_DIR", data_dir)
    monkeypatch.setattr(mr, "MLX_9B_DIR", missing_model_dir)
    monkeypatch.setattr(mr, "MLX_BGE_DIR", missing_bge_dir)

    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: False))
    monkeypatch.setattr(OllamaClient, "check_health", lambda self: False)
    monkeypatch.setattr(TrainedModelClient, "available", classmethod(lambda cls, *a, **kw: False))

    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "history.db"))

    if hasattr(server.study_material_manager, "_instances"):
        server.study_material_manager._instances.clear()
    local_mgr = getattr(server.study_material_manager, "_local", server.study_material_manager)
    monkeypatch.setattr(local_mgr, "_materials", {})
    monkeypatch.setattr(local_mgr, "_raw_files", {})

    yield {
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "src_dir": src_dir,
        "db_path": tmp_path / "knowledge.db",
        "local_mgr": local_mgr,
    }

    if hasattr(server.study_material_manager, "_instances"):
        server.study_material_manager._instances.clear()


def test_upload_generate_missing_model_returns_503_retains_extraction_and_keeps_health_unready(
    isolated_readiness_env,
):
    client = TestClient(server.app, raise_server_exceptions=False)
    tmp_path = isolated_readiness_env["tmp_path"]
    local_mgr = isolated_readiness_env["local_mgr"]

    health_before = client.get("/api/health")
    assert health_before.status_code == 200
    assert health_before.json()["qa_ready"] is False

    raw_text = (
        "Bài 1: Hệ hô hấp gồm mũi, hầu, thanh quản, khí quản và hai lá phổi. "
        "Phế nang là nơi diễn ra quá trình trao đổi khí oxy và cacbonic."
    ).encode("utf-8")

    res = client.post(
        "/api/materials/upload",
        json={
            "filename": "ho_hap.txt",
            "file_base64": base64.b64encode(raw_text).decode("ascii"),
            "quiz_count": 2,
            "flashcard_count": 2,
            "difficulty": "medium",
            "generate_study_set": True,
            "persist_to_src": False,
        },
    )
    assert res.status_code == 503, res.text
    detail = res.json()["detail"]
    assert "Traceback" not in detail
    assert str(tmp_path) not in detail
    assert "/Users/" not in detail
    assert "config.json" not in detail

    # Valid upload extraction and raw file must remain available for study/preview.
    saved = list(local_mgr.materials_cache.values())
    assert len(saved) == 1
    mat_id = saved[0]["id"]
    assert saved[0]["filename"] == "ho_hap.txt"
    assert saved[0]["status"] == "unindexed"

    got = client.get(f"/api/materials/{mat_id}")
    assert got.status_code == 200
    assert "Phế nang" in got.json()["pages"][0]["text"]

    dl = client.get(f"/api/materials/download?material_id={mat_id}")
    assert dl.status_code == 200
    assert dl.content == raw_text

    # Error handling must never mark qa_ready true.
    health_after = client.get("/api/health")
    assert health_after.status_code == 200
    assert health_after.json()["qa_ready"] is False


def test_upload_generate_persist_to_src_and_account_private_retain_on_503(
    isolated_readiness_env, monkeypatch
):
    tmp_path = isolated_readiness_env["tmp_path"]
    src_dir = isolated_readiness_env["src_dir"]
    local_mgr = isolated_readiness_env["local_mgr"]

    client = TestClient(server.app, raise_server_exceptions=False)
    raw_bytes = (
        "Tài liệu sinh lý học: Tim co bóp nhịp nhàng theo chu kỳ tâm thu và tâm trương "
        "để duy trì tuần hoàn máu nuôi cơ thể."
    ).encode("utf-8")
    res_persist = client.post(
        "/api/materials/upload",
        json={
            "filename": "tim_mach.txt",
            "file_base64": base64.b64encode(raw_bytes).decode("ascii"),
            "quiz_count": 1,
            "flashcard_count": 1,
            "difficulty": "easy",
            "generate_study_set": True,
            "persist_to_src": True,
        },
    )
    assert res_persist.status_code == 503, res_persist.text
    assert str(tmp_path) not in res_persist.json()["detail"]
    assert (src_dir / "tim_mach.txt").is_file()
    saved = list(local_mgr.materials_cache.values())
    assert len(saved) == 1
    assert saved[0]["saved_to_src"] is True
    assert saved[0]["status"] == "indexed"
    mat_detail = client.get(f"/api/materials/{saved[0]['id']}")
    assert mat_detail.status_code == 200
    assert mat_detail.json()["src_path"].startswith("src/")

    # Authenticated PrivateMaterials path must also retain privately on 503.
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    user_a = TestClient(server.app, headers={"X-Study-Request": "1"}, raise_server_exceptions=False)
    user_b = TestClient(server.app, headers={"X-Study-Request": "1"}, raise_server_exceptions=False)
    assert user_a.post("/api/account/register", json={"username": "mat_owner", "password": "private-example-123"}).status_code == 200
    assert user_b.post("/api/account/register", json={"username": "mat_other", "password": "private-example-123"}).status_code == 200

    res_priv = user_a.post(
        "/api/materials/upload",
        json={
            "filename": "private_note.txt",
            "file_base64": base64.b64encode(raw_bytes).decode("ascii"),
            "generate_study_set": True,
            "persist_to_src": False,
        },
    )
    assert res_priv.status_code == 503, res_priv.text
    assert str(tmp_path) not in res_priv.json()["detail"]

    priv_instances = list(server.study_material_manager._instances.values())
    priv_lists = [inst.list_materials() for inst in priv_instances if inst.list_materials()]
    assert len(priv_lists) == 1
    priv_list = priv_lists[0]
    assert len(priv_list) == 1
    priv_id = priv_list[0]["id"]
    got_priv = user_a.get(f"/api/materials/{priv_id}")
    assert got_priv.status_code == 200
    assert "tâm thu" in got_priv.json()["pages"][0]["text"]
    assert user_a.get(f"/api/materials/download?material_id={priv_id}").content == raw_bytes
    assert user_b.get(f"/api/materials/{priv_id}").status_code == 404
    user_a.close()
    user_b.close()


def test_library_generate_missing_model_returns_503_and_invalid_input_stays_422(
    isolated_readiness_env, monkeypatch
):
    tmp_path = isolated_readiness_env["tmp_path"]
    src_dir = isolated_readiness_env["src_dir"]
    local_mgr = isolated_readiness_env["local_mgr"]
    (src_dir / "lib_note.txt").write_text(
        "Nội dung bài học về trí nhớ làm việc và mã hóa thông tin trong vỏ não.",
        encoding="utf-8",
    )

    client = TestClient(server.app, raise_server_exceptions=False)
    res_lib = client.post(
        "/api/materials/generate-from-library",
        json={
            "filename": "lib_note.txt",
            "quiz_count": 2,
            "flashcard_count": 2,
            "difficulty": "medium",
        },
    )
    assert res_lib.status_code == 503, res_lib.text
    detail = res_lib.json()["detail"]
    assert "Traceback" not in detail
    assert str(tmp_path) not in detail
    assert "/Users/" not in detail

    # Invalid/empty upload stays 422 and evicts raw file cache.
    res_bad = client.post(
        "/api/materials/upload",
        json={
            "filename": "empty.txt",
            "file_base64": base64.b64encode(b"   ").decode("ascii"),
            "generate_study_set": True,
        },
    )
    assert res_bad.status_code == 422
    assert [r for r in local_mgr.materials_cache.values() if r["filename"] == "empty.txt"] == []

    # Validation StudyMaterialError (e.g. difficulty='hard') stays 422.
    raw_ok = base64.b64encode("Tài liệu hợp lệ có đủ độ dài để kiểm tra lựa chọn độ khó.".encode("utf-8")).decode("ascii")

    def raise_hard_rejection(*args, **kwargs):
        raise StudyMaterialError("Hiện chỉ hỗ trợ tạo học liệu ở mức độ dễ (easy) hoặc trung bình (medium).")

    monkeypatch.setattr(local_mgr, "generate_materials_from_pages", raise_hard_rejection)
    res_hard = client.post(
        "/api/materials/upload",
        json={
            "filename": "hard_opt.txt",
            "file_base64": raw_ok,
            "difficulty": "hard",
            "generate_study_set": True,
        },
    )
    assert res_hard.status_code == 422
    assert [r for r in local_mgr.materials_cache.values() if r["filename"] == "hard_opt.txt"] == []

    # Model-unavailability wrapped in StudyMaterialError maps to 503, strips host path, retains extraction.
    def raise_missing_weights(*args, **kwargs):
        raise StudyMaterialError(
            f"Mô hình AI chưa sẵn sàng hoặc thiếu tệp trọng số cục bộ: {tmp_path}/models/weights.safetensors"
        )

    monkeypatch.setattr(local_mgr, "generate_materials_from_pages", raise_missing_weights)
    res_unavail = client.post(
        "/api/materials/upload",
        json={
            "filename": "wrapped_unavail.txt",
            "file_base64": raw_ok,
            "difficulty": "medium",
            "generate_study_set": True,
        },
    )
    assert res_unavail.status_code == 503
    assert str(tmp_path) not in res_unavail.json()["detail"]
    assert any(r["filename"] == "wrapped_unavail.txt" for r in local_mgr.materials_cache.values())


def test_chat_sync_stream_replay_and_history_strip_review_path_and_preserve_grounding(
    isolated_readiness_env, monkeypatch
):
    tmp_path = isolated_readiness_env["tmp_path"]
    host_review = "/Users/private/host/data/reviews/giao_trinh/0013.json"
    host_file = "/Users/private/host/src/Giao_trinh.pdf"
    literal_quote = "Tỷ lệ 3/4 và ký hiệu luận lý P(A|B)/P(B) trong trang 13."

    sample_citation = {
        "id": "S1",
        "book_title": "Giáo trình Sinh lý",
        "filename": "Giao_trinh.pdf",
        "doc_id": 7,
        "page_num": 13,
        "chunk_index": 0,
        "snippet": f"Đoạn trích 1/2: {literal_quote}",
        "snippet_vi": "Bản dịch 1/2 giữ nguyên",
        "score": 0.91,
        "pdf_url": "/api/documents/7/pdf#page=13",
        "original_url": "/api/documents/7/original",
        "download_url": "/api/materials/download?material_id=mat_1",
        "evidence_quotes": [literal_quote],
        "claims": [{"text": literal_quote, "quote": literal_quote}],
        "page_quality": {
            "needs_review": True,
            "warnings": ["trang chụp còn mờ ở góc phải"],
            "review_path": host_review,
            "raw_capture": True,
        },
        "filepath": host_file,
        "nested": {"review_path": host_review, "image_path": "/Users/private/host/page13.png"},
    }
    sample_trace_chunk = {
        "chunk_id": 101,
        "filename": "Giao_trinh.pdf",
        "doc_id": 7,
        "page_num": 13,
        "text": literal_quote,
        "page_quality": {
            "needs_review": True,
            "warnings": ["trang chụp còn mờ ở góc phải"],
            "review_path": host_review,
            "raw_capture": True,
        },
        "filepath": host_file,
    }

    class FakeAgent:
        def __init__(self):
            self.return_error = False

        def process_query_sync(self, **kwargs):
            if self.return_error:
                return {
                    "answer": "",
                    "citations": [],
                    "answer_mode": "rag",
                    "search_scope": {"type": "global"},
                    "error": f"Không tìm thấy tệp cấu hình mô hình tại {tmp_path}/models/config.json",
                    "response_kind": "technical_error",
                }
            return {
                "answer": f"Theo tài liệu [S1]: {literal_quote}",
                "citations": [json.loads(json.dumps(sample_citation))],
                "retrieval_trace": [json.loads(json.dumps(sample_trace_chunk))],
                "answer_mode": "rag",
                "search_scope": {"type": "global"},
                "evidence_quotes": [literal_quote],
                "claims": [{"text": literal_quote}],
                "check_status": "verified",
            }

        def process_query_stream(self, **kwargs):
            yield {
                "type": "retrieval_trace",
                "chunks": [json.loads(json.dumps(sample_trace_chunk))],
                "is_broad": False,
            }
            yield {
                "type": "citations",
                "citations": [json.loads(json.dumps(sample_citation))],
            }
            yield {"type": "token", "content": f"Theo [S1]: {literal_quote}"}
            yield {
                "type": "done",
                "citations": [json.loads(json.dumps(sample_citation))],
                "search_scope": {"type": "global"},
            }

    fake_agent = FakeAgent()
    monkeypatch.setattr(server, "agent", fake_agent)
    client = TestClient(server.app, raise_server_exceptions=False)

    # 1) Sync chat + persisted history + idempotent replay
    res_sync = client.post(
        "/api/chat",
        json={"query": "Giải thích trang 13", "save_history": True, "request_id": "req-sync-proj-1"},
    )
    assert res_sync.status_code == 200, res_sync.text
    sync_payload = res_sync.json()
    raw_sync = res_sync.text
    assert "review_path" not in raw_sync
    assert "filepath" not in raw_sync
    assert "/Users/private/host" not in raw_sync
    assert _find_keys(sync_payload, {"review_path", "filepath", "image_path"}) == []

    # 1b) Sync error payload sanitizes host path
    fake_agent.return_error = True
    res_sync_err = client.post(
        "/api/chat",
        json={"query": "Lỗi cấu hình sync", "save_history": False},
    )
    assert res_sync_err.status_code == 200
    assert str(tmp_path) not in res_sync_err.json().get("error", "")
    assert "config.json" not in res_sync_err.json().get("error", "")
    fake_agent.return_error = False

    # Grounding content & citation metadata stay intact.
    cit = sync_payload["citations"][0]
    assert cit["filename"] == "Giao_trinh.pdf"
    assert cit["doc_id"] == 7
    assert cit["page_num"] == 13
    assert cit["pdf_url"] == "/api/documents/7/pdf#page=13"
    assert cit["original_url"] == "/api/documents/7/original"
    assert cit["download_url"] == "/api/materials/download?material_id=mat_1"
    assert cit["evidence_quotes"] == [literal_quote]
    assert literal_quote in cit["snippet"]
    assert cit["page_quality"] == {
        "needs_review": True,
        "warnings": ["trang chụp còn mờ ở góc phải"],
        "raw_capture": True,
    }
    assert sync_payload["recent_source"]["page_quality"]["needs_review"] is True
    assert "review_path" not in sync_payload["recent_source"]["page_quality"]

    # Saved session and legacy history turn must NOT store review_path.
    sess_id = sync_payload["session_id"]
    turn_id = sync_payload["turn_id"]
    sess_res = client.get(f"/api/sessions/{sess_id}")
    assert sess_res.status_code == 200
    assert "review_path" not in sess_res.text
    assert "filepath" not in sess_res.text
    assert literal_quote in sess_res.text
    hist_res = client.get(f"/api/history/{turn_id}")
    assert hist_res.status_code == 200
    assert "review_path" not in hist_res.text

    replay_sync = client.post(
        "/api/chat",
        json={"query": "Giải thích trang 13", "save_history": True, "request_id": "req-sync-proj-1"},
    )
    assert replay_sync.status_code == 200
    assert replay_sync.json()["cached_replay"] is True
    assert "review_path" not in replay_sync.text
    assert "filepath" not in replay_sync.text

    # 2) Stream chat + SSE replay
    res_stream = client.post(
        "/api/chat/stream",
        json={"query": "Giải thích trang 13 stream", "save_history": True, "request_id": "req-stream-proj-1"},
    )
    assert res_stream.status_code == 200
    assert "review_path" not in res_stream.text
    assert "filepath" not in res_stream.text
    assert "/Users/private/host" not in res_stream.text
    assert literal_quote in res_stream.text
    sse_events = [
        json.loads(line[6:])
        for line in res_stream.text.splitlines()
        if line.startswith("data: ")
    ]
    assert any(ev.get("type") == "retrieval_trace" for ev in sse_events)
    assert any(ev.get("type") == "citations" for ev in sse_events)
    done_ev = next(ev for ev in sse_events if ev.get("type") == "done")
    assert done_ev["citations"][0]["page_quality"]["needs_review"] is True

    replay_stream = client.post(
        "/api/chat/stream",
        json={"query": "Giải thích trang 13 stream", "save_history": True, "request_id": "req-stream-proj-1"},
    )
    assert replay_stream.status_code == 200
    assert "review_path" not in replay_stream.text

    # 3) Stream error event sanitizes host config path without failing SSE framing
    class FailingStreamAgent:
        def process_query_stream(self, **kwargs):
            resolve_mlx_model_dir()  # keeps unused import meaningful
            from app.dialogue import ContextLimitError

            raise ContextLimitError(f"Không tìm thấy tệp cấu hình mô hình tại {tmp_path}/models/qwen3.5-9b-4bit/config.json")
            yield {}

    monkeypatch.setattr(server, "agent", FailingStreamAgent())
    res_err_stream = client.post(
        "/api/chat/stream",
        json={"query": "Câu hỏi lỗi stream", "save_history": False},
    )
    assert res_err_stream.status_code == 200
    assert str(tmp_path) not in res_err_stream.text
    assert "config.json" not in res_err_stream.text
    err_events = [
        json.loads(line[6:])
        for line in res_err_stream.text.splitlines()
        if line.startswith("data: ")
    ]
    assert err_events and err_events[0]["type"] == "error"


def test_document_practice_learning_material_routes_project_paths_and_auth_source_routes(
    isolated_readiness_env, monkeypatch
):
    tmp_path = isolated_readiness_env["tmp_path"]
    src_dir = isolated_readiness_env["src_dir"]
    db_path = isolated_readiness_env["db_path"]

    host_review = "/Users/private/host/reviews/doc1/0002.json"
    host_src = "/Users/private/host/src/bai_hoc.pdf"
    sample_pdf = src_dir / "bai_hoc.pdf"
    sample_pdf.write_bytes(b"%PDF-1.4\n% synthetic test payload\n%%EOF\n")

    # Seed minimal fresh document row in isolated SQLite for real authenticated /original & /pdf checks.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                filename TEXT UNIQUE,
                title TEXT,
                total_pages INTEGER,
                filepath TEXT,
                file_hash TEXT,
                index_status TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO documents (id, filename, title, total_pages, filepath, file_hash, index_status)
            VALUES (1, 'bai_hoc.pdf', 'Bài học 1', 1, ?, 'abc', 'ready')
            """,
            (str(sample_pdf),),
        )
        conn.commit()

    monkeypatch.setattr(
        server.document_lookup,
        "documents",
        lambda: [{"doc_id": 1, "filename": "bai_hoc.pdf", "title": "Bài học 1", "filepath": host_src, "page_quality": {"needs_review": False, "review_path": host_review}}],
    )
    monkeypatch.setattr(
        server.document_lookup,
        "search",
        lambda *args, **kwargs: {
            "items": [
                {
                    "doc_id": 1,
                    "filename": "bai_hoc.pdf",
                    "page_num": 2,
                    "snippet": "Trích dẫn 1/2 giữ nguyên",
                    "pdf_url": "/api/documents/1/pdf#page=2",
                    "original_url": "/api/documents/1/original",
                    "page_quality": {"needs_review": True, "warnings": ["blur"], "review_path": host_review},
                    "filepath": host_src,
                }
            ]
        },
    )
    monkeypatch.setattr(
        server.document_lookup,
        "source_preview",
        lambda fn, pg: {
            "filename": fn,
            "doc_id": 1,
            "page_num": pg,
            "pdf_url": "/api/documents/1/pdf#page=2",
            "original_url": "/api/documents/1/original",
            "page_quality": {"needs_review": True, "warnings": ["blur"], "review_path": host_review},
            "storage_path": "/Users/private/host/cache/p2.png",
        },
    )
    monkeypatch.setattr(
        server.practice_service,
        "generate",
        lambda **kwargs: {
            "practice_id": "p" * 16,
            "questions": [
                {
                    "id": "q1",
                    "question": "Câu hỏi 1/2?",
                    "evidence_quotes": ["Đoạn chứng minh A/B"],
                    "page_quality": {"needs_review": True, "review_path": host_review},
                    "filepath": host_src,
                }
            ],
        },
    )
    monkeypatch.setattr(
        server.practice_service,
        "get_session",
        lambda pid: {
            "practice_id": pid,
            "questions": [{"id": "q1", "page_quality": {"review_path": host_review}}],
        },
    )
    monkeypatch.setattr(
        server.practice_service,
        "answer",
        lambda pid, qid, ans: {
            "correct": True,
            "citation": {"filename": "bai_hoc.pdf", "page_quality": {"needs_review": False, "review_path": host_review}},
        },
    )
    monkeypatch.setattr(
        server.learning_service,
        "topics",
        lambda: {"topics": [{"name": "Hô hấp", "config_path": "/Users/private/host/config.json"}]},
    )
    monkeypatch.setattr(
        server.learning_service,
        "generate",
        lambda **kwargs: {
            "topic": kwargs["topic"],
            "quiz": [{"question": "Q?", "page_quality": {"needs_review": True, "review_path": host_review}}],
            "flashcards": [],
        },
    )
    monkeypatch.setattr(
        server.learning_service,
        "exercises",
        lambda **kwargs: {
            "topic": kwargs["topic"],
            "exercises": [{"question": "E?", "evidence_quotes": ["Tỷ lệ 2/3"], "raw_capture_path": "/Users/private/host/cap.png"}],
        },
    )

    local_mgr = isolated_readiness_env["local_mgr"]
    monkeypatch.setattr(
        local_mgr,
        "inspect_library_document",
        lambda **kwargs: {
            "id": "mat_lib_1",
            "filename": "bai_hoc.pdf",
            "src_path": "src/bai_hoc.pdf",
            "filepath": host_src,
            "page_quality": {"needs_review": True, "review_path": host_review},
        },
    )
    monkeypatch.setattr(
        local_mgr,
        "list_available_documents",
        lambda *args, **kwargs: [{"doc_id": 1, "filename": "bai_hoc.pdf", "filepath": host_src, "review_path": host_review}],
    )
    monkeypatch.setattr(
        server.document_lookup,
        "inspect_document_status",
        lambda **kwargs: {
            "fresh": True,
            "status": "ready",
            "doc": {"id": 1, "filename": "bai_hoc.pdf", "total_pages": 5, "filepath": str(sample_pdf)},
        },
    )

    client = TestClient(server.app, raise_server_exceptions=False)
    for method, url, payload in [
        ("GET", "/api/documents", None),
        ("GET", "/api/documents/search?q=hoc", None),
        ("GET", "/api/documents/source-preview?filename=bai_hoc.pdf&page_num=2", None),
        ("POST", "/api/practice/generate", {"source_document_id": 1, "source_page_num": 2, "count": 1}),
        ("GET", f"/api/practice/session/{'p' * 16}", None),
        ("POST", "/api/practice/answer", {"practice_id": "p" * 16, "question_id": "q1", "answer": "A"}),
        ("GET", "/api/learning/topics", None),
        ("POST", "/api/learning/generate", {"topic": "Hô hấp", "count": 1}),
        ("POST", "/api/learning/exercises", {"topic": "Hô hấp", "limit": 1}),
        ("POST", "/api/materials/inspect-library", {"filename": "bai_hoc.pdf"}),
        ("GET", "/api/materials/documents", None),
    ]:
        res = client.get(url) if method == "GET" else client.post(url, json=payload)
        assert res.status_code == 200, f"{method} {url} -> {res.text}"
        body = res.json()
        assert "review_path" not in res.text, f"review_path leaked at {url}"
        assert "/Users/private/host" not in res.text, f"host path leaked at {url}"
        assert _find_keys(body, {"review_path", "filepath", "storage_path", "config_path", "raw_capture_path", "image_path"}) == []

    # Verify relative src_path and URLs remain intact
    inspect_json = client.post("/api/materials/inspect-library", json={"filename": "bai_hoc.pdf"}).json()["material"]
    assert inspect_json["src_path"] == "src/bai_hoc.pdf"
    assert inspect_json["page_quality"] == {"needs_review": True}

    # Verify real document file routes enforce authentication when accounts are required.
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    anon = TestClient(server.app, headers={"X-Study-Request": "1"}, raise_server_exceptions=False)
    assert anon.get("/api/documents/1/original").status_code == 401
    assert anon.get("/api/documents/1/pdf").status_code == 401
    assert anon.get("/api/materials/download?material_id=missing").status_code == 401

    assert anon.post("/api/account/register", json={"username": "doc_reader", "password": "private-example-123"}).status_code == 200
    assert anon.get("/api/documents/1/original").status_code == 200
    assert anon.get("/api/documents/1/pdf").status_code == 200
    anon.close()


def test_internal_searcher_retains_review_path_and_projection_helper_contracts(isolated_readiness_env):
    from app.response_projection import project_public_response, sanitize_error_detail
    from app.searcher import HybridSearcher

    db_path = isolated_readiness_env["db_path"]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE corpus_page_quality (filename TEXT, page_num INTEGER, needs_review INTEGER, warnings_json TEXT, review_path TEXT, raw_capture INTEGER)"
        )
        conn.execute(
            "INSERT INTO corpus_page_quality VALUES (?, ?, ?, ?, ?, ?)",
            ("doc.pdf", 5, 1, '["w1"]', "/abs/host/review.json", 1),
        )
        conn.commit()

    searcher = HybridSearcher(db_path=db_path)
    items = [{"filename": "doc.pdf", "page_num": 5, "text": "Phép chia 3/4"}]
    searcher._attach_page_quality_metadata(items)
    assert items[0]["page_quality"]["review_path"] == "/abs/host/review.json"

    projected = project_public_response(
        {
            "filename": "doc.pdf",
            "doc_id": 3,
            "page_num": 5,
            "src_path": "src/doc.pdf",
            "pdf_url": "/api/documents/3/pdf#page=5",
            "snippet": "Phép chia 3/4 và đường dẫn logic A/B.",
            "page_quality": {
                "needs_review": True,
                "warnings": ["w1"],
                "review_path": "/abs/host/review.json",
                "raw_capture": True,
            },
            "filepath": "/abs/host/doc.pdf",
        }
    )
    assert projected["src_path"] == "src/doc.pdf"
    assert projected["snippet"] == "Phép chia 3/4 và đường dẫn logic A/B."
    assert projected["page_quality"] == {"needs_review": True, "warnings": ["w1"], "raw_capture": True}
    assert "filepath" not in projected

    assert project_public_response({"src_path": "/abs/host/doc.pdf"}) == {}
    assert "/abs/host" not in sanitize_error_detail("Không tìm thấy tệp cấu hình mô hình tại /abs/host/config.json")
    assert sanitize_error_detail("Lỗi kết nối tạm thời 1/2.") == "Lỗi kết nối tạm thời 1/2."


def _seed_preexisting_historical_store(db_path: Path, *, title_prefix: str, request_id: str) -> dict:
    """Seed synthetic pre-existing history containing internal host paths and literal quotes."""
    store = HistoryStore(db_path)
    literal_quote = "/Users/example/as-authored"
    session = store.create_session(title=f"{title_prefix} {literal_quote}")
    sid = session["id"]
    turn = store.add_turn(
        session_id=sid,
        question=f"{title_prefix} question quoting {literal_quote}",
        answer=f"Literal source quote {literal_quote} remains text.",
        citations=[
            {
                "id": "S1",
                "doc_id": 1,
                "filename": "fixture.pdf",
                "page_num": 1,
                "snippet": f"Literal quote {literal_quote}",
                "snippet_vi": f"Bản dịch 1/2 {literal_quote}",
                "evidence_quotes": [f"Exact quote {literal_quote}"],
                "src_path": "src/fixture.pdf",
                "pdf_url": "/api/documents/1/pdf#page=1",
                "original_url": "/api/documents/1/original",
                "page_quality": {
                    "needs_review": True,
                    "warnings": ["trang chụp hơi mờ"],
                    "review_path": "/Users/private/history/review.json",
                    "raw_capture": True,
                },
                "filepath": "/Users/private/history/fixture.pdf",
                "raw_capture_path": "/Users/private/history/raw.png",
                "image_path": "/Users/private/history/page1.png",
                "storage_path": "/Users/private/history/cache.bin",
                "config_path": "/Users/private/history/config.json",
                "nested": {"review_path": "/Users/private/history/nested_review.json"},
            }
        ],
        model="fixture-no-inference",
        request_id=request_id,
        search_scope={
            "type": "global",
            "src_path": "/Users/private/history/abs_src.pdf",
            "nested": {"review_path": "/Users/private/history/scope.json"},
        },
    )
    store.update_session_summary(
        session_id=sid,
        summary=f"Summary quoting {literal_quote} for {title_prefix}.",
        expected_revision=store.get_session(sid)["revision"],
    )
    return {
        "store": store,
        "session_id": sid,
        "legacy_id": turn["legacy_id"],
        "question": f"{title_prefix} question quoting {literal_quote}",
        "literal_quote": literal_quote,
    }


def _assert_historical_responses_projected_and_db_intact(
    client: TestClient,
    *,
    db_path: Path,
    sid: str,
    legacy_id: int,
    question: str,
    request_id: str,
    search_query: str,
    literal_quote: str,
):
    forbidden_keys = {
        "review_path",
        "filepath",
        "raw_capture_path",
        "image_path",
        "storage_path",
        "config_path",
    }

    hist_list = client.get("/api/history")
    assert hist_list.status_code == 200, hist_list.text
    assert "/Users/private/history/" not in hist_list.text
    assert _find_keys(hist_list.json(), forbidden_keys) == []
    assert literal_quote in hist_list.text

    hist_detail = client.get(f"/api/history/{legacy_id}")
    assert hist_detail.status_code == 200, hist_detail.text
    assert "/Users/private/history/" not in hist_detail.text
    assert "review_path" not in hist_detail.text
    assert "filepath" not in hist_detail.text
    hist_obj = hist_detail.json()
    assert _find_keys(hist_obj, forbidden_keys) == []
    assert hist_obj["answer"] == f"Literal source quote {literal_quote} remains text."
    cit = hist_obj["citations"][0]
    assert cit["id"] == "S1"
    assert cit["doc_id"] == 1
    assert cit["filename"] == "fixture.pdf"
    assert cit["page_num"] == 1
    assert cit["src_path"] == "src/fixture.pdf"
    assert cit["pdf_url"] == "/api/documents/1/pdf#page=1"
    assert cit["original_url"] == "/api/documents/1/original"
    assert cit["snippet"] == f"Literal quote {literal_quote}"
    assert cit["evidence_quotes"] == [f"Exact quote {literal_quote}"]
    assert cit["page_quality"] == {
        "needs_review": True,
        "warnings": ["trang chụp hơi mờ"],
        "raw_capture": True,
    }

    sess_list = client.get("/api/sessions?include_legacy=true")
    assert sess_list.status_code == 200, sess_list.text
    assert "/Users/private/history/" not in sess_list.text
    assert _find_keys(sess_list.json(), forbidden_keys) == []
    assert literal_quote in sess_list.text

    sess_search = client.get(f"/api/sessions/search?q={search_query}")
    assert sess_search.status_code == 200, sess_search.text
    assert "/Users/private/history/" not in sess_search.text
    assert _find_keys(sess_search.json(), forbidden_keys) == []
    assert sess_search.json()["total"] == 1
    assert literal_quote in sess_search.text

    sess_detail = client.get(f"/api/sessions/{sid}")
    assert sess_detail.status_code == 200, sess_detail.text
    assert "/Users/private/history/" not in sess_detail.text
    assert "review_path" not in sess_detail.text
    assert "filepath" not in sess_detail.text
    sess_obj = sess_detail.json()
    assert _find_keys(sess_obj, forbidden_keys) == []
    assert literal_quote in sess_obj["title"]
    assert literal_quote in sess_obj["summary"]
    assert literal_quote in sess_obj["recent_question"]
    assert sess_obj["turns"][0]["answer"] == f"Literal source quote {literal_quote} remains text."
    assert sess_obj["turns"][0]["search_scope"]["type"] == "global"
    assert "src_path" not in sess_obj["turns"][0]["search_scope"]
    assert sess_obj["recent_source"]["filename"] == "fixture.pdf"
    assert sess_obj["recent_source"]["page_quality"]["needs_review"] is True

    # Sync & SSE request_id replay on pre-existing turn stays compatible and projected.
    sync_replay = client.post(
        "/api/chat",
        json={
            "query": question,
            "session_id": sid,
            "request_id": request_id,
            "save_history": True,
        },
    )
    assert sync_replay.status_code == 200, sync_replay.text
    assert sync_replay.json()["cached_replay"] is True
    assert "/Users/private/history/" not in sync_replay.text
    assert _find_keys(sync_replay.json(), forbidden_keys) == []
    assert literal_quote in sync_replay.json()["answer"]

    stream_replay = client.post(
        "/api/chat/stream",
        json={
            "query": question,
            "session_id": sid,
            "request_id": request_id,
            "save_history": True,
        },
    )
    assert stream_replay.status_code == 200, stream_replay.text
    assert "/Users/private/history/" not in stream_replay.text
    assert "review_path" not in stream_replay.text
    assert literal_quote in stream_replay.text

    # Internal stored DB records must remain intact (never rewritten/migrated/deleted).
    with sqlite3.connect(db_path) as conn:
        row_turn = conn.execute(
            "SELECT citations_json, search_scope_json FROM session_turns WHERE session_id=?",
            (sid,),
        ).fetchone()
        row_legacy = conn.execute(
            "SELECT citations_json FROM searches WHERE id=?",
            (legacy_id,),
        ).fetchone()
    assert row_turn is not None and row_legacy is not None
    assert "/Users/private/history/review.json" in row_turn[0]
    assert "/Users/private/history/scope.json" in row_turn[1]
    assert "/Users/private/history/review.json" in row_legacy[0]


def test_preexisting_history_responses_project_paths_without_mutating_db_or_quotes(
    isolated_readiness_env, monkeypatch
):
    tmp_path = isolated_readiness_env["tmp_path"]
    guest_db = tmp_path / "history.db"
    seeded = _seed_preexisting_historical_store(
        guest_db,
        title_prefix="Historical fixture",
        request_id="hist-guest-replay-1",
    )
    monkeypatch.setattr(server, "history_store", seeded["store"])
    client = TestClient(server.app, raise_server_exceptions=False)

    _assert_historical_responses_projected_and_db_intact(
        client,
        db_path=guest_db,
        sid=seeded["session_id"],
        legacy_id=seeded["legacy_id"],
        question=seeded["question"],
        request_id="hist-guest-replay-1",
        search_query="Historical",
        literal_quote=seeded["literal_quote"],
    )

    # All session routes (including list/search/create/rename/summarize) apply boundary projection.
    monkeypatch.setattr(
        seeded["store"],
        "list",
        lambda *a, **kw: [
            {
                "id": 99,
                "question": f"Quoting {seeded['literal_quote']}",
                "review_path": "/Users/private/history/list.json",
            }
        ],
    )
    assert "review_path" not in client.get("/api/history").text
    assert seeded["literal_quote"] in client.get("/api/history").text

    monkeypatch.setattr(
        seeded["store"],
        "list_sessions",
        lambda *a, **kw: {
            "items": [
                {
                    "id": seeded["session_id"],
                    "title": f"Historical {seeded['literal_quote']}",
                    "review_path": "/Users/private/history/sessions.json",
                }
            ],
            "total": 1,
            "limit": 50,
            "offset": 0,
        },
    )
    assert "review_path" not in client.get("/api/sessions").text
    assert seeded["literal_quote"] in client.get("/api/sessions").text

    monkeypatch.setattr(
        seeded["store"],
        "search_sessions",
        lambda *a, **kw: {
            "items": [
                {
                    "id": seeded["session_id"],
                    "title": f"Historical {seeded['literal_quote']}",
                    "filepath": "/Users/private/history/search.json",
                }
            ],
            "total": 1,
            "limit": 20,
            "offset": 0,
            "query": "Historical",
        },
    )
    assert "filepath" not in client.get("/api/sessions/search?q=Historical").text
    assert seeded["literal_quote"] in client.get("/api/sessions/search?q=Historical").text

    monkeypatch.setattr(
        server.summarizer,
        "summarize_session_sync",
        lambda **kw: {
            "status": "fallback",
            "title": f"Summary title {seeded['literal_quote']}",
            "summary": f"Summary text {seeded['literal_quote']}",
            "error": f"Không tìm thấy tệp cấu hình mô hình tại {tmp_path}/models/config.json",
            "reason": f"Runtime failure at {tmp_path}/models/weights.safetensors",
            "review_path": "/Users/private/history/summary.json",
        },
    )
    sum_res = client.post(f"/api/sessions/{seeded['session_id']}/summarize", json={})
    assert sum_res.status_code == 200, sum_res.text
    sum_json = sum_res.json()
    assert "review_path" not in sum_res.text
    assert str(tmp_path) not in sum_res.text
    assert "config.json" not in sum_res.text
    assert sum_json["title"] == f"Summary title {seeded['literal_quote']}"
    assert sum_json["summary"] == f"Summary text {seeded['literal_quote']}"

    monkeypatch.setattr(
        server.summarizer,
        "summarize_session_sync",
        lambda **kw: {"status": "error", "message": f"Missing {tmp_path}/history.db"},
    )
    sum_err = client.post(f"/api/sessions/{seeded['session_id']}/summarize", json={})
    assert sum_err.status_code == 404
    assert str(tmp_path) not in sum_err.json()["detail"]


def test_preexisting_history_projection_in_account_context_preserves_isolation(
    isolated_readiness_env, monkeypatch
):
    tmp_path = isolated_readiness_env["tmp_path"]
    accounts_dir = tmp_path / "accounts"
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    monkeypatch.setattr(server, "_history_store", None)
    monkeypatch.setattr(server, "history_store", server._LazyHistoryStore())
    monkeypatch.setattr(server.summarizer, "history_store", server.history_store)

    owner = TestClient(server.app, headers={"X-Study-Request": "1"}, raise_server_exceptions=False)
    other = TestClient(server.app, headers={"X-Study-Request": "1"}, raise_server_exceptions=False)
    anon = TestClient(server.app, headers={"X-Study-Request": "1"}, raise_server_exceptions=False)

    reg_owner = owner.post("/api/account/register", json={"username": "hist_owner", "password": "private-example-123"})
    assert reg_owner.status_code == 200, reg_owner.text
    owner_id = reg_owner.json()["account"]["id"]
    assert other.post("/api/account/register", json={"username": "hist_other", "password": "private-example-123"}).status_code == 200

    owner_db = accounts_dir / owner_id / "history.db"
    seeded = _seed_preexisting_historical_store(
        owner_db,
        title_prefix="Account historical",
        request_id="hist-account-replay-1",
    )

    _assert_historical_responses_projected_and_db_intact(
        owner,
        db_path=owner_db,
        sid=seeded["session_id"],
        legacy_id=seeded["legacy_id"],
        question=seeded["question"],
        request_id="hist-account-replay-1",
        search_query="Account",
        literal_quote=seeded["literal_quote"],
    )

    # Summarize in account context with missing runtime uses fallback without leaking host paths.
    sum_res = owner.post(f"/api/sessions/{seeded['session_id']}/summarize", json={})
    assert sum_res.status_code == 200, sum_res.text
    assert str(tmp_path) not in sum_res.text
    assert "/Users/private/" not in sum_res.text
    assert seeded["literal_quote"] in sum_res.json()["summary"]

    # Account isolation: another user and anonymous user cannot read or list owner's history.
    assert other.get(f"/api/sessions/{seeded['session_id']}").status_code == 404
    assert other.get(f"/api/history/{seeded['legacy_id']}").status_code == 404
    assert other.get("/api/sessions?include_legacy=true").json()["total"] == 0
    assert other.get("/api/sessions/search?q=Account").json()["total"] == 0
    assert other.get("/api/history").json()["items"] == []
    assert other.post(f"/api/sessions/{seeded['session_id']}/summarize", json={}).status_code == 404

    assert anon.get(f"/api/sessions/{seeded['session_id']}").status_code == 401
    assert anon.get(f"/api/history/{seeded['legacy_id']}").status_code == 401

    owner.close()
    other.close()
    anon.close()


def test_native_health_coverage_uses_verified_status_identity_cache_and_preserves_query_full_hash(
    isolated_readiness_env, monkeypatch
):
    """Isolated synthetic-artifact regression for FIX-SRC-HEALTH-05.

    Uses real temporary files under tmp_path with PINNED_BGE_FILES bound to
    those synthetic files' actual byte lengths and SHA-256 digests (no live
    model weights, no inference, and no hardcoded identity mocks pretending live
    acceptance).
    """
    import hashlib
    import sys

    from app import config as cfg
    from app import native_embedding
    from app.indexer import pack_vector
    from app.native_embedding import BGE_NATIVE_IDENTITY

    tmp_path = isolated_readiness_env["tmp_path"]
    data_dir = isolated_readiness_env["data_dir"]
    db_path = isolated_readiness_env["db_path"]

    bge_dir = data_dir / "models" / "bge-m3-mlx-fp16"
    bge_dir.mkdir(parents=True, exist_ok=True)
    synthetic_files = {
        "config.json": b'{"model_type":"xlm-roberta"}',
        "tokenizer.json": b'{"version":"1.0"}',
        "tokenizer_config.json": b"{}",
        "special_tokens_map.json": b"{}",
        "model.safetensors": b"SYNTHETIC_WEIGHTS_V1_" + (b"\x01" * 4096),
    }
    pinned_meta = {}
    for fname, content in synthetic_files.items():
        fpath = bge_dir / fname
        fpath.write_bytes(content)
        pinned_meta[fname] = {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    weights_path = bge_dir / "model.safetensors"
    resolved_weights = weights_path.resolve()

    monkeypatch.setattr(native_embedding, "PINNED_BGE_FILES", pinned_meta)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setattr(server, "DB_PATH", db_path)
    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(
        server,
        "probe_mlx_runtime",
        lambda force=False: {"ready": False, "code": "isolated_test", "message": "isolated"},
    )
    monkeypatch.setenv("EMBED_BACKEND", "native")

    NativeEmbeddingClient.clear_status_identity_cache()
    try:
        # Count actual binary opens/reads of synthetic model.safetensors.
        weight_reads = 0
        orig_open = Path.open

        def counted_open(self, mode="r", *args, **kwargs):
            nonlocal weight_reads
            handle = orig_open(self, mode, *args, **kwargs)
            try:
                if "rb" in str(mode) and self.resolve() == resolved_weights:
                    weight_reads += 1
            except OSError:
                pass
            return handle

        monkeypatch.setattr(Path, "open", counted_open)

        text = "Synthetic native health coverage chunk."
        vec_blob = pack_vector([1.0] + [0.0] * 1023)
        text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        vec_sha = hashlib.sha256(vec_blob).hexdigest()

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE chunks (id INTEGER PRIMARY KEY, text TEXT NOT NULL, embedding BLOB)"
            )
            conn.execute(
                "INSERT INTO chunks (id, text, embedding) VALUES (1, ?, ?)",
                (text, vec_blob),
            )
            conn.execute(
                """
                CREATE TABLE corpus_vector_provenance (
                    chunk_id INTEGER PRIMARY KEY,
                    text_sha256 TEXT NOT NULL,
                    model_digest TEXT NOT NULL,
                    vector_sha256 TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    full_text INTEGER NOT NULL,
                    embedded_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO corpus_vector_provenance VALUES (1, ?, ?, ?, 1024, 1, 'fixture')",
                (text_sha, BGE_NATIVE_IDENTITY, vec_sha),
            )

        client = TestClient(server.app, raise_server_exceptions=False)

        # 1. Installed native provenance branch: cold verify hashes once; warm polls read 0 weights.
        cov_cold = server._configured_index_coverage()
        assert weight_reads == 1
        assert cov_cold["compatible_native_chunks"] == 1
        assert cov_cold["compatible_chunks"] == 1
        assert cov_cold["status"] == "full"
        assert cov_cold["degraded_fallback"] is False
        assert cov_cold["model_identity"] == BGE_NATIVE_IDENTITY
        assert cov_cold["configured_model_identity"] == BGE_NATIVE_IDENTITY

        for _ in range(3):
            cov_warm = server._configured_index_coverage()
            assert cov_warm["compatible_native_chunks"] == 1
            assert cov_warm["status"] == "full"
            assert cov_warm["configured_model_identity"] == BGE_NATIVE_IDENTITY
            health_res = client.get("/api/health")
            assert health_res.status_code == 200
            health_cov = health_res.json()["index_coverage"]
            assert health_cov["compatible_native_chunks"] == 1
            assert health_cov["status"] == "full"
            assert health_cov["configured_model_identity"] == BGE_NATIVE_IDENTITY
        assert weight_reads == 1, "Unchanged warm health polls must not re-hash weights"

        # 2. Default-native branch (no installed bge-m3-mlx-fp16@ provenance row) also reuses status cache.
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM corpus_vector_provenance")
            conn.commit()
        monkeypatch.delenv("EMBED_BACKEND", raising=False)
        cov_default_1 = server._configured_index_coverage()
        cov_default_2 = server._configured_index_coverage()
        health_default = client.get("/api/health")
        assert health_default.status_code == 200
        assert weight_reads == 1, "Default-native health branch must reuse verified status cache"
        assert cov_default_1["configured_model_identity"] == BGE_NATIVE_IDENTITY
        assert cov_default_2["configured_model_identity"] == BGE_NATIVE_IDENTITY
        assert cov_default_1["compatible_native_chunks"] == 0
        assert cov_default_1["status"] == "keyword_only"
        assert cov_default_1["degraded_fallback"] is True

        # Restore native provenance row for subsequent checks.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO corpus_vector_provenance VALUES (1, ?, ?, ?, 1024, 1, 'fixture')",
                (text_sha, BGE_NATIVE_IDENTITY, vec_sha),
            )
            conn.commit()

        # 3. Query/admission explicit verification (get_identity) MUST still hash actual weight bytes every time.
        assert NativeEmbeddingClient().get_identity() == BGE_NATIVE_IDENTITY
        assert weight_reads == 2
        assert NativeEmbeddingClient().get_identity() == BGE_NATIVE_IDENTITY
        assert weight_reads == 3

        # 4. File-change invalidation (same-size weight modification via atomic replace):
        # next poll re-hashes, detects custom artifact, reports 0 compatible chunks, then caches custom id.
        modified_weights = b"SYNTHETIC_WEIGHTS_V2_" + (b"\x02" * 4096)
        assert len(modified_weights) == len(synthetic_files["model.safetensors"])
        staging_weights = tmp_path / "model.safetensors.tmp"
        staging_weights.write_bytes(modified_weights)
        staging_weights.replace(weights_path)

        cov_custom_1 = server._configured_index_coverage()
        reads_after_custom = weight_reads
        assert reads_after_custom > 3
        assert cov_custom_1["configured_model_identity"].startswith("bge-m3-custom@")
        assert cov_custom_1["model_identity"] is None
        assert cov_custom_1["compatible_native_chunks"] == 0
        assert cov_custom_1["compatible_chunks"] == 0
        assert cov_custom_1["status"] == "keyword_only"
        assert cov_custom_1["degraded_fallback"] is True

        cov_custom_2 = server._configured_index_coverage()
        health_custom = client.get("/api/health")
        assert health_custom.status_code == 200
        assert weight_reads == reads_after_custom
        assert cov_custom_2["compatible_native_chunks"] == 0
        assert health_custom.json()["index_coverage"]["compatible_native_chunks"] == 0
        assert health_custom.json()["index_coverage"]["configured_model_identity"].startswith("bge-m3-custom@")

        # 5. Missing / unverified artifact directory: never claims pinned identity or ready chunks.
        missing_dir = tmp_path / "absent_bge_dir"
        monkeypatch.setenv("MLX_BGE_MODEL_DIR", str(missing_dir))
        cov_missing = server._configured_index_coverage()
        assert cov_missing["configured_model_identity"] == "bge-m3-unknown@unverified"
        assert cov_missing["model_identity"] is None
        assert cov_missing["compatible_native_chunks"] == 0
        assert cov_missing["status"] == "keyword_only"
        assert cov_missing["degraded_fallback"] is True

        empty_dir = tmp_path / "empty_bge_dir"
        empty_dir.mkdir()
        monkeypatch.setenv("MLX_BGE_MODEL_DIR", str(empty_dir))
        cov_empty = server._configured_index_coverage()
        assert cov_empty["configured_model_identity"] == "bge-m3-unknown@unverified"
        assert cov_empty["model_identity"] is None
        assert cov_empty["compatible_native_chunks"] == 0

        # 6. Non-darwin guard for installed native provenance.
        monkeypatch.setenv("MLX_BGE_MODEL_DIR", str(bge_dir))
        monkeypatch.setattr(server.sys, "platform", "win32")
        cov_win = server._configured_index_coverage()
        assert cov_win["configured_model_identity"] is None
        assert cov_win["model_identity"] is None
        assert cov_win["compatible_native_chunks"] == 0
        monkeypatch.setattr(server.sys, "platform", "darwin")

        # 7. Ollama branch preserved (both ollama-sha256 provenance and EMBED_BACKEND=ollama).
        ollama_digest = "ollama-sha256:" + ("c" * 64)
        monkeypatch.setattr(server.ollama, "embedding_identity", lambda model="bge-m3": ollama_digest)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE corpus_vector_provenance SET model_digest = ?", (ollama_digest,))
            conn.commit()
        cov_ollama = server._configured_index_coverage()
        assert cov_ollama["compatible_chunks"] == 1
        assert cov_ollama["status"] == "full"
        assert cov_ollama["configured_model_identity"] == ollama_digest

        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM corpus_vector_provenance")
            conn.commit()
        monkeypatch.setenv("EMBED_BACKEND", "ollama")
        cov_ollama_env = server._configured_index_coverage()
        assert cov_ollama_env["configured_model_identity"] == ollama_digest
        assert cov_ollama_env["compatible_chunks"] == 0

        monkeypatch.setattr(server.ollama, "embedding_identity", lambda model="bge-m3": None)
        cov_ollama_missing = server._configured_index_coverage()
        assert cov_ollama_missing["compatible_chunks"] == 0
        assert cov_ollama_missing["status"] == "keyword_only"
    finally:
        NativeEmbeddingClient.clear_status_identity_cache()
