"""Comprehensive unit and integration tests for GPU coordination and recovery.

Tests:
1. Mutual exclusion between training and inference.
2. Two concurrent training commands: second cannot overwrite or clear first's state.
3. Lock inheritance: if wrapper process dies, child process holding lock descriptor
   keeps GPU locked until child finishes.
4. Race condition prevention: re-check after shared lock acquisition.
5. Ollama eviction verification: confirms HTTP success and empty /api/ps.
6. Web server endpoints behavior during training lock:
   - Non-GPU endpoints (/, /api/health, /api/status, /api/history) return 200.
   - /api/chat returns 503 with clear Vietnamese message.
   - /api/chat/stream yields busy event.
   - Blank query returns 422.
"""

import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR
from app.gpu_lock import GPULockManager, GPUBusyError, STATE_TRAINING, STATE_IDLE, STATE_WAITING, is_pid_alive
from app.ollama_client import OllamaClient
from app.server import app


@pytest.fixture
def temp_gpu_lock(tmp_path):
    """Provide an isolated GPULockManager for testing."""
    lock_file = tmp_path / "gpu.lock"
    state_file = tmp_path / "gpu_state.json"
    mgr = GPULockManager(lock_path=lock_file, state_path=state_file)
    return mgr, lock_file, state_file


def test_basic_training_inference_mutual_exclusion(temp_gpu_lock):
    mgr, _, _ = temp_gpu_lock
    assert mgr.get_status()["busy"] is False

    with mgr.acquire_for_training(timeout=5.0, evict_ollama=False):
        status = mgr.get_status()
        assert status["busy"] is True
        assert status["state"] == STATE_TRAINING

        # Inference attempt must fail
        with pytest.raises(GPUBusyError):
            with mgr.acquire_for_inference():
                pass

    # After training exits, state must return to idle
    assert mgr.get_status()["busy"] is False
    with mgr.acquire_for_inference():
        pass


def test_concurrent_inference_readers_succeed(temp_gpu_lock):
    mgr, _, _ = temp_gpu_lock
    assert mgr.get_status()["busy"] is False

    # Two concurrent readers acquiring shared lock simultaneously must both succeed
    with mgr.acquire_for_inference():
        status1 = mgr.get_status()
        assert status1["busy"] is False
        with mgr.acquire_for_inference():
            status2 = mgr.get_status()
            assert status2["busy"] is False

    assert mgr.get_status()["busy"] is False


def test_native_worker_contention_does_not_claim_training_is_running(temp_gpu_lock):
    mgr, lock_file, state_file = temp_gpu_lock
    contender = GPULockManager(lock_path=lock_file, state_path=state_file)
    with mgr.acquire_for_inference():
        with pytest.raises(GPUBusyError) as error:
            with contender.acquire_for_inference(timeout=0.01):
                pass
    assert "huấn luyện" not in str(error.value).lower()
    assert "tác vụ" in str(error.value).lower()


def _worker_hold_training_lock(lock_file, state_file, hold_seconds, ready_event):
    mgr = GPULockManager(lock_path=Path(lock_file), state_path=Path(state_file))
    with mgr.acquire_for_training(timeout=5.0, extra_info={"worker": "proc1"}, evict_ollama=False):
        ready_event.set()
        time.sleep(hold_seconds)


