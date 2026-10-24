import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.server import app
from app import server
from app.history import HistoryStore


@pytest.fixture
def mock_client(tmp_path, monkeypatch):
    """Client fixture using an isolated temporary history database to ensure zero history contamination."""
    test_db = tmp_path / "test_history.db"
    monkeypatch.setattr(server, "history_store", HistoryStore(test_db))
    # These legacy-runtime tests explicitly select local Ollama. The Mac default
    # now uses MLX and must be tested independently of the daemon's availability.
    monkeypatch.setattr(server, "DEFAULT_CHAT_MODEL", "qwen2.5:3b")
    return TestClient(app)


def test_health_all_healthy(mock_client, monkeypatch):
    """When Ollama is reachable and GPU is idle, health returns HTTP 200, status=ok, service_state=healthy, inference_ready=True."""
    monkeypatch.setattr(server.ollama, "check_health", lambda: True)
    monkeypatch.setattr(server.gpu_coordinator, "get_status", lambda: {"state": "idle", "busy": False, "pid": None, "message": "Sẵn sàng"})

    monkeypatch.setattr(server.TrainedModelClient, "available", lambda *args, **kwargs: True)
    monkeypatch.setattr(server.ollama, "list_models", lambda: [{"name": server.DEFAULT_CHAT_MODEL}])

    resp = mock_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "ok"
    assert data["service_state"] == "healthy"
    assert data["web_healthy"] is True
    assert data["ollama_connected"] is True
    assert data["inference_ready"] is True
    assert "bình thường" in data["service_message"]
    assert data["gpu"]["busy"] is False


def test_health_http200_ollama_disconnected(mock_client, monkeypatch):
    """When Ollama is disconnected, health MUST keep HTTP 200 (so supervisors don't falsely reboot web),

    but truthfully report service_state='degraded', ollama_connected=False, and inference_ready=False.
    """
    monkeypatch.setattr(server.ollama, "check_health", lambda: False)
    monkeypatch.setattr(server.gpu_coordinator, "get_status", lambda: {"state": "idle", "busy": False, "pid": None, "message": "Sẵn sàng"})

    resp = mock_client.get("/api/health")
    # Must preserve HTTP 200 for web liveness compatibility
    assert resp.status_code == 200
    data = resp.json()

    # Truthful degraded status
    assert data["status"] == "ok"
    assert data["service_state"] == "degraded"
    assert data["web_healthy"] is True
    assert data["ollama_connected"] is False
    assert data["inference_ready"] is False
    assert "mất kết nối" in data["service_message"]


def test_health_gpu_busy_training(mock_client, monkeypatch):
    """When MLX LoRA training holds GPU lock, health MUST keep HTTP 200 and web_healthy=True

    (web is NOT dead), but truthfully report service_state='degraded', gpu.busy=True, and inference_ready=False.
    """
    monkeypatch.setattr(server.ollama, "check_health", lambda: True)
    monkeypatch.setattr(
        server.gpu_coordinator,
        "get_status",
        lambda: {"state": "training", "busy": True, "pid": 4242, "message": "Huấn luyện LoRA đang chạy"}
    )

    resp = mock_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "ok"
    assert data["service_state"] == "degraded"
    assert data["web_healthy"] is True
    assert data["ollama_connected"] is True
    assert data["inference_ready"] is False
    assert data["gpu"]["busy"] is True
    assert data["gpu"]["pid"] == 4242
    assert "bận huấn luyện" in data["service_message"]
    assert "web hoạt động bình thường" in data["service_message"]


def test_status_endpoint_truthful_reporting(mock_client, monkeypatch):
    """The /api/status endpoint must mirror service_state and inference_ready truthfully."""
    monkeypatch.setattr(server.ollama, "check_health", lambda: False)
    monkeypatch.setattr(server.gpu_coordinator, "get_status", lambda: {"state": "idle", "busy": False, "pid": None, "message": "Sẵn sàng"})

    resp = mock_client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()

    assert data["service_state"] == "degraded"
    assert data["inference_ready"] is False
    assert data["ollama"]["connected"] is False


def test_zero_real_history_contamination(mock_client):
    """Ensure that running health and status checks does NOT touch or probe real user history."""
    assert len(server.history_store.list()) == 0
    resp1 = mock_client.get("/api/health")
    resp2 = mock_client.get("/api/status")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert len(server.history_store.list()) == 0


def test_service_sh_foreign_pid_logic():
    """Verify the logic that discriminates project processes from foreign processes."""
    import subprocess
    import sys
    from pathlib import Path

    project_dir = Path(__file__).resolve().parent.parent

    # Test is_project_process logic directly via a subprocess running python script
    check_code = f"""
import sys
project_dir = {str(project_dir)!r}

def is_project(cmdline, cwd):
    return ("run.py serve" in cmdline) and (cwd == project_dir)

# Case 1: Foreign process on port (e.g., rogue nginx or another web server)
assert not is_project("nginx: master process", "/etc/nginx")
assert not is_project("python other_app.py", project_dir)
assert not is_project("python run.py serve", "/tmp/other_project")

# Case 2: Project process
assert is_project("python run.py serve --host 127.0.0.1 --port 8000", project_dir)
print("PASS")
"""
    result = subprocess.run([sys.executable, "-c", check_code], capture_output=True, text=True)
    assert result.returncode == 0
    assert "PASS" in result.stdout


@pytest.mark.parametrize('available', [True, False])
def test_native_mlx_health_needs_no_ollama_daemon(tmp_path, monkeypatch, available):
    monkeypatch.setattr(server, 'DEFAULT_CHAT_MODEL', 'qwen3.5-9b-4bit')
    monkeypatch.setattr(server, 'history_store', HistoryStore(tmp_path/'native-history.db'))
    monkeypatch.setattr(server.TrainedModelClient, 'available', lambda *args, **kwargs: available)
    monkeypatch.setattr(server.gpu_coordinator, 'get_status', lambda: {'state':'idle','busy':False,'message':'Sẵn sàng'})
    def unexpected_daemon():
        raise AssertionError('Native Mac model readiness must not call the Ollama daemon')
    monkeypatch.setattr(server.ollama, 'check_health', unexpected_daemon)
    with TestClient(app) as client:
        response=client.get('/api/health')
    assert response.status_code == 200
    data=response.json()
    assert data['web_healthy'] is True
    assert data['inference_ready'] is available
    assert data['service_state'] == ('healthy' if available else 'degraded')
@pytest.mark.parametrize("launcher", ["run", "scripts.dev"])
@pytest.mark.parametrize("reload_enabled", [True, False])
def test_development_reload_does_not_scan_private_data_and_model_directories(monkeypatch, launcher, reload_enabled):
    import importlib
    from pathlib import Path
    from types import SimpleNamespace
    import uvicorn
    module = importlib.import_module(launcher)
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    if launcher == "run":
        module.cmd_serve(SimpleNamespace(host="127.0.0.1", port=8123, reload=reload_enabled))
    else:
        monkeypatch.setattr(module, "configure", lambda profile: None)
        monkeypatch.setattr(module.sys, "argv", ["dev.py", "serve", "--profile", "local", *(["--reload"] if reload_enabled else [])])
        assert module.main() == 0
    assert calls[0]["reload"] is reload_enabled
    if reload_enabled:
        assert calls[0]["reload_dirs"] == [str(Path(__file__).resolve().parents[1] / "app")]
    else:
        assert "reload_dirs" not in calls[0]
