"""Regression tests for 20-user concurrency, FIFO inference queueing, HTTPS anti-attack guards, upload quotas, and infra health."""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from fastapi.testclient import TestClient
import pytest

from app import accounts, server
from app.dialogue import DialogueBusyError, DialogueGate, acquire_inference
from app.native_embedding import NativeEmbeddingClient
from app.ollama_client import OllamaClient
from app.request_admission import rate_limiter
from app.trained_client import TrainedModelClient


@pytest.fixture(autouse=True)
def isolated_multiuser_env(tmp_path, monkeypatch):
    """Isolate database, accounts, materials, and local model checks inside tmp_path."""
    import app.config as cfg
    import app.study_materials as sm

    data_dir = tmp_path / "data"
    src_dir = tmp_path / "src"
    accounts_dir = tmp_path / "accounts"
    data_dir.mkdir(parents=True, exist_ok=True)
    src_dir.mkdir(parents=True, exist_ok=True)
    accounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    kb_path = tmp_path / "knowledge_base.db"
    import sqlite3
    with sqlite3.connect(kb_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, filename TEXT, clean_title TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, document_id INTEGER, text TEXT)")
        conn.commit()

    monkeypatch.setenv("AGENT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AGENT_SRC_DIR", str(src_dir))
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    monkeypatch.setenv("STUDY_ALLOW_REGISTRATION", "1")
    monkeypatch.setenv("STUDY_SECURE_COOKIES", "0")
    monkeypatch.setenv("HISTORY_DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(tmp_path / "timetable.db"))
    monkeypatch.setenv("TIMETABLE_IMAGES_DIR", str(tmp_path / "timetable_images"))

    monkeypatch.setattr(cfg, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DATA_DIR", data_dir)
    monkeypatch.setattr(cfg, "SRC_DIR", src_dir)
    monkeypatch.setattr(cfg, "DB_PATH", kb_path)
    monkeypatch.setattr(sm, "DATA_DIR", data_dir)

    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: False))
    monkeypatch.setattr(OllamaClient, "check_health", lambda self: False)
    monkeypatch.setattr(TrainedModelClient, "available", classmethod(lambda cls, *a, **kw: False))
    monkeypatch.setattr(server, "_history_store", None)

    if hasattr(server.study_material_manager, "_instances"):
        server.study_material_manager._instances.clear()
    yield tmp_path
    if hasattr(server.study_material_manager, "_instances"):
        server.study_material_manager._instances.clear()


def _register_user_client(username: str) -> TestClient:
    client = TestClient(server.app, headers={"X-Study-Request": "1"}, raise_server_exceptions=False)
    res = client.post(
        "/api/account/register",
        json={"username": username, "password": f"pw-{username}-12345"},
    )
    assert res.status_code == 200, res.text
    return client


def _collect_asgi_send():
    messages = []

    async def send(msg):
        messages.append(msg)

    def parsed():
        start = next((m for m in messages if m["type"] == "http.response.start"), None)
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        headers = {k.lower(): v for k, v in (start or {}).get("headers", ())}
        return start, headers, body

    return send, parsed


def _asgi_scope(
    method: str,
    path: str,
    *,
    scheme: str = "http",
    client_host: str = "127.0.0.1",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers or [],
        "client": (client_host, 50000),
        "server": ("127.0.0.1", 8000),
    }


