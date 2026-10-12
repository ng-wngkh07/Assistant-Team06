"""User-goal API contracts; no page selection and no client model override."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from app import server
from app.config import DEFAULT_CHAT_MODEL


class LearningSpy:
    def __init__(self):
        self.calls = []

    def topics(self):
        return {"topics": [{"id": "Trí nhớ", "label": "Trí nhớ", "document_count": 3}]}

    def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return {"practice_id": "a" * 32, "count": 1, "purpose": kwargs["purpose"],
                "questions": [{"id": "q1", "question": "Trí nhớ làm việc có đặc điểm nào?",
                               "choices": [{"label": key, "text": key} for key in "ABCD"],
                               "source": {"doc_id": 1, "page_num": 1}}]}

    def exercises(self, **kwargs):
        self.calls.append(("exercises", kwargs))
        return {"items": [{"question": "Bài 1: Tính 2+3.", "answer": "5",
                           "answer_status": "source_document",
                           "source": {"doc_id": 1, "page_num": 2},
                           "answer_source": {"doc_id": 1, "page_num": 3}}]}


@pytest.fixture
def api(monkeypatch):
    spy = LearningSpy()
    monkeypatch.setattr(server, "learning_service", spy, raising=False)
    monkeypatch.setattr(server, "dialogue_gate", SimpleNamespace(acquire=lambda: nullcontext()))
    monkeypatch.setattr(server, "gpu_coordinator", SimpleNamespace(acquire_for_inference=lambda: nullcontext()))
    return TestClient(server.app), spy


def test_topics_come_from_current_library(api):
    client, _ = api
    response = client.get("/api/learning/topics")
    assert response.status_code == 200
    assert response.json()["topics"][0]["label"] == "Trí nhớ"


@pytest.mark.parametrize("purpose", ["practice", "review"])
def test_learning_generation_requires_topic_but_no_document_or_page(api, purpose):
    client, spy = api
    response = client.post("/api/learning/generate", json={
        "topic": "Trí nhớ", "count": 1, "difficulty": "medium", "purpose": purpose,
        "weak_topics": ["trí nhớ làm việc"], "model": "untrusted-model"})
    assert response.status_code == 200
    called = spy.calls[-1][1]
    assert called["model"] == DEFAULT_CHAT_MODEL
    assert called["topic"] == "Trí nhớ" and called["purpose"] == purpose
    assert called["weak_topics"] == ["trí nhớ làm việc"]
    assert "answer" not in response.json()["questions"][0]


def test_exercises_return_question_and_separate_answer_page(api):
    client, spy = api
    response = client.post("/api/learning/exercises", json={"topic": "Phép cộng", "limit": 3, "solve_missing": False})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["question"].startswith("Bài 1")
    assert item["source"]["page_num"] == 2 and item["answer_source"]["page_num"] == 3
    assert item["answer_status"] == "source_document"
    assert spy.calls[-1][1]["solve_missing"] is False


@pytest.mark.parametrize("payload", [
    {"topic": ""}, {"topic": "x" * 241}, {"topic": "Toán", "count": 4},
    {"topic": "Toán", "difficulty": "impossible"}, {"topic": "Toán", "purpose": "upload"},
    {"topic": "Toán", "weak_topics": ["x"] * 21},
])
def test_goal_input_is_bounded_before_model_call(api, payload):
    client, spy = api
    assert client.post("/api/learning/generate", json=payload).status_code == 422
    assert not spy.calls
