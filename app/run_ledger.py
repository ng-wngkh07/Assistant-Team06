"""Run Ledger: immutable trial execution registry, monitoring, and reconciliation.

Provides pre-allocated run IDs, immutable manifests, atomic writes,
loss/memory telemetry logging, duplicate signature blocking, and startup reconciliation
for interrupted or killed runs with PID reuse validation and child process liveness checks.
"""

import os
import sys
import json
import time
import uuid
import hashlib
import logging
import subprocess
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

from app.config import BASE_DIR, DATA_DIR

logger = logging.getLogger(__name__)

RUNS_DIR = DATA_DIR / "runtime" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)
LEDGER_INDEX_FILE = DATA_DIR / "runtime" / "run_ledger.jsonl"

STATUS_CREATED = "CREATED"
STATUS_PREFLIGHT = "PREFLIGHT"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_PROMOTED = "PROMOTED"
STATUS_REJECTED = "REJECTED"
STATUS_FAILED = "FAILED"
STATUS_OOM = "OOM"
STATUS_BLOCKED = "BLOCKED"
STATUS_CANCELLED = "CANCELLED"
STATUS_KILLED_ABNORMAL = "KILLED_ABNORMAL"

TERMINAL_STATUSES = {
    STATUS_COMPLETED, STATUS_PROMOTED, STATUS_REJECTED,
    STATUS_FAILED, STATUS_OOM, STATUS_BLOCKED,
    STATUS_CANCELLED, STATUS_KILLED_ABNORMAL,
}

VALID_STATUSES = {
    STATUS_CREATED, STATUS_PREFLIGHT, STATUS_RUNNING,
    STATUS_COMPLETED, STATUS_PROMOTED, STATUS_REJECTED,
    STATUS_FAILED, STATUS_OOM, STATUS_BLOCKED,
    STATUS_CANCELLED, STATUS_KILLED_ABNORMAL,
}

_ledger_lock = threading.Lock()