def test_concurrent_training_commands_preserves_first_state(tmp_path):
    lock_file = tmp_path / "gpu.lock"
    state_file = tmp_path / "gpu_state.json"
    ready = multiprocessing.Event()

    # Process 1 acquires training lock for 2 seconds
    p1 = multiprocessing.Process(
        target=_worker_hold_training_lock,
        args=(str(lock_file), str(state_file), 2.0, ready)
    )
    p1.start()
    try:
        assert ready.wait(timeout=5.0)

        # Process 2 tries to acquire with short timeout
        mgr2 = GPULockManager(lock_path=lock_file, state_path=state_file)
        with pytest.raises(TimeoutError):
            with mgr2.acquire_for_training(timeout=0.4, extra_info={"worker": "proc2"}, evict_ollama=False):
                pass

        # Verify that process 2's timeout DID NOT overwrite process 1's state
        status_during = mgr2.get_status()
        assert status_during["busy"] is True
        assert status_during["pid"] == p1.pid

    finally:
        p1.join(timeout=5.0)

    # After p1 finishes, state becomes idle
    mgr_after = GPULockManager(lock_path=lock_file, state_path=state_file)
    assert mgr_after.get_status()["busy"] is False


def _wrapper_dies_child_sleeps(lock_file, state_file, child_sleep_sec, pipe_out):
    """Wrapper process that acquires lock, spawns child inheriting fds, then kills itself."""
    import subprocess
    mgr = GPULockManager(lock_path=Path(lock_file), state_path=Path(state_file))
    with mgr.acquire_for_training(timeout=5.0, evict_ollama=False) as lock_info:
        fds = getattr(lock_info, "fds", (int(lock_info),))
        # Spawn child python process that sleeps, inheriting both lock_fd and intent_fd
        cmd = [sys.executable, "-c", f"import time, sys; time.sleep({child_sleep_sec})"]
        proc = subprocess.Popen(cmd, pass_fds=fds)
        mgr.update_child_pid(proc.pid)
        pipe_out.send(proc.pid)
        pipe_out.close()
        # Wrapper exits abruptly (simulates kill -9 / crash)
        os._exit(0)


def test_lock_inheritance_when_wrapper_killed_child_alive(tmp_path):
    lock_file = tmp_path / "gpu.lock"
    state_file = tmp_path / "gpu_state.json"
    pipe_in, pipe_out = multiprocessing.Pipe(duplex=False)

    p_wrapper = multiprocessing.Process(
        target=_wrapper_dies_child_sleeps,
        args=(str(lock_file), str(state_file), 2.0, pipe_out)
    )
    p_wrapper.start()
    child_pid = None
    try:
        if pipe_in.poll(5.0):
            child_pid = pipe_in.recv()
        else:
            p_wrapper.terminate()
            p_wrapper.join(timeout=2.0)
            pytest.fail("Worker process did not send child_pid within 5s")
    finally:
        pipe_in.close()
        p_wrapper.join(timeout=5.0)
        if p_wrapper.is_alive():
            p_wrapper.terminate()
            p_wrapper.join(timeout=2.0)

    # At this point, the wrapper process is dead!
    assert p_wrapper.is_alive() is False

    # But the child process is still running and holds the inherited descriptors!
    mgr = GPULockManager(lock_path=lock_file, state_path=state_file)
    status_while_child_running = mgr.get_status()
    assert status_while_child_running["busy"] is True
    assert status_while_child_running["child_pid"] == child_pid

    # 1. Inference attempt while child is running MUST be blocked
    with pytest.raises(GPUBusyError):
        with mgr.acquire_for_inference():
            pass

    # 2. A second training attempt MUST ALSO be blocked and cannot overwrite child state
    mgr2 = GPULockManager(lock_path=lock_file, state_path=state_file)
    with pytest.raises(TimeoutError):
        with mgr2.acquire_for_training(timeout=0.3, evict_ollama=False):
            pass
    assert mgr2.get_status()["child_pid"] == child_pid

    # Wait for the child process to complete
    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        try:
            os.kill(child_pid, 0)
            time.sleep(0.1)
        except OSError:
            break

    # Now child has exited; lock must automatically free!
    time.sleep(0.2)
    status_after_child = mgr.get_status()
    assert status_after_child["busy"] is False

    # Inference should now succeed without error
    with mgr.acquire_for_inference():
        pass


