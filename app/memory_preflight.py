"""Unified hardware profile, memory-aware preflight, and live monitor for MLX training.

Enforces strict unified memory discipline for 16GB Apple Silicon Mac,
guards RAM reserve for OS and running apps, fails closed if memory is unmeasurable,
performs exact token audits without silent truncation, and monitors memory/swap peaks.
"""

import os
import sys
import json
import time
import shutil
import logging
import threading
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.config import BASE_DIR, DATA_DIR, MLX_MODEL_DIR
from app.gpu_lock import gpu_coordinator

logger = logging.getLogger(__name__)

def parse_swapusage(output: str) -> Dict[str, float]:
    """Parse sysctl vm.swapusage output into MB values.
    
    Example input: 'vm.swapusage: total = 4096.00M  used = 3390.38M  free = 705.62M  (encrypted)'
    """
    import re
    def _to_mb(val_str: str, unit: str) -> float:
        val = float(val_str)
        unit = unit.upper()
        if unit == "G":
            return val * 1024.0
        elif unit == "K":
            return val / 1024.0
        elif unit == "B":
            return val / (1024.0 * 1024.0)
        return val  # 'M'

    pattern = re.compile(r"(\w+)\s*=\s*([0-9.]+)([BKMGTbkmgt])")
    matches = pattern.findall(output)
    result = {}
    for key, num, unit in matches:
        result[key.lower()] = round(_to_mb(num, unit), 2)
    if "total" not in result or "used" not in result:
        raise ValueError(f"Không thể phân tích vm.swapusage từ output: {output}")
    return result


def get_hardware_memory_profile() -> Dict[str, Any]:
    """Retrieve macOS Unified Memory and swap statistics.

    FAIL-CLOSED: If sysctl or vm_stat fails, returns measurement_error and blocks training.
    """
    total_bytes = None
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        total_bytes = int(out)
    except Exception as exc:
        return {
            "eligible": False,
            "measurement_error": f"Không thể đọc sysctl hw.memsize: {exc}",
        }

    total_gb = total_bytes / (1024 ** 3)
    free_gb = None
    active_gb = None
    inactive_gb = None
    wired_gb = None

    try:
        vm_out = subprocess.check_output(
            ["vm_stat"], text=True, stderr=subprocess.DEVNULL
        )
        page_size = 4096
        if "page size of" in vm_out:
            for line in vm_out.splitlines():
                if "page size of" in line:
                    parts = line.split("page size of")
                    if len(parts) > 1:
                        page_size = int(parts[1].split()[0])
                    break

        for line in vm_out.splitlines():
            line = line.strip()
            if line.startswith("Pages free:"):
                free_gb = (int(line.split(":")[1].strip().rstrip(".")) * page_size) / (1024 ** 3)
            elif line.startswith("Pages active:"):
                active_gb = (int(line.split(":")[1].strip().rstrip(".")) * page_size) / (1024 ** 3)
            elif line.startswith("Pages inactive:"):
                inactive_gb = (int(line.split(":")[1].strip().rstrip(".")) * page_size) / (1024 ** 3)
            elif line.startswith("Pages wired down:"):
                wired_gb = (int(line.split(":")[1].strip().rstrip(".")) * page_size) / (1024 ** 3)
    except Exception as exc:
        return {
            "eligible": False,
            "total_gb": round(total_gb, 2),
            "measurement_error": f"Không thể đọc vm_stat: {exc}",
        }

    available_gb = (free_gb + inactive_gb) if (free_gb is not None and inactive_gb is not None) else None

    # Swap query
    swap_total_mb = None
    swap_used_mb = None
    swap_error = None
    try:
        swap_out = subprocess.check_output(
            ["sysctl", "vm.swapusage"], text=True, stderr=subprocess.DEVNULL
        )
        swap_stats = parse_swapusage(swap_out)
        swap_total_mb = swap_stats["total"]
        swap_used_mb = swap_stats["used"]
    except Exception as exc:
        swap_error = f"Lỗi đọc vm.swapusage: {exc}"

    # Disk query
    disk_free_gb = 0.0
    try:
        st = shutil.disk_usage(DATA_DIR)
        disk_free_gb = st.free / (1024 ** 3)
    except Exception:
        pass

    return {
        "eligible": True,
        "total_gb": round(total_gb, 2),
        "available_gb": round(available_gb, 2) if available_gb is not None else None,
        "free_gb": round(free_gb, 2) if free_gb is not None else None,
        "inactive_gb": round(inactive_gb, 2) if inactive_gb is not None else None,
        "active_gb": round(active_gb, 2) if active_gb is not None else None,
        "wired_gb": round(wired_gb, 2) if wired_gb is not None else None,
        "swap_total_mb": round(swap_total_mb, 2) if swap_total_mb is not None else None,
        "swap_used_mb": round(swap_used_mb, 2) if swap_used_mb is not None else None,
        "disk_free_gb": round(disk_free_gb, 2),
        "measurement_error": swap_error,
    }


