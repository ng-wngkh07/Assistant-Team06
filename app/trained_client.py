"""Bridge the main app to local MLX models (Qwen3.5 9B default on Mac or legacy 3B)."""

import hashlib
import json
import logging
import os
import sys
import subprocess
from pathlib import Path
from typing import Generator, Optional

from app.config import BASE_DIR, MLX_ADAPTER_DIR, MLX_MODEL_DIR, TRAINED_MODEL_NAME
from app.active_registry import active_registry
from app.gpu_lock import gpu_coordinator
from app.model_resolver import (
    MLX_9B_NAME,
    MLX_3B_NAME,
    is_9b_model,
    is_3b_model,
    is_mlx_model,
    resolve_mlx_model_dir,
    get_model_context_capacity,
)

logger = logging.getLogger(__name__)


class TrainedModelClient:
    """Unified client for local MLX models (Qwen3.5 9B or legacy Qwen2.5 3B)."""

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

    @staticmethod
    def _verify_legacy_adapter(adapter_path: Path) -> bool:
        """Validate legacy adapter directory if explicitly configured in tests or registry."""
        ready_path = adapter_path / "ready.json"
        if not ready_path.exists():
            return False
        try:
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        version = ready.get("data_version", "v1")
        if not isinstance(version, str) or not version.startswith("v") or not version[1:].isdigit():
            return False
        training_dir = BASE_DIR / "data" / "training" / version
        train_path = training_dir / "train.jsonl"
        valid_path = training_dir / "valid.jsonl"
        approval_path = training_dir / "approval.json"
        if not all(path.exists() for path in (
            MLX_MODEL_DIR / "config.json", adapter_path / "adapters.safetensors",
            train_path, valid_path, approval_path,
        )):
            return False
        try:
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            digest = hashlib.sha256(train_path.read_bytes() + valid_path.read_bytes()).hexdigest()
            if approval.get("approved_manifest_sha256"):
                manifest = training_dir / "approved_manifest.jsonl"
                if not manifest.exists() or hashlib.sha256(manifest.read_bytes()).hexdigest() != approval["approved_manifest_sha256"]:
                    return False
        except (OSError, ValueError):
            return False
        return ready.get("dataset_sha256") == approval.get("dataset_sha256") == digest

    @classmethod
    def available(cls, model_name: Optional[str] = None) -> bool:
        """Check availability.
        
        Validates runtime python, local weights (sharded or single), config, and tokenizer.
        Rejects incompatible adapters before load.
        """
        if sys.platform == "win32":
            return False
        runtime = BASE_DIR / ".train-venv" / "bin" / "python"
        if not (runtime.exists() and runtime.is_file() and os.access(runtime, os.X_OK)):
            return False

        model_dir = resolve_mlx_model_dir(model_name)
        if not cls._verify_weights(model_dir):
            return False

        active_dir = active_registry.get_active_adapter_dir()
        if active_dir is not None:
            if is_9b_model(model_name):
                # 3B LoRA adapters cannot attach to 9B base; reject adapter
                return True
            return (active_dir / "adapters.safetensors").exists()

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

        # Resolve adapter: ONLY semantically accepted adapter via active registry for compatible 3B base
        adapter_path: Optional[str] = None
        if not is_9b_model(target_model):
            active_dir = active_registry.get_active_adapter_dir()
            if active_dir is not None and (active_dir / "adapters.safetensors").exists():
                adapter_path = str(active_dir)

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
                "timeout": 240,
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
        self.last_done_reason = result_data.get("done_reason") or ("length" if result_data.get("is_truncated") else "stop")
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
