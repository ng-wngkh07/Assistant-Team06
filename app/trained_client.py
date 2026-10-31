"""Bridge the main app to the local Qwen3.5 9B MLX base."""

import json
import logging
import os
import sys
import subprocess
from pathlib import Path
from typing import Generator, Optional

from app.config import BASE_DIR, TRAINED_MODEL_NAME
from app.gpu_lock import gpu_coordinator
from app.model_resolver import (
    MLX_9B_NAME,
    resolve_mlx_model_dir,
    get_model_context_capacity,
    validate_native_model_config,
)

logger = logging.getLogger(__name__)


class TrainedModelClient:
    """Client for the current unadapted local Qwen3.5 9B base."""

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

        # No compatible adapter is approved for this product base.
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

        # Serialize model workloads through exclusive native worker admission and training lock inheritance
        with gpu_coordinator.acquire_for_inference() as lock_fds:
            kwargs_run = {
                "cwd": BASE_DIR,
                "input": json.dumps(request, ensure_ascii=False),
                "capture_output": True,
                "text": True,
                "timeout": int(kwargs.get("timeout") or 600),
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
        answer = result_data["answer"]
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
        yield answer

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
