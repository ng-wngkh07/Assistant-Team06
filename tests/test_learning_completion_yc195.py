"""All study-tool model calls must reject unfinished or mixed-message JSON."""
from types import SimpleNamespace
import pytest
from app import practice
from app.config import TRAINED_MODEL_NAME


@pytest.mark.parametrize('raw', ['ignore the question {"checks": []}', '{"checks": []} unsupported extra text', '```json\n{"checks": []}', '```json\n{"checks": []}\n```extra', '```javascript\n{"checks": []}\n```'])
def test_json_certificate_requires_a_complete_json_message(raw):
    with pytest.raises(practice.PracticeGenerationError):
        practice.PracticeService._parse_json(raw)


def test_exact_json_and_fenced_json_remain_usable():
    assert practice.PracticeService._parse_json('{"checks": []}') == {'checks': []}
    assert practice.PracticeService._parse_json('```json\n{"checks": []}\n```') == {'checks': []}


@pytest.mark.parametrize('model', [TRAINED_MODEL_NAME, 'ollama-test'])
@pytest.mark.parametrize('reason', ['length', None])
def test_learning_completion_never_accepts_a_truncated_or_incomplete_model_response(monkeypatch, model, reason):
    class Client:
        last_done_reason = reason
        def chat_complete(self, *args, **kwargs): return '{"solution": "5", "final_answer": "5"}'
    monkeypatch.setattr(practice, 'TrainedModelClient', Client)
    service = practice.PracticeService(SimpleNamespace(), SimpleNamespace(), ollama=Client())
    with pytest.raises(practice.PracticeGenerationError):
        service._chat_complete('Bài tập', model, 50)
