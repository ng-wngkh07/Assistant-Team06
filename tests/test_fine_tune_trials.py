import hashlib
import json
from pathlib import Path

import pytest
import app.fine_tune as fine_tune


def test_new_dataset_version_requires_matching_approval(tmp_path):
    data_dir = tmp_path / "data" / "training" / "v2"
    data_dir.mkdir(parents=True)
    train = data_dir / "train.jsonl"
    valid = data_dir / "valid.jsonl"
    train.write_text("{}\n" * 20)
    valid.write_text("{}\n" * 2)
    with pytest.raises(ValueError, match="approval.json"):
        fine_tune.train(data_dir)
    (data_dir / "approval.json").write_text(json.dumps({"dataset_sha256": "stale"}))
    with pytest.raises(ValueError, match="thay đổi"):
        fine_tune.train(data_dir)


def test_retired_training_protocol_never_spawns_or_modifies_active_adapter(tmp_path, monkeypatch):
    import subprocess
    data_dir = tmp_path/'training'; data_dir.mkdir()
    (data_dir/'train.jsonl').write_text('{}\n'*20)
    (data_dir/'valid.jsonl').write_text('{}\n'*2)
    digest=hashlib.sha256((data_dir/'train.jsonl').read_bytes()+(data_dir/'valid.jsonl').read_bytes()).hexdigest()
    (data_dir/'approval.json').write_text(json.dumps({'dataset_sha256':digest}))
    active=tmp_path/'active';active.mkdir()
    (active/'ready.json').write_text('previous adapter')
    monkeypatch.setattr(fine_tune,'BASE_DIR',tmp_path)
    def forbidden(*a,**kw):
        pytest.fail('Retired training must never start a model process')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    with pytest.raises(RuntimeError,match='9B'):
        fine_tune.train(data_dir,adapter_dir=tmp_path/'trial',evict_ollama=False)
    assert (active/'ready.json').read_text()=='previous adapter'
    assert not (tmp_path/'trial').exists()


def test_training_rejects_cross_book_data_entirely_removed_by_holdout(tmp_path):
    data_dir = tmp_path / "v1"
    data_dir.mkdir()
    train_file = data_dir / "train.jsonl"
    valid_file = data_dir / "valid.jsonl"
    train_file.write_text("{}\n" * 8)
    valid_file.write_text("{}\n" * 2)
    digest = hashlib.sha256(train_file.read_bytes() + valid_file.read_bytes()).hexdigest()
    (data_dir / "approval.json").write_text(json.dumps({"dataset_sha256": digest}))
    (data_dir / "summary.json").write_text(json.dumps({
        "cross_book_pairs": 7,
        "skipped_cross_book_pairs_for_holdout": 7,
    }))

    with pytest.raises(ValueError, match="Tất cả ví dụ liên sách đã bị loại"):
        fine_tune.train(data_dir)


def test_matching_chat_hash_cannot_authorize_incomplete_source_audit(tmp_path):
    data_dir = tmp_path / "v5"
    data_dir.mkdir()
    (data_dir/"train.jsonl").write_bytes(b"{}\n" * 20)
    (data_dir/"valid.jsonl").write_bytes(b"{}\n" * 2)
    digest = hashlib.sha256((data_dir/"train.jsonl").read_bytes() + (data_dir/"valid.jsonl").read_bytes()).hexdigest()
    (data_dir/"approval.json").write_text(json.dumps({"dataset_sha256": digest}))
    (data_dir/"summary.json").write_text(json.dumps({"audit_complete": False}))
    with pytest.raises(ValueError, match="Chưa kiểm tra xong"):
        fine_tune.train(data_dir)


def test_manifest_tampering_invalidates_training_approval(tmp_path):
    data_dir = tmp_path/"v5"
    data_dir.mkdir()
    (data_dir/"train.jsonl").write_bytes(b"{}\n" * 20)
    (data_dir/"valid.jsonl").write_bytes(b"{}\n" * 2)
    digest = hashlib.sha256((data_dir/"train.jsonl").read_bytes() + (data_dir/"valid.jsonl").read_bytes()).hexdigest()
    (data_dir/"approved_manifest.jsonl").write_text("modified sources")
    (data_dir/"approval.json").write_text(json.dumps({"dataset_sha256": digest, "approved_manifest_sha256": "previous"}))
    with pytest.raises(ValueError, match="Manifest"):
        fine_tune.train(data_dir)
