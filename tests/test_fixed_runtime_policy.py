"""Public requests cannot switch inference/vector policy after UI settings removal."""
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from app import server
from app.config import DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL, DEFAULT_TOP_K
from app.history import HistoryStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    old = server._history_store
    store = HistoryStore(tmp_path / 'history.db')
    server.set_history_store(store)
    monkeypatch.setattr(server, 'gpu_coordinator', SimpleNamespace(
        check_inference_allowed=lambda: (True, ''), acquire_for_inference=lambda: nullcontext()))
    monkeypatch.setattr(server, 'dialogue_gate', SimpleNamespace(acquire=lambda: nullcontext()))
    with TestClient(server.app) as api:
        yield api, store
    server._history_store = old
    server.summarizer.history_store = server.history_store


@pytest.mark.parametrize('overrides', [
    {'model': 'unexpected-7b', 'embed_model': 'nomic-embed-text', 'top_k': 12},
    {'model': None, 'embed_model': None, 'top_k': 2},
])
@pytest.mark.parametrize('endpoint', ['/api/chat', '/api/chat/stream'])
def test_both_chat_routes_use_server_policy(client, monkeypatch, endpoint, overrides):
    calls = []
    def sync(**kwargs):
        calls.append(kwargs)
        return {'answer': 'Đã trả lời [S1].', 'citations': []}
    def stream(**kwargs):
        calls.append(kwargs)
        yield {'type': 'token', 'content': 'Đã trả lời [S1].'}
        yield {'type': 'done', 'citations': []}
    monkeypatch.setattr(server, 'agent', SimpleNamespace(process_query_sync=sync, process_query_stream=stream))
    api, _ = client
    response = api.post(endpoint, json={'query': 'Kiểm tra chính sách.', 'save_history': False, **overrides})
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]['model'] == DEFAULT_CHAT_MODEL
    assert calls[0]['embed_model'] == DEFAULT_EMBED_MODEL
    assert calls[0]['top_k'] == DEFAULT_TOP_K
    if endpoint == '/api/chat':
        assert response.json()['model'] == DEFAULT_CHAT_MODEL


def test_practice_uses_fixed_model(client, monkeypatch):
    calls = []
    monkeypatch.setattr(server.document_lookup, 'document', lambda _: {'total_pages': 5})
    monkeypatch.setattr(server.practice_service, 'generate', lambda **kw: calls.append(kw) or {'questions': []})
    api, _ = client
    response = api.post('/api/practice/generate', json={'source_document_id': 1, 'source_page_num': 1, 'model': 'other'})
    assert response.status_code == 200
    assert calls[0]['model'] == DEFAULT_CHAT_MODEL


def test_summary_uses_fixed_model(client, monkeypatch):
    calls = []
    api, store = client
    session = store.create_session()
    monkeypatch.setattr(server.summarizer, 'summarize_session_sync', lambda **kw: calls.append(kw) or {'status': 'ok'})
    response = api.post(f"/api/sessions/{session['id']}/summarize", json={'model': 'other'})
    assert response.status_code == 200, response.text
    assert calls[0]['model'] == DEFAULT_CHAT_MODEL


def test_index_uses_same_embedding_policy_as_queries(client, monkeypatch):
    calls = []
    monkeypatch.setattr(server.indexer, 'index_all', lambda **kw: calls.append(kw) or {})
    api, _ = client
    response = api.post('/api/index', json={'force': False, 'ocr': False, 'embed_model': 'nomic-embed-text'})
    assert response.status_code == 200
    assert calls[0]['embed_model'] == DEFAULT_EMBED_MODEL


def test_ui_no_longer_requires_model_or_topk_controls():
    root = Path(__file__).resolve().parents[1]
    html = (root / 'static/index.html').read_text()
    script = (root / 'static/app.js').read_text()
    for name in ('select-model', 'select-embed', 'input-topk', 'topk-val'):
        assert f'id="{name}"' not in html
        assert f'getElementById("{name}")' not in script
    for cls in (server.ChatRequest, server.PracticeGenerateRequest, server.SessionSummarizeRequest, server.IndexRequest):
        assert {'model', 'embed_model', 'top_k'}.isdisjoint(cls.model_fields)


def test_chat_model_resolution_never_substitutes_a_different_model(monkeypatch):
    from app.ollama_client import OllamaClient
    client = OllamaClient()
    monkeypatch.setattr(client, 'get_available_model_names', lambda: ['other-local:latest', 'qwen3.5:9b-custom'])
    assert client.find_best_chat_model('qwen3.5:9b') == 'qwen3.5:9b'


def test_embedding_resolution_does_not_fall_back_to_a_different_vector_space(monkeypatch):
    from app.ollama_client import OllamaClient
    client = OllamaClient()
    monkeypatch.setattr(client, 'get_available_model_names', lambda: ['nomic-embed-text:latest', 'bge-m3-custom:latest'])
    assert client.find_best_embed_model('bge-m3') is None


def test_exact_default_tag_aliases_are_allowed(monkeypatch):
    from app.ollama_client import OllamaClient
    client = OllamaClient()
    monkeypatch.setattr(client, 'get_available_model_names', lambda: ['bge-m3:latest', 'qwen3.5:9b'])
    assert client.find_best_embed_model('bge-m3') == 'bge-m3:latest'
    assert client.find_best_chat_model('qwen3.5:9b') == 'qwen3.5:9b'


@pytest.mark.parametrize('endpoint', ['/api/chat', '/api/chat/stream'])
def test_persisted_turn_records_the_fixed_model(client, monkeypatch, endpoint):
    def sync(**kwargs):
        return {'answer': 'Nội dung kiểm thử [S1].', 'citations': []}
    def stream(**kwargs):
        yield {'type': 'token', 'content': 'Nội dung kiểm thử [S1].'}
        yield {'type': 'done', 'citations': []}
    monkeypatch.setattr(server, 'agent', SimpleNamespace(process_query_sync=sync, process_query_stream=stream))
    api, _ = client
    response = api.post(endpoint, json={'query': 'Kiểm tra lưu mô hình.', 'model': 'other', 'save_history': True})
    assert response.status_code == 200
    turns = api.get('/api/history').json()['items']
    assert len(turns) == 1
    assert turns[0]['model'] == DEFAULT_CHAT_MODEL
