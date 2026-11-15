"""Bridge the main app to the local Qwen3.5 9B MLX base."""

import inspect
import json
import logging
import os
import sys
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Generator, Optional

from app.config import BASE_DIR, TRAINED_MODEL_NAME
from app.gpu_lock import GPUBusyError, IndexingCancelledError, gpu_coordinator
from app.model_resolver import (
    MLX_9B_NAME,
    resolve_mlx_model_dir,
    get_model_context_capacity,
    validate_native_model_config,
)
from app.stream_worker import ResidentStreamWorker

logger = logging.getLogger(__name__)

_STDLIB_RUN = subprocess.run
_STDLIB_POPEN = subprocess.Popen


_review_cache_dir = ContextVar("native_review_cache_dir", default=None)


def current_review_cache_dir():
    return _review_cache_dir.get()


@contextmanager
def native_review_session():
    if current_review_cache_dir() is not None:
        yield
        return
    with tempfile.TemporaryDirectory(prefix="study-review-") as directory:
        token = _review_cache_dir.set(directory)
        try:
            yield
        finally:
            _review_cache_dir.reset(token)


class TrainedModelClient:
    """Client for the current unadapted local Qwen3.5 9B base."""

    _warmup_lock = threading.Lock()
    _warmup_done = threading.Event()
    _warmup_done.set()
    _startup_warmup_thread: Optional[threading.Thread] = None
    _startup_warmup_cancel = threading.Event()

    @staticmethod
    def _verify_weights(model_dir: Path) -> bool:
        """Validate single safetensors or sharded index with path safety and non-empty files."""
        if not model_dir.is_dir():
            return False
        if not (model_dir / "config.json").is_file() or (model_dir / "config.json").stat().st_size == 0:
            return False
        if not (model_dir / "tokenizer.json").is_file() or (model_dir / "tokenizer.json").stat().st_size == 0:
            return False

        shard_index = model_dir / "model.safetensors.index.json"
        single_weights = model_dir / "model.safetensors"

        if shard_index.is_file() and shard_index.stat().st_size > 0:
            try:
                data = json.loads(shard_index.read_text(encoding="utf-8"))
                weight_map = data.get("weight_map")
                if not isinstance(weight_map, dict) or not weight_map:
                    return False
                resolved_base = model_dir.resolve()
                shard_files = set()
                for sf in weight_map.values():
                    if not isinstance(sf, str) or not sf.strip():
                        return False
                    shard_files.add(sf)
                for sf in shard_files:
                    sf_path = (model_dir / sf).resolve()
                    # Path safety: must reside within model directory
                    if not sf_path.is_relative_to(resolved_base):
                        return False
                    if not sf_path.is_file() or sf_path.stat().st_size == 0:
                        return False
                return True
            except (OSError, ValueError, KeyError, TypeError):
                return False
        elif single_weights.is_file() and single_weights.stat().st_size > 0:
            return True

        return False

    @classmethod
    def available(cls, model_name: Optional[str] = None) -> bool:
        """Check availability.
        
        Validates runtime python, local weights (sharded or single), config, and tokenizer.
        Rejects incompatible adapters before load.
        """
        if sys.platform != "darwin":
            return False
        runtime = BASE_DIR / ".train-venv" / "bin" / "python"
        if not (runtime.exists() and runtime.is_file() and os.access(runtime, os.X_OK)):
            return False

        try:
            model_dir = resolve_mlx_model_dir(model_name)
            validate_native_model_config(model_dir)
        except (ValueError, TypeError):
            return False
        if not cls._verify_weights(model_dir):
            return False

        return True

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or (MLX_9B_NAME if sys.platform == "darwin" else TRAINED_MODEL_NAME)
        self.model_dir = resolve_mlx_model_dir(self.model_name)
        self.last_done_reason = None
        self.last_diagnostics = {}
        self.last_usage = {}

    @staticmethod
    def _stream_worker_command() -> list[str]:
        training_python = BASE_DIR / ".train-venv" / "bin" / "python"
        return [str(training_python), "-m", "app.mlx_infer", "--stream"]

    @staticmethod
    def _worker_env() -> dict[str, str]:
        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_DATASETS_OFFLINE"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        return env

    @classmethod
    def evict_stream_worker(cls) -> None:
        """Stop any idle resident native MLX inference worker to free unified memory."""
        _INFER_STREAM_WORKER.evict()

    @classmethod
    def _default_warmup_prompts(cls) -> list[str]:
        from app.rag_agent import MLX_SOURCE_LINE_CONTRACT, MLX_SOURCE_PROMPT

        return [
            f"{MLX_SOURCE_PROMPT}\n{MLX_SOURCE_LINE_CONTRACT}",
            MLX_SOURCE_PROMPT,
        ]

    @classmethod
    def warm_prefix_caches(
        cls,
        system_prompts: Optional[list[str]] = None,
        model_name: Optional[str] = None,
        *,
        timeout_sec: float = 120.0,
        gpu_wait_timeout: float = 0.25,
        cancel_event: Optional[threading.Event] = None,
    ) -> dict:
        """Pre-load the resident MLX worker and warm immutable system-prefix KV caches."""
        if cancel_event is not None and cancel_event.is_set():
            return {"warmed": False, "skipped": "cancelled"}
        target_model = model_name or (MLX_9B_NAME if sys.platform == "darwin" else TRAINED_MODEL_NAME)
        if not cls.available(target_model):
            return {"warmed": False, "skipped": "unavailable"}
        model_dir = resolve_mlx_model_dir(target_model)
        prompts = list(cls._default_warmup_prompts() if system_prompts is None else system_prompts)
        prompts = [p for p in prompts if isinstance(p, str) and p.strip()]
        if not prompts:
            return {"warmed": False, "skipped": "no_prompts"}

        acquire_fn = gpu_coordinator.acquire_for_inference
        acq_kwargs = {}
        try:
            params = inspect.signature(acquire_fn).parameters
            if "timeout" in params:
                acq_kwargs["timeout"] = float(gpu_wait_timeout)
            if "cancel_event" in params:
                acq_kwargs["cancel_event"] = cancel_event
        except (TypeError, ValueError):
            acq_kwargs = {}

        request = {
            "op": "warmup",
            "model_path": str(model_dir),
            "adapter_path": None,
            "system_prompts": prompts,
            "num_ctx": get_model_context_capacity(target_model),
        }

        try:
            with acquire_fn(**acq_kwargs):
                cls._warmup_done.clear()
                try:
                    return _INFER_STREAM_WORKER.request_json(
                        request,
                        timeout=float(timeout_sec),
                        cancel_event=cancel_event,
                    )
                finally:
                    cls._warmup_done.set()
        except (GPUBusyError, IndexingCancelledError):
            return {"warmed": False, "skipped": "gpu_busy_or_cancelled"}

    @classmethod
    def start_startup_warmup(
        cls,
        *,
        delay_seconds: float = 1.5,
        model_name: Optional[str] = None,
    ) -> bool:
        """Schedule non-blocking resident prefix warmup once after server startup."""
        disabled = os.getenv("STUDY_DISABLE_STARTUP_WARMUP", "").strip().lower() in {"1", "true", "yes", "on"}
        if disabled or os.getenv("PYTEST_CURRENT_TEST") or sys.platform != "darwin":
            return False
        if not cls.available(model_name):
            return False
        with cls._warmup_lock:
            existing = cls._startup_warmup_thread
            if existing is not None and existing.is_alive():
                return False
            cancel_evt = threading.Event()
            cls._startup_warmup_cancel = cancel_evt

            def _runner():
                if delay_seconds > 0 and cancel_evt.wait(timeout=float(delay_seconds)):
                    return
                try:
                    result = cls.warm_prefix_caches(
                        model_name=model_name,
                        cancel_event=cancel_evt,
                    )
                    logger.info("Native MLX startup prefix warmup finished: %s", result.get("timings") or result.get("skipped"))
                except Exception as exc:
                    logger.debug("Native MLX startup prefix warmup skipped/failed: %s", exc)

            thread = threading.Thread(target=_runner, name="mlx-prefix-warmup", daemon=True)
            cls._startup_warmup_thread = thread
            thread.start()
            return True

    @classmethod
    def stop_startup_warmup(cls, timeout: float = 2.0) -> None:
        """Cancel any pending or active startup warmup thread during shutdown."""
        with cls._warmup_lock:
            cancel_evt = cls._startup_warmup_cancel
            thread = cls._startup_warmup_thread
        cancel_evt.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
        cls._warmup_done.set()

    def _record_result_metrics(
        self,
        result_data: dict,
        *,
        compact_review: bool,
        grounded_review: bool,
    ) -> str:
        if compact_review:
            from app.practice_adapter import COMPACT_REVIEW_PROFILE
            result_data["practice_review_profile"] = COMPACT_REVIEW_PROFILE
        elif grounded_review:
            from app.practice_adapter import GROUNDED_REVIEW_PROFILE
            result_data["practice_review_profile"] = GROUNDED_REVIEW_PROFILE
        answer = result_data.get("answer", "")
        reason = result_data.get("done_reason")
        self.last_done_reason = reason if reason in {"stop", "length"} else None
        self.last_usage = {
            "input_tokens": result_data.get("input_tokens"),
            "output_tokens": result_data.get("output_tokens"),
            "prompt_tokens": result_data.get("input_tokens"),
            "generation_tokens": result_data.get("output_tokens"),
            "prompt_eval_count": result_data.get("input_tokens"),
            "eval_count": result_data.get("output_tokens"),
            "total_tokens": (result_data.get("input_tokens") or 0) + (result_data.get("output_tokens") or 0),
        }
        self.last_diagnostics = result_data
        return answer

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        system_prompt: str = "",
        num_predict: int | None = None,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        target_model = model or self.model_name
        if not self.available(target_model):
            raise RuntimeError(f"Mô hình MLX {target_model} chưa sẵn sàng hoặc thiếu tệp trọng số cục bộ.")

        model_dir = resolve_mlx_model_dir(target_model)
        training_python = BASE_DIR / ".train-venv" / "bin" / "python"
        tokens_limit = int(max_tokens or num_predict or kwargs.get("num_predict") or 700)

        adapter_path: Optional[str] = None

        full_messages = list(messages)
        if system_prompt:
            if not full_messages or full_messages[0].get("role") != "system":
                full_messages = [{"role": "system", "content": system_prompt}] + full_messages

        request = {
            "model_path": str(model_dir),
            "adapter_path": adapter_path,
            "messages": full_messages,
            "max_tokens": tokens_limit,
        }
        if kwargs.get("response_schema") is not None:
            request["response_schema"] = kwargs["response_schema"]
        num_ctx = kwargs.get("num_ctx")
        if num_ctx is not None:
            request["num_ctx"] = int(num_ctx)
        else:
            request["num_ctx"] = get_model_context_capacity(target_model)
        temp = temperature if temperature is not None else kwargs.get("temperature")
        if temp is not None:
            request["temperature"] = temp
        sd = seed if seed is not None else kwargs.get("seed")
        if sd is not None:
            request["seed"] = sd

        from app.practice_adapter import (
            active_practice_adapter,
            request_matches_scope,
            request_matches_grounded_review,
            request_matches_grounded_adapter_scope,
            GROUNDED_REVIEW_PROFILE,
            COMPACT_REVIEW_PROFILE,
        )
        compact_review = request_matches_scope(request)
        grounded_review = request_matches_grounded_review(request)
        if compact_review:
            request["seed"] = 42
            accepted = active_practice_adapter(model_dir)
            if accepted:
                request["adapter_path"] = accepted["path"]
        elif grounded_review:
            request["seed"] = 42
            request["adapter_path"] = None
            if request_matches_grounded_adapter_scope(request):
                accepted = active_practice_adapter(model_dir, profile=GROUNDED_REVIEW_PROFILE)
                if accepted and accepted.get('review_profile') == GROUNDED_REVIEW_PROFILE:
                    request["adapter_path"] = accepted["path"]

        if grounded_review and current_review_cache_dir() is not None:
            request["review_cache_dir"] = current_review_cache_dir()

        timeout_sec = int(kwargs.get("timeout") or 600)
        use_oneshot = (
            subprocess.run is not _STDLIB_RUN
            or subprocess.Popen is not _STDLIB_POPEN
            or type(self)._stream_worker_command is not TrainedModelClient._stream_worker_command
        )

        if not type(self)._warmup_done.is_set():
            type(self)._warmup_done.wait(timeout=90.0)

        # Serialize model workloads through exclusive native worker admission and training lock inheritance
        with gpu_coordinator.acquire_for_inference() as lock_fds:
            if use_oneshot:
                kwargs_run = {
                    "cwd": BASE_DIR,
                    "input": json.dumps(request, ensure_ascii=False),
                    "capture_output": True,
                    "text": True,
                    "timeout": timeout_sec,
                    "check": False,
                }
                if sys.platform != "win32" and lock_fds and isinstance(lock_fds, (tuple, list)):
                    kwargs_run["pass_fds"] = tuple(fd for fd in lock_fds if isinstance(fd, int))

                completed = subprocess.run(
                    [str(training_python), "-m", "app.mlx_infer"],
                    **kwargs_run,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"Không thể chạy mô hình MLX: {completed.stderr[-1000:]}")
                result_data = json.loads(completed.stdout)
                answer = self._record_result_metrics(
                    result_data,
                    compact_review=compact_review,
                    grounded_review=grounded_review,
                )
                yield answer
                return

            for evt in _INFER_STREAM_WORKER.stream_events(request, timeout=float(timeout_sec)):
                if "token" in evt:
                    tok = evt["token"]
                    if tok:
                        yield tok
                elif evt.get("done"):
                    self._record_result_metrics(
                        evt,
                        compact_review=compact_review,
                        grounded_review=grounded_review,
                    )

    def chat_complete(
        self,
        messages: list[dict[str, str]],
        system_prompt: str = "",
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        """Synchronous chat completion returning full response string."""
        tokens = list(self.chat_stream(
            messages,
            system_prompt=system_prompt,
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
            model=model,
            **kwargs
        ))
        return "".join(tokens)


_INFER_STREAM_WORKER = ResidentStreamWorker(
    command_fn=TrainedModelClient._stream_worker_command,
    env_fn=TrainedModelClient._worker_env,
    idle_ttl=180.0,
    role="mlx_infer",
    evict_roles_on_use={"mlx_vision"},
    error_prefix="Không thể chạy mô hình MLX",
)
