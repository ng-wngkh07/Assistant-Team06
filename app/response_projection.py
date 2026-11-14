"""Boundary projection and error sanitization helpers for public/authenticated APIs."""

from __future__ import annotations

import re
import subprocess
from typing import Any

INTERNAL_PATH_KEYS = frozenset(
    {
        "review_path",
        "filepath",
        "raw_capture_path",
        "image_path",
        "storage_path",
        "config_path",
    }
)

_DEFAULT_ERROR_MESSAGE = "Hệ thống AI cục bộ đang tạm thời không khả dụng. Vui lòng thử lại sau."
_MODEL_CONFIG_ERROR_MESSAGE = (
    "Mô hình AI cục bộ chưa sẵn sàng hoặc thiếu cấu hình hợp lệ. "
    "Bạn vẫn có thể xem nội dung tài liệu đã trích xuất; vui lòng kiểm tra mô hình cục bộ trước khi tạo câu hỏi."
)

_WINDOWS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\s\"'<>|;,)]+")
_FILE_URI_RE = re.compile(r"file://[^\s\"'<>|;,)]+", re.IGNORECASE)
_POSIX_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:/])/(?!api(?:/|$)|static(?:/|$))(?:[^/\s\"'<>|;,)]+/)+[^/\s\"'<>|;,)]*"
)
_WINDOWS_SRC_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def is_absolute_host_path(value: str) -> bool:
    """Return True when ``value`` is an absolute host filesystem path or file URI."""
    text = (value or "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower.startswith(("file://", "http://", "https://", "/api/", "/static/")):
        return lower.startswith("file://")
    if text.startswith("/") or bool(_WINDOWS_SRC_PATH_RE.match(text)):
        return True
    return False


def project_public_response(value: Any) -> Any:
    """Return a copy of ``value`` with internal host path fields removed recursively.

    Preserves citation identity, grounding quotes/claims, page quality flags
    (``needs_review``, ``warnings``, ``raw_capture``), API download/preview URLs,
    and workspace-relative ``src_path`` values.
    """
    if isinstance(value, dict):
        projected: dict[Any, Any] = {}
        for key, item in value.items():
            key_lower = str(key).strip().lower()
            if key_lower in INTERNAL_PATH_KEYS:
                continue
            if key_lower == "src_path" and isinstance(item, str) and is_absolute_host_path(item):
                continue
            projected[key] = project_public_response(item)
        return projected
    if isinstance(value, list):
        return [project_public_response(item) for item in value]
    if isinstance(value, tuple):
        return [project_public_response(item) for item in value]
    return value


def sanitize_error_detail(detail: Any, fallback: str = _DEFAULT_ERROR_MESSAGE) -> str:
    """Strip tracebacks and absolute host filesystem paths from runtime error messages."""
    if detail is None:
        return fallback
    text = str(detail).strip()
    if not text:
        return fallback
    if "Traceback (most recent call last)" in text:
        return fallback

    lower = text.lower()
    if any(
        marker in lower
        for marker in (
            "cấu hình mô hình",
            "config.json",
            "max_position_embeddings",
            "thiếu tệp trọng số",
            "mô hình ai chưa sẵn sàng",
            "trọng số không khớp",
        )
    ):
        return _MODEL_CONFIG_ERROR_MESSAGE

    scrubbed = _FILE_URI_RE.sub("", text)
    scrubbed = _WINDOWS_PATH_RE.sub("", scrubbed)
    scrubbed = _POSIX_PATH_RE.sub("", scrubbed)
    if scrubbed == text:
        return text
    cleaned = re.sub(r"(?:\s+(?:tại|at))?\s*[:.]?\s*$", "", scrubbed).strip()
    if not cleaned:
        return fallback
    if not cleaned.endswith((".", "!", "?")):
        cleaned += "."
    return cleaned


def project_chat_payload(payload: Any) -> Any:
    """Apply public API boundary projection and error sanitization to a chat payload."""
    projected = project_public_response(payload)
    if not isinstance(projected, dict):
        return projected
    for key in ("error", "failure_reason", "detail"):
        if isinstance(projected.get(key), str) and projected[key].strip():
            projected[key] = sanitize_error_detail(projected[key])
    if projected.get("type") == "error" and isinstance(projected.get("content"), str) and projected["content"].strip():
        projected["content"] = sanitize_error_detail(projected["content"])
    return projected


def is_degraded_runtime_error(exc: Exception) -> bool:
    """Return True when ``exc`` indicates an unavailable/unconfigured local runtime or model."""
    from app.dialogue import ContextLimitError
    from app.study_materials import StudyMaterialError

    msg = str(exc).strip().lower()
    if isinstance(exc, ContextLimitError):
        return not any(token in msg for token in ("câu hỏi quá dài", "quá dài cho cửa sổ"))
    if isinstance(exc, StudyMaterialError):
        return any(
            token in msg
            for token in (
                "mô hình ai chưa sẵn sàng",
                "thiếu tệp trọng số",
                "không tìm thấy tệp cấu hình mô hình",
                "không đọc được cấu hình",
                "trọng số không khớp",
                "mô hình phản hồi quá thời gian",
                "bước kiểm định câu hỏi",
            )
        )
    if isinstance(exc, (FileNotFoundError, RuntimeError, TimeoutError, subprocess.TimeoutExpired, ConnectionError, OSError)):
        return True
    if isinstance(exc, ValueError):
        return any(
            token in msg
            for token in (
                "cấu hình",
                "trọng số",
                "mô hình",
                "mlx",
                "model",
                "tokenizer",
                "config",
                "adapter",
                "retired",
            )
        )
    return False
