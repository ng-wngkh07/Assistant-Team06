"""Bridge the main app to the shared MLX 3B model (unadapted base or semantically accepted adapter)."""

import hashlib
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import Generator, Optional

from app.config import BASE_DIR, MLX_ADAPTER_DIR, MLX_MODEL_DIR
from app.active_registry import active_registry


class TrainedModelClient:
    """Unified client for Qwen2.5-3B-Instruct-4bit under MLX (unadapted base or approved adapter)."""

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
    def available(cls) -> bool:
        """Check availability.
        
        The shared MLX 3B base model is available whenever base model weights,
        tokenizer, config, and runtime python exist.
        An active adapter is used only if approved via active registry.
        """
        if sys.platform == "win32":
            return False
        runtime = BASE_DIR / ".train-venv" / "bin" / "python"
        if not (runtime.exists() and runtime.is_file() and os.access(runtime, os.X_OK)):
            return False

        if not all((MLX_MODEL_DIR / f).is_file() and (MLX_MODEL_DIR / f).stat().st_size > 0
                   for f in ("config.json", "model.safetensors", "tokenizer.json")):
            return False

        active_dir = active_registry.get_active_adapter_dir()
        if active_dir is not None:
            return (active_dir / "adapters.safetensors").exists()

        return True

    def __init__(self):
        self.last_done_reason = None

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        system_prompt: str = "",
        num_predict: int | None = None,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        if not self.available():
            raise RuntimeError("Mô hình MLX 3B chưa sẵn sàng hoặc adapter chưa được phê duyệt.")

        training_python = BASE_DIR / ".train-venv" / "bin" / "python"
        tokens_limit = int(max_tokens or num_predict or kwargs.get("num_predict") or 700)

        # Resolve adapter: ONLY semantically accepted adapter via active registry
        adapter_path: Optional[str] = None
        active_dir = active_registry.get_active_adapter_dir()
        if active_dir is not None and (active_dir / "adapters.safetensors").exists():
            adapter_path = str(active_dir)

        request = {
            "model_path": str(MLX_MODEL_DIR),
            "adapter_path": adapter_path,
            "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
            "max_tokens": tokens_limit,
        }
        num_ctx = kwargs.get("num_ctx")
        if num_ctx is not None:
            request["num_ctx"] = int(num_ctx)
        temp = temperature if temperature is not None else kwargs.get("temperature")
        if temp is not None:
            request["temperature"] = temp
        sd = seed if seed is not None else kwargs.get("seed")
        if sd is not None:
            request["seed"] = sd

        completed = subprocess.run(
            [str(training_python), "-m", "app.mlx_infer"],
            cwd=BASE_DIR,
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Không thể chạy mô hình MLX: {completed.stderr[-1000:]}")
        result_data = json.loads(completed.stdout)
        answer = result_data["answer"]
        self.last_done_reason = "length" if result_data.get("is_truncated") else "stop"
        yield answer

    def chat_complete(
        self,
        messages: list[dict[str, str]],
        system_prompt: str = "",
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """Synchronous chat completion returning full response string."""
        tokens = list(self.chat_stream(
            messages,
            system_prompt=system_prompt,
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
            **kwargs
        ))
        return "".join(tokens)
