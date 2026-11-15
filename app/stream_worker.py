"""Shared POSIX/local persistent NDJSON stdio worker manager with idle eviction."""

from __future__ import annotations

import atexit
import os
import select
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

from app.config import BASE_DIR
from app.gpu_lock import IndexingCancelledError, gpu_coordinator


def terminate_process_tree(proc: Optional[subprocess.Popen], grace_seconds: float = 0.5) -> None:
    """Terminate and reap an owned child worker and its POSIX session group."""
    if proc is None:
        return
    try:
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
    except OSError:
        pass
    if proc.poll() is None:
        kill_pg = False
        if os.name == "posix":
            try:
                kill_pg = os.getpgid(proc.pid) == proc.pid
            except OSError:
                kill_pg = False
        try:
            if kill_pg:
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except OSError:
            pass
        deadline = time.monotonic() + max(0.05, float(grace_seconds))
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if proc.poll() is None:
            try:
                if kill_pg:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=1.5)
    except Exception:
        pass
    for stream_attr in ("stdout", "stderr"):
        stream_obj = getattr(proc, stream_attr, None)
        if stream_obj is not None:
            try:
                stream_obj.close()
            except OSError:
                pass


class ResidentStreamWorker:
    """Thread-safe persistent NDJSON subprocess worker with idle TTL and GPU-role eviction."""

    def __init__(
        self,
        command_fn: Callable[[], List[str]],
        env_fn: Callable[[], Dict[str, str]],
        *,
        idle_ttl: float = 180.0,
        role: Optional[str] = None,
        evict_roles_on_use: Optional[set[str]] = None,
        error_prefix: str = "Không thể chạy worker MLX",
        cancel_message: str = "Đã hủy tác vụ MLX theo yêu cầu dừng.",
    ) -> None:
        self._command_fn = command_fn
        self._env_fn = env_fn
        self.idle_ttl = float(idle_ttl)
        self.role = role
        self._evict_roles_on_use = set(evict_roles_on_use) if evict_roles_on_use else None
        self._error_prefix = error_prefix
        self._cancel_message = cancel_message
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._read_buffer: bytes = b""
        self._idle_timer: Optional[threading.Timer] = None
        if role is not None:
            gpu_coordinator.register_resident_evictor(self.evict, role=role)
        else:
            gpu_coordinator.register_resident_evictor(self.evict)
        atexit.register(self.evict)

    def _cancel_idle_timer_locked(self) -> None:
        timer = self._idle_timer
        self._idle_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _schedule_idle_eviction_locked(self) -> None:
        self._cancel_idle_timer_locked()
        ttl = max(0.0, float(self.idle_ttl))
        if ttl <= 0.0:
            return
        timer = threading.Timer(ttl, self.evict)
        timer.daemon = True
        timer.start()
        self._idle_timer = timer

    def _terminate_locked(self, grace_seconds: float = 0.5) -> None:
        self._cancel_idle_timer_locked()
        proc = self._proc
        self._proc = None
        self._read_buffer = b""
        terminate_process_tree(proc, grace_seconds=grace_seconds)

    def evict(self) -> None:
        """Stop any idle resident worker and release its resources."""
        with self._lock:
            self._terminate_locked()

    def _spawn_locked(self) -> subprocess.Popen:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return proc
        self._terminate_locked()
        popen_kwargs: dict = {
            "cwd": BASE_DIR,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "bufsize": 0,
            "env": self._env_fn(),
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
            # Never inherit host GPU locks into long-lived resident workers.
            popen_kwargs["pass_fds"] = ()
        proc = subprocess.Popen(self._command_fn(), **popen_kwargs)
        self._proc = proc
        self._read_buffer = b""
        return proc

    def _write_locked(
        self,
        proc: subprocess.Popen,
        payload_bytes: bytes,
        deadline: float,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        if proc.stdin is None:
            raise RuntimeError("Worker stdin unavailable")
        fd = proc.stdin.fileno()
        if sys.platform != "win32":
            os.set_blocking(fd, False)
        total = 0
        while total < len(payload_bytes):
            if cancel_event is not None and cancel_event.is_set():
                raise IndexingCancelledError(self._cancel_message)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Hết thời gian chờ ghi yêu cầu tới worker cục bộ.")
            if sys.platform != "win32":
                _, writable, _ = select.select([], [fd], [], min(0.04, remaining))
                if not writable:
                    continue
            try:
                written = os.write(fd, payload_bytes[total:])
            except BlockingIOError:
                continue
            if written == 0:
                raise BrokenPipeError("Worker cục bộ đã đóng stdin.")
            total += written

    def _read_line_locked(
        self,
        proc: subprocess.Popen,
        deadline: float,
        cancel_event: Optional[threading.Event] = None,
    ) -> str:
        if proc.stdout is None:
            raise RuntimeError("Worker stdout unavailable")
        fd = proc.stdout.fileno()
        while b"\n" not in self._read_buffer:
            if cancel_event is not None and cancel_event.is_set():
                raise IndexingCancelledError(self._cancel_message)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Hết thời gian chờ phản hồi từ worker cục bộ.")
            if sys.platform != "win32":
                readable, _, _ = select.select([fd], [], [], min(0.04, remaining))
                if not readable:
                    if proc.poll() is not None:
                        raise EOFError("Worker cục bộ đã thoát bất ngờ.")
                    continue
            chunk = os.read(fd, 8192)
            if not chunk:
                raise EOFError("Worker cục bộ đóng luồng đầu ra.")
            self._read_buffer += chunk
        line, self._read_buffer = self._read_buffer.split(b"\n", 1)
        return line.decode("utf-8", errors="replace")

    @staticmethod
    def _read_stderr_tail(proc: subprocess.Popen) -> str:
        try:
            if proc.stderr is not None and proc.poll() is not None:
                return proc.stderr.read().decode("utf-8", errors="replace")[-1000:]
        except OSError:
            pass
        return ""

    def stream_events(
        self,
        payload_obj: dict,
        *,
        timeout: float = 600.0,
        cancel_event: Optional[threading.Event] = None,
    ):
        """Send one JSON line and yield NDJSON event dicts until terminal 'done' event or single response."""
        import json

        if self._evict_roles_on_use:
            gpu_coordinator.evict_resident_workers(roles=self._evict_roles_on_use)
        payload_line = (json.dumps(payload_obj, ensure_ascii=False) + "\n").encode("utf-8")
        deadline = time.monotonic() + max(1.0, float(timeout))
        with self._lock:
            self._cancel_idle_timer_locked()
            completed = False
            try:
                for attempt in range(2):
                    emitted_any = False
                    proc = self._spawn_locked()
                    try:
                        self._write_locked(proc, payload_line, deadline, cancel_event=cancel_event)
                        while True:
                            line = self._read_line_locked(proc, deadline, cancel_event=cancel_event)
                            if not line.strip():
                                continue
                            evt = json.loads(line)
                            if isinstance(evt, dict) and "error" in evt:
                                raise RuntimeError(f"{self._error_prefix}: {evt['error']}")
                            emitted_any = True
                            yield evt
                            if isinstance(evt, dict) and (evt.get("done") or "token" not in evt):
                                completed = True
                                break
                        break
                    except (BrokenPipeError, EOFError) as exc:
                        stderr_tail = self._read_stderr_tail(proc)
                        self._terminate_locked()
                        if attempt == 0 and not emitted_any:
                            continue
                        detail = stderr_tail or str(exc)
                        raise RuntimeError(f"{self._error_prefix}: {detail}") from exc
            finally:
                if not completed:
                    self._terminate_locked()
                else:
                    self._schedule_idle_eviction_locked()

    def request_json(
        self,
        payload_obj: dict,
        *,
        timeout: float = 420.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> dict:
        """Send one JSON line and return the single JSON dict response."""
        last_evt: Optional[dict] = None
        for evt in self.stream_events(payload_obj, timeout=timeout, cancel_event=cancel_event):
            last_evt = evt
        if not isinstance(last_evt, dict):
            raise ValueError("Phản hồi từ worker MLX phải là đối tượng JSON.")
        return last_evt
