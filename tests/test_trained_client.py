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
    assert trained_client.TrainedModelClient().chat_complete([{'role':'user','content':'test'}])=='fixture'  # nosec B101
    assert requests[0]['adapter_path'] is None  # nosec B101


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
    assert client.chat_complete([], response_schema=schema) == '{}'  # nosec B101
    assert requests[0]['response_schema'] == schema  # nosec B101
    assert requests[0]['num_ctx'] == 8192  # nosec B101
    assert client.last_done_reason is None  # nosec B101


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

    import subprocess  # nosec B404
    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # Test with custom num_predict = 1100
    generator = client.chat_stream([{"role": "user", "content": "Xin chào"}], "System prompt", num_predict=1100)
    answer = "".join(generator)
    assert answer == "Phản hồi thử nghiệm"  # nosec B101
    assert captured_requests[-1]["max_tokens"] == 1100  # nosec B101
    assert client.last_done_reason == "stop"  # nosec B101

    # Test with small num_predict = 500 (triggers mock truncation)
    generator = client.chat_stream([{"role": "user", "content": "Xin chào"}], "System prompt", num_predict=500)
    answer = "".join(generator)
    assert answer == "Phản hồi thử nghiệm"  # nosec B101
    assert captured_requests[-1]["max_tokens"] == 500  # nosec B101
    assert client.last_done_reason == "length"  # nosec B101



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
    assert requests[-1]['adapter_path'] == (accepted['path'] if use_adapter else None)  # nosec B101
    assert requests[-1]['seed'] == 42  # nosec B101
    client.chat_complete([{'role': 'user', 'content': 'Ordinary QA'}], max_tokens=1000, temperature=0)
    assert requests[-1]['adapter_path'] is None  # nosec B101


def test_grounded_review_v2_profile_defaults_to_base_and_sets_diagnostics(monkeypatch):
    from app import practice_adapter
    from app.practice import build_grounded_review_prompt, practice_review_schema
    monkeypatch.setattr(trained_client.TrainedModelClient, 'available', lambda *args: True)
    monkeypatch.setattr(trained_client, 'get_model_context_capacity', lambda *args: 8192)
    monkeypatch.setattr(trained_client.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    accepted = {'path': '/synthetic/accepted', 'sha256': 'fixture', 'scope': 'single_question_practice_review'}
    monkeypatch.setattr(practice_adapter, 'active_practice_adapter', lambda path, **kwargs: accepted)
    requests = []
    def run(*args, **kwargs):
        requests.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'answer': '{}', 'done_reason': 'stop'}))
    monkeypatch.setattr(trained_client.subprocess, 'run', run)
    item = dict(index=1, source='Synthetic source.', question='Synthetic question?', choices=dict(A='one', B='two', C='three', D='four'), proposed_answer='A', explanation='Synthetic explanation.', evidence_quote='Synthetic source.')
    grounded_prompt = build_grounded_review_prompt([item])
    client = trained_client.TrainedModelClient()
    client.chat_complete([{'role': 'user', 'content': grounded_prompt}], system_prompt=practice_adapter.SYSTEM_PROMPT, max_tokens=1000, temperature=0, response_schema=practice_review_schema(1, None))
    # Must NOT load legacy adapter: defaults to base model!
    assert requests[-1]['adapter_path'] is None  # nosec B101
    assert requests[-1]['seed'] == 42  # nosec B101
    # Diagnostics must report grounded_review_v2
    assert client.last_diagnostics.get('practice_review_profile') == 'grounded_review_v2'  # nosec B101


