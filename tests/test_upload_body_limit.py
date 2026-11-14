"""Regression tests for early request-body admission limits on upload, chat, and learning endpoints.

All tests run against isolated temporary storage with synthetic fixtures only;
no real course sources, serving databases, or local model weights are used.
"""

import asyncio
import base64
import gc
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app import accounts, server
from app.history import HistoryStore
from app.learning import LearningService
from app.native_embedding import NativeEmbeddingClient
from app.ollama_client import OllamaClient
from app.routes import material_routes
from app.study_materials import StudyMaterialManager
from app.trained_client import TrainedModelClient

MIB = 1024 * 1024
KIB = 1024
UPLOAD_CAP_BYTES = 28 * MIB
CHAT_CAP_BYTES = 128 * KIB
FILE_CAP_BYTES = 20 * MIB


@pytest.fixture(autouse=True)
def isolated_upload_test_env(tmp_path, monkeypatch):
    """Keep upload/account/history writes strictly inside pytest tmp_path with no model calls."""
    import app.config as cfg
    import app.study_materials as sm

    data_dir = tmp_path / "data"
    src_dir = tmp_path / "src"
    accounts_dir = tmp_path / "accounts"
    data_dir.mkdir(parents=True, exist_ok=True)
    src_dir.mkdir(parents=True, exist_ok=True)
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    monkeypatch.setenv("AGENT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AGENT_SRC_DIR", str(src_dir))
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "0")
    monkeypatch.setenv("HISTORY_DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(tmp_path / "timetable.db"))
    monkeypatch.setenv("TIMETABLE_IMAGES_DIR", str(tmp_path / "timetable_images"))

    monkeypatch.setattr(cfg, "DATA_DIR", data_dir)
    monkeypatch.setattr(cfg, "SRC_DIR", src_dir)
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(sm, "DATA_DIR", data_dir)

    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: False))
    monkeypatch.setattr(OllamaClient, "check_health", lambda self: False)
    monkeypatch.setattr(TrainedModelClient, "available", classmethod(lambda cls, *a, **kw: False))

    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "history.db"))

    # Isolate both the local fallback material manager and per-account cache.
    if hasattr(server.study_material_manager, "_instances"):
        server.study_material_manager._instances.clear()
    local_mgr = getattr(server.study_material_manager, "_local", server.study_material_manager)
    monkeypatch.setattr(local_mgr, "_materials", {})
    monkeypatch.setattr(local_mgr, "_raw_files", {})
    yield
    if hasattr(server.study_material_manager, "_instances"):
        server.study_material_manager._instances.clear()


def _http_scope(path: str, headers: list[tuple[bytes, bytes]] | None = None, method: str = "POST") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers or [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }


def _collect_send():
    messages = []

    async def send(msg):
        messages.append(msg)

    def parsed():
        start = next((m for m in messages if m["type"] == "http.response.start"), None)
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return start, body

    return send, messages, parsed


def _build_exact_size_upload_json(target_bytes: int, text: str = "Synthetic valid study text for upload limit test.") -> bytes:
    payload = json.dumps(
        {
            "filename": "synthetic.txt",
            "file_base64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "generate_study_set": False,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    pad = target_bytes - len(payload)
    assert pad >= 0, f"Base payload ({len(payload)}) already exceeds target ({target_bytes})"
    return payload + (b" " * pad)


def test_upload_declared_content_length_over_28mib_rejected_before_decode_and_receive(monkeypatch):
    decode_calls = []
    extract_calls = []
    monkeypatch.setattr(
        material_routes.base64,
        "b64decode",
        lambda *a, **k: decode_calls.append(len(a[0])) or (b"x" * (FILE_CAP_BYTES + 1)),
    )
    monkeypatch.setattr(
        StudyMaterialManager,
        "extract_document",
        lambda self, *a, **k: extract_calls.append(a),
    )

    async def run():
        for declared_len in (UPLOAD_CAP_BYTES + 1, 29_360_201):
            receive_calls = 0

            async def fail_if_called():
                nonlocal receive_calls
                receive_calls += 1
                raise AssertionError("receive() must not be called when declared Content-Length exceeds cap")

            send, _, parsed = _collect_send()
            headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(declared_len).encode("ascii")),
            ]
            await server.app(_http_scope("/api/materials/upload", headers=headers), fail_if_called, send)
            start, body = parsed()
            assert start is not None and start["status"] == 413
            assert receive_calls == 0
            assert "detail" in json.loads(body.decode("utf-8"))

    asyncio.run(run())
    assert not decode_calls
    assert not extract_calls