def _atomic_write_json(file_path: Path, data: dict) -> None:
    """Write dictionary to JSON atomically using replace on same filesystem."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = file_path.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_file, file_path)


_FILE_SHA256_CACHE: Dict[Tuple[str, int, int, int, int, int], str] = {}


def compute_file_sha256(p: Path) -> str:
    """Compute sha256 of file content if it exists, cached by stat including ctime."""
    if not p.exists() or not p.is_file():
        return "MISSING"
    try:
        p_res = p.resolve()
        stat = p_res.stat()
        key = (str(p_res), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if key in _FILE_SHA256_CACHE:
            return _FILE_SHA256_CACHE[key]
        h = hashlib.sha256()
        with open(p_res, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        digest = h.hexdigest()
        _FILE_SHA256_CACHE[key] = digest
        return digest
    except Exception:
        return "ERROR_READING"


def compute_code_fingerprint() -> Dict[str, str]:
    """Calculate SHA256 of key training and validation files."""
    files_to_hash = [
        "app/fine_tune.py",
        "app/corrective_training.py",
        "app/preflight_7b.py",
        "app/memory_preflight.py",
        "app/token_auditor.py",
        "app/run_ledger.py",
        "app/paired_evaluation.py",
        "app/training_telemetry.py",
        "app/study_acceptance.py",
        "app/active_registry.py",
    ]
    fingerprints = {}
    for rel in files_to_hash:
        p = BASE_DIR / rel
        fingerprints[rel] = compute_file_sha256(p)
    return fingerprints


_orig_popen = subprocess.Popen


def get_process_start_time(pid: Optional[int]) -> Optional[str]:
    """Retrieve process start time string via ps on macOS/UNIX for PID reuse protection."""
    if not pid or pid <= 0:
        return None
    try:
        proc = _orig_popen(
            ["ps", "-p", str(pid), "-o", "lstart="],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        out, _ = proc.communicate(timeout=2.0)
        return out.strip() if out else None
    except Exception:
        return None


def is_process_alive_and_matched(
    pid: Optional[int],
    expected_start_time: Optional[str] = None,
    expected_tokens: Tuple[str, ...] = ("python", "mlx"),
) -> bool:
    """Check if process is alive, matches expected start time, and matches expected command token."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    if expected_start_time:
        current_start = get_process_start_time(pid)
        if not current_start or current_start != expected_start_time:
            return False

    try:
        proc = _orig_popen(
            ["ps", "-p", str(pid), "-o", "command="],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        out, _ = proc.communicate(timeout=2.0)
        cmd = out.strip().lower()
        return any(t in cmd for t in expected_tokens)
    except Exception:
        return True


def _compute_weights_hash(p: Path) -> str:
    if not p.exists():
        return "none"
    return compute_file_sha256(p)


def compute_signature_hash(
    config: dict,
    base_model_dir: Union[Path, str],
    dataset_sha256: str,
    target_hypothesis: str,
    frozen_holdout_id: str = "",
    code_fingerprint: Optional[dict] = None,
) -> str:
    """Generate comprehensive signature identifying an identical trial setup.

    Binds: config, base revision/file hashes, weights content, dataset hashes, holdout id, code fingerprint.
    """
    base_path = Path(base_model_dir)
    weights_path = base_path / "model.safetensors"
    base_hashes = {
        "path": str(base_path),
        "config_json": compute_file_sha256(base_path / "config.json"),
        "tokenizer_json": compute_file_sha256(base_path / "tokenizer.json"),
        "weights_size": weights_path.stat().st_size if weights_path.exists() else 0,
        "weights_hash": _compute_weights_hash(weights_path),
    }

    payload = {
        "config": config,
        "base_model": base_hashes,
        "dataset_sha256": dataset_sha256,
        "holdout_id": frozen_holdout_id,
        "hypothesis": target_hypothesis,
        "code_fingerprint": code_fingerprint or compute_code_fingerprint(),
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class RunLedger:
    """Manages immutable run lifecycles, step telemetry, and crash reconciliations."""

    def __init__(
        self,
        runs_dir: Path = RUNS_DIR,
        index_file: Path = LEDGER_INDEX_FILE,
        auto_reconcile: bool = False,
    ):
        self.runs_dir = runs_dir
        self.index_file = index_file
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        if auto_reconcile:
            self.reconcile_active_runs()

    @staticmethod
    def allocate_run_id() -> str:
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        short_id = uuid.uuid4().hex[:6]
        return f"run-{timestamp}-{short_id}"

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        path = self.runs_dir / f"{run_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Lỗi đọc run {run_id}: {e}")
        return None

    def check_duplicate_signature(
        self,
        signature_hash: str,
        remediation: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Block unchanged execution failures and quality rejections without remediation.

        Remediation must be a structured dict with diagnosis, evidence, and code_diff_hash.
        Fails closed on unreadable files, malformed JSON, or invalid record schemas.
        """
        for path in sorted(self.runs_dir.glob("run-*.json")):
            try:
                raw_text = path.read_text(encoding="utf-8")
                entry = json.loads(raw_text)
            except Exception as e:
                return True, f"Bản ghi ledger không thể đọc hoặc JSON lỗi tại {path.name}: {e}", None

            if not isinstance(entry, dict):
                return True, f"Bản ghi ledger tại {path.name} không phải là dict hợp lệ", None

            run_id = entry.get("run_id")
            if not isinstance(run_id, str) or not run_id.strip():
                return True, f"Bản ghi ledger tại {path.name} thiếu hoặc sai kiểu 'run_id'", None

            status = entry.get("status")
            if not isinstance(status, str) or status not in VALID_STATUSES:
                return True, f"Bản ghi ledger tại {path.name} có trạng thái không hợp lệ: {status!r}", None

            if "signature_hash" not in entry:
                return True, f"Bản ghi ledger tại {path.name} thiếu trường 'signature_hash'", None

            sig = entry["signature_hash"]
            if sig is None:
                manifest_dict = entry.get("manifest")
                dataset_dict = manifest_dict.get("dataset") if isinstance(manifest_dict, dict) else None
                is_pending = isinstance(dataset_dict, dict) and dataset_dict.get("dataset_sha256") == "pending_preflight"
                is_valid_provisional = (
                    (status in (STATUS_CREATED, STATUS_PREFLIGHT) and (manifest_dict is None or is_pending))
                    or (status in (STATUS_FAILED, STATUS_OOM, STATUS_BLOCKED, STATUS_CANCELLED, STATUS_KILLED_ABNORMAL) and is_pending)
                )
                if not is_valid_provisional:
                    return True, f"Bản ghi ledger tại {path.name} có signature_hash=null không hợp lệ với trạng thái '{status}'", None
                continue
            elif not isinstance(sig, str) or not sig.strip():
                return True, f"Bản ghi ledger tại {path.name} có 'signature_hash' không hợp lệ: {sig!r}", None

            if sig == signature_hash:
                if status in (STATUS_FAILED, STATUS_OOM, STATUS_KILLED_ABNORMAL, STATUS_BLOCKED, STATUS_REJECTED):
                    is_valid_remediation = (
                        isinstance(remediation, dict)
                        and bool(str(remediation.get("diagnosis", "")).strip())
                        and bool(str(remediation.get("evidence", "")).strip())
                        and bool(str(remediation.get("code_diff_hash", "")).strip())
                        and len(str(remediation.get("diagnosis", ""))) >= 15
                        and len(str(remediation.get("evidence", ""))) >= 15
                    )
                    if not is_valid_remediation:
                        msg = (
                            f"CHẶN CHẠY LẠI: Chữ ký cấu hình trùng lặp với run '{run_id}' "
                            f"(kết thúc với trạng thái '{status}'). Yêu cầu hồ sơ khắc phục cấu trúc "
                            "chứa {diagnosis, evidence, code_diff_hash} với giải trình kiểm chứng được."
                        )
                        return True, msg, entry
        return False, None, None

    def create_run(
        self,
        config: dict,
        base_model_dir: Path,
        dataset_dir: Path,
        dataset_sha256: str = "pending_preflight",
        target_hypothesis: str = "",
        baseline_metrics: Optional[dict] = None,
        frozen_holdout_id: str = "",
        continuation_from_run: Optional[str] = None,
        remediation: Optional[Dict[str, Any]] = None,
        child_pid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Pre-allocate immutable run record before preflight (Phase 1).

        If dataset_sha256 is already known (not 'pending_preflight'), checks duplicate signature immediately.
        Otherwise, pre-allocates an attempt record awaiting verified manifest binding.
        """
        run_id = self.allocate_run_id()
        code_fp = compute_code_fingerprint()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        runner_start = get_process_start_time(os.getpid())
        child_start = get_process_start_time(child_pid) if child_pid else None

        sig_hash = None
        if dataset_sha256 != "pending_preflight":
            sig_hash = compute_signature_hash(
                config=config,
                base_model_dir=base_model_dir,
                dataset_sha256=dataset_sha256,
                target_hypothesis=target_hypothesis,
                frozen_holdout_id=frozen_holdout_id,
                code_fingerprint=code_fp,
            )

            # Duplicate signature check
            is_dup, dup_msg, prev_run = self.check_duplicate_signature(sig_hash, remediation)
            if is_dup:
                blocked_record = {
                    "run_id": run_id,
                    "created_at": now,
                    "status": STATUS_BLOCKED,
                    "pid": os.getpid(),
                    "runner_start_time": runner_start,
                    "child_pid": child_pid,
                    "child_start_time": child_start,
                    "signature_hash": sig_hash,
                    "target_hypothesis": target_hypothesis,
                    "remediation": remediation,
                    "diagnosis": dup_msg,
                    "decision": "BLOCKED_DUPLICATE_FAILED_SIGNATURE",
                    "blocked_by_run_id": prev_run.get("run_id") if prev_run else None,
                    "telemetry": {
                        "start_time": now,
                        "end_time": now,
                        "duration_seconds": 0.0,
                        "return_code": 1,
                        "peak_memory_mb": 0.0,
                        "peak_swap_mb": 0.0,
                        "step_losses": [],
                    },
                    "manifest": {
                        "config": config,
                        "base_model": {"path": str(base_model_dir)},
                        "dataset": {"path": str(dataset_dir), "dataset_sha256": dataset_sha256},
                        "code_fingerprint": code_fp,
                        "frozen_holdout_id": frozen_holdout_id,
                    },
                }
                path = self.runs_dir / f"{run_id}.json"
                _atomic_write_json(path, blocked_record)
                self._append_to_index(run_id, STATUS_BLOCKED, now)
                raise ValueError(f"{dup_msg} (Đã ghi nhận bản ghi: {run_id})")

        record = {
            "run_id": run_id,
            "created_at": now,
            "status": STATUS_CREATED,
            "pid": os.getpid(),
            "runner_start_time": runner_start,
            "child_pid": child_pid,
            "child_start_time": child_start,
            "signature_hash": sig_hash,
            "target_hypothesis": target_hypothesis,
            "remediation": remediation,
            "continuation": {
                "is_continuation": continuation_from_run is not None,
                "parent_run_id": continuation_from_run,
                "resume_mode": "weights_only_continuation" if continuation_from_run else "none",
                "notice": (
                    "MLX LoRA không hỗ trợ exact resume đầy đủ optimizer/RNG/data offsets; "
                    "tiếp tục từ weights được đối xử như một run con (weights-only continuation)."
                    if continuation_from_run else None
                ),
            },
            "manifest": {
                "config": config,
                "base_model": {
                    "path": str(base_model_dir),
                    "config_exists": (base_model_dir / "config.json").exists(),
                    "weights_exists": (base_model_dir / "model.safetensors").exists(),
                },
                "dataset": {
                    "path": str(dataset_dir),
                    "dataset_sha256": dataset_sha256,
                },
                "code_fingerprint": code_fp,
                "baseline_metrics": baseline_metrics or {},
                "frozen_holdout_id": frozen_holdout_id,
            },
            "telemetry": {
                "step_losses": [],
                "peak_memory_mb": 0.0,
                "system_memory_used_mb": 0.0,
                "peak_swap_mb": 0.0,
                "mlx_peak_memory_gb": None,
                "start_time": None,
                "end_time": None,
                "duration_seconds": 0.0,
                "return_code": None,
            },
            "checkpoints": [],
            "diagnosis": None,
            "decision": None,
        }

        path = self.runs_dir / f"{run_id}.json"
        _atomic_write_json(path, record)
        self._append_to_index(run_id, STATUS_CREATED, now)
        return record

    def bind_verified_manifest(
        self,
        run_id: str,
        config: dict,
        base_model_dir: Union[Path, str],
        dataset_dir: Union[Path, str],
        dataset_sha256: str,
        target_hypothesis: str,
        baseline_metrics: dict,
        frozen_holdout_id: str,
        continuation_from_run: Optional[str] = None,
        remediation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Phase 2: Finalize immutable bound manifest + check duplicate signature before MLX spawn."""
        with _ledger_lock:
            record = self.get_run(run_id)
            if not record:
                raise FileNotFoundError(f"Run {run_id} không tồn tại trong ledger")

            if record.get("signature_hash") is not None and record.get("manifest", {}).get("dataset", {}).get("dataset_sha256") != "pending_preflight":
                raise ValueError(f"Run {run_id} đã được khóa manifest bất biến (signature_hash={record['signature_hash']}); không thể tái liên kết (rebound).")

            code_fp = compute_code_fingerprint()
            sig_hash = compute_signature_hash(
                config=config,
                base_model_dir=base_model_dir,
                dataset_sha256=dataset_sha256,
                target_hypothesis=target_hypothesis,
                frozen_holdout_id=frozen_holdout_id,
                code_fingerprint=code_fp,
            )

            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            is_dup, dup_msg, prev_run = self.check_duplicate_signature(sig_hash, remediation)
            if is_dup:
                record["status"] = STATUS_BLOCKED
                record["signature_hash"] = sig_hash
                record["diagnosis"] = dup_msg
                record["decision"] = "BLOCKED_DUPLICATE_FAILED_SIGNATURE"
                record["blocked_by_run_id"] = prev_run.get("run_id") if prev_run else None
                record["manifest"]["config"] = config
                record["manifest"]["dataset"]["dataset_sha256"] = dataset_sha256
                record["manifest"]["code_fingerprint"] = code_fp
                record["manifest"]["baseline_metrics"] = baseline_metrics
                record["manifest"]["frozen_holdout_id"] = frozen_holdout_id
                path = self.runs_dir / f"{run_id}.json"
                _atomic_write_json(path, record)
                self._append_to_index(run_id, STATUS_BLOCKED, now)
                raise ValueError(f"{dup_msg} (Đã ghi nhận bản ghi: {run_id})")

            base_model_path = Path(base_model_dir)
            record["signature_hash"] = sig_hash
            record["target_hypothesis"] = target_hypothesis
            record["remediation"] = remediation
            record["manifest"]["config"] = config
            record["manifest"]["base_model"] = {
                "path": str(base_model_dir),
                "config_exists": (base_model_path / "config.json").exists(),
                "weights_exists": (base_model_path / "model.safetensors").exists(),
            }
            record["manifest"]["dataset"] = {
                "path": str(dataset_dir),
                "dataset_sha256": dataset_sha256,
            }
            record["manifest"]["code_fingerprint"] = code_fp
            record["manifest"]["baseline_metrics"] = baseline_metrics
            record["manifest"]["frozen_holdout_id"] = frozen_holdout_id

            path = self.runs_dir / f"{run_id}.json"
            _atomic_write_json(path, record)
            return record

    def update_child_pid(self, run_id: str, child_pid: int) -> None:
        """Register child MLX training process PID and process start time."""
        with _ledger_lock:
            record = self.get_run(run_id)
            if record:
                record["child_pid"] = child_pid
                record["child_start_time"] = get_process_start_time(child_pid)
                path = self.runs_dir / f"{run_id}.json"
                _atomic_write_json(path, record)

    def update_status(
        self,
        run_id: str,
        status: str,
        diagnosis: Optional[str] = None,
        decision: Optional[str] = None,
        return_code: Optional[int] = None,
        peak_memory_mb: Optional[float] = None,
        peak_swap_mb: Optional[float] = None,
        system_used_mb: Optional[float] = None,
        mlx_peak_memory_gb: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update run state and persist atomically."""
        with _ledger_lock:
            record = self.get_run(run_id)
            if not record:
                raise FileNotFoundError(f"Run {run_id} không tồn tại trong ledger")

            record["status"] = status
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            if status == STATUS_RUNNING and not record["telemetry"]["start_time"]:
                record["telemetry"]["start_time"] = now
                record["pid"] = os.getpid()

            # Evaluation can reject/promote a completed run much later. Its event
            # timestamp belongs in the index; the measured execution clock stays.
            if status in TERMINAL_STATUSES and not record["telemetry"].get("end_time"):
                record["telemetry"]["end_time"] = now
                start_str = record["telemetry"]["start_time"] or record["created_at"]
                if start_str:
                    try:
                        t_start = time.mktime(time.strptime(start_str, "%Y-%m-%dT%H:%M:%SZ"))
                        t_end = time.mktime(time.strptime(now, "%Y-%m-%dT%H:%M:%SZ"))
                        record["telemetry"]["duration_seconds"] = max(0.0, t_end - t_start)
                    except Exception:
                        pass

            if diagnosis is not None:
                record["diagnosis"] = diagnosis
            if decision is not None:
                record["decision"] = decision
            if return_code is not None:
                record["telemetry"]["return_code"] = return_code
            if peak_memory_mb is not None:
                record["telemetry"]["peak_memory_mb"] = max(record["telemetry"].get("peak_memory_mb", 0.0), peak_memory_mb)
            if system_used_mb is not None:
                record["telemetry"]["system_memory_used_mb"] = max(record["telemetry"].get("system_memory_used_mb", 0.0), system_used_mb)
            if peak_swap_mb is not None:
                record["telemetry"]["peak_swap_mb"] = max(record["telemetry"].get("peak_swap_mb", 0.0), peak_swap_mb)
            if mlx_peak_memory_gb is not None:
                current_peak = record["telemetry"].get("mlx_peak_memory_gb")
                record["telemetry"]["mlx_peak_memory_gb"] = max(current_peak or 0.0, mlx_peak_memory_gb)

            path = self.runs_dir / f"{run_id}.json"
            _atomic_write_json(path, record)
            self._append_to_index(run_id, status, now)
            return record

    def record_step(self, run_id: str, step: int, loss: float, memory_mb: float = 0.0,
                    mlx_peak_memory_gb: Optional[float] = None) -> None:
        """Record loss and separately sourced RSS/MLX measurements with deduplication.

        Legacy memory_mb=0 means unmeasured, never zero RAM consumption.
        MLX reports cumulative allocation peak in decimal GB; it is not RSS.
        """
        with _ledger_lock:
            record = self.get_run(run_id)
            if not record:
                return
            existing_steps = {s["step"] for s in record["telemetry"]["step_losses"]}
            if step in existing_steps:
                for s in record["telemetry"]["step_losses"]:
                    if s["step"] == step:
                        s["loss"] = loss
                        if memory_mb:
                            s["memory_mb"] = memory_mb
                            s["memory_mb_measured"] = True
                        if mlx_peak_memory_gb is not None:
                            s["mlx_peak_memory_gb"] = mlx_peak_memory_gb
                        break
            else:
                record["telemetry"]["step_losses"].append({
                    "step": step, "loss": loss, "memory_mb": memory_mb,
                    "memory_mb_measured": memory_mb > 0,
                    "mlx_peak_memory_gb": mlx_peak_memory_gb,
                })
            if memory_mb > record["telemetry"]["peak_memory_mb"]:
                record["telemetry"]["peak_memory_mb"] = memory_mb
            path = self.runs_dir / f"{run_id}.json"
            _atomic_write_json(path, record)

    def record_checkpoint(self, run_id: str, step: int, checkpoint_path: Path, weights_hash: Optional[str] = None) -> None:
        """Record saved checkpoint step, path, and weights hash into run ledger.
        
        Skips disk IO if the checkpoint file stat and step are unchanged.
        Maintains revision history when weights change.
        """
        with _ledger_lock:
            record = self.get_run(run_id)
            if not record:
                return
            ckpt_path = Path(checkpoint_path)
            if not ckpt_path.is_file():
                return
            w_hash = weights_hash or compute_file_sha256(ckpt_path)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            existing = {c["path"]: c for c in record.get("checkpoints", [])}
            if str(ckpt_path) in existing:
                c = existing[str(ckpt_path)]
                # Skip unchanged checkpoint IO
                if c.get("step") == step and c.get("weights_hash") == w_hash:
                    return
                # Weights or step changed: record revision and update
                if "revisions" not in c:
                    c["revisions"] = []
                c["revisions"].append({
                    "step": c.get("step"),
                    "weights_hash": c.get("weights_hash"),
                    "replaced_at": now,
                })
                c["step"] = step
                c["weights_hash"] = w_hash
                c["updated_at"] = now
            else:
                record.setdefault("checkpoints", []).append({
                    "step": step,
                    "path": str(ckpt_path),
                    "weights_hash": w_hash,
                    "recorded_at": now,
                })
            path = self.runs_dir / f"{run_id}.json"
            _atomic_write_json(path, record)

    def reconcile_active_runs(self) -> int:
        """Scan and automatically reconcile runs stuck in CREATED, PREFLIGHT, or RUNNING after processes died."""
        reconciled_count = 0
        with _ledger_lock:
            for path in sorted(self.runs_dir.glob("run-*.json")):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    status = record.get("status")
                    if status in (STATUS_CREATED, STATUS_PREFLIGHT, STATUS_RUNNING):
                        runner_pid = record.get("pid")
                        child_pid = record.get("child_pid")

                        runner_alive = is_process_alive_and_matched(
                            runner_pid, expected_start_time=record.get("runner_start_time")
                        )
                        child_alive = is_process_alive_and_matched(
                            child_pid, expected_start_time=record.get("child_start_time")
                        )

                        # If child MLX process is still active, do not declare killed
                        if child_alive:
                            continue

                        # If runner is still alive, do not declare killed
                        if runner_alive:
                            continue

                        # Both runner and child process are confirmed dead
                        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        record["status"] = STATUS_KILLED_ABNORMAL
                        record["telemetry"]["end_time"] = now
                        start_str = record["telemetry"].get("start_time") or record.get("created_at")
                        if start_str:
                            try:
                                t_start = time.mktime(time.strptime(start_str, "%Y-%m-%dT%H:%M:%SZ"))
                                t_end = time.mktime(time.strptime(now, "%Y-%m-%dT%H:%M:%SZ"))
                                record["telemetry"]["duration_seconds"] = max(0.0, t_end - t_start)
                            except Exception:
                                pass
                        record["telemetry"]["return_code"] = -9
                        record["diagnosis"] = (
                            f"Tự động đối soát: Tiến trình runner (PID {runner_pid}) và child MLX (PID {child_pid}) "
                            "không còn hoạt động trên máy. Tự động chuyển trạng thái từ "
                            f"{status} sang KILLED_ABNORMAL để đóng chu trình."
                        )
                        record["decision"] = "ABORTED_SYSTEM"
                        _atomic_write_json(path, record)
                        self._append_to_index(record["run_id"], STATUS_KILLED_ABNORMAL, now)
                        reconciled_count += 1
                except Exception as exc:
                    logger.error(f"Lỗi khi đối soát bản ghi {path}: {exc}")
                    continue
        return reconciled_count

    def _append_to_index(self, run_id: str, status: str, timestamp: str) -> None:
        """Append state event to index file with explicit error logging."""
        entry = {"run_id": run_id, "status": status, "timestamp": timestamp}
        try:
            with self.index_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error(f"Lỗi ghi sổ index {self.index_file} cho run {run_id}: {exc}")


# Lazy singleton instance (does not run reconcile automatically on import)
run_ledger = RunLedger(auto_reconcile=False)