def inspect_base_model(model_dir: Path) -> Dict[str, Any]:
    """Verify base model architecture, safetensors weights header/layout, shards, and tokenizer."""
    if not model_dir.exists():
        raise FileNotFoundError(f"Thư mục mô hình không tồn tại: {model_dir}")

    config_path = model_dir / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"config.json không tìm thấy trong {model_dir}")

    from app.model_resolver import validate_native_model_config
    config = validate_native_model_config(model_dir)
    text = config['text_config']
    hidden_size = text['hidden_size']
    num_layers = text['num_hidden_layers']
    architectures = config['architectures']
    quant_bits = (config.get('quantization') or config.get('quantization_config'))['bits']

    # Weights layout check: single model.safetensors or multi-shard index
    weights_path = model_dir / "model.safetensors"
    index_path = model_dir / "model.safetensors.index.json"
    has_weights = False
    weight_bytes = 0

    if weights_path.exists():
        weight_bytes = weights_path.stat().st_size
        if weight_bytes >= 8:
            import struct
            with open(weights_path, "rb") as f:
                header_len = struct.unpack("<Q", f.read(8))[0]
                if 0 < header_len < weight_bytes:
                    has_weights = True
    elif index_path.exists():
        try:
            index_data = json.loads(index_path.read_text(encoding="utf-8"))
            shards = set(index_data.get("weight_map", {}).values())
            if shards and all((model_dir / s).exists() for s in shards):
                has_weights = True
                weight_bytes = sum((model_dir / s).stat().st_size for s in shards)
        except Exception:
            has_weights = False

    # Tokenizer check: requires tokenizer.json OR (vocab.json + merges.txt) with tokenizer_config.json
    has_tokenizer = (model_dir / "tokenizer_config.json").exists() and (
        (model_dir / "tokenizer.json").exists()
        or ((model_dir / "vocab.json").exists() and (model_dir / "merges.txt").exists())
    )

    return {
        "model_dir": str(model_dir),
        "model_type": "Qwen3.5-9B-4bit",
        "is_supported_native": True,
        "hidden_size": hidden_size,
        "num_hidden_layers": num_layers,
        "architectures": architectures,
        "quantization_bits": quant_bits,
        "has_weights": has_weights,
        "weights_bytes": weight_bytes,
        "total_weight_mb": weight_bytes / (1024 ** 2),
        "has_tokenizer": has_tokenizer,
    }