def test_twenty_concurrent_accounts_isolated_crud_while_inference_busy(tmp_path):
    """20 concurrent authenticated users must perform isolated session CRUD without blocking on busy GPU inference."""
    clients = [_register_user_client(f"pilot_user_{i:02d}") for i in range(20)]
    gate = server.dialogue_gate
    gate_lock_held = threading.Event()
    release_gate = threading.Event()

    def hold_global_inference():
        with gate.acquire():
            gate_lock_held.set()
            release_gate.wait(timeout=5.0)

    holder = threading.Thread(target=hold_global_inference, daemon=True)
    holder.start()
    assert gate_lock_held.wait(timeout=2.0)

    try:
        health_during = clients[0].get("/api/health")
        assert health_during.status_code == 200
        assert health_during.json()["admission"]["active_requests"] == 1

        def run_user_flow(idx: int) -> tuple[int, str, str]:
            c = clients[idx]
            title = f"Session_{idx:02d}_Unique"
            created = c.post("/api/sessions", json={"title": title})
            assert created.status_code == 200, created.text
            sid = created.json()["id"]
            patched = c.patch(f"/api/sessions/{sid}", json={"title": f"{title}_Updated"})
            assert patched.status_code == 200, patched.text
            detail = c.get(f"/api/sessions/{sid}")
            assert detail.status_code == 200, detail.text
            return created.status_code, sid, detail.json().get("title", "")

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(run_user_flow, range(20)))

        for idx, (status_code, sid, actual_title) in enumerate(results):
            assert status_code == 200
            expected = f"Session_{idx:02d}_Unique_Updated"
            assert actual_title == expected, f"Account {idx} saw unexpected title: {actual_title}"
            other = clients[(idx + 1) % 20].get(f"/api/sessions/{sid}")
            assert other.status_code == 404
    finally:
        release_gate.set()
        holder.join(timeout=3.0)
        for c in clients:
            c.close()


def test_inference_admission_fifo_queue_and_single_inflight_per_account(tmp_path, monkeypatch):
    """Authenticated accounts queue cleanly in FIFO order on busy GPU gate, while same-account double requests fail fast."""
    monkeypatch.setenv("STUDY_MAX_INFERENCE_QUEUE", "4")
    monkeypatch.setenv("STUDY_INFERENCE_QUEUE_TIMEOUT_SECONDS", "3.0")
    gate = DialogueGate(tmp_path / "global_dialogue.lock")

    order: list[str] = []
    order_lock = threading.Lock()
    holder_entered = threading.Event()
    release_holder = threading.Event()
    errors: list[BaseException] = []

    def holder_worker():
        tok = accounts.account_id.set("aa" * 16)
        try:
            with acquire_inference(gate):
                holder_entered.set()
                release_holder.wait(timeout=5.0)
        finally:
            accounts.account_id.reset(tok)

    def waiter_worker(acct_hex: str, label: str):
        tok = accounts.account_id.set(acct_hex)
        try:
            with acquire_inference(gate):
                with order_lock:
                    order.append(label)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            accounts.account_id.reset(tok)

    t_hold = threading.Thread(target=holder_worker, daemon=True)
    t_hold.start()
    assert holder_entered.wait(timeout=2.0)

    # Same account cannot queue a second concurrent inference while already active.
    tok_same = accounts.account_id.set("aa" * 16)
    try:
        with pytest.raises(DialogueBusyError):
            with acquire_inference(gate):
                pass
    finally:
        accounts.account_id.reset(tok_same)

    waiters: list[threading.Thread] = []
    for idx, ch in enumerate(("bb", "cc", "dd")):
        t = threading.Thread(target=waiter_worker, args=(ch * 16, f"u{idx}"), daemon=True)
        t.start()
        waiters.append(t)
        time.sleep(0.05)  # Preserve deterministic submission sequence

    assert gate.get_status()["active_requests"] == 1
    assert gate.get_status()["queued_requests"] >= 1
    release_holder.set()
    t_hold.join(timeout=3.0)
    for t in waiters:
        t.join(timeout=3.0)

    assert not errors
    assert order == ["u0", "u1", "u2"]