def test_upload_chunked_without_content_length_rejects_and_stops_reading_at_cap(monkeypatch):
    decode_calls = []
    extract_calls = []
    monkeypatch.setattr(
        material_routes.base64,
        "b64decode",
        lambda *a, **k: decode_calls.append(len(a[0])) or (b"x" * (FILE_CAP_BYTES + 1)),
    )
    monkeypatch.setattr(
        StudyMaterialManager,
        "extract_document",
        lambda self, *a, **k: extract_calls.append(a),
    )

    yielded = 0

    async def exercise():
        async def chunks():
            nonlocal yielded
            yielded += 1
            yield b'{"filename":"probe.txt","generate_study_set":false,"file_base64":"'
            # 28 MiB + 10 extra MiB sentinel chunks after cap
            for _ in range(38):
                yielded += 1
                yield b"A" * MIB
            yielded += 1
            yield b'"}'

        transport = httpx.ASGITransport(app=server.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            resp = await client.post(
                "/api/materials/upload",
                content=chunks(),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 413
            assert "detail" in resp.json()

    asyncio.run(exercise())
    # Must stop buffering once total exceeds 28 MiB (chunk 1 header + 28 chunks = 28 MiB + 62B)
    assert yielded <= 29
    assert not decode_calls
    assert not extract_calls


def test_upload_claimed_shorter_content_length_cannot_bypass_counted_bytes(monkeypatch):
    decode_calls = []
    extract_calls = []
    monkeypatch.setattr(
        material_routes.base64,
        "b64decode",
        lambda *a, **k: decode_calls.append(len(a[0])) or (b"x" * (FILE_CAP_BYTES + 1)),
    )
    monkeypatch.setattr(
        StudyMaterialManager,
        "extract_document",
        lambda self, *a, **k: extract_calls.append(a),
    )

    async def exercise():
        chunks = [b'{"filename":"probe.txt","generate_study_set":false,"file_base64":"'] + [b"A" * MIB] * 35 + [b'"}']
        idx = 0

        async def receive():
            nonlocal idx
            if idx < len(chunks):
                chunk = chunks[idx]
                idx += 1
                return {"type": "http.request", "body": chunk, "more_body": idx < len(chunks)}
            return {"type": "http.disconnect"}

        send, _, parsed = _collect_send()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", b"1"),
        ]
        await server.app(_http_scope("/api/materials/upload", headers=headers), receive, send)
        start, _ = parsed()
        assert start is not None and start["status"] == 413
        assert idx <= 29

    asyncio.run(exercise())
    assert not decode_calls
    assert not extract_calls


def test_upload_exact_28mib_boundary_and_off_by_one():
    client = TestClient(server.app, raise_server_exceptions=False)
    at_limit = _build_exact_size_upload_json(UPLOAD_CAP_BYTES)
    assert len(at_limit) == UPLOAD_CAP_BYTES
    ok_resp = client.post(
        "/api/materials/upload",
        content=at_limit,
        headers={"Content-Type": "application/json"},
    )
    assert ok_resp.status_code == 200, ok_resp.text

    over_by_one = _build_exact_size_upload_json(UPLOAD_CAP_BYTES + 1)
    assert len(over_by_one) == UPLOAD_CAP_BYTES + 1
    over_resp = client.post(
        "/api/materials/upload",
        content=over_by_one,
        headers={"Content-Type": "application/json"},
    )
    assert over_resp.status_code == 413


def test_upload_20mib_file_with_data_uri_and_options_passes_and_20mib_plus_1_hits_422():
    client = TestClient(server.app, raise_server_exceptions=False)

    raw_20mib = b"A" * FILE_CAP_BYTES
    b64_20mib = "data:text/plain;base64," + base64.b64encode(raw_20mib).decode("ascii")
    payload_20mib = {
        "filename": "synthetic_" + ("x" * 180) + ".txt",
        "file_base64": b64_20mib,
        "quiz_count": 3,
        "flashcard_count": 5,
        "difficulty": "medium",
        "generate_study_set": False,
        "persist_to_src": False,
    }
    encoded_20mib = json.dumps(payload_20mib).encode("utf-8")
    assert len(encoded_20mib) < UPLOAD_CAP_BYTES

    resp_ok = client.post(
        "/api/materials/upload",
        content=encoded_20mib,
        headers={"Content-Type": "application/json"},
    )
    assert resp_ok.status_code == 200, resp_ok.text
    mat_id = resp_ok.json()["material_id"]
    dl = client.get(f"/api/materials/download?material_id={mat_id}")
    assert dl.status_code == 200
    assert dl.content == raw_20mib

    raw_over = b"A" * (FILE_CAP_BYTES + 1)
    payload_over = {
        "filename": "synthetic_over_20mib.txt",
        "file_base64": "data:text/plain;base64," + base64.b64encode(raw_over).decode("ascii"),
        "generate_study_set": False,
        "persist_to_src": False,
    }
    encoded_over = json.dumps(payload_over).encode("utf-8")
    assert len(encoded_over) < UPLOAD_CAP_BYTES
    resp_over = client.post(
        "/api/materials/upload",
        content=encoded_over,
        headers={"Content-Type": "application/json"},
    )
    assert resp_over.status_code == 422
    assert "20MB" in resp_over.json()["detail"]


def test_small_valid_text_upload_with_accounts_is_private_and_functional():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
        user_a = TestClient(server.app, headers={"X-Study-Request": "1"})
        user_b = TestClient(server.app, headers={"X-Study-Request": "1"})
        assert user_a.post("/api/account/register", json={"username": "upload_alpha", "password": "private-example-123"}).status_code == 200
        assert user_b.post("/api/account/register", json={"username": "upload_bravo", "password": "private-example-123"}).status_code == 200

        raw_bytes = b"Synthetic private course note for isolated storage verification."
        res = user_a.post(
            "/api/materials/upload",
            json={
                "filename": "private_note.txt",
                "file_base64": base64.b64encode(raw_bytes).decode("ascii"),
                "generate_study_set": False,
            },
        )
        assert res.status_code == 200, res.text
        mat_id = res.json()["material_id"]
        assert user_a.get(f"/api/materials/{mat_id}").status_code == 200
        assert user_b.get(f"/api/materials/{mat_id}").status_code == 404
        dl = user_a.get(f"/api/materials/download?material_id={mat_id}")
        assert dl.status_code == 200 and dl.content == raw_bytes
        user_a.close()
        user_b.close()


def test_chat_and_learning_128kib_boundaries_and_streaming_preserved(monkeypatch):
    monkeypatch.setattr(
        LearningService,
        "generate",
        lambda self, **kwargs: {"topic": kwargs["topic"], "quiz": [], "flashcards": []},
    )
    client = TestClient(server.app, raise_server_exceptions=False)

    # /api/chat exact boundary (128 KiB) vs off-by-one (128 KiB + 1)
    base_chat = json.dumps({"query": "Tôi muốn tự tử", "save_history": False}, separators=(",", ":")).encode("utf-8")
    chat_exact = base_chat + b" " * (CHAT_CAP_BYTES - len(base_chat))
    assert len(chat_exact) == CHAT_CAP_BYTES
    assert client.post("/api/chat", content=chat_exact, headers={"Content-Type": "application/json"}).status_code == 200

    chat_over = base_chat + b" " * (CHAT_CAP_BYTES + 1 - len(base_chat))
    assert len(chat_over) == CHAT_CAP_BYTES + 1
    assert client.post("/api/chat", content=chat_over, headers={"Content-Type": "application/json"}).status_code == 413

    # /api/chat/stream streaming response & 128 KiB guard
    stream_ok = client.post("/api/chat/stream", json={"query": "Tôi muốn tự tử", "save_history": False})
    assert stream_ok.status_code == 200
    assert "text/event-stream" in stream_ok.headers["content-type"]
    assert "data:" in stream_ok.text
    assert client.post("/api/chat/stream", content=chat_over, headers={"Content-Type": "application/json"}).status_code == 413

    # /api/learning/generate exact boundary vs off-by-one
    base_learn = json.dumps({"topic": "Trí nhớ"}, separators=(",", ":")).encode("utf-8")
    learn_exact = base_learn + b" " * (CHAT_CAP_BYTES - len(base_learn))
    assert client.post("/api/learning/generate", content=learn_exact, headers={"Content-Type": "application/json"}).status_code == 200
    learn_over = base_learn + b" " * (CHAT_CAP_BYTES + 1 - len(base_learn))
    assert client.post("/api/learning/generate", content=learn_over, headers={"Content-Type": "application/json"}).status_code == 413


def test_account_authentication_and_origin_behavior_preserved(monkeypatch):
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    anon = TestClient(server.app, headers={"X-Study-Request": "1"})
    small_payload = {
        "filename": "note.txt",
        "file_base64": base64.b64encode(
            b"Synthetic authentication guard text that exceeds minimum extract length."
        ).decode("ascii"),
        "generate_study_set": False,
    }
    assert anon.post("/api/materials/upload", json=small_payload).status_code == 401

    assert anon.post("/api/account/register", json={"username": "origin_user", "password": "private-example-123"}).status_code == 200
    bad_origin = anon.post("/api/materials/upload", json=small_payload, headers={"Origin": "https://evil.invalid"})
    assert bad_origin.status_code == 403

    no_confirm = TestClient(server.app)
    no_confirm.cookies.set(accounts.COOKIE, anon.cookies.get(accounts.COOKIE))
    assert no_confirm.post("/api/materials/upload", json=small_payload).status_code == 403

    assert anon.post("/api/materials/upload", json=small_payload).status_code == 200
    anon.close()
    no_confirm.close()


def test_upload_body_replay_and_malformed_json(monkeypatch):
    raw_content = b"Synthetic multi-chunk body replay verification content."
    payload = json.dumps(
        {
            "filename": "replayed.txt",
            "file_base64": base64.b64encode(raw_content).decode("ascii"),
            "generate_study_set": False,
        }
    ).encode("utf-8")

    async def exercise_replay():
        async def chunked():
            step = 13
            for i in range(0, len(payload), step):
                yield payload[i : i + step]

        transport = httpx.ASGITransport(app=server.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            res = await client.post(
                "/api/materials/upload",
                content=chunked(),
                headers={"Content-Type": "application/json"},
            )
            assert res.status_code == 200, res.text
            mat_id = res.json()["material_id"]
            dl = await client.get(f"/api/materials/download?material_id={mat_id}")
            assert dl.status_code == 200
            assert dl.content == raw_content

    asyncio.run(exercise_replay())

    decode_calls = []
    monkeypatch.setattr(material_routes.base64, "b64decode", lambda *a, **k: decode_calls.append(a))
    client = TestClient(server.app, raise_server_exceptions=False)
    malformed_small = client.post(
        "/api/materials/upload",
        content=b'{"filename":"bad.txt","file_base64":',
        headers={"Content-Type": "application/json"},
    )
    assert malformed_small.status_code == 422

    malformed_huge = b'{"filename":' + (b"x" * (UPLOAD_CAP_BYTES + 64))
    malformed_over = client.post(
        "/api/materials/upload",
        content=malformed_huge,
        headers={"Content-Type": "application/json"},
    )
    assert malformed_over.status_code == 413
    assert not decode_calls


def test_upload_disconnect_and_cancellation_cleanup(monkeypatch):
    decode_calls = []
    monkeypatch.setattr(material_routes.base64, "b64decode", lambda *a, **k: decode_calls.append(a))

    # 1. Mid-body http.disconnect must clean up and never reach endpoint/decoder or raise 500
    async def run_disconnect():
        msgs = [
            {"type": "http.request", "body": b'{"filename":"partial.txt","file_base64":"', "more_body": True},
            {"type": "http.disconnect"},
        ]

        async def receive():
            return msgs.pop(0) if msgs else {"type": "http.disconnect"}

        send, sent, _ = _collect_send()
        await server.app(_http_scope("/api/materials/upload"), receive, send)
        assert sent == []

    asyncio.run(run_disconnect())
    assert not decode_calls

    # 2. Task cancellation mid-upload must propagate CancelledError and release partial buffer
    unique_tag = os.urandom(24)

    async def run_cancel():
        started = asyncio.Event()
        delivered_first = False

        async def receive():
            nonlocal delivered_first
            if not delivered_first:
                delivered_first = True
                return {
                    "type": "http.request",
                    "body": b'{"filename":"cancel.txt","file_base64":"' + unique_tag + (b"B" * (256 * KIB)),
                    "more_body": True,
                }
            started.set()
            await asyncio.Future()

        send, _, _ = _collect_send()
        task = asyncio.create_task(server.app(_http_scope("/api/materials/upload"), receive, send))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_cancel())
    gc.collect()
    retained = [obj for obj in gc.get_objects() if isinstance(obj, bytes) and unique_tag in obj and obj != unique_tag]
    assert retained == []
    assert not decode_calls


def test_concurrent_requests_do_not_share_body_state():
    async def exercise():
        transport = httpx.ASGITransport(app=server.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            valid_items = [
                (f"concurrent_{i}.txt", f"Unique synthetic concurrent body content #{i}.".encode("utf-8"))
                for i in range(4)
            ]

            async def send_valid(name: str, raw: bytes):
                b64 = base64.b64encode(raw).decode("ascii")
                res = await client.post(
                    "/api/materials/upload",
                    json={"filename": name, "file_base64": b64, "generate_study_set": False},
                )
                assert res.status_code == 200, res.text
                mat = res.json()["material"]
                assert mat["filename"] == name
                assert mat["pages"][0]["text"] == raw.decode("utf-8")
                dl = await client.get(f"/api/materials/download?material_id={mat['id']}")
                assert dl.status_code == 200 and dl.content == raw

            async def send_oversize():
                async def chunks():
                    yield b'{"filename":"big.txt","generate_study_set":false,"file_base64":"'
                    for _ in range(29):
                        yield b"A" * MIB
                    yield b'"}'

                res = await client.post(
                    "/api/materials/upload",
                    content=chunks(),
                    headers={"Content-Type": "application/json"},
                )
                assert res.status_code == 413

            async def send_chat():
                res = await client.post(
                    "/api/chat",
                    json={"query": "Tôi muốn tự tử", "save_history": False},
                )
                assert res.status_code == 200 and res.json()["is_crisis"] is True

            await asyncio.gather(
                *[send_valid(n, r) for n, r in valid_items],
                send_oversize(),
                send_oversize(),
                send_chat(),
            )

    asyncio.run(exercise())
