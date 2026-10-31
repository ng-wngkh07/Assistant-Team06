"""Exact token/schema gates retained after obsolete training protocol retirement."""
import json
import pytest
from app.token_auditor import audit_context_truncation


def test_audit_context_truncation_missing_valid_dataset(tmp_path):
    missing_valid_dir = tmp_path / "missing_valid_dataset"
    missing_valid_dir.mkdir()
    train_file = missing_valid_dir / "train.jsonl"
    train_file.write_text('{"messages": [{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}]}\n', encoding="utf-8")

    missing_audit = audit_context_truncation(missing_valid_dir, max_seq_length=1024)
    assert missing_audit["acceptable"] is False


def test_audit_context_truncation_with_valid_and_malformed(tmp_path):
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    train_file = dataset_dir / "train.jsonl"
    valid_file = dataset_dir / "valid.jsonl"

    # Write samples within limit
    lines = []
    for i in range(10):
        lines.append(json.dumps({"messages": [{"role": "user", "content": "Ngắn"}, {"role": "assistant", "content": "Ngắn"}]}))
    train_file.write_text("\n".join(lines), encoding="utf-8")
    valid_file.write_text("\n".join(lines[:2]), encoding="utf-8")

    audit = audit_context_truncation(dataset_dir, max_seq_length=1024)
    if audit.get("status") == "FAIL_CLOSED" and any(v.get("code") in ("ENVIRONMENT_MISSING", "SUBPROCESS_ERROR") for v in audit.get("violations", [])):
        assert audit["acceptable"] is False
        pytest.skip("Local model tokenizer environment (.train-venv/model_dir) is not present; verified fail-closed gate.")

    assert audit["acceptable"] is True
    assert audit["train"]["malformed_samples"] == 0

    # Malformed record must fail closed
    malformed_dir = tmp_path / "malformed_dataset"
    malformed_dir.mkdir()
    (malformed_dir / "train.jsonl").write_text('{"messages": "not a list"}\n{"corrupt json\n', encoding="utf-8")
    (malformed_dir / "valid.jsonl").write_text("\n".join(lines[:2]), encoding="utf-8")

    malformed_audit = audit_context_truncation(malformed_dir, max_seq_length=1024)
    assert malformed_audit["acceptable"] is False
    assert malformed_audit["train"]["malformed_samples"] > 0
