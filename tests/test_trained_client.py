import json
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


def test_schema_reaches_native_worker_and_missing_finish_is_not_success(monkeypatch):
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
    assert client.last_done_reason is None


def test_trained_model_chat_stream_respects_num_predict_and_truncation(monkeypatch):
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