def test_ollama_unload_verification():
    client = OllamaClient(base_url="http://127.0.0.1:11434")

    # Case 1: /api/ps returns models, POST unload succeeds, /api/ps becomes empty
    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        # First call to /api/ps returns 1 model, subsequent call returns empty
        resp_with_model = MagicMock(status_code=200)
        resp_with_model.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}

        resp_empty = MagicMock(status_code=200)
        resp_empty.json.return_value = {"models": []}

        mock_get.side_effect = [resp_with_model, resp_empty, resp_empty]
        mock_post.return_value = MagicMock(status_code=200, text="ok")

        unloaded = client.unload_resident_models(timeout=2.0)
        assert unloaded == ["qwen2.5:7b"]

    # Case 2: POST unload fails (non-200) -> must raise RuntimeError
    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"models": [{"name": "qwen2.5:7b"}]})
        mock_post.return_value = MagicMock(status_code=500, text="internal error")

        with pytest.raises(RuntimeError) as exc:
            client.unload_resident_models(timeout=1.0)
        assert "thất bại" in str(exc.value)

    # Case 3: /api/ps refuses to empty within timeout -> must raise RuntimeError
    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"models": [{"name": "stuck:7b"}]})
        mock_post.return_value = MagicMock(status_code=200, text="ok")

        with pytest.raises(RuntimeError) as exc:
            client.unload_resident_models(timeout=0.6)
        assert "Không thể xác nhận GPU rỗng" in str(exc.value)

    # Case 4: /api/ps returns HTTP 500 during polling -> must raise RuntimeError (not falsely succeed)
    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        resp_with_model = MagicMock(status_code=200)
        resp_with_model.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}
        resp_500 = MagicMock(status_code=500, text="server error")
        mock_get.side_effect = [resp_with_model, resp_500, resp_500, resp_500]
        mock_post.return_value = MagicMock(status_code=200, text="ok")

        with pytest.raises(RuntimeError) as exc:
            client.unload_resident_models(timeout=0.6)
        assert "Không thể xác nhận GPU rỗng" in str(exc.value)

    # Case 5: /api/ps returns malformed JSON during polling -> must raise RuntimeError
    with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
        resp_with_model = MagicMock(status_code=200)
        resp_with_model.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}
        resp_bad = MagicMock(status_code=200)
        resp_bad.json.side_effect = ValueError("invalid json syntax")
        mock_get.side_effect = [resp_with_model, resp_bad, resp_bad, resp_bad]
        mock_post.return_value = MagicMock(status_code=200, text="ok")

        with pytest.raises(RuntimeError) as exc:
            client.unload_resident_models(timeout=0.6)
        assert "Không thể xác nhận GPU rỗng" in str(exc.value)


def _worker_training_intent_writer(lock_file, state_file, intent_file):
    m = GPULockManager(
        lock_path=Path(lock_file),
        state_path=Path(state_file),
        intent_path=Path(intent_file),
    )
    try:
        with m.acquire_for_training(timeout=3.0, evict_ollama=False):
            pass
    except Exception:
        pass


def test_writer_intent_blocks_new_inference_while_waiting(tmp_path):
    lock_file = tmp_path / "gpu.lock"
    state_file = tmp_path / "gpu_state.json"
    intent_file = tmp_path / "gpu_intent.lock"

    mgr = GPULockManager(lock_path=lock_file, state_path=state_file, intent_path=intent_file)

    # Reader holds shared inference lock
    with mgr.acquire_for_inference():
        # Writer starts waiting in background
        p_writer = multiprocessing.Process(
            target=_worker_training_intent_writer,
            args=(str(lock_file), str(state_file), str(intent_file)),
        )
        p_writer.start()

        # Wait until writer enters STATE_WAITING
        t0 = time.time()
        while time.time() - t0 < 3.0:
            st = mgr.get_status()
            if st["state"] == STATE_WAITING:
                break
            time.sleep(0.05)

        assert mgr.get_status()["state"] == STATE_WAITING
        assert mgr.get_status()["busy"] is True

        # Now verify: new inference queries are BLOCKED immediately!
        allowed, msg = mgr.check_inference_allowed()
        assert allowed is False
        assert "MLX LoRA" in msg

        with pytest.raises(GPUBusyError):
            with mgr.acquire_for_inference():
                pass

        # Verify: a second training command cannot acquire intent lock
        mgr2 = GPULockManager(lock_path=lock_file, state_path=state_file, intent_path=intent_file)
        with pytest.raises(TimeoutError):
            with mgr2.acquire_for_training(timeout=0.2, evict_ollama=False):
                pass

    # Reader released lock; background writer finishes
    p_writer.join(timeout=3.0)

    # State returns to idle
    st_after = mgr.get_status()
    assert st_after["busy"] is False
    assert st_after["state"] == STATE_IDLE


