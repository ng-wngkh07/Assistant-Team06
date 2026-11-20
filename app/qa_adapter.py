"""Fail-closed Q&A LoRA adapter registry, request scope validation, and acceptance gates."""

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
from app.qa_prompts import qa_system_prompt_matches

QA_ADAPTER_SCOPE = "grounded_qa_generation"
QA_ADAPTER_PROFILE = "qa_grounded_v1"
QA_SCHEMA_VERSION = "study-qa-9b-v1"

_DEFAULT_REGISTRY_FILE = DATA_DIR / "adapters" / "qa-9b.json"
REGISTRY_FILE = _DEFAULT_REGISTRY_FILE


def _registry_path() -> Path:
    if REGISTRY_FILE != _DEFAULT_REGISTRY_FILE:
        return REGISTRY_FILE
    return DATA_DIR / "adapters" / "qa-9b.json"


def _read_bound_file(entry: Any, label: str) -> tuple[Path, str]:
    if not isinstance(entry, dict):
        raise ValueError(f"Q&A adapter manifest missing bound entry '{label}'")
    raw_path = entry.get("path")
    expected_sha = entry.get("sha256")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"Q&A adapter manifest missing path for '{label}'")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise ValueError(f"Q&A adapter manifest invalid sha256 for '{label}'")
    path = Path(raw_path)
    if not path.is_file():
        raise ValueError(f"Q&A adapter evidence file missing for '{label}': {path}")
    actual_sha = get_file_sha256_cached(path)
    if actual_sha != expected_sha:
        raise ValueError(f"Q&A adapter digest mismatch for '{label}': {path.name}")
    return path, expected_sha


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Q&A adapter invalid JSON in '{label}'") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Q&A adapter '{label}' must be a JSON object")
    return data


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [json.loads(line) for line in lines]
    except (OSError, ValueError) as exc:
        raise ValueError(f"Q&A adapter invalid JSONL in '{label}'") from exc
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Q&A adapter '{label}' must contain JSON object rows")
    return rows


def _verify_base_model_files(model_dir: Path, base_hashes: Any) -> None:
    if not isinstance(base_hashes, dict) or not base_hashes:
        raise ValueError("Q&A adapter training receipt missing base_files_sha256")
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
        expected = base_hashes.get(rel)
        if expected is None:
            expected = base_hashes.get(fpath.name)
        if not expected or get_file_sha256_cached(fpath) != expected:
            raise ValueError(f"Base model digest mismatch for {rel}")
    missing = set(base_hashes.keys()) - seen_rel - {Path(k).name for k in seen_rel}
    if missing:
        raise ValueError(f"Unexpected/missing base files in receipt: {sorted(missing)}")


def _is_qa_schema(schema: Any) -> bool:
    if not isinstance(schema, dict) or schema.get("additionalProperties") is not False:
        return False
    claims_prop = (schema.get("properties") or {}).get("claims")
    if not isinstance(claims_prop, dict) or claims_prop.get("type") != "array":
        return False
    items = claims_prop.get("items")
    if not isinstance(items, dict) or items.get("additionalProperties") is not False:
        return False
    props = items.get("properties")
    req = items.get("required")
    if not isinstance(props, dict) or not isinstance(req, list):
        return False
    return {"text", "source_ids", "evidence_lines"}.issubset(props.keys()) and set(req) == {
        "text",
        "source_ids",
        "evidence_lines",
    }


def request_matches_qa_adapter_scope(request: dict[str, Any]) -> bool:
    """Return True only when request matches the verified native Q&A grounded generation contract."""
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

    sys_content = sys_msg.get("content")
    user_content = user_msg.get("content")
    if not isinstance(sys_content, str) or not isinstance(user_content, str):
        return False

    if not qa_system_prompt_matches(sys_content):
        return False

    if "<user_query>" not in user_content or "</user_query>" not in user_content:
        return False
    if "\nCác nguồn của lượt hiện tại:\n" not in user_content and "<context>\n" not in user_content:
        return False

    if request.get("enable_thinking") is True:
        return False
    temp = request.get("temperature")
    if temp is not None:
        try:
            temp_val = float(temp)
        except (TypeError, ValueError):
            return False
        if temp_val < 0.0 or temp_val > 0.3:
            return False

    num_ctx = request.get("num_ctx")
    if num_ctx is not None:
        try:
            if int(num_ctx) <= 0 or int(num_ctx) > 8192:
                return False
        except (TypeError, ValueError):
            return False

    return _is_qa_schema(request.get("response_schema"))


