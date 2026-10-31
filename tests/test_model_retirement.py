"""Product migration boundaries; synthetic artifacts, no model inference."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("name", ["qwen2.5-3b-4bit", "mlx-3b", "tam-ly-mlx", "qwen2.5:3b", "qwen2.5:7b", "qwen2.5vl:3b"])
def test_retired_models_fail_before_native_resolution_or_http(name, monkeypatch):
    from app.model_resolver import resolve_mlx_model_dir
    from app.ollama_client import OllamaClient
    with pytest.raises(ValueError, match="retired|ngừng"):
        resolve_mlx_model_dir(name)
    client = OllamaClient()
    monkeypatch.setattr(client.session, 'post', lambda *a, **k: pytest.fail('retired model reached HTTP'))
    with pytest.raises(ValueError, match="retired|ngừng"):
        client._verify_model_locality(name)


@pytest.mark.parametrize('platform,expected', [('darwin','qwen3.5-9b-4bit'),('win32','qwen3.5:9b'),('linux','qwen3.5:9b')])
def test_all_platform_defaults_use_new_family(tmp_path, platform, expected):
    env = dict(os.environ, AGENT_DATA_DIR=str(tmp_path))
    for key in ('DEFAULT_CHAT_MODEL','TRAINED_MODEL_NAME','OLLAMA_AUX_CHAT_MODEL'):
        env.pop(key,None)
    code = 'import sys; sys.platform='+repr(platform)+'; from app.config import DEFAULT_CHAT_MODEL, TRAINED_MODEL_NAME, OLLAMA_AUX_CHAT_MODEL; print(DEFAULT_CHAT_MODEL,TRAINED_MODEL_NAME,OLLAMA_AUX_CHAT_MODEL)'
    result = subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,check=True)
    assert result.stdout.split() == [expected,expected,expected]


def test_selected_9b_rejects_old_architecture_before_availability(tmp_path, monkeypatch):
    from app import trained_client
    runtime=tmp_path/'.train-venv/bin/python'
    runtime.parent.mkdir(parents=True); runtime.write_text('fixture'); runtime.chmod(0o700)
    artifact=tmp_path/'misnamed-9b'; artifact.mkdir()
    for name in ('tokenizer.json','model.safetensors'):
        (artifact/name).write_text('synthetic-file-presence')
    (artifact/'config.json').write_text(json.dumps({'model_type':'qwen2','hidden_size':2048,'num_hidden_layers':36}))
    monkeypatch.setattr(trained_client,'BASE_DIR',tmp_path)
    monkeypatch.setattr(trained_client,'sys',SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(trained_client,'resolve_mlx_model_dir',lambda *a:artifact)
    assert trained_client.TrainedModelClient.available('qwen3.5-9b-4bit') is False


def test_missing_preferred_chat_never_selects_retired_or_unrelated_model(monkeypatch):
    from app.ollama_client import OllamaClient
    from app.config import DEFAULT_CHAT_MODEL
    client=OllamaClient()
    monkeypatch.setattr(client,'get_available_model_names',lambda:['qwen2.5:3b','unrelated:latest'])
    assert client.find_best_chat_model() == DEFAULT_CHAT_MODEL


def test_current_architecture_remains_available_without_any_adapter(tmp_path, monkeypatch, current_native_config):
    from app import trained_client
    runtime=tmp_path/'.train-venv/bin/python'
    runtime.parent.mkdir(parents=True); runtime.write_text('fixture'); runtime.chmod(0o700)
    model=tmp_path/'current-base'; model.mkdir()
    (model/'config.json').write_text(json.dumps(current_native_config))
    (model/'tokenizer.json').write_text('{}')
    (model/'model.safetensors').write_bytes(b'synthetic-presence-only')
    monkeypatch.setattr(trained_client,'BASE_DIR',tmp_path)
    monkeypatch.setattr(trained_client,'sys',SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(trained_client,'resolve_mlx_model_dir',lambda *a:model)
    assert trained_client.TrainedModelClient.available('qwen3.5-9b-4bit') is True


def test_native_backend_is_unavailable_off_mac_even_with_runtime_stub(tmp_path, monkeypatch):
    from app import trained_client
    monkeypatch.setattr(trained_client,'sys',SimpleNamespace(platform='linux'))
    monkeypatch.setattr(trained_client,'resolve_mlx_model_dir',lambda *a:pytest.fail('native resolution on unsupported platform'))
    assert trained_client.TrainedModelClient.available() is False
