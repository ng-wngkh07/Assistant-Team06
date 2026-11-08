import json
import pytest
from contextlib import nullcontext
from types import SimpleNamespace

import app.trained_client as trained_client


def test_current_base_never_loads_registry_adapter(tmp_path, monkeypatch):
    from app.active_registry import active_registry
    adapter=tmp_path/'retired-adapter'; adapter.mkdir()
    (adapter/'adapters.safetensors').write_bytes(b'old-weights')
    (adapter/'ready.json').write_text('{"approved":true}')
    monkeypatch.setattr(active_registry,'get_active_adapter_dir',lambda:adapter)
    monkeypatch.setattr(trained_client.TrainedModelClient,'available',lambda *a:True)
    monkeypatch.setattr(trained_client,'get_model_context_capacity',lambda *a:8192)
    monkeypatch.setattr(trained_client.gpu_coordinator,'acquire_for_inference',lambda:nullcontext())
    requests=[]
    def run(*args,**kwargs):
        requests.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0,stderr='',stdout=json.dumps({'answer':'fixture','done_reason':'stop'}))
    monkeypatch.setattr(trained_client.subprocess,'run',run)
    assert trained_client.TrainedModelClient().chat_complete([{'role':'user','content':'test'}])=='fixture'
    assert requests[0]['adapter_path'] is None


def test_schema_reaches_native_worker_and_missing_finish_is_not_success(monkeypatch, native_context_metadata):
    monkeypatch.setattr(trained_client.TrainedModelClient, 'available', lambda *a: True)
    monkeypatch.setattr(trained_client.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    requests = []
    def run(*args, **kwargs):
        requests.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'answer': '{}', 'is_truncated': False}))
    monkeypatch.setattr(trained_client.subprocess, 'run', run)
    schema = {'type': 'object', 'additionalProperties': False}
    client = trained_client.TrainedModelClient()
    assert client.chat_complete([], response_schema=schema) == '{}'
    assert requests[0]['response_schema'] == schema
    assert requests[0]['num_ctx'] == 8192
    assert client.last_done_reason is None


def test_trained_model_chat_stream_respects_num_predict_and_truncation(monkeypatch, native_context_metadata):
    client = trained_client.TrainedModelClient()
    monkeypatch.setattr(client, "available", lambda *args, **kwargs: True)

    captured_requests = []

    class MockCompleted:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def mock_subprocess_run(cmd, cwd=None, input=None, capture_output=True, text=True, timeout=None, check=False, **kwargs):
        req = json.loads(input)
        captured_requests.append(req)
        truncated = req.get("max_tokens") < 1000
        return MockCompleted(json.dumps({"answer": "Phản hồi thử nghiệm", "is_truncated": truncated,
                                         "done_reason": "length" if truncated else "stop"}))

    import subprocess
    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # Test with custom num_predict = 1100
    generator = client.chat_stream([{"role": "user", "content": "Xin chào"}], "System prompt", num_predict=1100)
    answer = "".join(generator)
    assert answer == "Phản hồi thử nghiệm"
    assert captured_requests[-1]["max_tokens"] == 1100
    assert client.last_done_reason == "stop"

    # Test with small num_predict = 500 (triggers mock truncation)
    generator = client.chat_stream([{"role": "user", "content": "Xin chào"}], "System prompt", num_predict=500)
    answer = "".join(generator)
    assert answer == "Phản hồi thử nghiệm"
    assert captured_requests[-1]["max_tokens"] == 500
    assert client.last_done_reason == "length"



@pytest.mark.parametrize('use_adapter', [False, True])
def test_accepted_practice_adapter_is_selected_only_for_exact_request(monkeypatch, use_adapter):
    from app import practice_adapter
    from app.practice import practice_review_schema
    monkeypatch.setattr(trained_client.TrainedModelClient, 'available', lambda *args: True)
    monkeypatch.setattr(trained_client, 'get_model_context_capacity', lambda *args: 8192)
    monkeypatch.setattr(trained_client.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    accepted = {'path': '/synthetic/accepted', 'sha256': 'fixture', 'scope': 'single_question_practice_review'}
    monkeypatch.setattr(practice_adapter, 'active_practice_adapter', lambda path: accepted if use_adapter else None)
    requests = []
    def run(*args, **kwargs):
        requests.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'answer': '{}', 'done_reason': 'stop'}))
    monkeypatch.setattr(trained_client.subprocess, 'run', run)
    item = dict(index=1, source='Synthetic source.', question='Synthetic question?', choices=dict(A='one', B='two', C='three', D='four'), proposed_answer='A', explanation='Synthetic explanation.', evidence_quote='Synthetic source.')
    client = trained_client.TrainedModelClient()
    client.chat_complete([{'role': 'user', 'content': practice_adapter.build_compact_review_prompt(item)}], system_prompt=practice_adapter.SYSTEM_PROMPT, max_tokens=1000, temperature=0, response_schema=practice_review_schema(1, None))
    assert requests[-1]['adapter_path'] == (accepted['path'] if use_adapter else None)
    assert requests[-1]['seed'] == 42
    client.chat_complete([{'role': 'user', 'content': 'Ordinary QA'}], max_tokens=1000, temperature=0)
    assert requests[-1]['adapter_path'] is None