def canonical_result_sha256(row: dict[str, Any]) -> str:
    """Compute canonical SHA-256 digest binding raw evaluation outputs to content review."""
    raw_payload = json.dumps(
        {"answer": row.get("answer"), "actual_kind": row.get("actual_kind"), "calls": row.get("calls")},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw_payload).hexdigest()


def _score_paired_evaluation(
    suite_rows: list[dict[str, Any]],
    result_rows: list[dict[str, Any]],
    review_doc: dict[str, Any],
    *,
    weights_sha256: str,
) -> dict[str, Any]:
    if review_doc.get("status") != "COMPLETE":
        raise ValueError("Q&A adapter content review status must be COMPLETE")
    review_cases = review_doc.get("cases")
    if not isinstance(review_cases, list):
        raise ValueError("Q&A adapter content review missing cases")

    suite_ids = [str(row["id"]) for row in suite_rows]
    if len(set(suite_ids)) != len(suite_ids):
        raise ValueError("Q&A eval suite contains duplicate case IDs")
    safety_ids = {
        str(row["id"])
        for row in suite_rows
        if row.get("expected_kind") == "insufficient_evidence"
        or "bleed" in str(row.get("category", ""))
        or "trap" in str(row.get("category", ""))
        or "insufficient" in str(row.get("category", ""))
    }

    review_index: dict[tuple[str, str, int], dict[str, Any]] = {}
    for entry in review_cases:
        if not isinstance(entry, dict):
            raise ValueError("Malformed entry in Q&A content review")
        key = (str(entry["id"]), str(entry["arm"]), int(entry.get("round", 0)))
        if key in review_index:
            raise ValueError(f"Duplicate review entry: {key}")
        review_index[key] = entry

    by_arm: dict[str, dict[str, dict[str, Any]]] = {"base": {}, "adapter": {}}
    for row in result_rows:
        arm = row.get("arm")
        if arm not in by_arm:
            continue
        cid = str(row["id"])
        rnd = int(row.get("round", 0))
        if cid in by_arm[arm]:
            raise ValueError(f"Duplicate result row for {(cid, arm)}")
        review = review_index.get((cid, arm, rnd))
        if not review:
            raise ValueError(f"Missing manual content review for {(cid, arm, rnd)}")
        if review.get("result_sha256") != canonical_result_sha256(row):
            raise ValueError(f"Content review hash mismatch for {(cid, arm, rnd)}")
        if (row.get("seconds") is None or float(row["seconds"]) <= 0) and int(row.get("generation_calls") or 0) > 0:
            raise ValueError(f"Invalid positive seconds missing for {(cid, arm)}")

        if arm == "adapter":
            calls = row.get("calls") or []
            if not calls:
                raise ValueError(f"Adapter arm missing call diagnostics on {cid}")
            qa_calls = [c for c in calls if not c.get("is_auditor")]
            if not qa_calls:
                raise ValueError(f"Adapter arm missing Q&A call diagnostics on {cid}")
            for call in calls:
                diag = call.get("diagnostics") or {}
                if call.get("done_reason") != "stop" or diag.get("is_truncated"):
                    raise ValueError(f"Adapter truncated or non-stop on {cid}")
                if call.get("is_auditor"):
                    if diag.get("adapter_loaded"):
                        raise ValueError(f"Careful auditor unexpectedly loaded Q&A adapter on {cid}")
                    continue
                if diag.get("adapter_loaded") is not True:
                    raise ValueError(f"Adapter arm did not load adapter on {cid}")
                if diag.get("adapter_sha256") != weights_sha256:
                    raise ValueError(f"Adapter SHA mismatch during eval on {cid}")
                if diag.get("adapter_scope") != QA_ADAPTER_SCOPE:
                    raise ValueError(f"Adapter scope mismatch during eval on {cid}")
            if review.get("unsafe") or review.get("repetition_loop"):
                raise ValueError(f"Adapter failed safety/repetition review on {cid}")
            if review.get("status") != "PASS":
                raise ValueError(f"Adapter manual content review did not PASS on {cid}")
        else:
            for call in (row.get("calls") or []):
                diag = call.get("diagnostics") or {}
                if diag.get("adapter_loaded"):
                    raise ValueError(f"Base control arm unexpectedly loaded adapter on {cid}")

        by_arm[arm][cid] = {"row": row, "review": review}

    if len(suite_ids) < 8:
        raise ValueError("Q&A acceptance requires at least 8 frozen evaluation cases")
    for arm in ("base", "adapter"):
        if set(by_arm[arm].keys()) != set(suite_ids):
            raise ValueError(f"Incomplete evaluation results for arm '{arm}'")

    base_pass = sum(1 for cid in suite_ids if by_arm["base"][cid]["review"].get("status") == "PASS")
    adapter_pass = sum(1 for cid in suite_ids if by_arm["adapter"][cid]["review"].get("status") == "PASS")
    base_call1 = sum(
        1
        for cid in suite_ids
        if int(by_arm["base"][cid]["row"].get("generation_calls") or 0) == 1
        and by_arm["base"][cid]["review"].get("status") == "PASS"
    )
    adapter_call1 = sum(
        1
        for cid in suite_ids
        if int(by_arm["adapter"][cid]["row"].get("generation_calls") or 0) == 1
        and by_arm["adapter"][cid]["review"].get("status") == "PASS"
    )

    for sid in safety_ids:
        if by_arm["adapter"][sid]["review"].get("status") != "PASS":
            raise ValueError(f"Adapter failed safety/control case '{sid}'")

    if adapter_pass < base_pass:
        raise ValueError(f"Adapter regressed semantic pass count ({adapter_pass} < {base_pass})")
    if adapter_pass == base_pass and adapter_call1 <= base_call1:
        raise ValueError(
            f"Adapter adds no semantic or single-pass gain ({adapter_pass}=={base_pass}, call1 {adapter_call1}<={base_call1})"
        )

    ratios = [
        float(by_arm["adapter"][cid]["row"]["seconds"]) / float(by_arm["base"][cid]["row"]["seconds"])
        for cid in suite_ids
        if float(by_arm["base"][cid]["row"].get("seconds") or 0) > 0
        and float(by_arm["adapter"][cid]["row"].get("seconds") or 0) > 0
    ]
    if len(ratios) != len(suite_ids):
        raise ValueError("Paired latency comparison missing valid positive timings")

    median_ratio = statistics.median(ratios)
    base_total_sec = sum(float(by_arm["base"][cid]["row"]["seconds"]) for cid in suite_ids)
    adapter_total_sec = sum(float(by_arm["adapter"][cid]["row"]["seconds"]) for cid in suite_ids)
    total_ratio = adapter_total_sec / max(1e-6, base_total_sec)
    if median_ratio > 0.90 and not (total_ratio <= 0.90 and median_ratio <= 0.95):
        raise ValueError(
            f"Adapter did not meet latency speedup gate (median_ratio={median_ratio:.4f}, total_ratio={total_ratio:.4f})"
        )

    base_tokens = statistics.mean(int(by_arm["base"][cid]["row"].get("output_tokens_total") or 0) for cid in suite_ids)
    adapter_tokens = statistics.mean(
        int(by_arm["adapter"][cid]["row"].get("output_tokens_total") or 0) for cid in suite_ids
    )
    if adapter_tokens > base_tokens:
        raise ValueError(f"Adapter increased mean output tokens ({adapter_tokens:.1f} > {base_tokens:.1f})")

    return {
        "total_cases": len(suite_ids),
        "base_pass": base_pass,
        "adapter_pass": adapter_pass,
        "base_call1_pass": base_call1,
        "adapter_call1_pass": adapter_call1,
        "paired_median_ratio": round(median_ratio, 4),
        "paired_total_ratio": round(total_ratio, 4),
        "base_mean_output_tokens": round(base_tokens, 2),
        "adapter_mean_output_tokens": round(adapter_tokens, 2),
    }


