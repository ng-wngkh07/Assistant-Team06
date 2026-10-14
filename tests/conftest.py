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
    monkeypatch.setattr(dialogue_gate, 'path', runtime/'dialogue.lock')
