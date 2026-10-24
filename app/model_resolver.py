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
    TRAINED_MODEL_NAME,
)

# 9B Model Constants
MLX_9B_NAME = "qwen3.5-9b-4bit"
MLX_9B_ALIASES = {MLX_9B_NAME, "mlx-9b", "qwen3.5:9b"}

# Legacy 3B Model Constants
MLX_3B_NAME = "qwen2.5-3b-4bit"
MLX_3B_ALIASES = {MLX_3B_NAME, "tam-ly-mlx", "mlx-3b", "local", TRAINED_MODEL_NAME.casefold()}

# Model directories (supporting explicit MLX_MODEL_DIR and distinct 3B/BGE overrides independent of AGENT_DATA_DIR)
MLX_9B_DIR = Path(
    os.getenv("MLX_MODEL_DIR")
    or os.getenv("MLX_9B_MODEL_DIR")
    or str(DATA_DIR / "models" / "qwen3.5-9b-4bit")
)
MLX_3B_DIR = Path(
    os.getenv("MLX_3B_MODEL_DIR")
    or str(DATA_DIR / "models" / "qwen2.5-3b-4bit")
)
MLX_BGE_DIR = Path(
    os.getenv("MLX_BGE_MODEL_DIR")
    or os.getenv("BGE_MODEL_DIR")
    or str(DATA_DIR / "models" / "bge-m3-mlx-fp16")
)


def is_9b_model(model_name: Optional[str] = None) -> bool:
    """Return True if model identity resolves strictly to 9B base via exact allowlist."""
    target = (model_name or DEFAULT_CHAT_MODEL or "").strip().lower()
    if not target and sys.platform == "darwin":
        return True
    if target == "qwen3.5:9b":
        return sys.platform == "darwin"
    return target in {MLX_9B_NAME, "mlx-9b"}


def is_3b_model(model_name: Optional[str] = None) -> bool:
    """Return True if model identity resolves strictly to 3B base via exact allowlist."""
    target = (model_name or DEFAULT_CHAT_MODEL or "").strip().lower()
    return target in MLX_3B_ALIASES


def is_mlx_model(model_name: Optional[str] = None) -> bool:
    """Return True if the model runs directly under MLX (never Ollama or remote)."""
    target = (model_name or DEFAULT_CHAT_MODEL or "").strip().lower()
    if not target:
        return sys.platform == "darwin"
    if target == "qwen3.5:9b":
        return sys.platform == "darwin"
    if target in {MLX_9B_NAME, "mlx-9b"}:
        return sys.platform == "darwin"
    if target in MLX_3B_ALIASES:
        return True
    return False


def resolve_mlx_model_dir(model_name: Optional[str] = None) -> Path:
    """Resolve exact base model weights directory. Unknown models raise ValueError before load."""
    if model_name is not None and model_name.strip():
        target = model_name.strip().lower()
        if is_9b_model(target):
            return MLX_9B_DIR
        if is_3b_model(target):
            return MLX_3B_DIR
        raise ValueError(f"Không thể phân giải mô hình không xác định sang thư mục cục bộ: {model_name}")

    target = (DEFAULT_CHAT_MODEL or "").strip().lower()
    if is_9b_model(target):
        return MLX_9B_DIR
    if is_3b_model(target):
        return MLX_3B_DIR
    if sys.platform == "darwin":
        return MLX_9B_DIR
    return MLX_3B_DIR


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
