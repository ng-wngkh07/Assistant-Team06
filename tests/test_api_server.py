import pytest
from fastapi.testclient import TestClient
from app.server import app
from app import server
from app.history import HistoryStore

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "history.db"))
    return TestClient(app)

def test_homepage_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Tâm Lý Học AI" in response.text or "Trợ Lý" in response.text

def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "ollama_connected" in data

def test_status_endpoint(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "knowledge_base" in data
    kb = data["knowledge_base"]
    assert kb["total_documents"] == len(kb["documents"])
    assert kb["scanned_documents"] == sum(bool(d["is_scanned"]) for d in kb["documents"])

def test_crisis_chat_endpoint(client):
    response = client.post("/api/chat", json={
        "query": "Tôi muốn tự tử",
        "top_k": 3
    })
    assert response.status_code == 200
    data = response.json()
    assert data["is_crisis"] is True
    assert "115" in data["answer"]

def test_empty_query_endpoint(client):
    response = client.post("/api/chat", json={"query": "   "})
    assert response.status_code == 422


def test_chat_save_history_false_preserves_empty_history(client):
    """When save_history is False, chat_sync must NOT persist to history_store."""
    assert len(server.history_store.list()) == 0
    response = client.post("/api/chat", json={
        "query": "Tôi muốn tự tử",
        "save_history": False
    })
    assert response.status_code == 200
    assert "history_id" not in response.json()
    assert len(server.history_store.list()) == 0


def test_chat_stream_save_history_false_preserves_empty_history(client):
    """When save_history is False, chat_stream must NOT persist to history_store."""
    assert len(server.history_store.list()) == 0
    response = client.post("/api/chat/stream", json={
        "query": "Tôi muốn tự tử",
        "save_history": False
    })
    assert response.status_code == 200
    assert len(server.history_store.list()) == 0


def test_chat_save_history_default_true(client):
    """By default (save_history=True), chat_sync persists to history_store."""
    assert len(server.history_store.list()) == 0
    response = client.post("/api/chat", json={
        "query": "Tôi muốn tự tử"
    })
    assert response.status_code == 200
    assert "history_id" in response.json()
    assert len(server.history_store.list()) == 1

