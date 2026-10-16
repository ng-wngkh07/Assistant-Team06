"""Tests for help command listing bot capabilities (Issue #21)."""

import json
import pytest
from fastapi.testclient import TestClient

from app.dialogue import dialogue_intent, HELP_MESSAGE
from app.server import app


def test_dialogue_intent_help_exact():
    """Verify exact help command variations return the help message."""
    for cmd in ["help", "/help", "HELP", "  help  ", "trợ giúp", "hướng dẫn", "bot có thể làm gì"]:
        reply, resolved = dialogue_intent(cmd, [])
        assert reply == HELP_MESSAGE, f"Failed for command: {cmd}"
        assert resolved == cmd


def test_dialogue_intent_help_content():
    """Verify help message lists all real capabilities of Study Agent."""
    assert "Tra cứu tài liệu" in HELP_MESSAGE
    assert "Hỏi đáp học tập" in HELP_MESSAGE
    assert "Luyện tập & Ôn tập" in HELP_MESSAGE
    assert "Thời khóa biểu" in HELP_MESSAGE


def test_dialogue_intent_does_not_intercept_longer_queries():
    """Verify queries containing 'help' as part of a sentence proceed to RAG search."""
    longer_queries = [
        "Help-seeking behavior in educational psychology",
        "Tôi cần tìm hiểu về help desk",
        "help me understand linear algebra",
    ]
    for q in longer_queries:
        reply, resolved = dialogue_intent(q, [])
        assert reply != HELP_MESSAGE, f"Should not intercept longer query: {q}"


def test_api_chat_help():
    """Verify /api/chat sync endpoint responds to 'help' with capabilities."""
    client = TestClient(app)
    resp = client.post("/api/chat", json={"query": "help"})
    assert resp.status_code == 200
    data = resp.json()
    assert "Tra cứu tài liệu" in data["answer"]
    assert "Hỏi đáp học tập" in data["answer"]
    assert "Thời khóa biểu" in data["answer"]
    assert data.get("response_kind") == "dialogue"
    assert data.get("citations") == []


def test_api_chat_stream_help():
    """Verify /api/chat/stream SSE endpoint streams help response without RAG."""
    client = TestClient(app)
    resp = client.post("/api/chat/stream", json={"query": "help"})
    assert resp.status_code == 200
    events = []
    for line in resp.text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    
    token_events = [e for e in events if e.get("type") == "token"]
    done_events = [e for e in events if e.get("type") == "done"]
    
    assert len(done_events) >= 1
    full_content = "".join(e.get("content", "") for e in token_events)
    assert "Tra cứu tài liệu" in full_content or any("Tra cứu" in str(e) for e in events)
    assert done_events[0].get("response_kind") == "dialogue"
    assert done_events[0].get("citations") == []