def test_security_headers_and_conditional_hsts(monkeypatch):
    """Security headers apply to all HTTP outcomes; HSTS activates under STUDY_SECURE_COOKIES or trusted HTTPS."""
    anon = TestClient(server.app, headers={"X-Study-Request": "1"})
    unauth = anon.get("/api/sessions")
    assert unauth.status_code == 401
    for hdr, expected in (
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "SAMEORIGIN"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
        ("cross-origin-opener-policy", "same-origin"),
    ):
        assert unauth.headers.get(hdr) == expected
    assert "camera=()" in unauth.headers.get("permissions-policy", "")
    assert "strict-transport-security" not in unauth.headers
    anon.close()

    monkeypatch.setenv("STUDY_SECURE_COOKIES", "1")
    user = _register_user_client("sec_headers_user")
    ok = user.get("/api/health")
    assert ok.status_code == 200
    assert ok.headers.get("strict-transport-security") == "max-age=31536000; includeSubDomains"
    assert ok.headers.get("x-content-type-options") == "nosniff"
    user.close()

    # Trusted loopback proxy x-forwarded-proto=https adds HSTS even when env flag is 0; spoofed external IP does not.
    monkeypatch.setenv("STUDY_SECURE_COOKIES", "0")

    async def check_forwarded_hsts():
        async def empty_recv():
            return {"type": "http.request", "body": b"", "more_body": False}

        send_trusted, parsed_trusted = _collect_asgi_send()
        await server.app(
            _asgi_scope(
                "GET",
                "/api/account/status",
                client_host="127.0.0.1",
                headers=[(b"x-forwarded-proto", b"https")],
            ),
            empty_recv,
            send_trusted,
        )
        _, hdrs_trusted, _ = parsed_trusted()
        assert hdrs_trusted.get(b"strict-transport-security") == b"max-age=31536000; includeSubDomains"

        send_spoofed, parsed_spoofed = _collect_asgi_send()
        await server.app(
            _asgi_scope(
                "GET",
                "/api/account/status",
                client_host="198.51.100.42",
                headers=[(b"x-forwarded-proto", b"https")],
            ),
            empty_recv,
            send_spoofed,
        )
        _, hdrs_spoofed, _ = parsed_spoofed()
        assert b"strict-transport-security" not in hdrs_spoofed

    asyncio.run(check_forwarded_hsts())


def test_api_mutation_body_caps_256kib_and_timetable_extract_28mib():
    """General API mutations enforce 256 KiB cap and /api/timetable/extract enforces 28 MiB cap early."""
    user = _register_user_client("body_cap_user")
    oversized = b'{"title":"' + (b"x" * (256 * 1024)) + b'"}'
    assert len(oversized) > 256 * 1024

    for method, path in (
        ("POST", "/api/sessions"),
        ("PATCH", "/api/sessions/1"),
        ("POST", "/api/timetable/confirm"),
    ):
        resp = user.request(method, path, content=oversized, headers={"Content-Type": "application/json"})
        assert resp.status_code == 413, f"{method} {path} returned {resp.status_code}: {resp.text}"
        assert resp.headers.get("x-content-type-options") == "nosniff"
    user.close()

    async def check_timetable_28mib_declared_reject():
        called = False

        async def fail_if_read():
            nonlocal called
            called = True
            raise AssertionError("receive() must not run over 28 MiB Content-Length")

        send, parsed = _collect_asgi_send()
        await server.app(
            _asgi_scope(
                "POST",
                "/api/timetable/extract",
                headers=[
                    (b"content-type", b"application/json"),
                    (b"content-length", str(28 * 1024 * 1024 + 1).encode("ascii")),
                ],
            ),
            fail_if_read,
            send,
        )
        start, headers, _ = parsed()
        assert start is not None and start["status"] == 413
        assert not called
        assert headers.get(b"x-content-type-options") == b"nosniff"

    asyncio.run(check_timetable_28mib_declared_reject())


