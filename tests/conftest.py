"""Keep mocked app tests independent of a real training job on this host.

Retain the real coordinator/lock behavior inside each test's temporary directory;
never read, heal, or alter a production job's lock/state from fixture tests.
The dialogue admission lock is isolated as well, so mocked API tests do not
contend with real inference running on the host.
"""
import pytest


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
