"""Offline readiness and blocker audit for corpus, hardware/memory, datasets, and MLX LoRA adapters.

Read-only inspection tool: never modifies databases, OCR caches, training datasets, or active adapters,
and never approves unverified pages or activates rejected adapters.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _read_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON object from disk, returning None when absent or malformed."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _count_ocr_cache_statuses(ocr_cache_dir: Path) -> Dict[str, Any]:
    """Summarize raw OCR cache review states without loading page text into the report."""
    counts: Counter[str] = Counter()
    total_files = 0
    if not ocr_cache_dir.is_dir():
        return {"present": False, "total_files": 0, "statuses": {}, "pending_or_unverified": 0}
    for entry in ocr_cache_dir.glob("*.json"):
        total_files += 1
        data = _read_json_safe(entry)
        if data is None:
            counts["corrupt"] += 1
            continue
        status = str(data.get("status") or ("unverified" if "text" in data else "unknown"))
        counts[status] += 1
    pending = sum(
        count
        for state, count in counts.items()
        if state in {"unverified", "CAPTURED_PENDING_REVIEW", "FAILED", "failed", "pending_review", "corrupt"}
    )
    return {
        "present": True,
        "total_files": total_files,
        "statuses": dict(sorted(counts.items())),
        "pending_or_unverified": pending,
    }


def _read_kb_document_statuses(db_path: Path) -> Dict[str, Any]:
    """Read document and chunk summary counts from knowledge_base.db using a read-only URI."""
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return {"present": False, "documents": 0, "chunks": 0, "document_statuses": {}}
    try:
        with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=1.5) as conn:
            doc_count = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            chunk_count = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
            status_counts: Dict[str, int] = {}
            if "status" in cols:
                rows = conn.execute("SELECT COALESCE(status, 'unknown'), COUNT(*) FROM documents GROUP BY status").fetchall()
                status_counts = {str(r[0]): int(r[1]) for r in rows}
            return {
                "present": True,
                "documents": doc_count,
                "chunks": chunk_count,
                "document_statuses": dict(sorted(status_counts.items())),
            }
    except sqlite3.Error as exc:
        return {"present": True, "documents": 0, "chunks": 0, "document_statuses": {}, "error": str(exc)}


def _audit_corpus_and_ocr(root: Path, data_dir: Path, src_dir: Path) -> Dict[str, Any]:
    """Audit corpus completeness, retrieval tree sidecar health, DB statuses, and OCR cache state."""
    from app.corpus_readiness import audit_corpus
    from app.retrieval_tree import inspect_retrieval_tree_health

    db_path = data_dir / "knowledge_base.db"
    kb_stats = _read_kb_document_statuses(db_path)
    tree_health = inspect_retrieval_tree_health(db_path)
    ocr_cache = _count_ocr_cache_statuses(data_dir / "ocr_cache")

    layout_matches_root = (
        data_dir.resolve() == (root / "data").resolve()
        and src_dir.resolve() == (root / "src").resolve()
    )
    if layout_matches_root and db_path.is_file():
        report = audit_corpus(root)
        failure_counts = dict(sorted(Counter(str(item.get("kind", "unknown")) for item in report.get("failures", [])).items()))
        corpus_section: Dict[str, Any] = {
            "audited": True,
            "complete": bool(report.get("complete")),
            "complete_all_sources": bool(report.get("complete_all_sources")),
            "integrity": report.get("integrity"),
            "supported_documents": report.get("supported_documents", 0),
            "indexed_documents": report.get("indexed_documents", 0),
            "total_pages": report.get("total_pages", 0),
            "in_scope_pages": report.get("in_scope_pages", 0),
            "excluded_page_count": report.get("excluded_page_count", 0),
            "exclusion_decision_id": report.get("exclusion_decision_id"),
            "page_states": report.get("page_states", {}),
            "vectors": report.get("vectors", {}),
            "unsupported_count": len(report.get("unsupported", [])),
            "symlink_violation_count": len(report.get("symlink_violations", [])),
            "failure_total": len(report.get("failures", [])),
            "failure_counts": failure_counts,
        }
    else:
        corpus_section = {
            "audited": False,
            "complete": False,
            "reason": "knowledge_base_missing" if not db_path.is_file() else "custom_src_or_data_layout",
            "failure_counts": {},
        }

    return {
        "corpus": corpus_section,
        "knowledge_base": kb_stats,
        "retrieval_tree": tree_health,
        "ocr_cache": ocr_cache,
    }


def _audit_base_model_and_memory(model_dir: Path, *, num_layers: int, max_seq_length: int) -> Dict[str, Any]:
    """Validate pinned native base model files and check hardware/protocol training preflight."""
    from app.memory_preflight import get_hardware_memory_profile, inspect_base_model, preflight_check_memory_training
    from app.model_resolver import validate_native_model_config

    hw = get_hardware_memory_profile()
    base_info: Dict[str, Any] = {"present": model_dir.is_dir(), "valid": False}
    if model_dir.is_dir():
        try:
            validate_native_model_config(model_dir)
            inspected = inspect_base_model(model_dir)
            base_info.update({
                "valid": True,
                "model_type": inspected.get("model_type"),
                "is_supported_native": bool(inspected.get("is_supported_native")),
                "hidden_size": inspected.get("hidden_size"),
                "num_hidden_layers": inspected.get("num_hidden_layers"),
                "quantization_bits": inspected.get("quantization_bits"),
                "has_weights": bool(inspected.get("has_weights")),
                "total_weight_mb": round(float(inspected.get("total_weight_mb") or 0.0), 2),
                "has_tokenizer": bool(inspected.get("has_tokenizer")),
            })
        except (FileNotFoundError, OSError, ValueError) as exc:
            base_info["error"] = str(exc)

    preflight = preflight_check_memory_training(
        model_dir=model_dir,
        num_layers=num_layers,
        max_seq_length=max_seq_length,
    )
    return {
        "base_model": base_info,
        "hardware": hw,
        "training_preflight": preflight,
    }


def _audit_adapters(data_dir: Path, model_dir: Path) -> Dict[str, Any]:
    """Inspect active adapter registry and any candidate practice-9b receipt without activating anything."""
    from app.active_registry import validate_adapter_acceptance
    from app.adapter_integrity import inspect_mlx_adapter_layout

    adapters_dir = data_dir / "adapters"
    active_reg_path = adapters_dir / "active_registry.json"
    active_reg = _read_json_safe(active_reg_path)
    if active_reg is None:
        active_section: Dict[str, Any] = {
            "present": active_reg_path.is_file(),
            "status": "missing" if not active_reg_path.is_file() else "invalid_json",
            "active_adapter_id": None,
            "verified": False,
        }
    else:
        active_id = active_reg.get("active_adapter_id")
        accepted_map = active_reg.get("accepted_adapters") if isinstance(active_reg.get("accepted_adapters"), dict) else {}
        base_meta = active_reg.get("base_model") if isinstance(active_reg.get("base_model"), dict) else {}
        active_section = {
            "present": True,
            "status": active_reg.get("status", "unknown"),
            "active_adapter_id": active_id,
            "accepted_adapters_count": len(accepted_map),
            "base_model_repo": base_meta.get("repo"),
            "base_model_revision": base_meta.get("revision"),
            "verified": False,
        }
        if active_id:
            entry = accepted_map.get(active_id)
            adapter_dir_raw = active_reg.get("adapter_dir") or (entry.get("adapter_dir") if isinstance(entry, dict) else None)
            if isinstance(entry, dict) and adapter_dir_raw:
                try:
                    validate_adapter_acceptance(Path(adapter_dir_raw), entry, model_dir)
                    active_section["verified"] = True
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    active_section["verification_error"] = str(exc)

    practice_path = adapters_dir / "practice-9b.json"
    practice_data = _read_json_safe(practice_path)
    if practice_data is None:
        practice_section: Dict[str, Any] = {
            "present": practice_path.is_file(),
            "status": "absent" if not practice_path.is_file() else "invalid_json",
        }
    else:
        practice_section = {
            "present": True,
            "schema": practice_data.get("schema"),
            "stage": practice_data.get("stage"),
            "review_profile": practice_data.get("review_profile"),
            "strict_decision": None,
            "failed_gates": [],
            "layout_matches": None,
        }
        data_root_resolved = data_dir.resolve()
        weights_raw = (practice_data.get("weights") or {}).get("path") if isinstance(practice_data.get("weights"), dict) else None
        if isinstance(weights_raw, str) and weights_raw:
            wp = Path(weights_raw).resolve()
            if wp.is_relative_to(data_root_resolved) and wp.is_file() and model_dir.is_dir():
                try:
                    layout = inspect_mlx_adapter_layout(model_dir, wp)
                    practice_section["layout_matches"] = bool(layout.get("layout_matches"))
                    practice_section["finite"] = bool(layout.get("finite"))
                except (OSError, ValueError) as exc:
                    practice_section["layout_error"] = str(exc)

        primary = practice_data.get("primary") if isinstance(practice_data.get("primary"), dict) else {}
        summary_raw = (primary.get("strict_summary") or {}).get("path") if isinstance(primary.get("strict_summary"), dict) else None
        if isinstance(summary_raw, str) and summary_raw:
            sp = Path(summary_raw).resolve()
            if sp.is_relative_to(data_root_resolved):
                summary_json = _read_json_safe(sp)
                if summary_json:
                    practice_section["strict_decision"] = summary_json.get("decision")
                    gates = summary_json.get("gates") if isinstance(summary_json.get("gates"), dict) else {}
                    practice_section["failed_gates"] = sorted(k for k, v in gates.items() if v is False)

    return {"active_registry": active_section, "practice_candidate": practice_section}


def _audit_dataset(
    dataset_dir: Optional[Path],
    *,
    root: Path,
    code_root: Path,
    model_dir: Path,
    check_tokens: bool,
    max_seq_length: int,
) -> Dict[str, Any]:
    """Validate an explicit offline training dataset directory for approval, scope, runtime drift, and tokens."""
    if dataset_dir is None:
        return {"specified": False, "ready": False, "blockers": ["no_dataset_specified"]}

    if not dataset_dir.is_dir():
        return {"specified": True, "ready": False, "blockers": ["dataset_dir_missing"]}

    from app.active_registry import get_file_sha256_cached
    from app.corpus_scope import verify_dataset_scope

    summary = _read_json_safe(dataset_dir / "summary.json")
    approval = _read_json_safe(dataset_dir / "approval.json")
    release = _read_json_safe(dataset_dir / "dataset-release.json")
    train_file = dataset_dir / "train.jsonl"
    valid_file = dataset_dir / "valid.jsonl"

    blockers: List[str] = []
    if not train_file.is_file() or not valid_file.is_file():
        blockers.append("missing_train_or_valid_jsonl")
    if summary is None:
        blockers.append("missing_or_invalid_summary")
    elif summary.get("audit_complete") is False or summary.get("training_approved") is False:
        blockers.append("dataset_summary_not_approved")
    if approval is None:
        blockers.append("missing_approval_json")

    if approval and train_file.is_file() and valid_file.is_file():
        import hashlib

        combined_sha = hashlib.sha256(train_file.read_bytes() + valid_file.read_bytes()).hexdigest()
        expected_combined = approval.get("dataset_sha256")
        if expected_combined and combined_sha != expected_combined:
            blockers.append("dataset_sha256_mismatch")

    drifted_runtime_files: List[str] = []
    for meta_doc in (release, summary):
        runtime_hashes = (meta_doc or {}).get("runtime_files_sha256")
        if isinstance(runtime_hashes, dict):
            code_root_resolved = code_root.resolve()
            for rel_path, expected_sha in runtime_hashes.items():
                candidate = (code_root_resolved / str(rel_path)).resolve()
                if not candidate.is_relative_to(code_root_resolved) or not candidate.is_file():
                    drifted_runtime_files.append(str(rel_path))
                elif get_file_sha256_cached(candidate) != expected_sha:
                    drifted_runtime_files.append(str(rel_path))
            break
    if drifted_runtime_files:
        blockers.append("runtime_files_sha256_drift")

    scope_ok: Optional[bool] = None
    scope_error: Optional[str] = None
    if (root / "data/runtime/corpus_completion_policy.json").is_file():
        try:
            verify_dataset_scope(dataset_dir, root=root)
            scope_ok = True
        except (OSError, ValueError) as exc:
            scope_ok = False
            scope_error = str(exc)
            blockers.append("dataset_scope_failed")

    token_audit: Optional[Dict[str, Any]] = None
    if check_tokens and train_file.is_file() and valid_file.is_file():
        try:
            from app.memory_preflight import audit_dataset_tokens

            token_audit = audit_dataset_tokens(dataset_dir, max_seq_length=max_seq_length, model_dir=model_dir)
            if not token_audit.get("passed", False):
                blockers.append("token_audit_exceeded")
        except (OSError, ValueError, RuntimeError) as exc:
            token_audit = {"passed": False, "error": str(exc)}
            blockers.append("token_audit_error")

    return {
        "specified": True,
        "ready": len(blockers) == 0,
        "status": (summary or {}).get("status"),
        "train_records": (summary or {}).get("train_records"),
        "valid_records": (summary or {}).get("valid_records"),
        "audit_complete": (summary or {}).get("audit_complete"),
        "training_approved": (summary or {}).get("training_approved"),
        "scope_verified": scope_ok,
        "scope_error": scope_error,
        "drifted_runtime_files": sorted(set(drifted_runtime_files)),
        "token_audit": token_audit,
        "blockers": sorted(set(blockers)),
    }


def _collect_verdict(
    *,
    corpus_report: Dict[str, Any],
    model_memory: Dict[str, Any],
    adapters: Dict[str, Any],
    dataset: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble explicit HOLD/READY verdicts and prioritized remediation steps."""
    blockers: List[Dict[str, Any]] = []
    next_steps: List[str] = []

    corpus = corpus_report["corpus"]
    ocr_cache = corpus_report["ocr_cache"]
    kb = corpus_report["knowledge_base"]

    if not corpus.get("audited"):
        blockers.append({"gate": "corpus", "code": corpus.get("reason", "corpus_not_audited"), "detail": "Corpus readiness check did not run."})
    elif not corpus.get("complete"):
        blockers.append({
            "gate": "corpus",
            "code": "corpus_incomplete",
            "failure_total": corpus.get("failure_total", 0),
            "failure_counts": corpus.get("failure_counts", {}),
        })
        next_steps.append("Resolve pending page reviews, unindexed documents, and full-text vector provenance before dataset generation.")

    unindexed_docs = sum(v for k, v in kb.get("document_statuses", {}).items() if k != "indexed")
    if unindexed_docs > 0:
        blockers.append({"gate": "knowledge_base", "code": "unindexed_or_partial_documents", "count": unindexed_docs, "document_statuses": kb.get("document_statuses", {})})

    if ocr_cache.get("pending_or_unverified", 0) > 0:
        blockers.append({"gate": "ocr_cache", "code": "pending_ocr_reviews", "count": ocr_cache["pending_or_unverified"], "statuses": ocr_cache.get("statuses", {})})
        next_steps.append("Complete independent source review of remaining pending/unverified OCR cache pages; do not index or train on unverified drafts.")

    base_model = model_memory["base_model"]
    if not base_model.get("present") or not base_model.get("valid"):
        blockers.append({"gate": "base_model", "code": "invalid_or_missing_base_model", "error": base_model.get("error")})

    preflight = model_memory["training_preflight"]
    if not preflight.get("eligible_for_training"):
        blockers.append({"gate": "training_protocol_and_memory", "code": "preflight_blocked", "violations": preflight.get("violations", [])})
        next_steps.append("Establish an empirically verified Qwen3.5 9B memory/sequence-length training protocol on an idle GPU host before running LoRA.")

    if dataset.get("specified"):
        if not dataset.get("ready"):
            blockers.append({"gate": "dataset", "code": "dataset_blocked", "blockers": dataset.get("blockers", []), "drifted_runtime_files": dataset.get("drifted_runtime_files", [])})
            if "runtime_files_sha256_drift" in dataset.get("blockers", []):
                next_steps.append("Regenerate and re-audit training inputs with current prompt/policy producers (`runtime_files_sha256` has drifted).")
    else:
        blockers.append({"gate": "dataset", "code": "no_dataset_specified"})
        next_steps.append("Specify an approved reviewed `--dataset-dir` once corpus gates pass, then run exact tokenizer length auditing (`--check-tokens`).")

    active_reg = adapters["active_registry"]
    practice_cand = adapters["practice_candidate"]
    adapter_blockers: List[str] = []
    if practice_cand.get("present") and practice_cand.get("strict_decision") == "DO_NOT_ACTIVATE":
        adapter_blockers.append("practice_candidate_rejected_by_strict_gates")
        next_steps.append("Keep unadapted Qwen3.5 9B active; do not activate rejected candidates (`DO_NOT_ACTIVATE`).")
    if active_reg.get("active_adapter_id") and not active_reg.get("verified"):
        adapter_blockers.append("active_adapter_failed_acceptance_verification")

    adapter_verdict = (
        "ACTIVE" if (active_reg.get("active_adapter_id") and active_reg.get("verified"))
        else ("HOLD" if (adapter_blockers or practice_cand.get("present")) else "NONE")
    )

    ready_for_training = len(blockers) == 0
    return {
        "training_verdict": "READY" if ready_for_training else "HOLD",
        "ready_for_training": ready_for_training,
        "adapter_activation_verdict": adapter_verdict,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "adapter_blockers": adapter_blockers,
        "recommended_next_steps": list(dict.fromkeys(next_steps)),
    }


