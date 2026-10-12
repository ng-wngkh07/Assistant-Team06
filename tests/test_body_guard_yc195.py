"""Reject excessive request bytes before JSON parsing or model dispatch."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import server


@pytest.mark.parametrize('route', ['/api/chat', '/api/chat/stream'])
@pytest.mark.parametrize('length_header', [None, '1'])
def test_body_limit_also_checks_actual_received_bytes(monkeypatch, route, length_header):
    calls = []
    monkeypatch.setattr(server, '_chat_sync_locked', lambda req: calls.append(req) or {'answer': 'sent'})
    # A small valid question plus padding: body limit must precede validation of fields.
    payload = json.dumps({'query': 'Trí nhớ là gì?', 'padding': 'x' * (129 * 1024)})
    headers = {'Content-Type': 'application/json'}
    if length_header is not None:
        headers['Content-Length'] = length_header
    response = TestClient(server.app, raise_server_exceptions=False).post(route, content=payload, headers=headers)
    assert response.status_code == 413
    assert not calls


def test_chunked_body_without_content_length_is_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(server, '_chat_sync_locked', lambda req: calls.append(req) or {'answer': 'sent'})

    async def exercise():
        async def chunks():
            yield b'{"query":"valid", "padding":"'
            for _ in range(33):
                yield b'x' * 4096
            yield b'"}'

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app, raise_app_exceptions=False), base_url='http://test') as client:
            response = await client.post('/api/chat', content=chunks(), headers={'Content-Type': 'application/json'})
            assert response.status_code == 413
    asyncio.run(exercise())
    assert not calls


@pytest.mark.parametrize('query', [None, [], {'content': 'x'}, 123, True])
def test_query_type_errors_do_not_turn_into_server_errors(monkeypatch, query):
    calls = []
    monkeypatch.setattr(server, '_chat_sync_locked', lambda req: calls.append(req) or {'answer': 'sent'})
    response = TestClient(server.app, raise_server_exceptions=False).post('/api/chat', json={'query': query})
    assert response.status_code == 422
    assert not calls


def test_malformed_json_is_a_client_error_and_does_not_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(server, '_chat_sync_locked', lambda req: calls.append(req) or {'answer': 'sent'})
    response = TestClient(server.app, raise_server_exceptions=False).post('/api/chat', content=b'{"query":', headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert not calls
