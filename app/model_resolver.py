"""Centralized selected-model identity, path resolution, and backend routing."""

import json
import os
import sys
from pathlib import Path
from typing import Optional

from app.config import (
    BASE_DIR,
    DATA_DIR,
    DEFAULT_CHAT_MODEL,
    MLX_CONTEXT_TOKENS,
)

# 9B Model Constants
MLX_9B_NAME = "qwen3.5-9b-4bit"
MLX_9B_ALIASES = {MLX_9B_NAME, "mlx-9b", "qwen3.5:9b"}

# Explicit path overrides remain independent of AGENT_DATA_DIR.
MLX_9B_DIR = Path(
    os.getenv("MLX_MODEL_DIR")
    or os.getenv("MLX_9B_MODEL_DIR")
    or str(DATA_DIR / "models" / "qwen3.5-9b-4bit")
)
MLX_BGE_DIR = Path(
    os.getenv("MLX_BGE_MODEL_DIR")
    or os.getenv("BGE_MODEL_DIR")
    or str(DATA_DIR / "models" / "bge-m3-mlx-fp16")
)


def reject_retired_model(model_name: str) -> None:
    """Reject retired identities before any provider call, including stale envs."""
    target = (model_name or "").strip().casefold()
    if "qwen2.5" in target or target in {"mlx-3b", "tam-ly-mlx", "qwen3:4b"}:
        raise ValueError(
            "Mô hình đã ngừng hỗ trợ (retired). Xóa cấu hình mô hình cũ trong .env; "
            "dùng Qwen3.5 9B cục bộ và chạy lại kiểm tra mô hình."
        )


def validate_native_model_config(model_dir: Path) -> dict:
    """Check the actual base architecture, never a directory name or adapter label."""
    try:
        cfg = json.loads((Path(model_dir) / "config.json").read_text(encoding="utf-8"))
        text = cfg.get("text_config", {})
        quant = cfg.get("quantization") or cfg.get("quantization_config") or {}
        valid = (
            cfg.get("model_type") == "qwen3_5"
            and isinstance(cfg.get("architectures"), list)
            and "Qwen3_5ForConditionalGeneration" in cfg["architectures"]
            and text.get("model_type") == "qwen3_5_text"
            and text.get("hidden_size") == 4096
            and text.get("num_hidden_layers") == 32
            and text.get("num_attention_heads") == 16
            and quant.get("bits") == 4 and quant.get("group_size") == 64
            and quant.get("mode") == "affine"
        )
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Không đọc được cấu hình Qwen3.5 9B 4-bit cục bộ.") from exc
    if not valid:
        raise ValueError("Trọng số không khớp Qwen3.5 9B 4-bit. Kiểm tra MLX_MODEL_DIR; không dùng mô hình hoặc adapter cũ.")
    return cfg


def is_9b_model(model_name: Optional[str] = None) -> bool:
    """Return True if model identity resolves strictly to 9B base via exact allowlist."""
    target = (model_name or DEFAULT_CHAT_MODEL or "").strip().lower()
    if not target and sys.platform == "darwin":
        return True
    return target in MLX_9B_ALIASES


def is_mlx_model(model_name: Optional[str] = None) -> bool:
    """Return True if the model runs directly under MLX (never Ollama or remote)."""
    if os.getenv("LLM_PROVIDER") == "ollama" or os.getenv("CHAT_BACKEND") == "ollama":
        return False
    target = (model_name or DEFAULT_CHAT_MODEL or "").strip().lower()
    if not target:
        return sys.platform == "darwin"
    if target == "qwen3.5:9b":
        return sys.platform == "darwin"
    if target in {MLX_9B_NAME, "mlx-9b"}:
        return sys.platform == "darwin"
    return False


def resolve_mlx_model_dir(model_name: Optional[str] = None) -> Path:
    """Resolve exact base model weights directory. Unknown models raise ValueError before load."""
    if model_name is not None and model_name.strip():
        target = model_name.strip().lower()
        reject_retired_model(target)
        if is_9b_model(target):
            return MLX_9B_DIR
        raise ValueError(f"Không thể phân giải mô hình không xác định sang thư mục cục bộ: {model_name}")

    target = (DEFAULT_CHAT_MODEL or "").strip().lower()
    reject_retired_model(target)
    if is_9b_model(target):
        return MLX_9B_DIR
    raise ValueError("Chọn Qwen3.5 9B trước khi dùng backend MLX.")


def get_model_context_capacity(model_name: Optional[str] = None) -> int:
    """Read max_position_embeddings from config (supporting nested text_config). Fail closed if missing."""
    model_dir = resolve_mlx_model_dir(model_name)
    config_path = model_dir / "config.json"
    if not config_path.is_file():
        from app.dialogue import ContextLimitError
        raise ContextLimitError(f"Không tìm thấy tệp cấu hình mô hình tại {config_path}")
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        text_cfg = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}
        raw = (
            text_cfg.get("max_position_embeddings")
            or text_cfg.get("seq_length")
            or cfg.get("max_position_embeddings")
            or cfg.get("seq_length")
        )
        if raw is None:
            from app.dialogue import ContextLimitError
            raise ContextLimitError("Thiếu thông số max_position_embeddings trong cấu hình mô hình")
        native_limit = int(raw)
        if native_limit < 2048:
            from app.dialogue import ContextLimitError
            raise ContextLimitError("Giới hạn ngữ cảnh cấu hình không đủ điều kiện tối thiểu (2048)")
        return min(native_limit, MLX_CONTEXT_TOKENS)
    except Exception as exc:
        from app.dialogue import ContextLimitError
        if isinstance(exc, ContextLimitError):
            raise
        raise ContextLimitError("Cấu hình mô hình không hợp lệ hoặc thiếu giới hạn ngữ cảnh") from exc
