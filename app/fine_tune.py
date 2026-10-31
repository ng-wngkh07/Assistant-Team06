"""Audit legacy training requests; execution is retired pending a reviewed 9B protocol."""

import argparse
import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Optional

from app.config import BASE_DIR, TRAINING_MODEL_DIR
from app.gpu_lock import GPULockManager, gpu_coordinator
from app.run_ledger import (
    RunLedger, run_ledger,
    STATUS_PREFLIGHT, STATUS_FAILED,
    STATUS_REJECTED, STATUS_KILLED_ABNORMAL,
)

logger = logging.getLogger(__name__)


def verify_auxiliary_files(data_dir: Path, approval: dict):
    """Bind separately reviewed curriculum provenance to the approved run."""
    for name, expected in approval.get("auxiliary_files_sha256", {}).items():
        path = data_dir / name
        if path.resolve().parent != data_dir.resolve() or not path.is_file():
            raise ValueError("Tệp dữ liệu bổ sung không hợp lệ: " + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Dữ liệu bổ sung đã thay đổi sau khi duyệt: " + name)


def _get_active_ledger(data_dir: Path, explicit_ledger: Optional[RunLedger] = None) -> RunLedger:
    if explicit_ledger is not None:
        return explicit_ledger
    import app.config as cfg
    if BASE_DIR != cfg.BASE_DIR:
        runs_dir = BASE_DIR / "data" / "runs"
        return RunLedger(runs_dir=runs_dir, index_file=runs_dir / "index.jsonl")
    try:
        data_dir.resolve().relative_to(BASE_DIR.resolve())
    except ValueError:
        test_runs = data_dir.parent / "test_runs"
        return RunLedger(runs_dir=test_runs, index_file=test_runs / "index.jsonl")
    return run_ledger


def train(data_dir: Path, iterations: int = 80, num_layers: int = 4,
          adapter_dir: Path | None = None, model_dir: Path | None = None,
          learning_rate: float = 0.00002,
          max_seq_length: int = 1024, coordinator: GPULockManager | None = None,
          evict_ollama: bool = True, ledger: Optional[RunLedger] = None,
          timeout_seconds: float = 3600.0):
    model_dir = model_dir or TRAINING_MODEL_DIR
    if adapter_dir is not None and not adapter_dir.is_absolute():
        adapter_dir = BASE_DIR / adapter_dir
    if not model_dir.is_absolute():
        model_dir = BASE_DIR / model_dir
    if not data_dir.is_absolute():
        data_dir = BASE_DIR / data_dir

    run_config = {
        "iterations": iterations,
        "num_layers": num_layers,
        "learning_rate": learning_rate,
        "max_seq_length": max_seq_length,
        "batch_size": 1,
        "grad_checkpoint": True,
        "mask_prompt": True,
    }

    # Step 1: Pre-allocate run in ledger BEFORE ANY file reading or gates!
    # If ledger cannot persist, must fail immediately without spawning MLX.
    active_ledger = _get_active_ledger(data_dir, ledger)
    run_record = active_ledger.create_run(
        config=run_config,
        base_model_dir=model_dir,
        dataset_dir=data_dir,
        dataset_sha256="pending_preflight",
        target_hypothesis=f"Sửa lỗi hành vi và duy trì chất lượng cho tập {data_dir.name}",
        baseline_metrics={},
        frozen_holdout_id=f"holdout-{data_dir.name}",
    )
    run_id = run_record["run_id"]

    terminal_status_set = False

    try:
        # Phase 1: Parameter checks
        if iterations < 1 or num_layers < 1 or not math.isfinite(learning_rate) or learning_rate <= 0 or max_seq_length < 1:
            raise ValueError("Số bước, số lớp và tốc độ học phải lớn hơn 0")

        # Phase 2: Source dataset checks & reading (inside try so unreadable files are recorded in ledger)
        train_file = data_dir / "train.jsonl"
        valid_file = data_dir / "valid.jsonl"
        if not train_file.is_file() or not valid_file.is_file():
            raise FileNotFoundError("Cần tạo train.jsonl và valid.jsonl bằng lệnh prepare-train trước")

        train_size = len(train_file.read_text(encoding="utf-8").splitlines())
        valid_size = len(valid_file.read_text(encoding="utf-8").splitlines())
        dataset_digest = hashlib.sha256(train_file.read_bytes() + valid_file.read_bytes()).hexdigest()

        approval_path = data_dir / "approval.json"
        if not approval_path.exists():
            raise ValueError("Bộ dữ liệu chưa có approval.json; cần duyệt dữ liệu trước khi huấn luyện")
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        if approval.get("dataset_sha256") != dataset_digest:
            raise ValueError("Dữ liệu đã thay đổi sau khi được duyệt; hãy kiểm tra và duyệt lại")
        verify_auxiliary_files(data_dir, approval)
        if approval.get("approved_manifest_sha256"):
            manifest = data_dir / "approved_manifest.jsonl"
            if not manifest.exists() or hashlib.sha256(manifest.read_bytes()).hexdigest() != approval["approved_manifest_sha256"]:
                raise ValueError("Manifest đã thay đổi sau khi duyệt; cần đối chiếu nguồn và duyệt lại")

        summary_path = data_dir / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if summary.get("audit_complete") is False:
                raise ValueError("Chưa kiểm tra xong dữ liệu; không được huấn luyện")
            cross_book_pairs = summary.get("cross_book_pairs", 0)
            training_cross_book_pairs = summary.get(
                "train_cross_book_pairs",
                cross_book_pairs - summary.get("skipped_cross_book_pairs_for_holdout", 0),
            )
            if cross_book_pairs and not training_cross_book_pairs:
                raise ValueError("Tất cả ví dụ liên sách đã bị loại khỏi tập học")
        if train_size < 8 or valid_size < 2:
            raise ValueError(f"Dữ liệu huấn luyện/kiểm tra quá ít: {train_size}/{valid_size}")
        if adapter_dir is None:
            raise ValueError("Cần --adapter-dir mới cho trial sửa lỗi; không dùng adapter đang phục vụ")
        if adapter_dir.exists() and any(adapter_dir.iterdir()):
            raise ValueError(f"Thư mục {adapter_dir} đã chứa adapter hoặc hồ sơ; chọn output mới")
        if not (model_dir / "config.json").exists():
            raise FileNotFoundError(f"Chưa tải mô hình MLX vào {model_dir}")

        # Bind verified dataset hashes & baseline into ledger record
        baseline_metrics = {"train_size": train_size, "valid_size": valid_size}
        target_hypothesis = f"Sửa lỗi hành vi và duy trì chất lượng cho tập {data_dir.name}"
        frozen_holdout_id = f"holdout-{data_dir.name}"
        plan_path = data_dir / "trial_plan.json"
        if plan_path.is_file():
            try:
                plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
                evidence = plan_data.get("evidence", {})
                b_info = evidence.get("baseline", {})
                b_path = Path(b_info.get("path", ""))
                if not b_path.is_file():
                    if (data_dir / b_path).is_file():
                        b_path = data_dir / b_path
                    elif (BASE_DIR / b_path).is_file():
                        b_path = BASE_DIR / b_path
                if b_path.is_file():
                    b_rep = json.loads(b_path.read_text(encoding="utf-8"))
                    baseline_metrics["baseline_report_sha256"] = b_info.get("sha256")
                    baseline_metrics["production_model"] = b_rep.get("production_model")
                    baseline_metrics["production_digest"] = b_rep.get("production_digest")
                    baseline_metrics["suite_sha256"] = b_rep.get("suite_sha256")
                    baseline_metrics["scores"] = b_rep.get("scores") or b_rep.get("per_error")
                    baseline_metrics["content_review_complete"] = b_rep.get("content_review_complete")
                    if b_rep.get("suite_sha256"):
                        frozen_holdout_id = b_rep["suite_sha256"]
                proto_info = evidence.get("evaluation_protocol", {})
                if proto_info:
                    baseline_metrics["evaluation_protocol_sha256"] = proto_info.get("sha256")
                    proto_path = Path(proto_info.get("path", ""))
                    if not proto_path.is_file():
                        if (data_dir / proto_path).is_file():
                            proto_path = data_dir / proto_path
                        elif (BASE_DIR / proto_path).is_file():
                            proto_path = BASE_DIR / proto_path
                    if proto_path.is_file():
                        proto_rep = json.loads(proto_path.read_text(encoding="utf-8"))
                        suite_info = proto_rep.get("suite", {})
                        if isinstance(suite_info, dict) and suite_info.get("sha256"):
                            frozen_holdout_id = suite_info["sha256"]
                        elif proto_rep.get("frozen_holdout_id"):
                            frozen_holdout_id = proto_rep["frozen_holdout_id"]
            except Exception as e:
                logger.warning(f"Không thể nạp baseline metrics đầy đủ từ trial_plan: {e}")

        # Phase 2b: Bind verified dataset manifest & baseline into ledger record
        target_hypothesis = f"Sửa lỗi hành vi và duy trì chất lượng cho tập {data_dir.name}"
        if plan_path.is_file():
            try:
                target_hypothesis = plan_data.get("hypothesis") or target_hypothesis
            except Exception:
                pass

        if hasattr(active_ledger, "bind_verified_manifest"):
            active_ledger.bind_verified_manifest(
                run_id=run_id,
                config=run_config,
                base_model_dir=model_dir,
                dataset_dir=data_dir,
                dataset_sha256=dataset_digest,
                target_hypothesis=target_hypothesis,
                baseline_metrics=baseline_metrics,
                frozen_holdout_id=frozen_holdout_id,
            )

        # Phase 3: Preflight checks
        active_ledger.update_status(run_id, STATUS_PREFLIGHT)

        # Historical dataset approvals cannot authorize execution for the
        # current multimodal 9B base.
        # No environment switch or old approval enables this path.
        raise RuntimeError(
            "Huấn luyện đang bị chặn: giao thức 3B/7B đã ngừng sử dụng; "
            "Qwen3.5 9B cần giao thức bộ nhớ, dữ liệu và nghiệm thu adapter mới. "
            "Dùng train-plan để lập đề xuất; chưa có adapter được phê duyệt."
        )


    except BaseException as err:
        if not terminal_status_set:
            status_to_record = STATUS_KILLED_ABNORMAL if isinstance(err, (KeyboardInterrupt, SystemExit)) else STATUS_REJECTED
            err_msg = str(err)
            if any(term in err_msg.lower() for term in ("gpu", "timeout", "lock", "popen")):
                status_to_record = STATUS_FAILED
            elif any(term in err_msg.lower() for term in ("thất bại", "đổi trong lúc", "không tìm thấy adapter")):
                status_to_record = STATUS_FAILED

            active_ledger.update_status(
                run_id, status_to_record,
                diagnosis=err_msg or type(err).__name__,
                decision=f"TERMINATED_{status_to_record}",
            )
            terminal_status_set = True
        raise


def main():
    parser = argparse.ArgumentParser(description="Huấn luyện LoRA cục bộ trên Apple Silicon")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--iters", type=int, required=True)
    parser.add_argument("--num-layers", type=int, required=True)
    parser.add_argument("--adapter-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=TRAINING_MODEL_DIR)
    parser.add_argument("--learning-rate", type=float, required=True)
    parser.add_argument("--max-seq-length", type=int, required=True)
    args = parser.parse_args()
    train(
        args.data,
        args.iters,
        args.num_layers,
        args.adapter_dir,
        model_dir=args.model_dir,
        learning_rate=args.learning_rate,
        max_seq_length=args.max_seq_length
    )


if __name__ == "__main__":
    main()
