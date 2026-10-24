"""Inter-process GPU coordinator using kernel-managed file locks (fcntl.flock).

Ensures mutual exclusion between MLX training and Ollama inference on 16GB Apple Silicon,
preventing unified memory contention, OOM crashes, or web server disconnection.

Features:
- Kernel-managed fcntl.flock ensures automatic release if any process crashes or exits.
- Lock descriptor inheritance (pass_fds) guarantees the lock stays valid if the wrapper
  is killed while the MLX child process is still running.
- Unique temp file generation prevents concurrent writes to state files.
- Multiple training commands do not clobber each other's state or prematurely clear idle.
- Race-condition elimination: re-checks state immediately after acquiring shared inference lock.
- Strict Ollama model eviction: verifies HTTP success and confirms /api/ps is empty before training.
"""

import errno
from app import file_lock as fcntl
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Tuple

from app.config import BASE_DIR, DATA_DIR
from app.ollama_client import OllamaClient

logger = logging.getLogger("gpu_lock")

RUNTIME_DIR = DATA_DIR / "runtime"
LOCK_FILE = RUNTIME_DIR / "gpu.lock"
INTENT_LOCK_FILE = RUNTIME_DIR / "gpu_intent.lock"
STATE_FILE = RUNTIME_DIR / "gpu_state.json"
NATIVE_LOCK_FILE = RUNTIME_DIR / "native_worker.lock"

STATE_IDLE = "idle"
STATE_WAITING = "waiting_for_training"
STATE_TRAINING = "training"

BUSY_MESSAGE = (
    "GPU hiện đang được sử dụng để huấn luyện mô hình (MLX LoRA). "
    "Lượt tra cứu và tạo embedding tạm thời không thể xử lý để tránh tràn bộ nhớ "
    "trên máy Mac 16GB. Vui lòng thử lại sau khi quá trình học hoàn tất."
)
NATIVE_BUSY_MESSAGE = (
    "Mô hình cục bộ đang xử lý một tác vụ khác. "
    "Vui lòng đợi tác vụ đó hoàn tất rồi thử lại để tránh quá tải bộ nhớ."
)


def _ensure_runtime_dir() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def is_pid_alive(pid: Optional[int]) -> bool:
    """Check if process with given PID is still running."""
    if not pid or pid <= 0:
        return False
    if fcntl.WINDOWS:
        return fcntl.windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError as err:
        return err.errno == errno.EPERM


def _read_state_file(state_path: Path = STATE_FILE) -> Dict[str, Any]:
    if not state_path.exists():
        return {"state": STATE_IDLE, "pid": None, "child_pid": None, "updated_at": None}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"state": STATE_IDLE, "pid": None, "child_pid": None, "updated_at": None}


def _write_state_file(
    state: str,
    pid: Optional[int] = None,
    child_pid: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    state_path: Path = STATE_FILE,
) -> None:
    """Atomic write to state file using unique temp file to avoid concurrent collisions."""
    data: Dict[str, Any] = {}
    if extra:
        data.update(extra)
    data["state"] = state
    data["pid"] = pid
    data["child_pid"] = child_pid
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    temp_dir = state_path.parent
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / f"gpu_state_{os.getpid()}_{time.time_ns()}.tmp"
    try:
        temp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(state_path)
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass



class GPUBusyError(Exception):
    """Raised when GPU is busy with training."""
    pass


class GPULockDescriptors(int):
    """Wrapper around lock_fd that also carries intent_fd and fds tuple for subprocess inheritance."""
    fds: Tuple[int, ...]
    lock_fd: int
    intent_fd: int

    def __new__(cls, lock_fd: int, intent_fd: int):
        obj = super().__new__(cls, lock_fd)
        obj.lock_fd = lock_fd
        obj.intent_fd = intent_fd
        obj.fds = (lock_fd, intent_fd)
        return obj

    def __iter__(self):
        return iter(self.fds)


