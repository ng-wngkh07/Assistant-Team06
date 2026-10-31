"""Keep mocked app tests independent of a real training job on this host.

Retain the real coordinator/lock behavior inside each test's temporary directory;
never read, heal, or alter a production job's lock/state from fixture tests.
The dialogue admission lock is isolated as well, so mocked API tests do not
contend with real inference running on the host.
"""
import pytest


@pytest.fixture
def offline_worker_environment(monkeypatch):
    """Restore process-wide worker guards after in-process worker tests."""
    import os
    import socket
    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE',
                 'HF_HUB_DISABLE_TELEMETRY'):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            # Register the original absence before the worker sets the key.
            monkeypatch.setenv(name, '')
            monkeypatch.delenv(name)


@pytest.fixture
def native_context_metadata(tmp_path, monkeypatch):
    """Provide only context metadata for tests whose inference client is mocked.

    No weights, tokenizer or availability checks are supplied. Tests for missing
    or corrupt assets keep the real model boundary unless they opt in explicitly.
    """
    from app import model_resolver
    model = tmp_path / 'mock-native-context'
    model.mkdir()
    (model / 'config.json').write_text('{"max_position_embeddings":8192}', encoding='utf-8')
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', model)
    return model


@pytest.fixture(autouse=True)
def isolated_default_gpu_coordinator(tmp_path, monkeypatch):
    from app.gpu_lock import gpu_coordinator
    from app.dialogue import dialogue_gate
    runtime = tmp_path/'app-gpu-coordination'
    runtime.mkdir()
    monkeypatch.setattr(gpu_coordinator, 'lock_path', runtime/'gpu.lock')
    monkeypatch.setattr(gpu_coordinator, 'state_path', runtime/'gpu_state.json')
    monkeypatch.setattr(gpu_coordinator, 'intent_path', runtime/'gpu_intent.lock')
    monkeypatch.setattr(gpu_coordinator, 'native_lock_path', runtime/'native_worker.lock')
    monkeypatch.setattr(dialogue_gate, 'path', runtime/'dialogue.lock')


@pytest.fixture
def legacy_model_http(monkeypatch):
    """Keep explicit legacy transport tests local and inspect the real request payload.

    Model locality metadata is answered separately; no fixture can reach a daemon.
    Native Mac routing is tested independently with all HTTP calls forbidden.
    """
    import requests
    from types import SimpleNamespace
    def install(callback):
        def post(session, url, **kwargs):
            assert session.trust_env is False
            assert kwargs.get('allow_redirects') is False
            if url.endswith('/api/show'):
                return SimpleNamespace(status_code=200, json=lambda: {
                    'details': {'format': 'gguf', 'family': 'qwen'},
                    'remote_host': None, 'remote_model': None,
                })
            return callback(url, **kwargs)
        monkeypatch.setattr(requests.Session, 'post', post)
    return install


@pytest.fixture
def current_native_config():
    """Synthetic architecture metadata; it does not provide executable weights."""
    return {
        'model_type': 'qwen3_5', 'architectures': ['Qwen3_5ForConditionalGeneration'],
        'text_config': {'model_type':'qwen3_5_text','hidden_size':4096,
                        'num_hidden_layers':32,'num_attention_heads':16,
                        'max_position_embeddings':262144},
        'quantization': {'bits':4,'group_size':64,'mode':'affine'},
    }