def run_adapter_readiness_audit(
    *,
    root: Path,
    data_dir: Optional[Path] = None,
    src_dir: Optional[Path] = None,
    model_dir: Optional[Path] = None,
    dataset_dir: Optional[Path] = None,
    code_root: Path = ROOT,
    num_layers: int = 16,
    max_seq_length: int = 1024,
    check_tokens: bool = False,
) -> Dict[str, Any]:
    """Run all offline corpus, hardware/protocol, dataset, and adapter gates cleanly read-only."""
    resolved_root = Path(root).resolve()
    resolved_data = Path(data_dir).resolve() if data_dir is not None else (resolved_root / "data")
    resolved_src = Path(src_dir).resolve() if src_dir is not None else (resolved_root / "src")
    resolved_model = Path(model_dir).resolve() if model_dir is not None else (resolved_data / "models" / "qwen3.5-9b-4bit")
    resolved_dataset = Path(dataset_dir).resolve() if dataset_dir is not None else None

    corpus_report = _audit_corpus_and_ocr(resolved_root, resolved_data, resolved_src)
    model_memory = _audit_base_model_and_memory(resolved_model, num_layers=num_layers, max_seq_length=max_seq_length)
    adapters = _audit_adapters(resolved_data, resolved_model)
    dataset = _audit_dataset(
        resolved_dataset,
        root=resolved_root,
        code_root=code_root,
        model_dir=resolved_model,
        check_tokens=check_tokens,
        max_seq_length=max_seq_length,
    )
    summary = _collect_verdict(
        corpus_report=corpus_report,
        model_memory=model_memory,
        adapters=adapters,
        dataset=dataset,
    )
    return {
        "schema": "study-adapter-readiness-audit-v1",
        **summary,
        **corpus_report,
        **model_memory,
        "adapters": adapters,
        "dataset": dataset,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Root directory containing src/ and data/.")
    parser.add_argument("--data-dir", type=Path, default=None, help="Override data directory path.")
    parser.add_argument("--src-dir", type=Path, default=None, help="Override source documents directory path.")
    parser.add_argument("--model-dir", type=Path, default=None, help="Override local MLX base model directory.")
    parser.add_argument("--dataset-dir", type=Path, default=None, help="Optional training dataset directory to validate.")
    parser.add_argument("--num-layers", type=int, default=16, help="Planned LoRA layer count for memory preflight.")
    parser.add_argument("--max-seq-length", type=int, default=1024, help="Planned sequence length for memory/token checks.")
    parser.add_argument("--check-tokens", action="store_true", help="Run exact local tokenizer audit on --dataset-dir.")
    parser.add_argument("--strict", action="store_true", help="Exit with code 2 when training_verdict is HOLD.")
    args = parser.parse_args(argv)

    env_data = os.getenv("AGENT_DATA_DIR")
    env_src = os.getenv("AGENT_SRC_DIR")
    default_root = Path(env_data).resolve().parent if (args.root is None and env_data) else ROOT
    root = args.root if args.root is not None else default_root
    data_dir = args.data_dir if args.data_dir is not None else (Path(env_data) if (args.root is None and env_data) else None)
    src_dir = args.src_dir if args.src_dir is not None else (Path(env_src) if (args.root is None and env_src) else None)

    result = run_adapter_readiness_audit(
        root=root,
        data_dir=data_dir,
        src_dir=src_dir,
        model_dir=args.model_dir,
        dataset_dir=args.dataset_dir,
        code_root=ROOT,
        num_layers=args.num_layers,
        max_seq_length=args.max_seq_length,
        check_tokens=args.check_tokens,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict and not result["ready_for_training"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