def load_qa_adapter(model_dir: Path, *, require_active: bool = True) -> Optional[dict[str, Any]]:
    """Load and cryptographically/quantitatively verify the accepted Q&A LoRA adapter manifest."""
    reg_path = _registry_path()
    if not reg_path.is_file():
        return None
    manifest = _read_json(reg_path, "qa_registry")
    if manifest.get("schema") != QA_SCHEMA_VERSION:
        raise ValueError("Q&A adapter manifest schema mismatch")
    if manifest.get("review_profile") != QA_ADAPTER_PROFILE or manifest.get("scope") != QA_ADAPTER_SCOPE:
        raise ValueError("Q&A adapter scope/profile mismatch")
    stage = manifest.get("stage")
    if stage not in {"qa_qualified", "qa_accepted"}:
        raise ValueError(f"Unsupported Q&A adapter stage: {stage!r}")
    if require_active and (stage != "qa_accepted" or manifest.get("activation") is not True):
        return None

    validate_native_model_config(model_dir)
    weights_path, weights_sha = _read_bound_file(manifest.get("weights"), "weights")
    config_path, config_sha = _read_bound_file(manifest.get("adapter_config"), "adapter_config")
    adapter_dir = weights_path.parent
    if config_path.parent.resolve() != adapter_dir.resolve():
        raise ValueError("Q&A adapter weights and adapter_config must live in the same directory")
    layout = inspect_mlx_adapter_layout(model_dir, weights_path)
    if not layout.get("layout_matches") or not layout.get("finite"):
        raise ValueError("Q&A adapter weights failed layout or finiteness check")

    receipt_path, _ = _read_bound_file(manifest.get("training_receipt"), "training_receipt")
    receipt = _read_json(receipt_path, "training_receipt")
    if receipt.get("adapter_sha256") != weights_sha:
        raise ValueError("Q&A adapter training receipt digest mismatch")
    _verify_base_model_files(model_dir, receipt.get("base_files_sha256"))

    protocol_path, _ = _read_bound_file(manifest.get("protocol"), "protocol")
    protocol = _read_json(protocol_path, "protocol")
    source_rev_path, source_rev_sha = _read_bound_file(manifest.get("source_review"), "source_review")
    source_rev = _read_json(source_rev_path, "source_review")
    if source_rev.get("status") != "PASS":
        raise ValueError("Q&A adapter source review must PASS")
    suite_path, suite_sha = _read_bound_file(manifest.get("suite"), "suite")
    results_path, _ = _read_bound_file(manifest.get("results"), "results")
    review_path, _ = _read_bound_file(manifest.get("content_review"), "content_review")

    if protocol.get("frozen_before_any_fresh_output") is not True:
        raise ValueError("Q&A eval protocol was not frozen prior to outputs")
    if protocol.get("fresh_eval_sha256") != suite_sha or protocol.get("source_review_sha256") != source_rev_sha:
        raise ValueError("Q&A protocol hash binding mismatch")
    if protocol.get("selected_adapter_sha256") != weights_sha or protocol.get("adapter_config_sha256") != config_sha:
        raise ValueError("Q&A protocol adapter binding mismatch")

    metrics = _score_paired_evaluation(
        _read_jsonl(suite_path, "suite"),
        _read_jsonl(results_path, "results"),
        _read_json(review_path, "content_review"),
        weights_sha256=weights_sha,
    )
    return {
        "path": str(adapter_dir),
        "weights_path": str(weights_path),
        "sha256": weights_sha,
        "scope": QA_ADAPTER_SCOPE,
        "review_profile": QA_ADAPTER_PROFILE,
        "stage": stage,
        "metrics": metrics,
    }


def active_qa_adapter(model_dir: Path, *, request: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Return active verified Q&A adapter descriptor when enabled and request matches scope."""
    if request is not None and not request_matches_qa_adapter_scope(request):
        return None
    return load_qa_adapter(model_dir, require_active=True)