class GPULockManager:
    """Coordinates GPU usage between training and web inference."""

    def __init__(
        self,
        lock_path: Path = LOCK_FILE,
        state_path: Path = STATE_FILE,
        intent_path: Optional[Path] = None,
        native_lock_path: Optional[Path] = None,
    ):
        self.lock_path = lock_path
        self.state_path = state_path
        self.intent_path = intent_path or (lock_path.parent / (lock_path.stem + "_intent" + lock_path.suffix))
        self.native_lock_path = native_lock_path or (lock_path.parent / "native_worker.lock")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.intent_path.parent.mkdir(parents=True, exist_ok=True)
        self.native_lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def get_status(self) -> Dict[str, Any]:
        """Return current GPU state, auto-healing stale state if all holder processes died.
        Auto-heal is serialized using the intent lock to avoid race conditions with incoming trainers."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state_data = _read_state_file(self.state_path)
        current_state = state_data.get("state", STATE_IDLE)
        pid = state_data.get("pid")
        child_pid = state_data.get("child_pid")

        # Fast path check: if state is waiting or training, verify process and lock
        if current_state in (STATE_WAITING, STATE_TRAINING):
            child_alive = is_pid_alive(child_pid)
            lock_held = self._is_file_locked()
            intent_held = self._is_intent_locked()

            is_active = False
            if current_state == STATE_WAITING:
                is_active = intent_held and is_pid_alive(pid)
            elif current_state == STATE_TRAINING:
                is_active = lock_held or child_alive

            if is_active:
                return {
                    "state": current_state,
                    "busy": True,
                    "pid": pid,
                    "child_pid": child_pid,
                    "message": BUSY_MESSAGE,
                    "updated_at": state_data.get("updated_at"),
                }

            # Holder appears dead. Serialize auto-heal via intent lock to prevent clobbering a newly starting trainer.
            heal_fd = None
            try:
                heal_fd = os.open(str(self.intent_path), os.O_RDWR | os.O_CREAT, 0o666)
                fcntl.flock(heal_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Acquired intent lock: re-verify fresh state under the lock
                fresh_state = _read_state_file(self.state_path)
                fresh_st = fresh_state.get("state", STATE_IDLE)
                fresh_pid = fresh_state.get("pid")
                fresh_child_pid = fresh_state.get("child_pid")

                if fresh_st in (STATE_WAITING, STATE_TRAINING):
                    fresh_child_alive = is_pid_alive(fresh_child_pid)
                    fresh_lock_held = self._is_file_locked()
                    fresh_is_active = False
                    if fresh_st == STATE_WAITING:
                        fresh_is_active = is_pid_alive(fresh_pid)
                    elif fresh_st == STATE_TRAINING:
                        fresh_is_active = fresh_lock_held or fresh_child_alive

                    if not fresh_is_active:
                        logger.info("Tự động phục hồi trạng thái GPU: các tiến trình PID %s/%s đã giải phóng", fresh_pid, fresh_child_pid)
                        _write_state_file(STATE_IDLE, pid=None, state_path=self.state_path)
                        return {"state": STATE_IDLE, "busy": False, "pid": None, "message": "Sẵn sàng"}

                    return {
                        "state": fresh_st,
                        "busy": True,
                        "pid": fresh_pid,
                        "child_pid": fresh_child_pid,
                        "message": BUSY_MESSAGE,
                        "updated_at": fresh_state.get("updated_at"),
                    }
                return {"state": STATE_IDLE, "busy": False, "pid": None, "message": "Sẵn sàng"}
            except (BlockingIOError, OSError):
                # An active or incoming trainer holds the intent lock!
                fresh_state = _read_state_file(self.state_path)
                return {
                    "state": fresh_state.get("state", STATE_WAITING),
                    "busy": True,
                    "pid": fresh_state.get("pid"),
                    "child_pid": fresh_state.get("child_pid"),
                    "message": BUSY_MESSAGE,
                    "updated_at": fresh_state.get("updated_at"),
                }
            finally:
                if heal_fd is not None:
                    try:
                        fcntl.flock(heal_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                    try:
                        os.close(heal_fd)
                    except OSError:
                        pass

        return {"state": STATE_IDLE, "busy": False, "pid": None, "message": "Sẵn sàng"}

    def _is_file_locked(self) -> bool:
        """Check if lock file is currently locked by any process."""
        if not self.lock_path.exists():
            return False
        fd = None
        try:
            fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o666)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except (BlockingIOError, OSError):
            return True
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _is_intent_locked(self) -> bool:
        """Check if intent lock file is currently locked by any process."""
        if not self.intent_path.exists():
            return False
        fd = None
        try:
            fd = os.open(str(self.intent_path), os.O_RDWR | os.O_CREAT, 0o666)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except (BlockingIOError, OSError):
            return True
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _heal_idle(self, expected_pid: Optional[int] = None) -> None:
        """Heal state to idle if expected_pid matches or no expected_pid given."""
        current = _read_state_file(self.state_path)
        if expected_pid is None or current.get("pid") == expected_pid:
            _write_state_file(STATE_IDLE, pid=None, state_path=self.state_path)

    def update_child_pid(self, child_pid: int) -> None:
        """Record MLX child subprocess PID in state file for monitoring."""
        current = _read_state_file(self.state_path)
        _write_state_file(
            state=current.get("state", STATE_TRAINING),
            pid=current.get("pid", os.getpid()),
            child_pid=child_pid,
            extra=current,
            state_path=self.state_path,
        )

    def check_inference_allowed(self) -> Tuple[bool, str]:
        """Check if an inference or embedding query is permitted to run."""
        status = self.get_status()
        if status.get("busy"):
            return False, status.get("message", BUSY_MESSAGE)
        return True, "Sẵn sàng"

    @contextmanager
    def acquire_for_training(
        self,
        timeout: float = 30.0,
        extra_info: Optional[Dict[str, Any]] = None,
        evict_ollama: bool = True,
    ) -> Generator[GPULockDescriptors, None, None]:
        """
        Acquire exclusive GPU lock for MLX training with writer serialization and reader starvation prevention.
        1. Acquires intent lock (gpu_intent.lock) to serialize multiple training commands.
        2. Sets state to STATE_WAITING so no new inference queries can enter or hold shared locks.
        3. Waits for in-flight inference queries to drain and acquires LOCK_EX on gpu.lock.
        4. Sets state to STATE_TRAINING and unloads resident Ollama models (if evict_ollama=True).
        5. Makes both lock_fd and intent_fd inheritable and returns GPULockDescriptors for pass_fds.
        6. On exit/finally, checks if child process is still alive. If child is still alive, does NOT
           call LOCK_UN or reset to idle, keeping GPU protected until child terminates.
        """
        if fcntl.WINDOWS:
            raise RuntimeError("Huấn luyện MLX chỉ chạy trên máy Mac của người phụ trách; Windows dùng Ollama để phát triển tính năng.")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.intent_path.parent.mkdir(parents=True, exist_ok=True)
        current_pid = os.getpid()
        start_time = time.time()

        # Phase 1: Acquire intent lock to serialize writers
        intent_fd = os.open(str(self.intent_path), os.O_RDWR | os.O_CREAT, 0o666)
        intent_acquired = False
        while time.time() - start_time < timeout:
            try:
                fcntl.flock(intent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                intent_acquired = True
                break
            except (BlockingIOError, OSError):
                time.sleep(0.1)

        if not intent_acquired:
            try:
                os.close(intent_fd)
            except OSError:
                pass
            raise TimeoutError(
                f"Không thể lấy khóa ý định huấn luyện sau {timeout}s: "
                "có lệnh huấn luyện khác đang được chuẩn bị hoặc đang chạy."
            )

        # Phase 2: Announce STATE_WAITING. This immediately blocks any new inference queries.
        _write_state_file(
            STATE_WAITING, pid=current_pid, extra=extra_info, state_path=self.state_path
        )

        # Phase 3: Acquire exclusive lock on gpu.lock, waiting for in-flight inference readers to drain
        lock_fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o666)
        lock_acquired = False

        while time.time() - start_time < timeout:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock_acquired = True
                break
            except (BlockingIOError, OSError):
                time.sleep(0.1)

        if not lock_acquired:
            self._heal_idle(current_pid)
            try:
                os.close(lock_fd)
            except OSError:
                pass
            try:
                fcntl.flock(intent_fd, fcntl.LOCK_UN)
                os.close(intent_fd)
            except OSError:
                pass
            raise TimeoutError(
                f"Không thể lấy quyền độc quyền GPU sau {timeout}s: "
                "các truy vấn suy luận đang chạy chưa giải phóng trong thời hạn cho phép."
            )

        # Make both descriptors inheritable so child process inherits both via pass_fds
        os.set_inheritable(lock_fd, True)
        os.set_inheritable(intent_fd, True)

        try:
            # Phase 4: Now holding exclusive lock, record STATE_TRAINING
            _write_state_file(
                STATE_TRAINING, pid=current_pid, extra=extra_info, state_path=self.state_path
            )

            # Evict resident Ollama models if enabled
            if evict_ollama:
                client = OllamaClient()
                unloaded = client.unload_resident_models(timeout=10.0)
                if unloaded:
                    logger.info("Đã giải phóng mô hình Ollama khỏi bộ nhớ GPU: %s", unloaded)

            # Yield descriptors wrapper so child process inherits both lock_fd and intent_fd
            yield GPULockDescriptors(lock_fd, intent_fd)

        finally:
            # Check if child process is still running
            current_state = _read_state_file(self.state_path)
            child_pid = current_state.get("child_pid")
            child_alive = is_pid_alive(child_pid)

            if child_alive:
                # CRITICAL: Child process is still alive!
                # We MUST NOT call fcntl.flock(LOCK_UN) or reset state to idle.
                # Closing our own fds decrements refcounts but leaves child's inherited fds locked.
                logger.warning(
                    "Tiến trình con MLX (PID %s) vẫn đang chạy khi thoát ngữ cảnh; "
                    "giữ nguyên khóa GPU và trạng thái training cho tới khi tiến trình con kết thúc.",
                    child_pid
                )
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
                try:
                    os.close(intent_fd)
                except OSError:
                    pass
            else:
                # Child is finished or was never started. Cleanly reset state and release locks.
                if current_state.get("pid") == current_pid:
                    _write_state_file(STATE_IDLE, pid=None, state_path=self.state_path)

                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
                try:
                    fcntl.flock(intent_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    os.close(intent_fd)
                except OSError:
                    pass

    @contextmanager
    def acquire_for_inference(self, timeout: float = 30.0) -> Generator[Tuple[int, int], None, None]:
        """
        Acquire shared GPU lock against training AND exclusive lock for native worker admission.
        Re-checks state immediately after acquiring LOCK_SH to close any race window.
        Makes lock descriptors inheritable and yields (lock_fd, native_fd) for pass_fds in child subprocesses.
        Supports thread-local re-entrancy so nested callers in the same thread do not self-deadlock.
        """
        status = self.get_status()
        if status.get("busy"):
            raise GPUBusyError(status.get("message", BUSY_MESSAGE))

        if not hasattr(self, "_local"):
            self._local = threading.local()
        if not hasattr(self._local, "inference_depth"):
            self._local.inference_depth = 0
            self._local.active_fds = None

        if self._local.inference_depth > 0 and self._local.active_fds is not None:
            self._local.inference_depth += 1
            try:
                yield self._local.active_fds
            finally:
                self._local.inference_depth -= 1
            return

        _ensure_runtime_dir()
        lock_fd = None
        native_fd = None
        try:
            lock_fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o666)
            native_fd = os.open(str(self.native_lock_path), os.O_RDWR | os.O_CREAT, 0o666)
            start_time = time.time()
            acquired = False

            while time.time() - start_time < timeout:
                st = self.get_status()
                if st.get("busy"):
                    raise GPUBusyError(st.get("message", BUSY_MESSAGE))
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    try:
                        fcntl.flock(native_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except (BlockingIOError, OSError):
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except (BlockingIOError, OSError):
                    pass
                time.sleep(0.05)

            if not acquired:
                st = self.get_status()
                if st.get("busy"):
                    raise GPUBusyError(st.get("message", BUSY_MESSAGE))
                raise GPUBusyError(NATIVE_BUSY_MESSAGE)

            st_after = self.get_status()
            if st_after.get("busy"):
                fcntl.flock(native_fd, fcntl.LOCK_UN)
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                raise GPUBusyError(st_after.get("message", BUSY_MESSAGE))

            if not fcntl.WINDOWS:
                os.set_inheritable(lock_fd, True)
                os.set_inheritable(native_fd, True)

            self._local.inference_depth = 1
            self._local.active_fds = (lock_fd, native_fd)
            yield (lock_fd, native_fd)

        finally:
            self._local.inference_depth = 0
            self._local.active_fds = None
            if native_fd is not None:
                try:
                    os.close(native_fd)
                except OSError:
                    pass
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    pass

    acquire_for_native_inference = acquire_for_inference


# Singleton instance
gpu_coordinator = GPULockManager()
