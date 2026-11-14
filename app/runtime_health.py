"""Probe executable capability in the serving process, separately from weights.

The child inherits the server's OS restrictions, so a desktop GPU check cannot
accidentally certify a restricted server. This probe does not load model weights.
"""
import subprocess
import sys
import threading
import time

from app.config import BASE_DIR

_lock = threading.Lock()
_cached = None
_checked_at = 0.0


def runtime_failure_message(error) -> str:
    text = str(error).lower()
    if any(k in text for k in ('done_reason', 'incomplete', 'unfinished', 'gián đoạn', 'vượt quá độ dài')):
        return 'Mô hình cục bộ phản hồi chưa trọn vẹn hoặc bị ngắt giữa chừng. Thử lại hoặc kiểm tra tài nguyên bộ nhớ máy.'
    if any(k in text for k in ('sqlite', 'database', 'disk i/o', 'retrieval_error')):
        return 'Không thể đọc hoặc truy xuất cơ sở dữ liệu học tập. Kiểm tra quyền truy cập tệp và không gian ổ đĩa.'
    if 'metal' in text or 'nanobind' in text:
        return ('Dịch vụ hỏi đáp tạm thời không truy cập được tài nguyên tăng tốc GPU Metal trên máy chủ. '
                'Vui lòng thử lại sau hoặc liên hệ người vận hành hệ thống để kiểm tra trạng thái dịch vụ.')
    return ('Không thể khởi chạy hoặc kiểm tra mô hình hỏi đáp trên máy chủ. '
            'Vui lòng thử lại sau hoặc liên hệ người vận hành hệ thống để kiểm tra dịch vụ.')


def probe_mlx_runtime(force=False):
    global _cached, _checked_at
    if sys.platform != 'darwin':
        return {
            'ready': False,
            'code': 'platform_unsupported',
            'message': 'MLX native runtime is Apple Silicon (macOS) only. Windows uses Ollama runtime.',
            'probe_kind': 'platform_check',
        }
    with _lock:
        if not force and _cached is not None and time.monotonic() - _checked_at < 30:
            return dict(_cached)
        runtime = BASE_DIR / '.train-venv' / 'bin' / 'python'
        try:
            result = subprocess.run(
                [str(runtime), '-c',
                 'import mlx.core as mx; assert mx.metal.is_available(); import mlx_lm; print("METAL_OK")'],
                cwd=BASE_DIR, capture_output=True, text=True, timeout=15, check=False,
            )
            ready = result.returncode == 0 and 'METAL_OK' in result.stdout
            detail = result.stderr
        except (OSError, subprocess.TimeoutExpired) as exc:
            ready, detail = False, str(exc)
        _cached = {
            'ready': ready,
            'code': 'ready' if ready else ('metal_unavailable' if any(t in detail.lower() for t in ('metal', 'nanobind')) else 'runtime_unavailable'),
            'message': 'Python và GPU Metal chạy được; chưa xác nhận chất lượng trả lời.' if ready else runtime_failure_message(detail),
            'probe_kind': 'runtime_import',
        }
        _checked_at = time.monotonic()
        return dict(_cached)


def _env_positive_int_health(name: str, default: int) -> int:
    import os
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
        return val if val >= 1 else default
    except ValueError:
        return default


def _env_positive_float_health(name: str, default: float) -> float:
    import os
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
        return val if val >= 0.5 else default
    except ValueError:
        return default


def inspect_infrastructure_health() -> dict:
    """Return non-sensitive disk, database, directory, security, and scaling admission health."""
    import os
    import shutil
    import sqlite3
    from app import accounts, config
    from app.request_admission import (
        MAX_API_MUTATION_BODY_BYTES,
        MAX_POST_BODY_BYTES,
        MAX_UPLOAD_BODY_BYTES,
        rate_limiter,
    )

    target_dir = config.DATA_DIR if config.DATA_DIR.exists() else config.BASE_DIR
    disk_free_bytes = 0
    disk_total_bytes = 0
    disk_used_bytes = 0
    disk_healthy = False
    disk_warning = None
    min_free_mb = _env_positive_int_health("STUDY_MIN_FREE_DISK_MB", 512)
    min_free_bytes = min_free_mb * 1024 * 1024
    try:
        usage = shutil.disk_usage(target_dir)
        disk_free_bytes = int(usage.free)
        disk_total_bytes = int(usage.total)
        disk_used_bytes = int(usage.used)
        disk_healthy = disk_free_bytes >= min_free_bytes
        if not disk_healthy:
            disk_warning = (
                f"Dung lượng ổ đĩa trống thấp ({disk_free_bytes // (1024 * 1024)} MiB < {min_free_mb} MiB)."
            )
    except OSError:
        disk_warning = "Không thể kiểm tra dung lượng ổ đĩa của máy chủ."

    kb_readable = False
    kb_path = config.DB_PATH
    if kb_path.is_file() and kb_path.stat().st_size > 0:
        conn = None
        try:
            conn = sqlite3.connect(f"file:{kb_path}?mode=ro", uri=True, timeout=1.5)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            kb_readable = True
        except sqlite3.Error:
            kb_readable = False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

    acc_dir = accounts.root_dir()
    accounts_dir_writable = bool(acc_dir.is_dir() and os.access(acc_dir, os.W_OK | os.X_OK))

    return {
        "healthy": bool(disk_healthy),
        "disk_free_bytes": disk_free_bytes,
        "disk_total_bytes": disk_total_bytes,
        "disk_used_bytes": disk_used_bytes,
        "disk_healthy": disk_healthy,
        "disk_warning": disk_warning,
        "knowledge_base_readable": kb_readable,
        "accounts_dir_writable": accounts_dir_writable,
        "security_posture": {
            "accounts_required": accounts.required(),
            "allow_registration": os.getenv("STUDY_ALLOW_REGISTRATION", "").strip() == "1",
            "secure_cookies": os.getenv("STUDY_SECURE_COOKIES", "").strip() == "1",
            "trusted_proxies_configured": bool(os.getenv("FORWARDED_ALLOW_IPS", "").strip()),
            "rate_limit": rate_limiter.get_metrics(),
        },
        "limits": {
            "max_post_body_bytes": MAX_POST_BODY_BYTES,
            "max_api_mutation_body_bytes": MAX_API_MUTATION_BODY_BYTES,
            "max_upload_body_bytes": MAX_UPLOAD_BODY_BYTES,
            "max_inference_queue": _env_positive_int_health("STUDY_MAX_INFERENCE_QUEUE", 20),
            "inference_queue_timeout_seconds": _env_positive_float_health(
                "STUDY_INFERENCE_QUEUE_TIMEOUT_SECONDS", 25.0
            ),
            "account_max_files": _env_positive_int_health("STUDY_ACCOUNT_MAX_FILES", 50),
            "account_max_storage_mb": _env_positive_int_health("STUDY_ACCOUNT_MAX_STORAGE_MB", 250),
        },
    }
