"""Direct native MLX vision inference client."""

import atexit
import hashlib
import inspect
import io
import json
import os
import signal
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageOps

from app.config import BASE_DIR
from app.gpu_lock import IndexingCancelledError, gpu_coordinator
from app.model_resolver import MLX_9B_DIR, resolve_mlx_model_dir, validate_native_model_config
from app.stream_worker import ResidentStreamWorker

MAX_INFERENCE_EDGE = 1024

_ORIG_SUBPROCESS_RUN = subprocess.run
_ORIG_SUBPROCESS_POPEN = subprocess.Popen
_ACTIVE_WORKERS_LOCK = threading.Lock()
_ACTIVE_WORKERS: set[subprocess.Popen] = set()


def _terminate_owned_worker(proc: subprocess.Popen, grace_seconds: float = 0.5) -> None:
    """Terminate and reap ONLY the owned child worker and its session group."""
    if proc.poll() is not None:
        if os.name == "nt" and getattr(proc, "_handle", None) is not None:
            try:
                proc._handle.Close()
            except Exception:  # nosec B110
                pass
        return
    kill_pg = False
    if os.name == "posix":
        try:
            kill_pg = os.getpgid(proc.pid) == proc.pid
        except OSError:
            kill_pg = False
    try:
        if os.name == "nt":
            try:
                kill_proc = subprocess.Popen(  # nosec B603, B607
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    kill_proc.wait(timeout=3.0)
                finally:
                    if getattr(kill_proc, "_handle", None) is not None and kill_proc.returncode is not None:
                        try:
                            kill_proc._handle.Close()
                        except Exception:  # nosec B110
                            pass
            except (OSError, subprocess.SubprocessError):
                pass
            if proc.poll() is None:
                proc.terminate()
        elif kill_pg:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except OSError:
        pass

    deadline = time.monotonic() + max(0.1, float(grace_seconds))
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
        proc.wait(timeout=2.0)
    except Exception:  # nosec B110
        pass
    if os.name == "nt" and getattr(proc, "_handle", None) is not None and proc.returncode is not None:
        try:
            proc._handle.Close()
        except Exception:  # nosec B110
            pass


def cancel_active_vision_workers() -> int:
    """Terminate all active owned native vision subprocesses immediately."""
    NativeVisionClient.evict_stream_worker()
    with _ACTIVE_WORKERS_LOCK:
        procs = list(_ACTIVE_WORKERS)
    for proc in procs:
        _terminate_owned_worker(proc, grace_seconds=0.3)
    return len(procs)


def _cleanup_active_workers() -> None:
    cancel_active_vision_workers()


atexit.register(_cleanup_active_workers)


def _acquire_inference_lock(timeout: float = 30.0, cancel_event: Optional[threading.Event] = None):
    fn = gpu_coordinator.acquire_for_inference
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        kwargs: Dict[str, Any] = {}
        if has_var_kw or "timeout" in params:
            kwargs["timeout"] = timeout
        if (has_var_kw or "cancel_event" in params) and cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        return fn(**kwargs)
    except (TypeError, ValueError):
        return fn()


class NativeVisionClient:
    """Direct local MLX vision client using Qwen3.5-9B-4bit via mlx-vlm."""

    _STATUS_IDENTITY_FILES = (
        "config.json",
        "model.safetensors.index.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.json",
        "merges.txt",
        "chat_template.jinja",
        "processor_config.json",
        "preprocessor_config.json",
        "video_preprocessor_config.json",
    )
    _status_identity_lock = threading.Lock()
    _status_identity_cache: "OrderedDict[str, Tuple[Tuple[Any, ...], str]]" = OrderedDict()
    _STATUS_IDENTITY_CACHE_MAX = 8
    _PARALLEL_HASH_MIN_BYTES = 1024 * 1024

    def __init__(self, model_dir: Optional[Path] = None):
        self.model_dir = Path(model_dir or resolve_mlx_model_dir("qwen3.5-9b-4bit"))

    @classmethod
    def clear_status_identity_cache(cls) -> None:
        """Clear cached status identities after explicit configuration or test resets."""
        with cls._status_identity_lock:
            cls._status_identity_cache.clear()

    @classmethod
    def _artifact_files(cls, model_dir: Path) -> List[Path]:
        files = set(model_dir.glob("*.safetensors"))
        files.update(model_dir / name for name in cls._STATUS_IDENTITY_FILES)
        return sorted(files)

    @classmethod
    def _artifact_status_signature(cls, resolved: Path) -> Optional[Tuple[Any, ...]]:
        if not resolved.is_dir():
            return None
        try:
            files = cls._artifact_files(resolved)
        except OSError:
            return None
        stats: List[Tuple[Any, ...]] = []
        found_any = False
        for path in files:
            try:
                if path.is_file():
                    st = path.stat()
                    found_any = True
                    stats.append((
                        path.name,
                        True,
                        int(st.st_dev),
                        int(st.st_ino),
                        int(st.st_size),
                        int(st.st_mtime_ns),
                        int(st.st_ctime_ns),
                    ))
                else:
                    stats.append((path.name, False))
            except OSError:
                return None
        return tuple(stats) if found_any else None

    @staticmethod
    def _digest_artifact_file(path: Path) -> bytes:
        if not path.is_file():
            return b"missing"
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").digest()

    def get_identity(self) -> str:
        """Bind cached recognition to actual local weights and processor assets.

        Read file bytes on each request; preserved size/mtime is not proof of identity.
        The prefix denotes a content fingerprint, never an inferred Hub revision.
        """
        from app.trained_client import TrainedModelClient
        validate_native_model_config(self.model_dir)
        if not TrainedModelClient._verify_weights(self.model_dir):
            raise RuntimeError("Incomplete local vision artifact")
        resolved = self.model_dir.resolve()
        pre_sig = self._artifact_status_signature(resolved)
        ordered_files = self._artifact_files(self.model_dir)
        large_count = 0
        for path in ordered_files:
            try:
                if path.is_file() and path.stat().st_size >= self._PARALLEL_HASH_MIN_BYTES:
                    large_count += 1
            except OSError:
                pass
        if large_count >= 2:
            with ThreadPoolExecutor(max_workers=min(4, large_count)) as pool:
                file_digests = list(pool.map(self._digest_artifact_file, ordered_files))
        else:
            file_digests = [self._digest_artifact_file(path) for path in ordered_files]

        digest = hashlib.sha256(b"native-vision-artifact-v1\0")
        for path, file_digest in zip(ordered_files, file_digests):
            digest.update(path.name.encode("utf-8") + b"\0")
            digest.update(file_digest)
        identity = "mlx-vision-sha256:" + digest.hexdigest()

        if type(self).get_identity is NativeVisionClient.get_identity and "get_identity" not in self.__dict__:
            post_sig = self._artifact_status_signature(resolved)
            with self._status_identity_lock:
                if pre_sig is not None and post_sig == pre_sig:
                    self._status_identity_cache[str(resolved)] = (post_sig, identity)
                    self._status_identity_cache.move_to_end(str(resolved))
                    while len(self._status_identity_cache) > self._STATUS_IDENTITY_CACHE_MAX:
                        self._status_identity_cache.popitem(last=False)
                else:
                    self._status_identity_cache.pop(str(resolved), None)
        return identity

    def get_status_identity(self) -> str:
        """Return stat-verified cached vision identity when weights/configs are unchanged."""
        if type(self).get_identity is not NativeVisionClient.get_identity or "get_identity" in self.__dict__:
            return self.get_identity()
        try:
            resolved = self.model_dir.resolve()
        except OSError:
            return self.get_identity()
        sig = self._artifact_status_signature(resolved)
        if sig is not None:
            with self._status_identity_lock:
                cached = self._status_identity_cache.get(str(resolved))
                if cached is not None and cached[0] == sig:
                    self._status_identity_cache.move_to_end(str(resolved))
                    return cached[1]
        return self.get_identity()

    @classmethod
    def available(cls, model_dir: Optional[Path] = None) -> bool:
        if sys.platform != "darwin":
            return False
        md = Path(model_dir or MLX_9B_DIR)
        try:
            validate_native_model_config(md)
        except ValueError:
            return False
        if not (md / "processor_config.json").is_file() or not (md / "preprocessor_config.json").is_file():
            return False
        if sys.platform == "darwin":
            runtime = BASE_DIR / ".train-venv" / "bin" / "python"
            if not (runtime.is_file() and os.access(runtime, os.X_OK)):
                return False
        from app.trained_client import TrainedModelClient
        return TrainedModelClient._verify_weights(md)

    @staticmethod
    def _worker_command() -> List[str]:
        training_python = BASE_DIR / ".train-venv" / "bin" / "python"
        return [str(training_python), "-m", "app.mlx_vision"]

    @classmethod
    def _stream_worker_command(cls) -> List[str]:
        return [*cls._worker_command(), "--stream"]

    @staticmethod
    def evict_stream_worker() -> None:
        _VISION_STREAM_WORKER.evict()

    @staticmethod
    def _worker_env() -> Dict[str, str]:
        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_DATASETS_OFFLINE"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        return env

    @staticmethod
    def _run_cancellable_popen(
        cmd: List[str],
        *,
        input_text: str,
        cwd: Path,
        env: Dict[str, str],
        timeout: float = 420.0,
        cancel_event: Optional[threading.Event] = None,
        pass_fds: tuple = (),
    ) -> subprocess.CompletedProcess:
        popen_kwargs: Dict[str, Any] = {
            "cwd": cwd,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
            if pass_fds:
                popen_kwargs["pass_fds"] = pass_fds

        proc = subprocess.Popen(cmd, **popen_kwargs)  # nosec B603
        with _ACTIVE_WORKERS_LOCK:
            _ACTIVE_WORKERS.add(proc)

        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []

        def _feed_stdin() -> None:
            try:
                if proc.stdin is not None:
                    proc.stdin.write(input_text)
                    proc.stdin.flush()
            except OSError:
                pass
            finally:
                try:
                    if proc.stdin is not None:
                        proc.stdin.close()
                except OSError:
                    pass

        def _drain_stream(stream, target: List[str]) -> None:
            try:
                if stream is not None:
                    target.append(stream.read())
            except OSError:
                pass
            finally:
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass

        t_in = threading.Thread(target=_feed_stdin, daemon=True)
        t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, stdout_chunks), daemon=True)
        t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, stderr_chunks), daemon=True)
        t_in.start()
        t_out.start()
        t_err.start()

        deadline = time.monotonic() + max(0.05, float(timeout))
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    _terminate_owned_worker(proc)
                    raise IndexingCancelledError("Đã hủy tác vụ OCR MLX cục bộ theo yêu cầu dừng.")
                rc = proc.poll()
                if rc is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    _terminate_owned_worker(proc)
                    raise subprocess.TimeoutExpired(cmd, timeout)
                wait_slice = min(0.04, remaining)
                if cancel_event is not None:
                    cancel_event.wait(timeout=wait_slice)
                else:
                    time.sleep(wait_slice)

            t_out.join(timeout=1.0)
            t_err.join(timeout=1.0)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=rc,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
            )
        finally:
            try:
                if proc.poll() is None:
                    _terminate_owned_worker(proc)
            finally:
                with _ACTIVE_WORKERS_LOCK:
                    _ACTIVE_WORKERS.discard(proc)
                t_in.join(timeout=0.2)
                t_out.join(timeout=0.2)
                t_err.join(timeout=0.2)

    def generate_from_image(
        self,
        image_bytes: bytes,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        timeout_seconds: int = 420,
        cancel_event: Optional[threading.Event] = None,
        gpu_timeout: float = 30.0,
        response_schema: Optional[Dict[str, Any]] = None,
        repetition_penalty: Optional[float] = None,
        repetition_context_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run bounded vision generation using MLX VLM. Original image remains untouched."""
        if cancel_event is not None and cancel_event.is_set():
            raise IndexingCancelledError("Đã hủy tác vụ OCR MLX cục bộ theo yêu cầu dừng.")
        if response_schema is not None and not isinstance(response_schema, dict):
            raise ValueError("response_schema phải là JSON object.")
        if not self.available(self.model_dir):
            raise RuntimeError("Mô hình MLX Vision chưa sẵn sàng")

        with tempfile.TemporaryDirectory(prefix="mlx_vision_") as tmp_dir:
            tmp_image_path = Path(tmp_dir) / "input.png"
            with Image.open(io.BytesIO(image_bytes)) as original:
                prepared = ImageOps.exif_transpose(original).convert("RGB")
                prepared.thumbnail((MAX_INFERENCE_EDGE, MAX_INFERENCE_EDGE), Image.Resampling.LANCZOS)
                prepared.save(tmp_image_path, "PNG")

            request: Dict[str, Any] = {
                "model_path": str(self.model_dir),
                "image_path": str(tmp_image_path),
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if response_schema is not None:
                request["response_schema"] = response_schema
            if repetition_penalty is not None:
                request["repetition_penalty"] = float(repetition_penalty)
            if repetition_context_size is not None:
                request["repetition_context_size"] = int(repetition_context_size)
            use_oneshot = (
                subprocess.run is not _ORIG_SUBPROCESS_RUN
                or subprocess.Popen is not _ORIG_SUBPROCESS_POPEN
                or self._worker_command is not _ORIG_VISION_WORKER_COMMAND
                or self._worker_env is not _ORIG_VISION_WORKER_ENV
            )
            completed: Optional[subprocess.CompletedProcess[str]] = None
            data: Any = None
            if use_oneshot:
                payload_text = json.dumps(request, ensure_ascii=False)
                cmd = self._worker_command()
                env = self._worker_env()
                with _acquire_inference_lock(timeout=gpu_timeout, cancel_event=cancel_event) as lock_fds:
                    pass_fds = (
                        tuple(fd for fd in lock_fds if isinstance(fd, int))
                        if sys.platform != "win32" and lock_fds and isinstance(lock_fds, (tuple, list))
                        else ()
                    )
                    if subprocess.run is not _ORIG_SUBPROCESS_RUN:
                        kwargs_run: Dict[str, Any] = {
                            "cwd": BASE_DIR,
                            "input": payload_text,
                            "capture_output": True,
                            "text": True,
                            "timeout": timeout_seconds,
                            "env": env,
                            "check": False,
                        }
                        if pass_fds:
                            kwargs_run["pass_fds"] = pass_fds
                        completed = subprocess.run(cmd, **kwargs_run)  # nosec B603
                    else:
                        completed = self._run_cancellable_popen(
                            cmd,
                            input_text=payload_text,
                            cwd=BASE_DIR,
                            env=env,
                            timeout=timeout_seconds,
                            cancel_event=cancel_event,
                            pass_fds=pass_fds,
                        )
            else:
                with _acquire_inference_lock(timeout=gpu_timeout, cancel_event=cancel_event):
                    data = _VISION_STREAM_WORKER.request_json(
                        request,
                        timeout=timeout_seconds,
                        cancel_event=cancel_event,
                    )

        if completed is not None:
            if completed.returncode != 0:
                detail = (completed.stderr or "").strip()[-1000:]
                raise RuntimeError(f"Lỗi chạy native MLX vision (exit {completed.returncode}): {detail}")

            try:
                data = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise ValueError("Phản hồi từ mô hình MLX vision không phải JSON hợp lệ.") from exc
        if not isinstance(data, dict):
            raise ValueError("Phản hồi từ mô hình MLX vision phải là đối tượng JSON.")
        if data.get("finish_reason") == "length" or data.get("is_truncated"):
            raise ValueError("Phản hồi của mô hình chạm giới hạn độ dài token; kết quả bị cắt cụt.")

        return data


_ORIG_VISION_WORKER_COMMAND = NativeVisionClient._worker_command
_ORIG_VISION_WORKER_ENV = NativeVisionClient._worker_env
_VISION_STREAM_WORKER = ResidentStreamWorker(
    command_fn=NativeVisionClient._stream_worker_command,
    env_fn=NativeVisionClient._worker_env,
    idle_ttl=180.0,
    role="mlx_vision",
    evict_roles_on_use={"mlx_infer"},
    error_prefix="Lỗi chạy native MLX vision",
    cancel_message="Đã hủy tác vụ OCR MLX cục bộ theo yêu cầu dừng.",
)
