"""Issue21: real routing, isolated stores, no retrieval/model side effects."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app import server
from app.dialogue import DialogueGate, dialogue_intent, HELP_MESSAGE
from app.history import HistoryStore
from app.rag_agent import PsychologyAgent

class NoExternalWork:
    def __getattr__(self, name):
        raise AssertionError(f"Help must not access retrieval/model/translation: {name}")

@pytest.fixture
def api(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "history.db")
    monkeypatch.setattr(server, "history_store", store)
    monkeypatch.setattr(server, "dialogue_gate", DialogueGate(tmp_path / "dialogue.lock"))
    monkeypatch.setattr(server, "gpu_coordinator", SimpleNamespace(
        check_inference_allowed=lambda: (True, ""), acquire_for_inference=nullcontext))
    monkeypatch.setattr(server, "agent", PsychologyAgent(
        searcher=NoExternalWork(), ollama=NoExternalWork(), translator=NoExternalWork()))
    return TestClient(server.app), store

@pytest.mark.parametrize("cmd", ["help", "/help", "HELP", "  help  ", "trợ giúp", "tro giup", "hướng dẫn", "bot có thể làm gì", "Các chức năng?", "help!"])
def test_exact_help(cmd):
    assert dialogue_intent(cmd, []) == (HELP_MESSAGE, cmd)

def test_help_describes_real_capabilities():
    for text in ["Tra cứu tài liệu", "Hỏi đáp học tập", "Luyện tập", "Thời khóa biểu"]:
        assert text in HELP_MESSAGE
    assert "luôn đi kèm trích dẫn" not in HELP_MESSAGE

@pytest.mark.parametrize("query", ["Help-seeking behavior in educational psychology", "Tôi cần tìm hiểu về help desk", "help me understand linear algebra", "hướng dẫn giải bài tập"])
def test_longer_query_is_not_intercepted(query):
    assert dialogue_intent(query, [])[0] != HELP_MESSAGE

@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/stream"])
def test_help_api_without_retrieval_or_model(api, route):
    client, store = api
    data = {"query": "help", "request_id": "help-once"}
    response = client.post(route, json=data)
    assert response.status_code == 200
    if route.endswith("stream"):
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert not any(e.get("type") == "error" for e in events)
        done = [e for e in events if e.get("type") == "done"]
        assert len(done) == 1
        assert "".join(e.get("content", "") for e in events if e.get("type") == "token") == HELP_MESSAGE
        result = done[0]
    else:
        result = response.json()
        assert result["answer"] == HELP_MESSAGE
    assert result["response_kind"] == "dialogue"
    assert result["citations"] == []
    assert result["generation_calls"] == 0
    assert len(store.list()) == 1
    replay = client.post("/api/chat", json=data)
    assert replay.status_code == 200 and replay.json()["cached_replay"]
    assert replay.json()["session_id"] == result["session_id"]
    assert len(store.list()) == 1

@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/stream"])
def test_help_does_not_write_when_history_disabled(api, route):
    client, store = api
    assert client.post(route, json={"query":"/help", "save_history":False}).status_code == 200
    assert store.list() == []
    assert store.list_sessions()["total"] == 0

def test_help_request_id_cannot_be_reused_for_different_query(api):
    client, store = api
    assert client.post("/api/chat", json={"query":"help", "request_id":"once"}).status_code == 200
    assert client.post("/api/chat", json={"query":"/help", "request_id":"once"}).status_code == 409
    assert len(store.list()) == 1


def test_streamlit_adapter_calls_real_help_api_and_replays_without_duplicate(api, monkeypatch):
    from ui import app as ui
    client, store = api
    def bridge(url, **kwargs):
        assert url.endswith('/api/chat')
        return client.post('/api/chat', json=kwargs['json'])
    monkeypatch.setattr(ui.requests, 'post', bridge)
    first = ui.call_chat_api('help', answer_mode='quick', processing_level='standard', request_id='ui-help')
    assert first['ok'] and first['answer'] == HELP_MESSAGE
    assert first['citations'] == [] and first['response_kind'] == 'dialogue'
    replay = ui.call_chat_api('help', answer_mode='quick', processing_level='standard', request_id='ui-help')
    assert replay['ok'] and replay['cached_replay']
    assert replay['session_id'] == first['session_id']
    assert len(store.list()) == 1