def audit_dataset_tokens(dataset_dir: Path, max_seq_length: int = 1024, model_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Exact token audit across training examples using base model chat template and tokenizer."""
    from app.token_auditor import audit_dataset_exact
    return audit_dataset_exact(dataset_dir=dataset_dir, model_dir=model_dir, max_seq_length=max_seq_length)


def preflight_check_memory_training(
    model_dir: Optional[Path] = None,
    dataset_dir: Optional[Path] = None,
    batch_size: int = 1,
    num_layers: int = 4,
    max_seq_length: int = 1024,
    lora_rank: int = 8,
    grad_checkpoint: bool = True,
) -> Dict[str, Any]:
    """Retired execution boundary; diagnostics cannot authorize current-model training.

    A new protocol needs measured Qwen3.5 memory, reviewed data and explicit
    owner authorization. No estimate from another architecture is reused.
    """
    return {
        "eligible_for_training": False,
        "violations": ["Chưa có giao thức huấn luyện Qwen3.5 9B được kiểm chứng và phê duyệt."],
        "estimated_peak_memory_gb": None,
        "is_empirical_measurement": False,
    }


class LiveMemoryMonitor:
    """Live monitor sampling memory and swap during MLX training, executing graceful abort under pressure."""

    def __init__(
        self,
        check_interval_sec: float = 1.5,
        abort_callback: Optional[callable] = None,
        telemetry_callback: Optional[callable] = None,
    ):
        self.check_interval_sec = check_interval_sec
        self.abort_callback = abort_callback
        self.telemetry_callback = telemetry_callback
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self.peak_swap_used_mb = 0.0
        self.min_avail_ram_gb = 999.0
        self.initial_swap_mb = 0.0
        self.abort_triggered = False
        self.abort_reason = None

    def start(self):
        hw = get_hardware_memory_profile()
        self.initial_swap_mb = hw.get("swap_used_mb", 0.0)
        self.peak_swap_used_mb = self.initial_swap_mb
        self.min_avail_ram_gb = hw.get("available_gb", 999.0) or 999.0
        self.is_running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def _monitor_loop(self):
        consecutive_errors = 0
        while self.is_running:
            hw = get_hardware_memory_profile()
            avail = hw.get("available_gb")
            swap = hw.get("swap_used_mb", 0.0)

            if self.telemetry_callback:
                try:
                    self.telemetry_callback(avail, swap)
                except Exception:
                    pass

            if not hw.get("eligible") or avail is None:
                consecutive_errors += 1
                if consecutive_errors >= 3 and not self.abort_triggered:
                    self.abort_triggered = True
                    self.abort_reason = (
                        "Mất khả năng đo lường bộ nhớ (sysctl/vm_stat lỗi liên tiếp trong lúc theo dõi). "
                        "Dừng khẩn cấp có kiểm soát để bảo vệ hệ thống."
                    )
                    logger.critical(self.abort_reason)
                    if self.abort_callback:
                        self.abort_callback(self.abort_reason)
                    break
            else:
                consecutive_errors = 0

            if avail is not None:
                if avail < self.min_avail_ram_gb:
                    self.min_avail_ram_gb = avail
                # Memory pressure emergency floor: available RAM < 1.0 GiB
                if avail < 1.0 and not self.abort_triggered:
                    self.abort_triggered = True
                    self.abort_reason = f"Áp lực RAM quá cao (RAM khả dụng tụt xuống {avail:.2f} GiB < 1.0 GiB). Kích hoạt dừng khẩn cấp có kiểm soát."
                    logger.critical(self.abort_reason)
                    if self.abort_callback:
                        self.abort_callback(self.abort_reason)
                    break

            if swap is not None and swap > self.peak_swap_used_mb:
                self.peak_swap_used_mb = swap
            # Swap spike check: swap growth > 1500 MB
            if swap is not None and (swap - self.initial_swap_mb) > 1500.0 and not self.abort_triggered:
                self.abort_triggered = True
                self.abort_reason = f"Hiện tượng tràn swap quá nhanh ({swap - self.initial_swap_mb:.1f} MB). Dừng có kiểm soát để bảo vệ ứng dụng người dùng."
                logger.critical(self.abort_reason)
                if self.abort_callback:
                    self.abort_callback(self.abort_reason)
                break

            time.sleep(self.check_interval_sec)

    def stop(self) -> Dict[str, Any]:
        self.is_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        return {
            "min_available_ram_gb": round(self.min_avail_ram_gb, 2),
            "peak_swap_used_mb": round(self.peak_swap_used_mb, 2),
            "swap_growth_mb": round(self.peak_swap_used_mb - self.initial_swap_mb, 2),
            "abort_triggered": self.abort_triggered,
            "abort_reason": self.abort_reason,
        }
