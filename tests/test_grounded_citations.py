from app.rag_agent import PsychologyAgent, weak_keyword_evidence, repetitive_answer


def test_weak_keyword_match_does_not_count_as_evidence():
    assert weak_keyword_evidence("xyzqwerty-no-such-concept", [{"text": "The concept is described here."}])
    assert not weak_keyword_evidence("hệ thống nhanh chậm", [{"text": "Hệ thống 1 vận hành nhanh; hệ thống 2 chậm."}])


def test_repeated_citation_answer_is_rejected():
    assert repetitive_answer("[S1] [S2] " * 20, 2)
    assert not repetitive_answer("Hai nguồn bổ sung cho nhau [S1] [S2].", 2)


class StubSearcher:
    def search_hybrid(self, **kwargs):
        return [{
            "book_title": "Sách kiểm thử",
            "filename": "sach.pdf",
            "page_num": 12,
            "chunk_index": 0,
            "text": "Hệ thống 1 xử lý nhanh.",
            "safe_text": "Hệ thống 1 xử lý nhanh.",
        }]


class StubOllama:
    def __init__(self, answer):
        self.answer = answer

    def chat_stream(self, **kwargs):
        yield self.answer


class StubTranslator:
    def translate(self, chunks):
        return {}


def test_citation_comes_from_retrieved_source():
    agent = PsychologyAgent(searcher=StubSearcher(), ollama=StubOllama("Hệ thống 1 xử lý nhanh [S1]."), translator=StubTranslator())
    result = agent.process_query_sync("Hệ thống 1 là gì?", embed_model=None, model="test-ollama")
    assert result["answer"] == "Hệ thống 1 xử lý nhanh."
    assert "[S1]" not in result["answer"]
    assert len(result["citations"]) == 1


def test_unknown_source_id_is_rejected():
    agent = PsychologyAgent(searcher=StubSearcher(), ollama=StubOllama("Thông tin không có nguồn [S9]."), translator=StubTranslator())
    result = agent.process_query_sync("Hệ thống 1 là gì?", embed_model=None, model="test-ollama")
    assert "chưa có đủ thông tin" in result["answer"]
    assert result["citations"] == []


def test_combined_source_id_is_normalized():
    agent = PsychologyAgent(searcher=StubSearcher(), ollama=StubOllama("Có liên hệ [S1, S1]."), translator=StubTranslator())
    result = agent.process_query_sync("Hệ thống 1 là gì?", embed_model=None, model="test-ollama")
    assert "không đề cập đủ thông tin" not in result["answer"]
    assert "[S1" not in result["answer"]


def test_selected_model_route_uses_native_base_client(monkeypatch):
    # This route test uses a stub base; asset identity has separate boundary tests.
    monkeypatch.setattr("app.rag_agent.context_capacity", lambda model: 8192)
    from app.trained_client import TrainedModelClient

    def fake_chat(self, **kwargs):
        self.last_done_reason = "stop"
        yield '{"claims":[{"text":"Hệ thống 1 xử lý nhanh.","source_ids":["S1"],"evidence_lines":[1,1]}]}'

    monkeypatch.setattr(TrainedModelClient, "chat_stream", fake_chat)
    agent = PsychologyAgent(searcher=StubSearcher(), ollama=StubOllama("Không dùng"), translator=StubTranslator())
    result = agent.process_query_sync("Hệ thống 1 là gì?", model="qwen3.5-9b-4bit", embed_model=None)
    assert result["answer"] == "Hệ thống 1 xử lý nhanh."
