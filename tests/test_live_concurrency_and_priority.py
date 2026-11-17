"""Regression tests for SSE stream thread affinity, busy-error propagation, and foreground GPU priority."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient

from app import gpu_lock, rag_agent as rag_agent_mod, server
from app.dialogue import DialogueBusyError, DialogueGate
from app.history import HistoryStore
from app.rag_agent import PsychologyAgent
from tests.test_qa_core_yc220 import NoTranslation, RecordingModel, RecordingSearcher, make_chunk


def test_chat_stream_keeps_single_thread_and_lock_reentrancy_under_concurrent_requests(tmp_path, monkeypatch):
    """SSE stream must hold DialogueGate + GPULockManager re-entrancy on one thread across yields."""
    import anyio
    import httpx

    mgr = gpu_lock.GPULockManager(lock_path=tmp_path / "gpu.lock", state_path=tmp_path / "gpu_state.json")
    gate = DialogueGate(path=tmp_path / "dialogue.lock")

    monkeypatch.setattr(server, "gpu_coordinator", mgr)
    monkeypatch.setattr(server, "dialogue_gate", gate)
    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "history.db"))
    monkeypatch.setattr(server, "DEFAULT_CHAT_MODEL", "test-ollama")
    monkeypatch.setattr(server, "DEFAULT_EMBED_MODEL", None)

    observed_threads: list[int] = []

    def fake_process_query_stream(**kwargs):
        observed_threads.append(threading.get_ident())
        yield {"type": "context", "input_tokens": 120, "max_tokens": 8192}
        # Re-entering GPU lock and DialogueGate inside the resumed stream must stay on the same thread
        # and succeed immediately instead of self-deadlocking on native_worker.lock / dialogue.lock.
        observed_threads.append(threading.get_ident())
        with gate.acquire(timeout=0.1), mgr.acquire_for_inference(timeout=0.1):
            pass
        yield {"type": "token", "content": "Cau tra loi co can cu. [S1]"}
        observed_threads.append(threading.get_ident())
        with gate.acquire(timeout=0.1), mgr.acquire_for_inference(timeout=0.1):
            pass
        yield {
            "type": "done",
            "citations": [{"id": "S1", "doc_id": 1, "book_title": "Test Book", "page_num": 1, "text": "Can cu."}],
            "response_kind": "answer",
            "answer_mode": "default",
            "processing_level": "careful",
            "resolved_processing_level": "careful",
            "generation_calls": 2,
            "check_status": "verified",
            "search_scope": {"kind": "library"},
        }

    monkeypatch.setattr(server.agent, "process_query_stream", fake_process_query_stream)

    async def _run():
        pinned = threading.Event()
        release = threading.Event()
        body_chunks = 0

        def hold_idle_worker():
            pinned.set()
            release.wait(timeout=2.0)

        async def asgi_interleaver(scope, receive, send):
            async def wrapped_send(message):
                nonlocal body_chunks
                await send(message)
                if message.get("type") == "http.response.body":
                    body_chunks += 1
                    if body_chunks == 1:
                        # Between chunk 1 finishing _next() and Starlette starting _next() #2,
                        # occupy the freed AnyIO worker thread, exactly as /api/status polling does
                        # while Tailscale/TCP sends the previous SSE chunk.
                        tg.start_soon(anyio.to_thread.run_sync, hold_idle_worker)
                        while not pinned.is_set():
                            await anyio.sleep(0.005)

            await server.app(scope, receive, wrapped_send)

        transport = httpx.ASGITransport(app=asgi_interleaver)
        lines: list[str] = []
        async with anyio.create_task_group() as tg:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
                try:
                    async with ac.stream(
                        "POST",
                        "/api/chat/stream",
                        json={"query": "Entropy là gì?", "processing_level": "careful", "save_history": False},
                    ) as resp:
                        assert resp.status_code == 200
                        async for ln in resp.aiter_lines():
                            if ln.startswith("data: "):
                                lines.append(ln)
                finally:
                    release.set()
        return lines

    lines = anyio.run(_run)
    assert len(set(observed_threads)) == 1
    events = [json.loads(ln[6:]) for ln in lines]
    assert any(ev.get("type") == "done" and ev.get("check_status") == "verified" for ev in events)
    assert getattr(mgr._local, "inference_depth", 0) == 0


def test_chat_stream_emits_sse_keepalive_during_long_generation(tmp_path, monkeypatch):
    """Long pauses before/between SSE events emit SSE keepalive comments so proxies stay open."""
    mgr = gpu_lock.GPULockManager(lock_path=tmp_path / "gpu.lock", state_path=tmp_path / "gpu_state.json")
    gate = DialogueGate(path=tmp_path / "dialogue.lock")

    monkeypatch.setattr(server, "gpu_coordinator", mgr)
    monkeypatch.setattr(server, "dialogue_gate", gate)
    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "history.db"))
    monkeypatch.setattr(server, "DEFAULT_CHAT_MODEL", "test-ollama")
    monkeypatch.setattr(server, "DEFAULT_EMBED_MODEL", None)
    monkeypatch.setattr(server, "STREAM_KEEPALIVE_SECONDS", 0.05, raising=False)

    def slow_stream(**kwargs):
        time.sleep(0.16)
        yield {"type": "token", "content": "Hoàn tất [S1]."}
        yield {
            "type": "done",
            "citations": [{"id": "S1", "doc_id": 1, "book_title": "Test", "page_num": 1, "text": "Hoàn tất."}],
            "response_kind": "answer",
            "generation_calls": 1,
            "check_status": "verified",
            "search_scope": {"kind": "library"},
        }

    monkeypatch.setattr(server.agent, "process_query_stream", slow_stream)

    client = TestClient(server.app)
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"query": "Test keepalive", "save_history": False},
    ) as resp:
        assert resp.status_code == 200
        raw_text = resp.read().decode("utf-8")

    assert ": keepalive" in raw_text
    assert '"type": "done"' in raw_text or '"type":"done"' in raw_text


@pytest.mark.parametrize("stage", ["primary", "repair", "auditor"])
def test_rag_agent_does_not_mask_gpu_busy_or_dialogue_busy_as_crash(stage):
    """GPUBusyError and DialogueBusyError must propagate to HTTP/SSE error handlers cleanly."""

    class BusyAtStageModel(RecordingModel):
        def chat_stream(self, **kwargs):
            self.calls.append(kwargs)
            call_idx = len(self.calls)
            if stage == "primary" and call_idx == 1:
                raise rag_agent_mod.GPUBusyError(gpu_lock.NATIVE_BUSY_MESSAGE)
            if stage == "repair":
                if call_idx == 1:
                    self.last_done_reason = "stop"
                    yield "Khong co ma nguon."
                    return
                raise DialogueBusyError("Dang ban.")
            if stage == "auditor":
                if call_idx == 1:
                    self.last_done_reason = "stop"
                    yield "Entropy đo độ bất định. [S1]"
                    return
                raise rag_agent_mod.GPUBusyError(gpu_lock.NATIVE_BUSY_MESSAGE)

    model = BusyAtStageModel()
    agent = PsychologyAgent(
        searcher=RecordingSearcher([make_chunk("Entropy đo độ bất định.")]),
        ollama=model,
        translator=NoTranslation(),
    )
    expected_exc = DialogueBusyError if stage == "repair" else rag_agent_mod.GPUBusyError
    with pytest.raises(expected_exc):
        list(
            agent.process_query_stream(
                "Entropy là gì?",
                model="test-ollama",
                embed_model=None,
                processing_level="careful" if stage == "auditor" else "standard",
            )
        )


def test_foreground_gpu_preempts_background_and_enforces_cooldown(tmp_path, monkeypatch):
    """Foreground inference cancels active background waits cleanly and keeps background deferred during cooldown."""
    monkeypatch.setenv("STUDY_BACKGROUND_GPU_COOLDOWN_SECONDS", "5.0")
    mgr = gpu_lock.GPULockManager(lock_path=tmp_path / "gpu.lock", state_path=tmp_path / "gpu_state.json")

    bg_yield = threading.Event()
    bg_acquired = threading.Event()
    bg_cancelled = threading.Event()

    def bg_worker():
        with mgr.background_scope(bg_yield):
            with mgr.acquire_for_inference(timeout=1.0, cancel_event=bg_yield):
                bg_acquired.set()
                while not bg_yield.is_set():
                    time.sleep(0.02)
                bg_cancelled.set()

    t = threading.Thread(target=bg_worker)
    t.start()
    assert bg_acquired.wait(timeout=2.0)

    t0 = time.monotonic()
    with mgr.acquire_for_inference(timeout=2.0):
        fg_wait_elapsed = time.monotonic() - t0
        assert bg_yield.is_set()
        assert mgr.should_yield_background() is True
    t.join(timeout=2.0)

    assert bg_cancelled.is_set()
    assert fg_wait_elapsed < 1.0
    # Immediately after foreground completes, background cooldown remains active.
    assert mgr.should_yield_background() is True
    with pytest.raises(gpu_lock.GPUBusyError):
        with mgr.background_scope():
            with mgr.acquire_for_inference(timeout=1.0):
                pass


def test_src_watcher_yields_to_foreground_without_burning_ocr_retries(tmp_path, monkeypatch):
    """When foreground preempts live OCR, SrcWatcher records waiting_gpu without erroring or burning retries."""
    from app import src_watcher as src_watcher_mod
    from app import vision_ocr
    from app.native_vision import NativeVisionClient
    from app.src_watcher import SrcWatcher

    monkeypatch.setenv("STUDY_BACKGROUND_GPU_COOLDOWN_SECONDS", "5.0")
    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    scan_pdf = src_dir / "scanned.pdf"
    doc = fitz.open()
    tmp_r = fitz.open()
    tp = tmp_r.new_page(width=200, height=200)
    tp.insert_text((20, 40), "Scanned page 1")
    img_bytes = tp.get_pixmap(alpha=False).tobytes("png")
    tmp_r.close()
    p = doc.new_page(width=200, height=200)
    p.insert_image(p.rect, stream=img_bytes)
    doc.save(str(scan_pdf))
    doc.close()

    mgr = gpu_lock.GPULockManager(lock_path=tmp_path / "gpu.lock", state_path=tmp_path / "gpu_state.json")
    monkeypatch.setattr("app.gpu_lock.gpu_coordinator", mgr)
    monkeypatch.setattr(vision_ocr, "gpu_coordinator", mgr)
    monkeypatch.setattr("app.src_watcher.gpu_coordinator", mgr, raising=False)
    monkeypatch.setattr(vision_ocr, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(vision_ocr, "CACHE_DIR", data_dir / "ocr_cache")
    monkeypatch.setattr(NativeVisionClient, "available", classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeVisionClient, "get_identity", lambda self: "mock-vlm-digest")

    ocr_started = threading.Event()

    def slow_or_cancellable_ocr(self, image_bytes, prompt="", cancel_event=None, **kw):
        ocr_started.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise src_watcher_mod.IndexingCancelledError("Preempted by foreground user request")
            time.sleep(0.02)
        raise AssertionError("Expected foreground preemption to cancel background OCR")

    monkeypatch.setattr(NativeVisionClient, "generate_from_image", slow_or_cancellable_ocr)

    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        debounce_seconds=0.0,
        ocr_batch_pages=1,
        auto_ocr=True,
        embed_model=None,
    )

    fg_thread = threading.Thread(
        target=lambda: (ocr_started.wait(timeout=3.0), mgr.note_foreground_activity())
    )
    fg_thread.start()
    watcher.scan_once()
    fg_thread.join(timeout=3.0)

    status = watcher.get_status()
    assert status["waiting_gpu_count"] == 1
    assert status["error_count"] == 0
    item = status["items"][0]
    assert item["status"] == "waiting_gpu"
    assert item["attempts"] == 0
    assert item["ocr_pages_error"] == 0
