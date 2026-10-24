"""Explicit inference controls must reach the runtime without exposing reasoning."""
import json

import pytest

from app.ollama_client import OllamaClient


class Response:
    status_code = 200

    def iter_lines(self):
        for chunk in (
            {"message": {"thinking": "private scratch work"}},
            {"message": {"content": "42"}},
            {"done": True, "done_reason": "stop", "eval_count": 7,
             "eval_duration": 1_000_000_000, "prompt_eval_count": 12},
        ):
            yield json.dumps(chunk).encode()

    def json(self):
        return {"message": {"content": "42", "thinking": "private scratch work"}, "done_reason": "stop"}


def local_client(monkeypatch, inference_response):
    """Intercept the real per-client session; no network or proxy is used."""
    client = OllamaClient()
    client._available_models = ['qwen3:4b', 'qwen3.5:9b']
    class LocalMetadata(Response):
        def json(self):
            return {'details':{'format':'gguf','family':'qwen'}, 'model_info':{},
                    'remote_host':None, 'remote_model':None}
    def post(url, **kwargs):
        assert kwargs.get('allow_redirects') is False
        return LocalMetadata() if url.endswith('/api/show') else inference_response(**kwargs)
    monkeypatch.setattr(client.session, 'post', post)
    return client


@pytest.mark.parametrize("method", ["chat_stream", "chat_complete", "chat_sync"])
def test_explicit_thinking_and_seed_reach_each_chat_api(monkeypatch, method):
    sent = []
    client = local_client(monkeypatch, lambda **kw: sent.append(kw['json']) or Response())
    result = getattr(client, method)([], model="qwen3:4b", think=True, seed=42)
    if method == "chat_stream":
        result = "".join(result)
    assert sent[0]["think"] is True
    assert sent[0]["options"]["seed"] == 42
    assert "private scratch work" not in str(result)


def test_default_stays_non_thinking_without_invented_seed(monkeypatch):
    sent = []
    client = local_client(monkeypatch, lambda **kw: sent.append(kw['json']) or Response())
    assert "".join(client.chat_stream([], model="qwen3.5:9b")) == "42"
    assert sent[0]["think"] is False
    assert "seed" not in sent[0]["options"]


def test_stream_records_runtime_usage_and_resets_between_requests(monkeypatch):
    client = local_client(monkeypatch, lambda **kw: Response())
    list(client.chat_stream([], model="qwen3.5:9b"))
    assert client.last_usage["eval_count"] == 7
    assert client.last_usage["eval_duration"] == 1_000_000_000
    assert "thinking" not in client.last_usage
    class Failed(Response):
        status_code = 503
        text = "busy"
    monkeypatch.setattr(client.session, 'post', lambda *a, **kw: Failed())
    list(client.chat_stream([], model="qwen3.5:9b"))
    assert client.last_usage == {}
