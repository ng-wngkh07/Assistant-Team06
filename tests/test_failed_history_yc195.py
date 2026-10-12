"""Generation failures must not become durable conversation evidence."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import server
from app.history import HistoryStore


@pytest.mark.parametrize('kind', ['generation_failed', 'technical_error'])
@pytest.mark.parametrize('stream', [False, True])
def test_failed_generation_does_not_create_a_session_or_turn(tmp_path, monkeypatch, kind, stream):
    store = HistoryStore(tmp_path / 'history.db')
    monkeypatch.setattr(server, 'history_store', store)
    monkeypatch.setattr(server, 'gpu_coordinator', SimpleNamespace(check_inference_allowed=lambda: (True, ''), acquire_for_inference=lambda: nullcontext()))
    result = {'answer': 'Chưa xác minh được câu trả lời.', 'citations': [], 'response_kind': kind, 'is_truncated': False}
    monkeypatch.setattr(server.agent, 'process_query_sync', lambda **kwargs: result.copy())

    def events(**kwargs):
        yield {'type': 'token', 'content': result['answer']}
        yield {'type': 'done', **result}
    monkeypatch.setattr(server.agent, 'process_query_stream', events)
    response = TestClient(server.app).post('/api/chat/stream' if stream else '/api/chat', json={'query': 'Giải thích trí nhớ', 'save_history': True})
    assert response.status_code == 200
    assert not store.list()
    # This API returns pagination metadata even for zero items; include empty
    # sessions so a failed request cannot leave an invisible orphan session.
    assert store.list_sessions(filter_empty=False)['total'] == 0
