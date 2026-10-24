"""The project must never send inference requests to an external model endpoint."""

import pytest

from app.ollama_client import OllamaClient


@pytest.mark.parametrize("endpoint", [
    "https://ollama.com", "https://api.openai.com", "http://192.168.1.20:11434",
    "http://localhost.evil.example:11434", "http://127.0.0.1.evil.example:11434",
    "http://user:password@localhost:11434", "file:///tmp/model",
])
def test_external_or_ambiguous_model_endpoint_is_rejected_before_any_request(endpoint):
    with pytest.raises(ValueError):
        OllamaClient(base_url=endpoint)


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434",
])
def test_explicit_loopback_endpoint_can_be_constructed_without_network(endpoint):
    client = OllamaClient(base_url=endpoint)
    assert client.base_url == endpoint


def test_loopback_client_disables_environment_proxy_and_does_not_follow_redirects(monkeypatch):
    client = OllamaClient(base_url="http://127.0.0.1:11434")
    assert client.session.trust_env is False
    calls = []
    class Redirect:
        status_code = 302
        text = "redirect rejected"
        headers = {"Location": "https://external-model.example/api/tags"}
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Redirect()
    monkeypatch.setattr(client.session, "get", get)
    assert client.check_health() is False
    assert client.list_models() == []
    assert len(calls) == 2
    assert all(call[1].get("allow_redirects") is False for call in calls)


@pytest.mark.parametrize("operation", ["chat", "embedding", "batch_embedding"])
def test_remote_model_disguised_as_local_alias_never_receives_user_content(monkeypatch, operation):
    """A loopback daemon can still route a custom alias to a cloud model."""
    client = OllamaClient(base_url="http://127.0.0.1:11434")
    monkeypatch.setattr(client, "find_best_chat_model", lambda model: "team-local-alias")
    calls = []
    class Metadata:
        status_code = 200
        text = ""
        def json(self):
            return {"remote_host": "https://ollama.com", "remote_model": "cloud-model", "details": {"family": "qwen"}}
        def raise_for_status(self):
            pass
    def post(url, **kwargs):
        calls.append((url, kwargs))
        # Metadata inspection is safe; inference must be rejected first.
        assert url.endswith("/api/show"), "User content reached inference before model locality was verified"
        assert kwargs.get("allow_redirects") is False
        return Metadata()
    monkeypatch.setattr(client.session, "post", post)
    with pytest.raises(ValueError, match="cloud|remote|cục bộ|từ xa"):
        if operation == "chat":
            list(client.chat_stream(messages=[{"role": "user", "content": "PRIVATE_TEST_CONTENT"}], model="team-local-alias"))
        elif operation == "embedding":
            client.get_embedding("PRIVATE_TEST_CONTENT", model="team-local-alias")
        else:
            client.get_batch_embeddings(["PRIVATE_TEST_CONTENT"], model="team-local-alias")
    assert all("PRIVATE_TEST_CONTENT" not in str(kwargs.get("json")) for _, kwargs in calls)


@pytest.mark.parametrize('metadata_state', ['unavailable', 'redirect', 'invalid_json', 'empty'])
@pytest.mark.parametrize('operation', ['chat', 'embedding', 'batch_embedding'])
def test_unverifiable_local_alias_fails_before_any_user_content(monkeypatch, metadata_state, operation):
    client = OllamaClient(base_url='http://127.0.0.1:11434')
    monkeypatch.setattr(client, 'find_best_chat_model', lambda model: 'team-local-alias')
    calls = []

    class Metadata:
        status_code = 302 if metadata_state == 'redirect' else 503 if metadata_state == 'unavailable' else 200
        def json(self):
            if metadata_state == 'invalid_json':
                raise ValueError('invalid JSON')
            return {}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        assert url.endswith('/api/show'), 'Unverified alias received private user content'
        return Metadata()

    monkeypatch.setattr(client.session, 'post', post)
    with pytest.raises((ValueError, RuntimeError)):
        if operation == 'chat':
            list(client.chat_stream(messages=[{'role': 'user', 'content': 'PRIVATE_TEST_CONTENT'}], model='team-local-alias'))
        elif operation == 'embedding':
            client.get_embedding('PRIVATE_TEST_CONTENT', model='team-local-alias')
        else:
            client.get_batch_embeddings(['PRIVATE_TEST_CONTENT'], model='team-local-alias')
    assert calls
    assert all('PRIVATE_TEST_CONTENT' not in str(kwargs.get('json')) for _, kwargs in calls)


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='Native Mac deployment')
def test_instrumenting_http_never_changes_native_timetable_backend(tmp_path, monkeypatch):
    import hashlib
    import json
    from pathlib import Path
    from app import timetable_vision as vision, timetable_table
    from app.native_vision import NativeVisionClient
    monkeypatch.setenv('TIMETABLE_DB_PATH', str(tmp_path/'calendar.db'))
    monkeypatch.setattr(timetable_table, 'extract_registered_table', lambda *a, **kw: None)
    monkeypatch.setattr(vision, 'get_vlm_digest', lambda: 'native-fixture')
    monkeypatch.setattr(vision.gpu_coordinator, 'check_inference_allowed', lambda: (True,''))
    monkeypatch.setattr(NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))
    calls=[]
    output={'entries':[{'course':'Entropy','weekday':1,'weekday_label':'Thứ 2','start_time':'08:00','end_time':'09:00','room':'A1'}],'uncertainties':[]}
    def native(self, *args, **kwargs):
        calls.append(kwargs)
        return {'text':json.dumps(output), 'finish_reason':'stop'}
    monkeypatch.setattr(NativeVisionClient, 'generate_from_image', native)
    def forbidden(*args, **kwargs):
        raise AssertionError('Mac timetable must not call any model HTTP service')
    import requests
    monkeypatch.setattr(requests.Session, 'post', forbidden)
    monkeypatch.setattr(requests, 'post', forbidden)
    image=(Path(__file__).parent/'fixtures/timetables/multisubject.png').read_bytes()
    result=vision.call_vlm_extract(image,hashlib.sha256(image).hexdigest(),'fixture.png')
    assert calls and result['entries'][0]['course']=='Entropy'


def test_legacy_ocr_verifies_the_actual_requested_model(monkeypatch):
    import requests
    from pathlib import Path
    from types import SimpleNamespace
    from app.vision_ocr import VisionOCRManager
    calls = []
    def post(session, url, **kwargs):
        calls.append((url, kwargs['json']))
        assert url.endswith('/api/show'), 'Unverified OCR model must never receive image content'
        return SimpleNamespace(status_code=200, json=lambda: {
            'details': {'format': 'gguf', 'family': 'qwen'},
            'remote_host': 'https://remote.invalid', 'remote_model': 'hosted-model',
        })
    monkeypatch.setattr(requests.Session, 'post', post)
    image = (Path(__file__).parent/'fixtures/timetables/demo.png').read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        VisionOCRManager(vision_model='team-ocr-alias')._ocr_ollama_vlm(image)
    assert len(calls) == 1
    assert calls[0][1]['name'] == 'team-ocr-alias'
