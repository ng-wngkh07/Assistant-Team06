"""Fail-closed timetable study-planning LoRA adapter validation and gating."""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any, Optional

from app.active_registry import get_file_sha256_cached
from app.adapter_integrity import inspect_mlx_adapter_layout
from app.config import DATA_DIR
from app.model_resolver import resolve_base_weight_files, validate_native_model_config
from app.planner_prompts import build_planner_response_schema, planner_system_prompt_matches

PLANNER_ADAPTER_SCOPE = "timetable_study_planning"
PLANNER_ADAPTER_PROFILE = "planner_timetable_v1"
PLANNER_SCHEMA_VERSION = "study-planner-9b-v1"

_DEFAULT_REGISTRY_FILE = DATA_DIR / "adapters" / "planner-9b.json"
REGISTRY_FILE = _DEFAULT_REGISTRY_FILE


def _registry_path() -> Path:
    if REGISTRY_FILE != _DEFAULT_REGISTRY_FILE:
        return REGISTRY_FILE
    return DATA_DIR / "adapters" / "planner-9b.json"


def _read_bound_file(entry: Any, label: str) -> tuple[Path, str]:
    if not isinstance(entry, dict):
        raise ValueError(f"Planner adapter manifest missing bound entry '{label}'")
    raw_path = entry.get("path")
    expected_sha = entry.get("sha256")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"Planner adapter manifest missing path for '{label}'")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise ValueError(f"Planner adapter manifest invalid sha256 for '{label}'")
    path = Path(raw_path)
    if not path.is_file():
        raise ValueError(f"Planner adapter evidence file missing for '{label}': {path}")
    actual_sha = get_file_sha256_cached(path)
    if actual_sha != expected_sha:
        raise ValueError(f"Planner adapter digest mismatch for '{label}': {path.name}")
    return path, expected_sha


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Planner adapter invalid JSON in '{label}'") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Planner adapter '{label}' must be a JSON object")
    return data


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [json.loads(line) for line in lines]
    except (OSError, ValueError) as exc:
        raise ValueError(f"Planner adapter invalid JSONL in '{label}'") from exc
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Planner adapter '{label}' must contain JSON object rows")
    return rows


def _verify_base_model_files(model_dir: Path, base_hashes: Any) -> None:
    if not isinstance(base_hashes, dict) or not base_hashes:
        raise ValueError("Planner adapter training receipt missing base_files_sha256")
    _, weight_files = resolve_base_weight_files(model_dir)
    required_files = [
        model_dir / "config.json",
        model_dir / "tokenizer.json",
        model_dir / "tokenizer_config.json",
        *weight_files,
    ]
    seen_rel: set[str] = set()
    for fpath in required_files:
        if not fpath.is_file():
            raise ValueError(f"Base model file missing: {fpath.name}")
        rel = fpath.relative_to(model_dir).as_posix()
        seen_rel.add(rel)
        expected = base_hashes.get(rel) if rel in base_hashes else base_hashes.get(fpath.name)
        if not expected or get_file_sha256_cached(fpath) != expected:
            raise ValueError(f"Base model digest mismatch for {rel}")
    missing = set(base_hashes.keys()) - seen_rel - {Path(k).name for k in seen_rel}
    if missing:
        raise ValueError(f"Unexpected/missing base files in receipt: {sorted(missing)}")


def request_matches_planner_adapter_scope(request: dict[str, Any]) -> bool:
    """Return True only when request matches the pinned timetable planner schema/prompt contract."""
    if not isinstance(request, dict):
        return False
    messages = request.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        return False
    sys_msg, user_msg = messages[0], messages[1]
    if not isinstance(sys_msg, dict) or not isinstance(user_msg, dict):
        return False
    if sys_msg.get("role") != "system" or user_msg.get("role") != "user":
        return False
    if not planner_system_prompt_matches(sys_msg.get("content", "")):
        return False
    u_content = user_msg.get("content", "")
    if "<schedule_context>" not in u_content or "<user_notes>" not in u_content:
        return False
    if request.get("enable_thinking") is True:
        return False
    temp = request.get("temperature")
    if temp is not None:
        try:
            if not (0.0 <= float(temp) <= 0.1):
                return False
        except (TypeError, ValueError):
            return False
    expected_schema = build_planner_response_schema()
    return json.dumps(request.get("response_schema"), sort_keys=True) == json.dumps(
        expected_schema, sort_keys=True
    )


