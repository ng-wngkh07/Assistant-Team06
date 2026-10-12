"""Regression for the server-wired summary endpoint, without GPU/model calls."""
from fastapi.testclient import TestClient
from app import server
from app.history import HistoryStore


def test_server_summary_uses_configured_mlx_backend(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "history.db")
    sid = store.create_session()["id"]
    store.add_turn(sid, "Trí nhớ làm việc là gì?", "Giữ và xử lý thông tin trong thời gian ngắn.", [], "qwen2.5-3b-4bit")
    monkeypatch.setattr(server.summarizer, "history_store", store)
    monkeypatch.setattr(server, "DEFAULT_CHAT_MODEL", "qwen2.5-3b-4bit")
    monkeypatch.setattr(server.TrainedModelClient, "available", staticmethod(lambda: True))
    calls = []
    def mlx(self, **kwargs):
        calls.append(kwargs)
        self.last_done_reason = "stop"
        return '{"title":"Trí nhớ làm việc","summary":"Giữ và xử lý thông tin ngắn hạn."}'
    monkeypatch.setattr(server.TrainedModelClient, "chat_complete", mlx)
    monkeypatch.setattr(server.ollama, "chat_complete", lambda **kw: "Lỗi từ Ollama (Mã 404): model not found")
    monkeypatch.setattr(server.ollama, "check_health", lambda: True)
    monkeypatch.setattr("app.topic_summarizer.gpu_coordinator.check_inference_allowed", lambda: (True, ""))
    response = TestClient(server.app).post(f"/api/sessions/{sid}/summarize", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "completed", response.json()
    assert len(calls) == 1
    assert store.get_session(sid)["summary"] == "Giữ và xử lý thông tin ngắn hạn."
