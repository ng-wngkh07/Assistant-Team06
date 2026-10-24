"""Native curation transport must retain real parsing and artifact provenance."""
import ast
import json
from pathlib import Path

import pytest


def test_all_application_modules_parse():
    for path in (Path(__file__).resolve().parents[1] / 'app').glob('*.py'):
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


def test_native_curation_identity_is_bound_to_weights_and_tokenizer(tmp_path, monkeypatch):
    from app import curate_dataset_v7 as curation, model_resolver
    monkeypatch.setattr(curation.sys, 'platform', 'darwin')
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', tmp_path)
    for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors'):
        (tmp_path / name).write_text('{}' if name.endswith('.json') else 'weights1')
    first = curation.get_ollama_model_digest('qwen3.5-9b-4bit')
    assert first.startswith('mlx-sha256:')
    assert first == curation.get_ollama_model_digest('qwen3.5-9b-4bit')
    (tmp_path / 'model.safetensors').write_text('weights2')
    second = curation.get_ollama_model_digest('qwen3.5-9b-4bit')
    assert second != first
    (tmp_path / 'tokenizer_config.json').write_text('{"added_tokens":true}')
    assert curation.get_ollama_model_digest('qwen3.5-9b-4bit') != second
    with pytest.raises(RuntimeError):
        curation.get_ollama_model_digest('unknown-model', default='invented')


@pytest.fixture
def native_curation(monkeypatch):
    from app import curate_dataset_v7 as module
    import app.trained_client as trained
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.time, 'sleep', lambda *_: None)
    monkeypatch.setattr(module, 'get_ollama_model_digest', lambda *_: 'bound-test-artifact')
    monkeypatch.setattr(module, 'is_substantive_psychology_source', lambda *_: (True, None))
    def outer_lock_forbidden():
        raise AssertionError('Native client owns admission; do not acquire a second native lock')
    monkeypatch.setattr(module.gpu_coordinator, 'acquire_for_inference', outer_lock_forbidden)
    replies = []
    class Client:
        last_done_reason = 'stop'
        def __init__(self, model_name): assert model_name == 'qwen3.5-9b-4bit'
        def chat_complete(self, **kwargs): return replies.pop(0)
    monkeypatch.setattr(trained, 'TrainedModelClient', Client)
    return module, replies


def test_native_teacher_parses_its_generated_json(native_curation):
    module, replies = native_curation
    expected = {'question':'Điều kiện hóa được định nghĩa như thế nào?',
                'answer':'Điều kiện hóa là quá trình học tập hình thành hành vi theo hệ quả [S1].',
                'claims':['Hành vi phụ thuộc hệ quả.']}
    replies.append(json.dumps(expected, ensure_ascii=False))
    result = module.call_teacher_model({'book_title':'Synthetic', 'page_num':1, 'text':'Nguồn giả lập.'},
                                      'overview', teacher_model='qwen3.5-9b-4bit', max_retries=1)
    assert result == expected
    assert replies == []


def test_native_judge_retains_parser_and_finish_reason(native_curation, monkeypatch):
    module, replies = native_curation
    replies.append('{"supported":false}')
    seen = []
    def parse(**kwargs):
        seen.append(kwargs)
        return {'supported':False, 'reason':'controlled-parser-result'}
    monkeypatch.setattr(module, 'validate_and_parse_judge_verdict', parse)
    result = module.call_judge_model({'text':'Nguồn giả lập.'}, {'question':'Câu hỏi?', 'answer':'Câu trả lời.'},
                                    judge_model='qwen3.5-9b-4bit', max_retries=1)
    assert result['reason'] == 'controlled-parser-result'
    assert seen[0]['done_reason'] == 'stop'
    assert seen[0]['raw_payload'] == '{"supported":false}'
    assert seen[0]['model_digest'] == 'bound-test-artifact'


def test_native_calibration_processes_every_case(native_curation, monkeypatch):
    module, replies = native_curation
    replies.extend(['{}'] * 100)
    seen = []
    def parse(**kwargs):
        seen.append(kwargs)
        return {'supported':False, 'evidence_spans':[], 'reason':'controlled-parser-result'}
    monkeypatch.setattr(module, 'validate_and_parse_judge_verdict', parse)
    result = module.run_judge_calibration('qwen3.5-9b-4bit')
    assert len(seen) == result['total_cases'] == len(result['cases']) > 1
    assert all('full_verdict' in case and 'error' not in case for case in result['cases'])
    assert result['all_passed'] is False, 'A mock parser cannot establish real model calibration'
