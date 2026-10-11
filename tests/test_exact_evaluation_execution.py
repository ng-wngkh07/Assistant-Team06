"""Replay frozen prompts and stream evidence without loading model weights or GPU."""
import contextlib
import hashlib
import json
import sys
import types

import pytest

from scripts import evaluate_exact_base as evaluator


@pytest.fixture
def replay(tmp_path, monkeypatch):
    model = tmp_path/'model'
    model.mkdir()
    for name in ('model.safetensors', 'config.json', 'tokenizer.json'):
        (model/name).write_bytes(b'pinned synthetic bundle '+name.encode())
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in model.iterdir()}
    identity = tmp_path/'identity.json'
    identity.write_text(json.dumps({'repo': 'mlx-community/Qwen2.5-3B-Instruct-4bit',
                                   'revision': evaluator.MODEL_PINNED_REVISION,
                                   'files_sha256': hashes}))
    suite = tmp_path/'suite.json'
    approval = tmp_path/'approval.json'
    output = tmp_path/'output.json'
    captured = {'messages': [], 'calls': []}
    final = {'finish_reason': 'stop', 'prompt_tokens': 17,
             'generation_tokens': 2, 'peak_memory': 1.25}

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            captured['messages'].append(json.loads(json.dumps(messages)))
            return json.dumps(messages, ensure_ascii=False)

    def stream_generate(**kwargs):
        captured['calls'].append(kwargs)
        yield types.SimpleNamespace(text='câu trả lời', **final)

    mx = types.ModuleType('mlx.core')
    mx.metal = types.SimpleNamespace(get_peak_memory=lambda: 7*1024**3)
    mx.random = types.SimpleNamespace(seed=lambda value: None)
    mx.get_peak_memory = lambda: 7*1024**3
    mlx = types.ModuleType('mlx')
    mlx.__path__ = []
    mlx.core = mx
    lm = types.ModuleType('mlx_lm')
    lm.__version__ = 'synthetic-stream-fixture'
    lm.load = lambda *args, **kwargs: (object(), Tokenizer())
    lm.stream_generate = stream_generate
    sample = types.ModuleType('mlx_lm.sample_utils')
    sample.make_sampler = lambda **kwargs: object()
    for name, module in [('mlx', mlx), ('mlx.core', mx), ('mlx_lm', lm),
                         ('mlx_lm.sample_utils', sample)]:
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv('CODEX_EXACT_BASE_EXECUTION_AUTHORIZED', '1')
    for key, value in [('MODEL_DIR', model), ('BASE_IDENTITY_PATH', identity),
                       ('HOLDOUT_INPUTS_PATH', suite), ('APPROVAL_PATH', approval),
                       ('OUTPUT_EVIDENCE_PATH', output)]:
        monkeypatch.setattr(evaluator, key, value)
    # Both field spellings isolate replay defects from the separately diagnosed memory-key bug.
    monkeypatch.setattr(evaluator, 'get_hardware_memory_profile',
                        lambda: {'eligible': True, 'measurement_error': None,
                                 'available_gb': 5.2, 'ram_available_gb': 5.2})
    monkeypatch.setattr(evaluator, 'inspect_base_model', lambda *a, **kw: {})
    monkeypatch.setattr(evaluator, 'compute_model_file_hashes', lambda *a: hashes, raising=False)
    monkeypatch.setattr(evaluator, 'gpu_coordinator',
                        types.SimpleNamespace(acquire_for_inference=lambda **kw: contextlib.nullcontext()))

    def run(messages, decoding=None, suite_decoding=False):
        case = {'id': 'frozen-one', 'domain': 'calculus', 'task': 'grounded_qa',
                'source_file': 'synthetic-source.pdf', 'source_file_sha256': 'a'*64,
                'reference': 'REFERENCE MUST NEVER ENTER INFERENCE', 'messages': messages,
                'decoding': decoding if decoding is not None else {'temperature': 0, 'max_tokens': 128, 'seed': 0}}
        payload = {'holdout_cases': [case]}
        if suite_decoding:
            payload = {'cases': [case], 'decoding': case.pop('decoding')}
        suite.write_text(json.dumps(payload, ensure_ascii=False))
        approval.write_text(json.dumps({'decision': 'PASS', 'independent_review_complete': True,
                                       'suite_sha256': hashlib.sha256(suite.read_bytes()).hexdigest()}))
        evaluator.run_exact_base_evaluation()
        return json.loads(output.read_text())

    return run, captured, final


@pytest.mark.parametrize('has_reference_message', [False, True])
def test_frozen_input_is_preserved_and_only_target_assistant_is_removed(replay, has_reference_message):
    run, captured, _ = replay
    inputs = [{'role': 'system', 'content': 'FROZEN SYSTEM MUST STAY BYTE IDENTICAL'},
              {'role': 'user', 'content': 'QUESTION MUST REMAIN IN INPUT'}]
    messages = inputs + ([{'role': 'assistant', 'content': 'REFERENCE MUST NEVER ENTER INFERENCE'}]
                         if has_reference_message else [])
    run(messages)
    assert captured['messages'] == [inputs]


def test_replay_uses_frozen_budget_and_actual_final_response_metrics(replay):
    run, captured, _ = replay
    report = run([{'role': 'system', 'content': 'frozen'}, {'role': 'user', 'content': 'question'}],
                 {'temperature': 0, 'max_tokens': 128, 'seed': 0})
    assert captured['calls'][0]['max_tokens'] == 128
    row = report['results'][0]
    assert row['prompt_tokens'] == 17 and row['generation_tokens'] == 2
    assert row['peak_memory_mb'] == 1.25*1024


def test_stream_without_actual_final_reason_is_incomplete(replay):
    run, _, final = replay
    final['finish_reason'] = None
    with pytest.raises(RuntimeError, match='finish_reason|incomplete|Incomplete'):
        run([{'role': 'system', 'content': 'frozen'}, {'role': 'user', 'content': 'question'}])


@pytest.mark.parametrize('field', ['prompt_tokens', 'generation_tokens', 'peak_memory'])
def test_missing_final_metrics_cannot_be_successful_evidence(replay, field):
    run, _, final = replay
    del final[field]
    with pytest.raises((ValueError, RuntimeError), match='metric|tokens|memory|incomplete|Incomplete'):
        run([{'role': 'system', 'content': 'frozen'}, {'role': 'user', 'content': 'question'}])


def test_length_finish_is_recorded_as_incomplete(replay):
    run, _, final = replay
    final['finish_reason'] = 'length'
    report = run([{'role': 'system', 'content': 'frozen'}, {'role': 'user', 'content': 'question'}])
    row = report['results'][0]
    assert row['finish_reason'] == 'length' and row['complete'] is False


def test_legacy_suite_level_frozen_decoding_is_preserved(replay):
    run, captured, _ = replay
    run([{'role': 'system', 'content': 'frozen'}, {'role': 'user', 'content': 'question'}],
        {'temperature': 0, 'max_tokens': 256, 'seed': 0}, suite_decoding=True)
    assert captured['calls'][0]['max_tokens'] == 256


def test_missing_frozen_decoding_cannot_silently_change_protocol(replay):
    run, _, _ = replay
    with pytest.raises((ValueError, RuntimeError), match='decoding|protocol|budget|max_tokens'):
        run([{'role': 'system', 'content': 'frozen'}, {'role': 'user', 'content': 'question'}], {})