def test_wait_exception_preserves_child_until_natural_completion(tmp_path):
    lock_file = tmp_path / "gpu.lock"
    state_file = tmp_path / "gpu_state.json"
    intent_file = tmp_path / "gpu_intent.lock"

    mgr = GPULockManager(lock_path=lock_file, state_path=state_file, intent_path=intent_file)

    child_cmd = [sys.executable, "-c", "import time; time.sleep(1.5)"]
    child_pid = None
    proc = None
    try:
        with pytest.raises(KeyboardInterrupt):
            with mgr.acquire_for_training(timeout=5.0, evict_ollama=False) as lock_info:
                fds = getattr(lock_info, "fds", (int(lock_info),))
                proc = subprocess.Popen(child_cmd, pass_fds=fds)
                child_pid = proc.pid
                mgr.update_child_pid(proc.pid)
                # Losing the observer does not authorize cancelling the child.
                raise KeyboardInterrupt("Simulated user interrupt during MLX run")
        assert is_pid_alive(child_pid)
        assert mgr.get_status()["busy"] is True
        with pytest.raises(GPUBusyError):
            with mgr.acquire_for_inference():
                pass
    finally:
        if proc is not None:
            proc.wait()

    # Only natural completion releases the child's inherited descriptors.
    assert not is_pid_alive(child_pid)

    # Verify that lock and state cleanly returned to idle
    st = mgr.get_status()
    assert st["busy"] is False
    assert st["state"] == STATE_IDLE

    # Verify that inference can acquire lock immediately
    with mgr.acquire_for_inference():
        pass


def test_child_survival_preserves_lock_if_wrapper_exits_early(tmp_path):
    lock_file = tmp_path / "gpu.lock"
    state_file = tmp_path / "gpu_state.json"
    intent_file = tmp_path / "gpu_intent.lock"

    mgr = GPULockManager(lock_path=lock_file, state_path=state_file, intent_path=intent_file)

    child_cmd = [sys.executable, "-c", "import time; time.sleep(1.5)"]
    child_proc = None

    # Suppose a caller exits the context manager early without waiting for child
    with mgr.acquire_for_training(timeout=5.0, evict_ollama=False) as lock_info:
        fds = getattr(lock_info, "fds", (int(lock_info),))
        child_proc = subprocess.Popen(child_cmd, pass_fds=fds)
        mgr.update_child_pid(child_proc.pid)

    try:
        # Context exited, BUT child is still running!
        # The lock MUST NOT be idle!
        st = mgr.get_status()
        assert st["busy"] is True
        assert st["state"] == STATE_TRAINING
        assert st["child_pid"] == child_proc.pid

        # Inference MUST still be rejected!
        with pytest.raises(GPUBusyError):
            with mgr.acquire_for_inference():
                pass

        # Wait for child to complete
        child_proc.wait()

        # Now that child finished, state auto-heals to idle
        time.sleep(0.2)
        assert mgr.get_status()["busy"] is False

    finally:
        child_proc.wait()


