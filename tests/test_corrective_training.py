"""Architecture-independent content gates; retired trials cannot authorize training."""
import pytest
from app.corrective_training import assess_paired_reviews


def test_target_gain_cannot_hide_loss_of_previously_correct_answers():
    suite = {"cases": [{"id": f"{kind}-{i}", "kind": kind, "error_id": "modality" if kind == "target" else None} for kind in ("target", "retention") for i in range(30)]}
    def review(improved):
        return {"content_review_complete": True, "reviewer": "Codex", "cases": {
            c["id"]: {"input_sha256": c["id"], "reason": "fixture claim review", "source_verified": True,
                      "grade": "pass" if improved or c["kind"] == "retention" else "fail"}
            for c in suite["cases"]}}
    before, after = review(False), review(True)
    criteria = {"min_target_accuracy": .8, "min_absolute_gain": .15, "max_retention_regressions": 0, "max_critical_regressions": 0}
    passed = assess_paired_reviews(suite, before, after, criteria)
    assert passed["status"] == "quality_gate_passed_pending_codex_decision" and passed["promoted"] is False
    after["cases"]["retention-0"]["grade"] = "partial"
    rejected = assess_paired_reviews(suite, before, after, criteria)
    assert rejected["status"] == "rejected_quality" and rejected["retention_regressions"] == ["retention-0"]
    before["content_review_complete"] = False
    with pytest.raises(ValueError, match="chưa chấm nội dung"):
        assess_paired_reviews(suite, before, after, criteria)
