import hashlib
import json

import app.trained_client as trained_client


def test_available_model_rejects_modified_source_manifest(tmp_path, monkeypatch):
    model, adapter = tmp_path/"model", tmp_path/"adapter"
    model.mkdir(); adapter.mkdir()
    (model/"config.json").write_text("{}")
    (adapter/"adapters.safetensors").write_bytes(b"fixture")
    training = tmp_path/"data"/"training"/"v5"
    training.mkdir(parents=True)
    (training/"train.jsonl").write_bytes(b"train\n")
    (training/"valid.jsonl").write_bytes(b"valid\n")
    manifest = training/"approved_manifest.jsonl"
    manifest.write_bytes(b"approved sources\n")
    data_hash = hashlib.sha256(b"train\nvalid\n").hexdigest()
    (training/"approval.json").write_text(json.dumps({"dataset_sha256": data_hash, "approved_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}))
    (adapter/"ready.json").write_text(json.dumps({"dataset_sha256": data_hash, "data_version": "v5"}))
    monkeypatch.setattr(trained_client, "BASE_DIR", tmp_path)
    monkeypatch.setattr(trained_client, "MLX_MODEL_DIR", model)
    monkeypatch.setattr(trained_client, "MLX_ADAPTER_DIR", adapter)
    assert trained_client.TrainedModelClient._verify_legacy_adapter(adapter)
    manifest.write_bytes(b"tampered sources\n")
    assert not trained_client.TrainedModelClient._verify_legacy_adapter(adapter)


def test_trained_model_only_available_for_approved_dataset(tmp_path, monkeypatch):
    model = tmp_path / "model"
    adapter = tmp_path / "adapter"
    training = tmp_path / "data" / "training" / "v1"
    for directory in (model, adapter, training):
        directory.mkdir(parents=True)
    (model / "config.json").write_text("{}")
    (adapter / "adapters.safetensors").write_bytes(b"adapter")
    train = training / "train.jsonl"
    valid = training / "valid.jsonl"
    train.write_text("example one\n")
    valid.write_text("example two\n")
    digest = hashlib.sha256(train.read_bytes() + valid.read_bytes()).hexdigest()
    (training / "approval.json").write_text(json.dumps({"dataset_sha256": digest}))
    (adapter / "ready.json").write_text(json.dumps({"dataset_sha256": digest}))
    monkeypatch.setattr(trained_client, "BASE_DIR", tmp_path)
    monkeypatch.setattr(trained_client, "MLX_MODEL_DIR", model)
    monkeypatch.setattr(trained_client, "MLX_ADAPTER_DIR", adapter)

    assert trained_client.TrainedModelClient._verify_legacy_adapter(adapter)
    train.write_text("modified\n")
    assert not trained_client.TrainedModelClient._verify_legacy_adapter(adapter)


def test_trained_model_uses_approved_data_version(tmp_path, monkeypatch):
    model = tmp_path / "model"
    adapter = tmp_path / "adapter"
    training = tmp_path / "data" / "training" / "v2"
    for directory in (model, adapter, training):
        directory.mkdir(parents=True)
    (model / "config.json").write_text("{}")
    (adapter / "adapters.safetensors").write_bytes(b"adapter")
    train = training / "train.jsonl"
    valid = training / "valid.jsonl"
    train.write_text("train\n")
    valid.write_text("valid\n")
    digest = hashlib.sha256(train.read_bytes() + valid.read_bytes()).hexdigest()
    (training / "approval.json").write_text(json.dumps({"dataset_sha256": digest}))
    ready = adapter / "ready.json"
    ready.write_text(json.dumps({"dataset_sha256": digest, "data_version": "v2"}))
    monkeypatch.setattr(trained_client, "BASE_DIR", tmp_path)
    monkeypatch.setattr(trained_client, "MLX_MODEL_DIR", model)
    monkeypatch.setattr(trained_client, "MLX_ADAPTER_DIR", adapter)
    assert trained_client.TrainedModelClient._verify_legacy_adapter(adapter)
    ready.write_text(json.dumps({"dataset_sha256": digest, "data_version": "../v2"}))
    assert not trained_client.TrainedModelClient._verify_legacy_adapter(adapter)


def test_trained_model_chat_stream_respects_num_predict_and_truncation(monkeypatch):
    client = trained_client.TrainedModelClient()
    monkeypatch.setattr(client, "available", lambda *args, **kwargs: True)

    captured_requests = []

    class MockCompleted:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def mock_subprocess_run(cmd, cwd=None, input=None, capture_output=True, text=True, timeout=None, check=False, **kwargs):
        req = json.loads(input)
        captured_requests.append(req)
        return MockCompleted(json.dumps({"answer": "Phản hồi thử nghiệm", "is_truncated": req.get("max_tokens") < 1000}))

    import subprocess
    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # Test with custom num_predict = 1100
    generator = client.chat_stream([{"role": "user", "content": "Xin chào"}], "System prompt", num_predict=1100)
    answer = "".join(generator)
    assert answer == "Phản hồi thử nghiệm"
    assert captured_requests[-1]["max_tokens"] == 1100
    assert client.last_done_reason == "stop"

    # Test with small num_predict = 500 (triggers mock truncation)
    generator = client.chat_stream([{"role": "user", "content": "Xin chào"}], "System prompt", num_predict=500)
    answer = "".join(generator)
    assert answer == "Phản hồi thử nghiệm"
    assert captured_requests[-1]["max_tokens"] == 500
    assert client.last_done_reason == "length"

