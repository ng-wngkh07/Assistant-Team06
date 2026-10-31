"""Architecture-independent source and paired content review utilities.

These checks bind evidence, not semantic truth. Codex must independently review
the diagnoses, sources, calibration, and paired answers before issuing approval.
"""

import hashlib
from pathlib import Path



def sha256_file(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError("Review nội dung chưa đủ điều kiện: " + message)


def model_files(model_dir: Path) -> set[str]:
    names = {"config.json", "tokenizer.json", "tokenizer_config.json"}
    names.update(p.name for p in model_dir.glob("*.safetensors"))
    names.update(p.name for p in model_dir.glob("*.json") if p.name != "model_identity.json")
    names.update(p.name for p in model_dir.glob("*.txt"))
    return names


def validate_manual_content_review(manifest_records, review, dataset_digest, manifest_digest):
    """Manual source review binds each reference; it has no automatic judge to calibrate."""
    _require(review.get('reviewer') == 'Codex' and review.get('content_review_complete') is True and
             review.get('dataset_sha256') == dataset_digest and
             review.get('manifest_sha256') == manifest_digest, 'thiếu review nội dung đúng release')
    cases = review.get('cases', [])
    _require(len(cases) == len(manifest_records), 'review nội dung thiếu/thừa mẫu')
    by_id = {c.get('id'): c for c in cases}
    _require(len(by_id) == len(cases) and set(by_id) == {m['id'] for m in manifest_records},
             'review nội dung sai ID hoặc trùng mẫu')
    for m in manifest_records:
        c = by_id[m['id']]
        expected = {'source_file_sha256':m['source_file_sha256'],
                    'passage_sha256':sha256_text(m['passage']),
                    'query_sha256':sha256_text(m['query']),
                    'answer_sha256':sha256_text(m['reference'])}
        _require(c.get('grade') == 'pass' and c.get('source_verified') is True and c.get('reason') and
                 all(c.get(k) == v for k,v in expected.items()), 'review nội dung không khớp mẫu '+m['id'])


def sha256_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def assess_paired_reviews(suite: dict, baseline: dict, candidate: dict, criteria: dict) -> dict:
    """Measure a frozen, claim-reviewed comparison; never promote automatically."""
    for report in (baseline, candidate):
        _require(report.get("content_review_complete") is True and report.get("reviewer") == "Codex",
                 "chưa chấm nội dung độc lập; loss/judge/tests không thay thế review")
    expected = {c["id"] for c in suite["cases"]}
    _require(len(expected) == len(suite["cases"]), "trùng ca đánh giá")
    before, after = baseline["cases"], candidate["cases"]
    _require(set(before) == expected and set(after) == expected, "thiếu/thừa ca trong so sánh cặp")
    for case_id in expected:
        _require(before[case_id].get("input_sha256") == after[case_id].get("input_sha256") and
                 before[case_id].get("input_sha256"), "đầu vào trước/sau khác nhau")
        for report in (before, after):
            _require(report[case_id].get("grade") in {"pass", "partial", "fail"} and
                     report[case_id].get("reason") and report[case_id].get("source_verified") is True,
                     "thiếu grade/lý do/đối chiếu nguồn")
    failures, per_error = [], {}
    error_ids = {c["error_id"] for c in suite["cases"] if c["kind"] == "target"}
    for error_id in sorted(error_ids):
        ids = [c["id"] for c in suite["cases"] if c["kind"] == "target" and c["error_id"] == error_id]
        initial = sum(before[i]["grade"] == "pass" for i in ids) / len(ids)
        final = sum(after[i]["grade"] == "pass" for i in ids) / len(ids)
        per_error[error_id] = {"cases": len(ids), "before": initial, "after": final, "absolute_gain": final - initial}
        if final < criteria["min_target_accuracy"] or final - initial + 1e-12 < criteria["min_absolute_gain"]:
            failures.append("Lỗi chưa cải thiện đủ: " + error_id)
    retention = [c["id"] for c in suite["cases"] if c["kind"] == "retention"]
    _require(retention and error_ids, "thiếu nhóm lỗi hoặc nhóm giữ năng lực cũ")
    regressions = [i for i in retention if before[i]["grade"] == "pass" and after[i]["grade"] != "pass"]
    critical = [c["id"] for c in suite["cases"] if c.get("critical") is True and
                before[c["id"]]["grade"] == "pass" and after[c["id"]]["grade"] != "pass"]
    if len(regressions) > criteria["max_retention_regressions"]:
        failures.append("Mất câu trước đây trả lời đúng")
    if len(critical) > criteria["max_critical_regressions"]:
        failures.append("Hồi quy nghiêm trọng")
    return {"status": "rejected_quality" if failures else "quality_gate_passed_pending_codex_decision",
            "per_error": per_error, "retention_regressions": regressions,
            "critical_regressions": critical, "failures": failures, "promoted": False}