def test_sliding_window_rate_limit_per_account_cookie_and_ip(monkeypatch):
    """Rate limiter blocks flooded requests with 429 + Retry-After while isolating distinct accounts behind one NAT IP."""
    monkeypatch.setenv("STUDY_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("STUDY_INFERENCE_RATE_LIMIT_PER_MINUTE", "3")
    monkeypatch.setenv("STUDY_API_RATE_LIMIT_PER_MINUTE", "20")

    user_a = _register_user_client("rate_user_a")
    user_b = _register_user_client("rate_user_b")

    for _ in range(3):
        ok = user_a.post("/api/chat", json={"query": "Tôi muốn tự tử", "save_history": False})
        assert ok.status_code == 200, ok.text

    throttled = user_a.post("/api/chat", json={"query": "Tôi muốn tự tử", "save_history": False})
    assert throttled.status_code == 429
    assert int(throttled.headers.get("retry-after", "0")) >= 1
    assert throttled.headers.get("cache-control") == "no-store"
    assert throttled.headers.get("x-content-type-options") == "nosniff"

    # Separate authenticated session on the same client IP must retain its own budget.
    user_b_ok = user_b.post("/api/chat", json={"query": "Tôi muốn tự tử", "save_history": False})
    assert user_b_ok.status_code == 200

    # General unauthenticated endpoint cap also returns 429 cleanly.
    rate_limiter.reset()
    monkeypatch.setenv("STUDY_API_RATE_LIMIT_PER_MINUTE", "4")
    anon = TestClient(server.app)
    for _ in range(4):
        assert anon.get("/api/account/status").status_code == 200
    assert anon.get("/api/account/status").status_code == 429

    anon.close()
    user_a.close()
    user_b.close()


def test_per_account_upload_storage_quotas_and_deduplication(monkeypatch):
    """Per-account file count and storage byte quotas block non-deduplicated excess while permitting duplicates."""
    monkeypatch.setenv("STUDY_ACCOUNT_MAX_FILES", "2")
    monkeypatch.setenv("STUDY_ACCOUNT_MAX_STORAGE_MB", "1")

    user_a = _register_user_client("quota_count_user")
    doc1_bytes = b"First unique synthetic study note with enough text for validation."
    doc2_bytes = b"Second unique synthetic study note with enough text for validation."
    doc3_bytes = b"Third unique synthetic study note exceeding max file count of two."

    def upload(client: TestClient, name: str, raw: bytes):
        return client.post(
            "/api/materials/upload",
            json={
                "filename": name,
                "file_base64": base64.b64encode(raw).decode("ascii"),
                "generate_study_set": False,
            },
        )

    assert upload(user_a, "one.txt", doc1_bytes).status_code == 200
    assert upload(user_a, "two.txt", doc2_bytes).status_code == 200

    over_count = upload(user_a, "three.txt", doc3_bytes)
    assert over_count.status_code == 422
    assert "tối đa 2 tệp" in over_count.json()["detail"]

    # Deduplicated re-upload of doc1_bytes succeeds without adding a new file object.
    dedup = upload(user_a, "one_copy.txt", doc1_bytes)
    assert dedup.status_code == 200
    user_a.close()

    monkeypatch.setenv("STUDY_ACCOUNT_MAX_FILES", "10")
    user_b = _register_user_client("quota_size_user")
    chunk_700k = b"A" * (700 * 1024)
    chunk_500k = b"B" * (500 * 1024)
    assert upload(user_b, "big1.txt", chunk_700k).status_code == 200
    over_size = upload(user_b, "big2.txt", chunk_500k)
    assert over_size.status_code == 422
    assert "1MB" in over_size.json()["detail"]
    user_b.close()


def test_health_and_status_expose_sanitized_infrastructure_and_admission(tmp_path):
    """Health and status APIs report disk/KB/security/queue health without leaking host filesystem paths."""
    user = _register_user_client("health_probe_user")
    for endpoint in ("/api/health", "/api/status"):
        resp = user.get(endpoint)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        infra = data["infrastructure"]
        assert infra["disk_free_bytes"] > 0
        assert infra["disk_total_bytes"] >= infra["disk_free_bytes"]
        assert infra["knowledge_base_readable"] is True
        assert infra["accounts_dir_writable"] is True
        assert infra["security_posture"]["accounts_required"] is True
        assert infra["security_posture"]["rate_limit"]["enabled"] is True
        assert infra["limits"]["max_inference_queue"] == 20
        assert infra["limits"]["account_max_files"] == 50
        assert "admission" in data
        assert data["admission"]["max_queue_size"] == 20
        serialized = json.dumps(data, ensure_ascii=False)
        assert str(tmp_path) not in serialized
        assert "/Users/" not in serialized
    user.close()
