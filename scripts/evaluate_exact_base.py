"""Generate exact-base baseline outputs on frozen holdout cases.

REPAIR NOTICE (YC-188 packet03):
- Reuses proven GPU coordinator and memory preflight primitives.
- Binds actual base model file hashes and protocol bytes.
- Uses mlx_lm.stream_generate with make_sampler(temp=0.0) for genuine finish_reason.
- Uses actual main runtime messages for evaluation.
- Execution blocked until Codex independently approves source/content/splits/eval labels.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gpu_lock import gpu_coordinator
from app.memory_preflight import get_hardware_memory_profile, inspect_base_model

ROOT_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT_DIR / "data/models/qwen2.5-3b-4bit"
HOLDOUT_INPUTS_PATH = ROOT_DIR / "data/evaluation/training-yc188-20261010/data04/feature-holdout-inputs.json"
OUTPUT_EVIDENCE_PATH = ROOT_DIR / "data/evaluation/training-yc188-20261010/execution05/exact-base-evidence.json"

BASE_IDENTITY_PATH = ROOT_DIR / "data/evaluation/training-yc188-20261010/base-identity-readback.json"
APPROVAL_PATH = ROOT_DIR / "data/evaluation/training-yc188-20261010/execution05/evaluation-approval.json"

MODEL_PINNED_REVISION = "4f83f8f146fdf28b512a06562b671d7af4fab457"


def compute_model_file_hashes(model_dir: Path | str) -> Dict[str, str]:
    """Compute sha256 of all files in model directory."""
    model_p = Path(model_dir)
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in model_p.iterdir()
        if p.is_file() and not p.name.startswith(".")
    }


def validate_base_identity(model_dir: Path | str, identity_path: Path | str) -> Dict[str, Any]:
    """Validate model_dir files strictly match identity_path files_sha256."""
    identity_p = Path(identity_path)
    if not identity_p.exists():
        raise ValueError(f"Base identity file not found: {identity_p}")
    identity = json.loads(identity_p.read_text(encoding="utf-8"))
    pinned_files: Dict[str, str] = identity.get("files_sha256", {})
    if not pinned_files:
        raise ValueError("Pinned files cannot be empty")

    model_p = Path(model_dir)
    actual_files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in model_p.iterdir()
        if p.is_file() and not p.name.startswith(".")
    }

    extra = set(actual_files) - set(pinned_files)
    if extra:
        raise ValueError(f"Unpinned extra files in model directory: {extra}")

    missing = set(pinned_files) - set(actual_files)
    if missing:
        raise ValueError(f"Missing pinned model files: {missing}")

    for name, pinned_hash in pinned_files.items():
        if actual_files[name] != pinned_hash:
            raise ValueError(f"Model file hash mismatch for {name}: {actual_files[name]} != {pinned_hash}")

    return identity


def validate_evaluation_approval(suite_path: Path | str, approval_path: Path | str) -> Dict[str, Any]:
    """Validate frozen evaluation suite against independent review approval record."""
    suite_p = Path(suite_path)
    approval_p = Path(approval_path)
    if not suite_p.exists():
        raise ValueError(f"Evaluation suite file not found: {suite_p}")
    if not approval_p.exists():
        raise ValueError(f"Evaluation approval file not found: {approval_p}")

    approval = json.loads(approval_p.read_text(encoding="utf-8"))
    if approval.get("decision") != "PASS":
        raise ValueError(f"Evaluation suite decision is not PASS: {approval.get('decision')}")
    if approval.get("independent_review_complete") is not True:
        raise ValueError("Independent review is not complete (independent_review_complete != True)")

    actual_suite_sha = hashlib.sha256(suite_p.read_bytes()).hexdigest()
    if approval.get("suite_sha256") != actual_suite_sha:
        raise ValueError(f"Evaluation suite sha256 mismatch: {actual_suite_sha} != {approval.get('suite_sha256')}")

    return approval


def run_exact_base_evaluation(
    model_dir: Path | str | None = None,
    identity_path: Path | str | None = None,
    suite_path: Path | str | None = None,
    approval_path: Path | str | None = None,
    output_path: Path | str | None = None,
):
    model_dir_p = Path(model_dir) if model_dir is not None else Path(MODEL_DIR)
    identity_path_p = Path(identity_path) if identity_path is not None else Path(BASE_IDENTITY_PATH)
    suite_path_p = Path(suite_path) if suite_path is not None else Path(HOLDOUT_INPUTS_PATH)
    approval_path_p = Path(approval_path) if approval_path is not None else Path(APPROVAL_PATH)
    output_path_p = Path(output_path) if output_path is not None else Path(OUTPUT_EVIDENCE_PATH)

    # STRICT FAIL-CLOSED SAFETY GUARD: Block execution if not authorized
    if os.environ.get("CODEX_EXACT_BASE_EXECUTION_AUTHORIZED") != "1":
        raise RuntimeError(
            "EXECUTION BLOCKED: scripts/evaluate_exact_base.py cannot be executed in training-yc188-execution05 without authorization."
        )

    # Identity and independent review gates must pass BEFORE hardware/resource use
    base_identity = validate_base_identity(model_dir_p, identity_path_p)
    validate_evaluation_approval(suite_path_p, approval_path_p)

    print("=== STARTING EXACT BASE EVALUATION ON FROZEN HOLDOUTS ===")

    # 1. Hardware & Memory Preflight
    hw_profile = get_hardware_memory_profile()
    if not hw_profile.get("eligible", False):
        err = hw_profile.get("measurement_error") or "Hardware profile is not eligible"
        raise RuntimeError(f"Memory preflight failure: {err}")
    if hw_profile.get("measurement_error"):
        raise RuntimeError(f"Memory preflight measurement error: {hw_profile['measurement_error']}")

    avail_gb = hw_profile.get("available_gb")
    if avail_gb is None or avail_gb < 3.5:
        raise RuntimeError(f"Memory preflight failure: available RAM ({avail_gb}) below 3.5 GB threshold")
    print(f"Hardware profile: RAM available {avail_gb:.2f} GB")

    # 2. Model Architecture & Weights Integrity
    model_inspect = inspect_base_model(model_dir_p)
    print(f"Base model verified: {model_inspect.get('model_type')} ({model_inspect.get('total_weight_mb', 0):.1f} MB)")
    actual_file_hashes = base_identity.get("files_sha256") or compute_model_file_hashes(model_dir_p)

    if not suite_path_p.exists():
        raise FileNotFoundError(f"Holdout inputs not found: {suite_path_p}")

    holdout_data = json.loads(suite_path_p.read_text(encoding="utf-8"))
    has_holdout_cases = "holdout_cases" in holdout_data
    has_cases = "cases" in holdout_data

    if has_holdout_cases and has_cases:
        if holdout_data["holdout_cases"] != holdout_data["cases"]:
            raise ValueError("Ambiguous holdout suite: both 'holdout_cases' and 'cases' present with differing contents")
        holdout_cases = holdout_data["holdout_cases"]
    elif has_holdout_cases:
        holdout_cases = holdout_data["holdout_cases"]
    elif has_cases:
        holdout_cases = holdout_data["cases"]
    else:
        raise ValueError("Neither 'holdout_cases' nor 'cases' present in holdout inputs")

    if not holdout_cases:
        raise ValueError("Holdout cases list cannot be empty")

    suite_decoding = holdout_data.get("decoding") or holdout_data.get("decoding_params") or {}

    # 3. Import MLX and lock GPU
    import mlx.core as mx
    import mlx_lm
    from mlx_lm.sample_utils import make_sampler

    print("Acquiring GPU coordinator lock for inference...")
    with gpu_coordinator.acquire_for_inference(timeout=30.0):
        # Load Model
        start_load = time.time()
        print(f"Loading base MLX model from {model_dir_p}...")
        model, tokenizer = mlx_lm.load(str(model_dir_p))
        load_duration = time.time() - start_load
        print(f"Model loaded in {load_duration:.2f}s.")

        results = []
        t0 = time.time()

        for idx, case in enumerate(holdout_cases, 1):
            raw_messages = case.get("messages", [])
            if not raw_messages:
                raise ValueError(f"Empty messages in case {case.get('id')}")

            # Preserve all input messages byte-identically; only strip actual trailing assistant reference
            messages = [dict(m) for m in raw_messages]
            if messages and messages[-1].get("role") == "assistant":
                messages = messages[:-1]

            decoding = case.get("decoding") or case.get("decoding_params") or suite_decoding
            if not decoding or "max_tokens" not in decoding:
                raise ValueError(
                    f"Missing frozen decoding parameters (max_tokens) for case {case.get('id')}; "
                    "cannot silently change evaluation protocol budget"
                )
            max_tokens = int(decoding["max_tokens"])
            temperature = float(decoding.get("temperature", 0.0))
            seed = decoding.get("seed", 0)

            if hasattr(mx, "random") and hasattr(mx.random, "seed") and seed is not None:
                mx.random.seed(seed)
            sampler = make_sampler(temp=temperature)

            prompt_str = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            prompt_bytes = prompt_str.encode("utf-8")
            prompt_hash = hashlib.sha256(prompt_bytes).hexdigest()

            case_start = time.time()
            output_tokens = []
            finish_reason = None
            prompt_tokens = None
            generation_tokens = None
            peak_mem_val = None

            for resp in mlx_lm.stream_generate(
                model=model,
                tokenizer=tokenizer,
                prompt=prompt_str,
                max_tokens=max_tokens,
                sampler=sampler,
            ):
                output_tokens.append(resp.text)
                if getattr(resp, "finish_reason", None) is not None:
                    finish_reason = resp.finish_reason
                if getattr(resp, "prompt_tokens", None) is not None:
                    prompt_tokens = resp.prompt_tokens
                if getattr(resp, "generation_tokens", None) is not None:
                    generation_tokens = resp.generation_tokens
                if getattr(resp, "peak_memory", None) is not None:
                    peak_mem_val = resp.peak_memory

            if finish_reason is None:
                raise RuntimeError(f"Missing finish_reason for case {case.get('id')}; generation failed incomplete")
            if finish_reason not in ("stop", "length"):
                raise RuntimeError(f"Incomplete generation for case {case.get('id')}: unexpected finish_reason={finish_reason}")

            if prompt_tokens is None:
                raise RuntimeError(f"Missing prompt_tokens metric for case {case.get('id')}; generation incomplete")
            if generation_tokens is None:
                raise RuntimeError(f"Missing generation_tokens metric for case {case.get('id')}; generation incomplete")
            if peak_mem_val is None:
                raise RuntimeError(f"Missing peak_memory metric for case {case.get('id')}; generation incomplete")

            case_duration = time.time() - case_start
            output_text = "".join(output_tokens).strip()

            peak_memory_gb = float(peak_mem_val)
            peak_memory_mb = round(peak_memory_gb * 1024.0, 2)

            record = {
                "index": idx,
                "id": case["id"],
                "domain": case["domain"],
                "task": case["task"],
                "source_file": case["source_file"],
                "source_file_sha256": case["source_file_sha256"],
                "chunk_id": case.get("chunk_id"),
                "input_hash": prompt_hash,
                "input_bytes": len(prompt_bytes),
                "generated_output": output_text,
                "target_reference": case.get("reference", ""),
                "decoding_params": {
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "seed": seed,
                },
                "duration_seconds": round(case_duration, 3),
                "prompt_tokens": prompt_tokens,
                "generation_tokens": generation_tokens,
                "peak_memory_gb": peak_memory_gb,
                "peak_memory_mb": peak_memory_mb,
                "finish_reason": finish_reason,
                "complete": finish_reason == "stop",
                "incomplete": finish_reason != "stop",
            }
            results.append(record)
            print(f"[{idx}/{len(holdout_cases)}] {case['id']} completed in {case_duration:.2f}s (finish: {finish_reason})")

        total_duration = time.time() - t0

        evidence_report = {
            "report_id": "exact-base-evidence-yc188-execution05",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": {
                "path": str(model_dir_p),
                "architecture": "Qwen2.5-3B-Instruct-4bit",
                "pinned_revision": MODEL_PINNED_REVISION,
                "runtime": "mlx-lm",
                "mlx_lm_version": getattr(mlx_lm, "__version__", "0.31.3"),
                "file_hashes": actual_file_hashes,
            },
            "hardware_preflight": hw_profile,
            "evaluation_summary": {
                "total_cases": len(results),
                "total_duration_seconds": round(total_duration, 2),
                "avg_latency_per_case_sec": round(total_duration / max(1, len(results)), 3),
                "device": "Apple Silicon GPU (Metal)",
                "status": "UNREVIEWED_BASE_BASELINE",
            },
            "results": results,
        }

        output_path_p.parent.mkdir(parents=True, exist_ok=True)
        output_path_p.write_text(json.dumps(evidence_report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Evidence report written to {output_path_p}")
        print("=== EXACT BASE EVALUATION FINISHED SUCCESSFULLY ===")


if __name__ == "__main__":
    run_exact_base_evaluation()
