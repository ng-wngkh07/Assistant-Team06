"""Portable app imports, isolated workspace paths, and native lock admission."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_web_app_imports_without_fcntl(tmp_path):
    # A clean Windows interpreter has no fcntl. The stub only represents the
    # native module at import time; real Windows locking runs in the CI job.
    code = '''
import importlib.abc, subprocess, sys, types
sys.modules.pop("fcntl", None)
class NoUnixLocks(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "fcntl":
            raise ModuleNotFoundError("fcntl is unavailable on Windows")
sys.meta_path.insert(0, NoUnixLocks())
native = types.ModuleType("msvcrt")
native.LK_NBLCK = 2
native.LK_UNLCK = 0
sys.modules["msvcrt"] = native
from app.server import app
assert any(route.path == "/api/documents/search" for route in app.routes)
assert any(route.path == "/api/chat" for route in app.routes)
'''
    env = {**os.environ, "AGENT_DATA_DIR": str(tmp_path / "data")}
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_environment_selects_isolated_source_and_data(tmp_path):
    env = {**os.environ, "AGENT_DATA_DIR": str(tmp_path / "data"),
           "AGENT_SRC_DIR": str(tmp_path / "sources")}
    result = subprocess.run([sys.executable, "-c",
        "from app.config import SRC_DIR, DATA_DIR, DB_PATH; "
        "import json; print(json.dumps([str(SRC_DIR),str(DATA_DIR),str(DB_PATH)]))"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(tmp_path / "sources"),
        str(tmp_path / "data"), str(tmp_path / "data" / "knowledge_base.db")]


def test_dialogue_lock_excludes_another_process_and_releases(tmp_path):
    from app.dialogue import DialogueGate, DialogueBusyError
    path = tmp_path / "dialogue.lock"
    ready = tmp_path / "ready"
    code = ("from app.dialogue import DialogueGate; import sys; "
            "gate=DialogueGate(sys.argv[1]); "
            "ctx=gate.acquire(); ctx.__enter__(); "
            "from pathlib import Path; Path(sys.argv[2]).write_text('READY'); "
            "input(); ctx.__exit__(None,None,None)")
    proc = subprocess.Popen([sys.executable, "-c", code, str(path), str(ready)], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Child did not acquire the lock before the deadline"
        with pytest.raises(DialogueBusyError):
            with DialogueGate(path).acquire():
                pytest.fail("Two processes admitted to dialogue")
        proc.communicate("\n", timeout=10)
        assert proc.returncode == 0
        with DialogueGate(path).acquire():
            pass
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_windows_training_guard_has_no_state_side_effect(tmp_path, monkeypatch):
    from app import file_lock
    from app.gpu_lock import GPULockManager
    mgr = GPULockManager(tmp_path / "gpu.lock", tmp_path / "state.json")
    monkeypatch.setattr(file_lock, "WINDOWS", True)
    with pytest.raises(RuntimeError, match="Mac"):
        with mgr.acquire_for_training(evict_ollama=False):
            pytest.fail("Windows training was admitted")
    assert not mgr.state_path.exists()
    assert not mgr.lock_path.exists()


def test_windows_lock_contention_is_reported(tmp_path, monkeypatch):
    from app import file_lock
    import errno
    calls = []
    def lock_region(fd, operation):
        calls.append(operation)
        raise BlockingIOError(errno.EAGAIN, "Another holder")
    monkeypatch.setattr(file_lock, "WINDOWS", True)
    monkeypatch.setattr(file_lock, "_windows_lock_region", lock_region)
    fd = os.open(tmp_path / "native.lock", os.O_RDWR | os.O_CREAT)
    try:
        with pytest.raises(BlockingIOError):
            file_lock.flock(fd, file_lock.LOCK_EX | file_lock.LOCK_NB)
        assert calls == [file_lock.LOCK_EX | file_lock.LOCK_NB]
    finally:
        os.close(fd)


def test_nested_shared_inference_locks_succeed(tmp_path):
    from app.gpu_lock import GPULockManager
    mgr = GPULockManager(tmp_path / "gpu.lock", tmp_path / "state.json")
    with mgr.acquire_for_inference():
        with mgr.acquire_for_inference():
            assert mgr.get_status()["busy"] is False


def test_ollama_base_summary_does_not_require_mlx(tmp_path, monkeypatch):
    monkeypatch.setattr("app.topic_summarizer.DEFAULT_CHAT_MODEL", "qwen2.5:3b")
    from app.history import HistoryStore
    from app.topic_summarizer import TopicSummarizer
    from app.trained_client import TrainedModelClient
    store = HistoryStore(tmp_path / "history.db")
    sid = store.create_session()["id"]
    store.add_turn(sid, "Trí nhớ làm việc", "Trí nhớ giữ thông tin ngắn hạn.", [], "qwen2.5:3b")
    summarizer = TopicSummarizer(history_store=store)
    monkeypatch.setattr(TrainedModelClient, "available", lambda: False)
    monkeypatch.setattr(summarizer.ollama, "check_health", lambda: True)
    monkeypatch.setattr(summarizer.ollama, "chat_complete", lambda **kw:
        '{"title":"Trí nhớ làm việc","summary":"Hỏi về trí nhớ ngắn hạn."}')
    monkeypatch.setattr(summarizer.ollama, "last_done_reason", "stop")
    result = summarizer.summarize_session_sync(sid, model="qwen2.5:3b")
    assert result["status"] == "completed"


def test_ollama_health_is_ready_without_mlx_only_when_model_present(monkeypatch):
    from fastapi.testclient import TestClient
    from app import server
    monkeypatch.setattr(server, "DEFAULT_CHAT_MODEL", "qwen2.5:3b")
    monkeypatch.setattr(server.TrainedModelClient, "available", lambda: False)
    monkeypatch.setattr(server.ollama, "check_health", lambda: True)
    monkeypatch.setattr(server.ollama, "list_models", lambda: [{"name":"qwen2.5:3b"}])
    client = TestClient(server.app)
    assert client.get("/api/health").json()["inference_ready"] is True
    assert client.get("/api/status").json()["inference_ready"] is True
    models = client.get("/api/models").json()
    assert models["default_model"] == "qwen2.5:3b"
    assert models["default_model_backend"] == "ollama"
    assert models["default_model_available"] is True
    monkeypatch.setattr(server.ollama, "list_models", lambda: [])
    assert client.get("/api/health").json()["inference_ready"] is False
    assert client.get("/api/status").json()["inference_ready"] is False
    assert client.get("/api/models").json()["default_model_available"] is False


def test_windows_flock_accepts_file_objects(tmp_path, monkeypatch):
    from app import file_lock
    calls = []
    def lock_region(descriptor, operation):
        assert isinstance(descriptor, int), "Native Windows API requires an integer descriptor"
        calls.append((descriptor, operation))
    monkeypatch.setattr(file_lock, "WINDOWS", True)
    monkeypatch.setattr(file_lock, "_windows_lock_region", lock_region)
    with (tmp_path / "native.lock").open("a+b") as handle:
        descriptor = handle.fileno()
        file_lock.flock(handle, file_lock.LOCK_EX | file_lock.LOCK_NB)
        file_lock.flock(handle, file_lock.LOCK_UN)
    assert calls == [(descriptor, file_lock.LOCK_EX | file_lock.LOCK_NB),
                     (descriptor, file_lock.LOCK_UN)]
