"""Paired content evaluation for base model and LoRA candidates.

Evaluates base vs candidate on identical frozen inputs with deterministic decoding,
scoring passage grounding, targeted error repair, retention, multi-domain compliance,
and no-source honest refusal.
"""

import os
import re
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.config import BASE_DIR, DATA_DIR, MLX_MODEL_DIR
from app.answer_format import clean_answer
from app.evaluation_metrics import has_repetition, has_language_drift

logger = logging.getLogger(__name__)


def evaluate_passage_grounding(answer: str, passage: str, is_no_source_expected: bool = False) -> Dict[str, Any]:
    """Score whether answer is strictly grounded in passage without hallucinations or contradictions."""
    ans = answer.strip()
    ans_lower = ans.lower()

    if is_no_source_expected:
        honest_keywords = [
            "chưa đủ căn cứ", "không có thông tin", "tài liệu không đề cập",
            "thiếu bằng chứng", "không đủ dữ liệu", "chưa thể kết luận",
            "không tìm thấy thông tin", "tài liệu không chứa",
        ]
        has_honest_keyword = any(k in ans_lower for k in honest_keywords)
        if not has_honest_keyword:
            return {
                "is_grounded": False,
                "no_source_honesty": False,
                "hallucination_detected": True,
                "semantic_review": {"accepted": False, "reason": "Không có tuyên bố từ chối trung thực khi thiếu nguồn."},
            }

        # Negative control: refusal keyword followed by an unsupported claim or fabrication
        adversative_claims = re.search(r"(?:nhưng|tuy nhiên|song|mặc dù vậy)\s+(.+)", ans_lower)
        unsupported_claim = False
        if adversative_claims:
            after_clause = adversative_claims.group(1).strip()
            if len(after_clause) > 5 and not any(k in after_clause for k in honest_keywords):
                unsupported_claim = True

        claim_triggers = [
            "chữa được mọi bệnh", "đã chứng minh", "khẳng định rằng",
            "thực tế là", "theo tác giả thì", "thuốc này",
        ]
        if any(t in ans_lower for t in claim_triggers):
            unsupported_claim = True

        if unsupported_claim:
            return {
                "is_grounded": False,
                "no_source_honesty": False,
                "hallucination_detected": True,
                "semantic_review": {
                    "accepted": False,
                    "reason": "Từ chối giả tạo kèm theo khẳng định bịa đặt ngoài tài liệu.",
                },
            }

        return {
            "is_grounded": True,
            "no_source_honesty": True,
            "hallucination_detected": False,
            "semantic_review": {
                "status": "diagnostic_only_pending_review",
                "accepted": None,
                "reason": "Khớp mẫu từ chối chẩn đoán; cần Codex/chuyên gia thẩm định nội dung độc lập.",
            },
        }

    # Lexical overlap with passage
    passage_words = set(re.findall(r"\w+", passage.lower()))
    answer_words = [w for w in re.findall(r"\w+", ans.lower()) if len(w) >= 3]
    overlap_count = sum(1 for w in answer_words if w in passage_words)
    overlap_ratio = (overlap_count / len(answer_words)) if answer_words else 0.0

    # Citation check [S#]
    citations = re.findall(r"\[S\d+\]", ans, re.IGNORECASE)
    has_citations = len(citations) > 0

    # Repetition or language drift
    repetition = has_repetition(ans)
    drift = has_language_drift(ans)

    # Negative control: Semantic contradiction where answer negates conditions in passage
    # Word overlap alone must NEVER certify a contradictory answer
    has_contradiction = False
    contradiction_reason = None
    negation_patterns = [
        r"không\s+cần\s+([^\.,;]+)",
        r"không\s+phải\s+(?:là\s+)?([^\.,;]+)",
        r"không\s+thuộc\s+([^\.,;]+)",
        r"chỉ\s+cần\s+([^\.,;]+)\s+và\s+không\s+cần\s+([^\.,;]+)",
    ]
    for pat in negation_patterns:
        for match in re.finditer(pat, ans_lower):
            for group_idx in range(1, len(match.groups()) + 1):
                negated_phrase = match.group(group_idx)
                if not negated_phrase:
                    continue
                negated_phrase = negated_phrase.strip()
                words = [w for w in re.findall(r"\w+", negated_phrase) if len(w) >= 3]
                if len(words) >= 2:
                    phrase_core = " ".join(words[:3])
                    if phrase_core in passage.lower() and not ("không " + phrase_core in passage.lower()):
                        has_contradiction = True
                        contradiction_reason = f"Phủ định điều kiện cơ sở '{phrase_core}' có trong tài liệu gốc: '{match.group(0)}'"
                        break
            if has_contradiction:
                break

    is_grounded = overlap_ratio >= 0.40 and not repetition and not drift and not has_contradiction

    return {
        "is_grounded": is_grounded,
        "overlap_ratio": round(overlap_ratio, 3),
        "has_citations": has_citations,
        "repetition": repetition,
        "language_drift": drift,
        "has_contradiction": has_contradiction,
        "contradiction_reason": contradiction_reason,
        "semantic_review": {
            "status": "rejected" if (has_contradiction or not is_grounded) else "diagnostic_only_pending_review",
            "accepted": False if (has_contradiction or not is_grounded) else None,
            "reason": contradiction_reason or ("Đạt tiêu chí chẩn đoán độ trùng lặp; bắt buộc thẩm định ngữ nghĩa độc lập trước khi phê duyệt." if is_grounded else "Không đạt ngưỡng căn cứ hoặc phát hiện lỗi suy thoái."),
        },
    }


