"""Current architecture diagnostics must never inherit retired training claims."""
import json
import struct

from app.memory_preflight import inspect_base_model, preflight_check_memory_training
from app.paired_evaluation import evaluate_paired_dossier


def test_current_nested_architecture_is_reported_without_training_authorization(tmp_path, current_native_config):
    (tmp_path / 'config.json').write_text(json.dumps(current_native_config))
    (tmp_path / 'tokenizer_config.json').write_text('{}')
    (tmp_path / 'tokenizer.json').write_text('{}')
    header = b'{"__metadata__": {}}'
    weights = struct.pack('<Q', len(header)) + header
    (tmp_path / 'model.safetensors').write_bytes(weights)
    info = inspect_base_model(tmp_path)
    assert info['model_type'] == 'Qwen3.5-9B-4bit'
    assert info['is_supported_native'] is True
    assert info['hidden_size'] == 4096 and info['num_hidden_layers'] == 32
    assert info['total_weight_mb'] == len(weights) / (1024 ** 2)
    assert preflight_check_memory_training(model_dir=tmp_path)['eligible_for_training'] is False


def test_absent_evaluation_identity_cannot_invent_an_old_repo_or_revision():
    report = evaluate_paired_dossier([], [], [])
    assert report['model_identity'] is None
    assert report['gates']['promoted'] is False