def canonical_planner_result_sha256(row: dict[str, Any]) -> str:
    """Compute canonical SHA-256 digest binding raw planner eval output to review."""
    raw_payload = json.dumps(
        {"answer": row.get("answer"), "done_reason": row.get("done_reason"), "valid_json": row.get("valid_json")},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw_payload).hexdigest()


def _score_planner_evaluation(
    suite_rows: list[dict[str, Any]],
    result_rows: list[dict[str, Any]],
    review_doc: dict[str, Any],
    *,
    weights_sha256: str,
) -> dict[str, Any]:
    if review_doc.get("status") != "COMPLETE":
        raise ValueError("Planner content review status must be COMPLETE")
    review_cases = review_doc.get("cases")
    if not isinstance(review_cases, list):
        raise ValueError("Planner content review missing cases")
    suite_ids = [str(row["id"]) for row in suite_rows]
    if len(set(suite_ids)) != len(suite_ids) or len(suite_ids) < 8:
        raise ValueError("Planner eval suite requires >= 8 unique cases")

    review_index = {
        (str(entry["id"]), str(entry["arm"]), int(entry.get("round", 0))): entry
        for entry in review_cases
        if isinstance(entry, dict)
    }
    by_arm: dict[str, dict[str, dict[str, Any]]] = {"base": {}, "adapter": {}}
    for row in result_rows:
        arm = row.get("arm")
        if arm not in by_arm:
            continue
        cid = str(row["id"])
        rnd = int(row.get("round", 0))
        review = review_index.get((cid, arm, rnd))
        if not review or review.get("result_sha256") != canonical_planner_result_sha256(row):
            raise ValueError(f"Missing or mismatched planner content review for {(cid, arm, rnd)}")
        if float(row.get("seconds") or 0) <= 0:
            raise ValueError(f"Invalid positive seconds missing for {(cid, arm)}")
        if arm == "adapter":
            diag = row.get("diagnostics") or {}
            if row.get("done_reason") != "stop" or diag.get("is_truncated"):
                raise ValueError(f"Planner adapter truncated on {cid}")
            if diag.get("adapter_loaded") is not True or diag.get("adapter_sha256") != weights_sha256:
                raise ValueError(f"Planner adapter binding failed on {cid}")
            if diag.get("adapter_scope") != PLANNER_ADAPTER_SCOPE:
                raise ValueError(f"Planner adapter scope mismatch on {cid}")
            if review.get("unsafe") or review.get("fabricated_course") or review.get("repetition_loop"):
                raise ValueError(f"Planner adapter failed safety/hallucination check on {cid}")
            if review.get("status") != "PASS":
                raise ValueError(f"Planner adapter content review did not PASS on {cid}")
        by_arm[arm][cid] = {"row": row, "review": review}

    for arm in ("base", "adapter"):
        if set(by_arm[arm].keys()) != set(suite_ids):
            raise ValueError(f"Incomplete planner evaluation coverage for '{arm}'")

    base_pass = sum(1 for cid in suite_ids if by_arm["base"][cid]["review"].get("status") == "PASS")
    adapter_pass = sum(1 for cid in suite_ids if by_arm["adapter"][cid]["review"].get("status") == "PASS")
    if adapter_pass < base_pass:
        raise ValueError(f"Planner adapter regressed accuracy ({adapter_pass} < {base_pass})")

    ratios = [
        float(by_arm["adapter"][cid]["row"]["seconds"]) / float(by_arm["base"][cid]["row"]["seconds"])
        for cid in suite_ids
    ]
    median_ratio = statistics.median(ratios)
    base_total = sum(float(by_arm["base"][cid]["row"]["seconds"]) for cid in suite_ids)
    adapter_total = sum(float(by_arm["adapter"][cid]["row"]["seconds"]) for cid in suite_ids)
    total_ratio = adapter_total / max(1e-6, base_total)
    improved_accuracy = adapter_pass > base_pass
    speed_ok = (median_ratio <= 0.90) or (total_ratio <= 0.90 and median_ratio <= 0.95)
    if not improved_accuracy and not speed_ok:
        raise ValueError(
            f"Planner adapter shows neither accuracy gain ({adapter_pass}=={base_pass}) nor speedup (med={median_ratio:.4f})"
        )
    if improved_accuracy and median_ratio > 1.05:
        raise ValueError(f"Planner adapter regressed latency despite accuracy gain (med={median_ratio:.4f} > 1.05)")

    return {
        "total_cases": len(suite_ids),
        "base_pass": base_pass,
        "adapter_pass": adapter_pass,
        "paired_median_ratio": round(median_ratio, 4),
        "paired_total_ratio": round(total_ratio, 4),
    }