import hashlib


def _sha256(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _is_bound_semantic_review(sem: Any, passage: str, b_ans: str, c_ans: str) -> bool:
    """Validate that semantic review contains verified grades, reasons, and bound hashes."""
    if not isinstance(sem, dict):
        return False
    if sem.get("is_verified") is not True:
        return False
    if not sem.get("reviewer"):
        return False
    # Must have non-empty explanation/reasons
    reasons = sem.get("reason") or sem.get("reasons") or sem.get("notes") or (sem.get("base_reason") and sem.get("candidate_reason"))
    if not reasons:
        return False
    # Must have explicit candidate grade
    has_cand_grade = (
        "candidate_grade" in sem
        or "grade" in sem
        or (isinstance(sem.get("grades"), dict) and "candidate" in sem["grades"])
        or "claim_reviews" in sem
        or "is_grounded_grade" in sem
    )
    if not has_cand_grade:
        return False
    # Must have explicit base grade across all cases including retention
    has_base_grade = (
        "base_grade" in sem
        or (isinstance(sem.get("grades"), dict) and "base" in sem["grades"])
    )
    if not has_base_grade:
        return False
    # Must bind exact source, candidate, and base hashes
    expected_src_hash = _sha256(passage)
    expected_cand_hash = _sha256(c_ans)
    expected_base_hash = _sha256(b_ans)
    if sem.get("source_hash") != expected_src_hash:
        return False
    if sem.get("candidate_hash") != expected_cand_hash:
        return False
    if sem.get("base_hash") != expected_base_hash:
        return False
    return True


def _extract_semantic_rank(sem: dict, target: str) -> int:
    """Return rank 2 for pass, 1 for partial, 0 for fail."""
    if not isinstance(sem, dict):
        return 0
    grade = None
    if target == "candidate":
        if "candidate_grade" in sem:
            grade = sem["candidate_grade"]
        elif "grade" in sem:
            grade = sem["grade"]
        elif isinstance(sem.get("grades"), dict):
            grade = sem["grades"].get("candidate")
        elif "claim_reviews" in sem and isinstance(sem["claim_reviews"], list):
            if all(c.get("verdict") in ("pass", True, "supported") for c in sem["claim_reviews"]):
                return 2
            elif any(c.get("verdict") in ("pass", True, "supported") for c in sem["claim_reviews"]):
                return 1
            return 0
    elif target == "base":
        if "base_grade" in sem:
            grade = sem["base_grade"]
        elif isinstance(sem.get("grades"), dict):
            grade = sem["grades"].get("base")

    if grade in (True, 1, "pass", "accepted", "acceptable"):
        return 2
    if grade in ("partial", "partially_acceptable", 0.5):
        return 1
    return 0


def _extract_semantic_pass(sem: dict, target: str) -> bool:
    """Extract pass/fail decision from bound semantic review."""
    return _extract_semantic_rank(sem, target) == 2


def evaluate_paired_dossier(
    test_cases: List[Dict[str, Any]],
    base_answers: List[str],
    candidate_answers: List[str],
    protocol: Optional[Dict[str, Any]] = None,
    model_identity: Optional[Dict[str, Any]] = None,
    decoding_parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compare base and candidate on exact same test cases, retaining exact inputs/outputs.

    - Diagnostic mode (protocol is None or absent): Computes lexical and semantic scores, but promotion gates fail closed (promoted = False).
    - Certification mode (protocol provided): Strictly verifies full coverage of frozen protocol case IDs, targets, retention, critical cases, and no-source honesty. Evaluates gates against frozen protocol thresholds without hardcoding.
    - Preserves exact raw inputs, model identity, decoding parameters, and protocol hashes.
    """
    if len(test_cases) != len(base_answers) or len(test_cases) != len(candidate_answers):
        raise ValueError("Số lượng test cases và câu trả lời phải bằng nhau")

    raw_inputs = []
    case_records = []
    targeted_pass_base = 0
    targeted_pass_cand = 0
    retention_pass_base = 0
    retention_pass_cand = 0
    multi_domain_pass_cand = 0

    total_targeted = 0
    total_retention = 0
    total_multi_domain = 0
    total_critical = 0
    total_no_source = 0

    no_source_ids = set()
    if protocol and isinstance(protocol, dict):
        no_source_ids = set(protocol.get("no_source_case_ids", []))
    no_source_ids.update({"codex-holdout-25", "codex-holdout-26", "codex-holdout-27", "codex-holdout-28"})

    for idx, tc in enumerate(test_cases):
        passage = tc.get("passage", "")
        category = tc.get("category") or tc.get("kind", "general")
        cid = str(tc.get("id", str(idx)))
        no_source = bool(
            tc.get("is_no_source", False)
            or (cid in no_source_ids)
            or category in ("negative_control_refusal", "no_source")
            or "refusal" in cid
        )
        query = tc.get("query") or tc.get("question", "")

        b_ans = base_answers[idx]
        c_ans = candidate_answers[idx]

        b_eval = evaluate_passage_grounding(b_ans, passage, is_no_source_expected=no_source)
        c_eval = evaluate_passage_grounding(c_ans, passage, is_no_source_expected=no_source)

        raw_inputs.append({
            "id": tc.get("id", str(idx)),
            "passage": passage,
            "query": query,
            "base_answer": b_ans,
            "candidate_answer": c_ans,
            "passage_sha256": _sha256(passage),
            "query_sha256": _sha256(query),
            "base_answer_sha256": _sha256(b_ans),
            "candidate_answer_sha256": _sha256(c_ans),
        })

        case_records.append({
            "id": tc.get("id", str(idx)),
            "passage": passage,
            "query": query,
            "base_answer": b_ans,
            "candidate_answer": c_ans,
            "category": category,
            "critical": bool(tc.get("critical", False)),
            "is_no_source": no_source,
            "diagnostic_base": b_eval,
            "diagnostic_candidate": c_eval,
            "semantic_review": tc.get("semantic_review"),
        })

        if category in ("targeted_repair", "target"):
            total_targeted += 1
            if b_eval["is_grounded"]:
                targeted_pass_base += 1
            if c_eval["is_grounded"]:
                targeted_pass_cand += 1
        elif category in ("retention_benchmark", "retention"):
            total_retention += 1
            if b_eval["is_grounded"]:
                retention_pass_base += 1
            if c_eval["is_grounded"]:
                retention_pass_cand += 1
        elif category in ("multi_domain",):
            total_multi_domain += 1
            is_pure = not any(w in c_ans.lower() for w in ["tâm lý", "freud", "trị liệu"])
            if c_eval["is_grounded"] and is_pure:
                multi_domain_pass_cand += 1

        if tc.get("critical"):
            total_critical += 1
        if no_source:
            total_no_source += 1

    # Check semantic review validity across all cases
    has_verified_semantic_review = len(test_cases) > 0 and all(
        _is_bound_semantic_review(tc.get("semantic_review"), tc.get("passage", ""), base_answers[idx], candidate_answers[idx])
        for idx, tc in enumerate(test_cases)
    )

    # Protocol & coverage verification
    has_full_coverage = False
    has_valid_protocol = False
    if protocol and isinstance(protocol, dict):
        proto_cases = protocol.get("cases", [])
        if proto_cases:
            proto_map = {str(c.get("id")): c for c in proto_cases}
            actual_ids = {str(tc.get("id")) for tc in test_cases}
            if set(proto_map.keys()) == actual_ids and len(proto_cases) == len(test_cases):
                inputs_matched = True
                for tc in test_cases:
                    cid = str(tc.get("id"))
                    pc = proto_map[cid]

                    # 1. passage
                    if tc.get("passage", "") != pc.get("passage", ""):
                        inputs_matched = False
                        break

                    # 2. question / query
                    for q_key in ("question", "query"):
                        if q_key in pc and tc.get(q_key) != pc.get(q_key):
                            inputs_matched = False
                            break
                    if not inputs_matched:
                        break

                    # 3. messages
                    if "messages" in pc and tc.get("messages") != pc.get("messages"):
                        inputs_matched = False
                        break

                    # 4. source_hash
                    if "source_hash" in pc and tc.get("source_hash") != pc.get("source_hash"):
                        inputs_matched = False
                        break

                    # 5. critical
                    if "critical" in pc and bool(tc.get("critical")) != bool(pc.get("critical")):
                        inputs_matched = False
                        break

                    # 6. is_no_source
                    if "is_no_source" in pc and bool(tc.get("is_no_source")) != bool(pc.get("is_no_source")):
                        inputs_matched = False
                        break

                    # 7. kind / category
                    if "kind" in pc and tc.get("kind") != pc.get("kind"):
                        inputs_matched = False
                        break
                    if "category" in pc and tc.get("category") != pc.get("category"):
                        inputs_matched = False
                        break

                    # 8. input_sha256
                    if "input_sha256" in pc and tc.get("input_sha256") != pc.get("input_sha256"):
                        inputs_matched = False
                        break

                has_full_coverage = inputs_matched
                has_valid_protocol = inputs_matched

    if has_verified_semantic_review:
        sem_targeted_pass_base = 0
        sem_targeted_pass_cand = 0
        sem_retention_pass_base = 0
        sem_retention_pass_cand = 0
        sem_multi_domain_pass_cand = 0
        retention_regressions = 0
        critical_regressions = 0
        no_source_failures = 0

        has_paired_base_review = all(
            ("base_grade" in tc.get("semantic_review", {}) or (isinstance(tc.get("semantic_review", {}).get("grades"), dict) and "base" in tc["semantic_review"]["grades"])) and
            (tc.get("semantic_review", {}).get("base_hash") == _sha256(base_answers[idx]))
            for idx, tc in enumerate(test_cases)
        )

        for idx, tc in enumerate(test_cases):
            cat = tc.get("category") or tc.get("kind", "general")
            sem = tc["semantic_review"]
            b_ans = base_answers[idx]
            c_ans = candidate_answers[idx]
            cid = str(tc.get("id", str(idx)))
            is_critical = bool(tc.get("critical", False))
            is_no_src = bool(
                tc.get("is_no_source", False)
                or (cid in no_source_ids)
                or cat in ("negative_control_refusal", "no_source")
                or "refusal" in cid
            )

            has_base_grade = ("base_grade" in sem) or (isinstance(sem.get("grades"), dict) and "base" in sem["grades"])

            if b_ans == c_ans:
                c_rank = _extract_semantic_rank(sem, "candidate")
                b_rank = c_rank
            else:
                c_rank = _extract_semantic_rank(sem, "candidate")
                b_rank = _extract_semantic_rank(sem, "base") if has_base_grade else 0

            c_pass = (c_rank == 2)
            b_pass = (b_rank == 2)

            if cat in ("targeted_repair", "target"):
                if b_pass: sem_targeted_pass_base += 1
                if c_pass: sem_targeted_pass_cand += 1
            elif cat in ("retention_benchmark", "retention"):
                if b_pass: sem_retention_pass_base += 1
                if c_pass: sem_retention_pass_cand += 1
                if c_rank < b_rank:
                    retention_regressions += 1
            elif cat in ("multi_domain",):
                if c_pass: sem_multi_domain_pass_cand += 1

            if is_critical and (c_rank < b_rank):
                critical_regressions += 1

            if is_no_src and not c_pass:
                no_source_failures += 1

        targeted_score_base = round(sem_targeted_pass_base / max(1, total_targeted), 3)
        targeted_score_cand = round(sem_targeted_pass_cand / max(1, total_targeted), 3)
        retention_score_base = round(sem_retention_pass_base / max(1, total_retention), 3)
        retention_score_cand = round(sem_retention_pass_cand / max(1, total_retention), 3)
        multi_domain_score_cand = round(sem_multi_domain_pass_cand / max(1, total_multi_domain), 3)
        gain = targeted_score_cand - targeted_score_base

        if has_valid_protocol and protocol:
            acc_cfg = protocol.get("acceptance", {})
            gate_cfg = protocol.get("promotion_gates_thresholds", {})
            min_target_accuracy = acc_cfg.get("min_target_accuracy", gate_cfg.get("min_target_accuracy", 0.80))
            min_gain = acc_cfg.get("min_absolute_gain", gate_cfg.get("min_absolute_gain", 0.05))
            max_retention_reg = acc_cfg.get("max_retention_regressions", gate_cfg.get("max_retention_regressions", 0))
            max_critical_reg = acc_cfg.get("max_critical_regressions", gate_cfg.get("max_critical_regressions", 0))
            no_source_all_pass_req = acc_cfg.get("no_source_all_pass", gate_cfg.get("no_source_all_pass", True))

            targeted_improved = bool(
                has_paired_base_review and
                (sem_targeted_pass_cand > sem_targeted_pass_base) and
                (gain >= min_gain) and
                (targeted_score_cand >= min_target_accuracy)
            )
            retention_passed = bool(retention_regressions <= max_retention_reg)
            critical_passed = bool(critical_regressions <= max_critical_reg)
            no_source_passed = bool(no_source_failures == 0 if (total_no_source > 0 and no_source_all_pass_req) else True)
            multi_domain_passed = bool(
                (sem_multi_domain_pass_cand / max(1, total_multi_domain)) >= gate_cfg.get("multi_domain_threshold", 0.80)
                if total_multi_domain > 0 else True
            )

            promoted = bool(
                has_full_coverage and
                has_paired_base_review and
                targeted_improved and
                retention_passed and
                critical_passed and
                no_source_passed and
                multi_domain_passed
            )

            gates = {
                "targeted_score_improved": targeted_improved,
                "retention_gate_passed": retention_passed,
                "critical_gate_passed": critical_passed,
                "no_source_gate_passed": no_source_passed,
                "multi_domain_gate_passed": multi_domain_passed,
                "promoted": promoted,
            }
        else:
            # Diagnostic only: absent frozen protocol cannot certify promotion
            gates = {
                "targeted_score_improved": False,
                "retention_gate_passed": False,
                "multi_domain_gate_passed": False,
                "promoted": False,
            }
    else:
        gates = {
            "targeted_score_improved": False,
            "retention_gate_passed": False,
            "multi_domain_gate_passed": False,
            "promoted": False,
        }
        targeted_score_base = round(targeted_pass_base / max(1, total_targeted), 3)
        targeted_score_cand = round(targeted_pass_cand / max(1, total_targeted), 3)
        retention_score_base = round(retention_pass_base / max(1, total_retention), 3)
        retention_score_cand = round(retention_pass_cand / max(1, total_retention), 3)
        multi_domain_score_cand = round(multi_domain_pass_cand / max(1, total_multi_domain), 3)

    return {
        "cases": case_records,
        "raw_inputs": raw_inputs,
        "protocol": {
            "protocol_version": protocol.get("protocol_version") or protocol.get("schema"),
            "protocol_sha256": _sha256(json.dumps(protocol, sort_keys=True)) if protocol else None,
            "total_cases": len(protocol.get("cases", [])) if protocol else None,
            "full_coverage_verified": has_full_coverage,
        } if protocol else None,
        "model_identity": model_identity,
        "decoding_parameters": decoding_parameters or (
            protocol.get("decoding") if protocol else {
                "temperature": 0.0,
                "max_tokens": 256,
                "seed": 0,
                "deterministic": True,
            }
        ),
        "targeted_score_base": targeted_score_base,
        "targeted_score_candidate": targeted_score_cand,
        "retention_score_base": retention_score_base,
        "retention_score_candidate": retention_score_cand,
        "multi_domain_score_candidate": multi_domain_score_cand,
        "has_verified_semantic_review": has_verified_semantic_review,
        "has_full_coverage": has_full_coverage,
        "gates": gates,
    }