def test_web_endpoints_during_gpu_training(tmp_path, monkeypatch):
    import app.server as server
    from app.history import HistoryStore

    # Provide isolated lock manager and isolated history store in tmp_path
    test_lock = tmp_path / "gpu.lock"
    test_state = tmp_path / "gpu_state.json"
    test_intent = tmp_path / "gpu_intent.lock"
    isolated_coordinator = GPULockManager(
        lock_path=test_lock, state_path=test_state, intent_path=test_intent
    )
    isolated_history = HistoryStore(tmp_path / "test_history.db")

    monkeypatch.setattr(server, "gpu_coordinator", isolated_coordinator)
    monkeypatch.setattr(server, "history_store", isolated_history)

    client = TestClient(server.app)

    # 1. Normal state: all endpoints 200
    r_health = client.get("/api/health")
    assert r_health.status_code == 200

    r_status = client.get("/api/status")
    assert r_status.status_code == 200
    assert r_status.json()["gpu"]["busy"] is False

    r_history = client.get("/api/history")
    assert r_history.status_code == 200

    r_index = client.get("/")
    assert r_index.status_code == 200

    # 2. Blank question returns 422 regardless of GPU
    r_blank = client.post("/api/chat", json={"query": "   "})
    assert r_blank.status_code == 422

    # 3. Acquire GPU training lock on isolated coordinator
    with isolated_coordinator.acquire_for_training(timeout=5.0, evict_ollama=False):
        # Non-GPU endpoints must remain 200 OK!
        r_health_busy = client.get("/api/health")
        assert r_health_busy.status_code == 200

        r_status_busy = client.get("/api/status")
        assert r_status_busy.status_code == 200
        assert r_status_busy.json()["gpu"]["busy"] is True
        assert "MLX LoRA" in r_status_busy.json()["gpu"]["message"]

        r_history_busy = client.get("/api/history")
        assert r_history_busy.status_code == 200

        r_index_busy = client.get("/")
        assert r_index_busy.status_code == 200

        # Blank query still returns 422
        assert client.post("/api/chat", json={"query": ""}).status_code == 422

        # Chat endpoint must return 503 with Vietnamese message
        r_chat_busy = client.post("/api/chat", json={"query": "Tâm lý học là gì?"})
        assert r_chat_busy.status_code == 503
        assert "MLX LoRA" in r_chat_busy.json()["detail"]

        # Stream endpoint returns busy SSE event
        r_stream_busy = client.post("/api/chat/stream", json={"query": "Tâm lý học là gì?"})
        assert r_stream_busy.status_code == 200
        stream_text = r_stream_busy.text
        assert "gpu_busy" in stream_text
        assert "MLX LoRA" in stream_text

    # 4. After lock released, status returns to idle
    r_status_idle = client.get("/api/status")
    assert r_status_idle.status_code == 200
    assert r_status_idle.json()["gpu"]["busy"] is False


@pytest.mark.skipif(os.name == 'nt', reason='POSIX inherited descriptor lifetime')
@pytest.mark.parametrize('lock_name', ['gpu.lock', 'native_worker.lock'])
def test_inference_wrapper_exit_keeps_live_child_locks(tmp_path, lock_name):
    """An orphaned worker must still exclude both training and another native load."""
    import subprocess
    from app import file_lock as fcntl
    from app.gpu_lock import GPULockManager
    manager = GPULockManager(lock_path=tmp_path/'gpu.lock',
        state_path=tmp_path/'state.json', native_lock_path=tmp_path/'native_worker.lock')
    child = None
    try:
        with manager.acquire_for_inference(timeout=1) as descriptors:
            child = subprocess.Popen([sys.executable, '-c',
                "import sys; print('ready',flush=True); sys.stdin.read()"],
                pass_fds=tuple(descriptors), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert child.stdout.readline().strip() == 'ready'
        assert child.poll() is None
        fd = os.open(str(tmp_path/lock_name), os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    finally:
        if child is not None:
            child.communicate(input='', timeout=5)