def load_planner_adapter(model_dir: Path, *, require_active: bool = True) -> Optional[dict[str, Any]]:
    """Load and cryptographically/quantitatively verify the accepted planner LoRA adapter manifest."""
    reg_path = _registry_path()
    if not reg_path.is_file():
        return None
    manifest = _read_json(reg_path, "planner_registry")
    if manifest.get("schema") != PLANNER_SCHEMA_VERSION:
        raise ValueError("Planner adapter manifest schema mismatch")
    if manifest.get("review_profile") != PLANNER_ADAPTER_PROFILE or manifest.get("scope") != PLANNER_ADAPTER_SCOPE:
        raise ValueError("Planner adapter scope/profile mismatch")
    stage = manifest.get("stage")
    if stage not in {"planner_qualified", "planner_accepted"}:
        raise ValueError(f"Unsupported planner adapter stage: {stage!r}")
    if require_active and (stage != "planner_accepted" or manifest.get("activation") is not True):
        return None

    validate_native_model_config(model_dir)
    weights_path, weights_sha = _read_bound_file(manifest.get("weights"), "weights")
    config_path, config_sha = _read_bound_file(manifest.get("adapter_config"), "adapter_config")
    adapter_dir = weights_path.parent
    if config_path.parent.resolve() != adapter_dir.resolve():
        raise ValueError("Planner adapter weights and adapter_config must live in same directory")
    layout = inspect_mlx_adapter_layout(model_dir, weights_path)
    if not layout.get("layout_matches") or not layout.get("finite"):
        raise ValueError("Planner adapter weights failed layout or finiteness check")

    receipt_path, _ = _read_bound_file(manifest.get("training_receipt"), "training_receipt")
    receipt = _read_json(receipt_path, "training_receipt")
    if receipt.get("adapter_sha256") != weights_sha:
        raise ValueError("Planner adapter training receipt digest mismatch")
    _verify_base_model_files(model_dir, receipt.get("base_files_sha256"))

    protocol_path, _ = _read_bound_file(manifest.get("protocol"), "protocol")
    protocol = _read_json(protocol_path, "protocol")
    source_rev_path, source_rev_sha = _read_bound_file(manifest.get("source_review"), "source_review")
    if _read_json(source_rev_path, "source_review").get("status") != "PASS":
        raise ValueError("Planner adapter source review must PASS")
    suite_path, suite_sha = _read_bound_file(manifest.get("suite"), "suite")
    results_path, _ = _read_bound_file(manifest.get("results"), "results")
    review_path, _ = _read_bound_file(manifest.get("content_review"), "content_review")

    if protocol.get("frozen_before_any_fresh_output") is not True:
        raise ValueError("Planner eval protocol was not frozen prior to outputs")
    if protocol.get("fresh_eval_sha256") != suite_sha or protocol.get("source_review_sha256") != source_rev_sha:
        raise ValueError("Planner protocol hash binding mismatch")
    if protocol.get("selected_adapter_sha256") != weights_sha or protocol.get("adapter_config_sha256") != config_sha:
        raise ValueError("Planner protocol adapter binding mismatch")

    metrics = _score_planner_evaluation(
        _read_jsonl(suite_path, "suite"),
        _read_jsonl(results_path, "results"),
        _read_json(review_path, "content_review"),
        weights_sha256=weights_sha,
    )
    return {
        "path": str(adapter_dir),
        "weights_path": str(weights_path),
        "sha256": weights_sha,
        "scope": PLANNER_ADAPTER_SCOPE,
        "review_profile": PLANNER_ADAPTER_PROFILE,
        "stage": stage,
        "metrics": metrics,
    }


def active_planner_adapter(model_dir: Path, *, request: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Return active verified planner adapter descriptor when enabled and request matches scope."""
    if request is not None and not request_matches_planner_adapter_scope(request):
        return None
    return load_planner_adapter(model_dir, require_active=True)