@pytest.mark.parametrize('qualified', [True, False])
def test_grounded_review_loads_only_separately_accepted_profile(monkeypatch, qualified):
    from app import practice_adapter
    from app.practice_proof import native_grounded_review_prompt, native_grounded_review_schema
    monkeypatch.setattr(trained_client.TrainedModelClient, 'available', lambda *args: True)
    monkeypatch.setattr(trained_client, 'get_model_context_capacity', lambda *args: 8192)
    monkeypatch.setattr(trained_client.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    accepted = dict(path='/synthetic/grounded', sha256='fixture', scope='single_question_practice_review', review_profile=practice_adapter.GROUNDED_REVIEW_PROFILE)
    profiles = []
    def active(path, profile):
        profiles.append(profile)
        return accepted if qualified else None
    monkeypatch.setattr(practice_adapter, 'active_practice_adapter', active)
    requests = []
    def run(*args, **kwargs):
        requests.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'answer': '{}', 'done_reason': 'stop'}))
    monkeypatch.setattr(trained_client.subprocess, 'run', run)
    item = dict(index=1, source='Synthetic source.', question='Synthetic question?', choices=dict(A='one', B='two', C='three', D='four'), proposed_answer='A', explanation='Synthetic explanation.', evidence_quote='Synthetic source.')
    client = trained_client.TrainedModelClient()
    client.chat_complete([{'role': 'user', 'content': native_grounded_review_prompt(item)}], system_prompt=practice_adapter.SYSTEM_PROMPT, max_tokens=1000, temperature=0, response_schema=native_grounded_review_schema(1, None))
    assert profiles == [practice_adapter.GROUNDED_REVIEW_PROFILE]  # nosec B101
    assert requests[-1]['adapter_path'] == (accepted['path'] if qualified else None)  # nosec B101
    assert requests[-1]['seed'] == 42  # nosec B101


def test_warm_prefix_caches_handles_busy_unavailable_and_success(tmp_path, monkeypatch):
    model_dir = tmp_path / 'qwen9b'
    model_dir.mkdir()
    monkeypatch.setattr(trained_client, 'resolve_mlx_model_dir', lambda *a: model_dir)
    monkeypatch.setattr(trained_client, 'get_model_context_capacity', lambda *a: 8192)

    monkeypatch.setattr(trained_client.TrainedModelClient, 'available', classmethod(lambda cls, *a: False))
    assert trained_client.TrainedModelClient.warm_prefix_caches() == {'warmed': False, 'skipped': 'unavailable'}  # nosec B101

    monkeypatch.setattr(trained_client.TrainedModelClient, 'available', classmethod(lambda cls, *a: True))

    def busy_acquire(**kw):
        raise trained_client.GPUBusyError('GPU busy')

    monkeypatch.setattr(trained_client.gpu_coordinator, 'acquire_for_inference', busy_acquire)
    res_busy = trained_client.TrainedModelClient.warm_prefix_caches(system_prompts=['SYS'])
    assert res_busy == {'warmed': False, 'skipped': 'gpu_busy_or_cancelled'}  # nosec B101
    assert trained_client.TrainedModelClient._warmup_done.is_set()  # nosec B101

    seen = []
    monkeypatch.setattr(trained_client.gpu_coordinator, 'acquire_for_inference', lambda **kw: nullcontext())
    monkeypatch.setattr(
        trained_client._INFER_STREAM_WORKER,
        'request_json',
        lambda payload, **kw: seen.append(payload) or {'op': 'warmup', 'ok': True, 'warmed': True},
    )
    res_ok = trained_client.TrainedModelClient.warm_prefix_caches(system_prompts=['SYS A', 'SYS B'])
    assert res_ok['warmed'] is True  # nosec B101
    assert len(seen) == 1  # nosec B101
    assert seen[0]['op'] == 'warmup'  # nosec B101
    assert seen[0]['system_prompts'] == ['SYS A', 'SYS B']  # nosec B101
    assert trained_client.TrainedModelClient._warmup_done.is_set()  # nosec B101


def test_start_startup_warmup_respects_disable_and_pytest_guards(monkeypatch):
    monkeypatch.setattr(trained_client.TrainedModelClient, 'available', classmethod(lambda cls, *a: True))
    assert trained_client.TrainedModelClient.start_startup_warmup() is False  # PYTEST_CURRENT_TEST active  # nosec B101

    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)
    monkeypatch.setenv('STUDY_DISABLE_STARTUP_WARMUP', '1')
    assert trained_client.TrainedModelClient.start_startup_warmup() is False  # nosec B101


def test_default_warmup_prompts_include_repair_prefix_and_keep_primary_last():
    from app.rag_agent import (
        MLX_CALCULATION_GUIDANCE,
        MLX_SOURCE_LINE_CONTRACT,
        MLX_SOURCE_PROMPT,
        MLX_VERDICT_SOURCE_PROMPT,
    )

    prompts = trained_client.TrainedModelClient._default_warmup_prompts()
    assert prompts == [  # nosec B101
        f"{MLX_SOURCE_PROMPT}\n{MLX_SOURCE_LINE_CONTRACT}",
        f"{MLX_SOURCE_PROMPT}\n{MLX_CALCULATION_GUIDANCE}",
        MLX_VERDICT_SOURCE_PROMPT,
        MLX_SOURCE_PROMPT,
    ]
