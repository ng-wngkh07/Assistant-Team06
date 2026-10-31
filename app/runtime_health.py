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
        return ('Máy chủ không truy cập được GPU Metal của máy. Dừng máy chủ cũ và mở '
                'Start Study Agent.command từ Finder hoặc chạy từ Terminal. Sau đó bấm Kiểm tra mô hình.')
    return ('Không khởi chạy được mô hình cục bộ. Bấm Kiểm tra mô hình để chẩn đoán; '
            'mở Start Study Agent.command (Mac) hoặc Start Study Agent.bat (Windows).')


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
