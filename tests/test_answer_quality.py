from app.answer_format import clean_answer
from app.bilingual_terms import translation_is_plausible
from app.query_normalizer import normalize_query, normalize_query_with_model, is_unclear_query
from app.rag_agent import PsychologyAgent, has_relevant_evidence


def test_private_reasoning_and_source_block_are_not_shown():
    text = "<think>đang suy nghĩ</think><final>Hai ý liên quan với nhau [S1, S2].</final>\nNguồn: sách X"
    assert clean_answer(text) == "Hai ý liên quan với nhau."
    assert clean_answer("Theo đoạn [S1], thuốc chỉ làm dịu biểu hiện.") == "Thuốc chỉ làm dịu biểu hiện."
    assert clean_answer("Giải thích suy luận: Tầm Thân là phần bị che giấu [S1].") == "Cái bóng là phần bị che giấu."


def test_prompt_normalization_preserves_meaning_and_handles_missing_accents():
    assert normalize_query("  tam ly   va cam xuc?  ") == "tâm lý va cảm xúc?"
    assert is_unclear_query("gì?")
    assert not is_unclear_query("lo âu kéo dài")


def test_model_rewrite_cannot_remove_negation_or_numbers(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": '{"normalized_query":"Tôi có 2 triệu chứng lo âu?"}'}}

    monkeypatch.setattr("app.query_normalizer.requests.post", lambda *_args, **_kwargs: Response())
    original = "Tôi không có 2 triệu chứng lo âu?"
    assert normalize_query_with_model(original, model="qwen2.5:7b") == original


def test_bad_jung_translation_is_rejected():
    source = "The shadow is projected onto another person and projection hides this experience."
    assert not translation_is_plausible(source, "Tầm thân là một dự án bóng của người khác và có thể thay đổi trải nghiệm này.")
    assert not translation_is_plausible(source, "Trả lời: Cái bóng được phóng chiếu [S1]. Đây là câu trả lời phân tích cho người hỏi.")
    assert translation_is_plausible(source, "Cái bóng được phóng chiếu lên người khác; sự phóng chiếu có thể che khuất trải nghiệm này.")


def test_unrelated_search_results_do_not_claim_book_evidence():
    unrelated = [{"text": "Một định luật về hành vi và cách ra quyết định.", "semantic_score": 0.48}]
    assert not has_relevant_evidence("định luật Ohm là gì", unrelated)


class EmptySearcher:
    def search_hybrid(self, **kwargs):
        return []


class StubModel:
    def chat_stream(self, **kwargs):
        yield "Lo âu có thể ảnh hưởng giấc ngủ, nhưng mức độ thay đổi tùy người."


class StubTranslator:
    def translate(self, chunks):
        return {}


class RelevantSearcher:
    def search_hybrid(self, **kwargs):
        text = "Lo âu có thể ảnh hưởng giấc ngủ; mức độ thay đổi tùy người."
        return [{"book_title": "Sách kiểm thử", "filename": "mock.pdf", "page_num": 1,
                 "chunk_index": 0, "text": text, "safe_text": text, "semantic_score": 0.9}]


def test_document_qa_states_insufficient_evidence_without_book_excerpt():
    agent = PsychologyAgent(searcher=EmptySearcher(), ollama=StubModel(), translator=StubTranslator())
    result = agent.process_query_sync("Lo âu ảnh hưởng giấc ngủ như thế nào?", model="test-model", embed_model=None)
    assert "chưa tìm thấy đủ căn cứ" in result["answer"]
    assert result["citations"] == []


def test_substantial_language_drift_is_not_delivered_as_an_answer():
    class MixedModel:
        def chat_stream(self, **kwargs):
            yield "Đây là kết luận. 这些说法没有充分依据而且不能用来判断个人的心理状况。"

    agent = PsychologyAgent(searcher=RelevantSearcher(), ollama=MixedModel(), translator=StubTranslator())
    result = agent.process_query_sync("Lo âu ảnh hưởng giấc ngủ như thế nào?", model="test-model", embed_model=None)
    assert "chưa tạo được câu trả lời tiếng Việt" in result["answer"]
    assert "这些" not in result["answer"]
    assert result["citations"] == []


def test_short_quoted_han_name_does_not_block_vietnamese_answer():
    from app.evaluation_metrics import has_language_drift
    assert not has_language_drift("Tên chữ Hán 心 được nhắc như một ví dụ ngôn ngữ, không phải kết luận tâm lý.")


def test_hidden_reasoning_language_does_not_reject_a_valid_final_answer():
    class ReasoningModel:
        def chat_stream(self, **kwargs):
            yield "<think>这些说法没有充分依据而且不能用来判断个人的心理状况。</think><final>Lo âu có thể ảnh hưởng giấc ngủ, nhưng mức độ thay đổi tùy người [S1].</final>"

    agent = PsychologyAgent(searcher=RelevantSearcher(), ollama=ReasoningModel(), translator=StubTranslator())
    result = agent.process_query_sync("Lo âu ảnh hưởng giấc ngủ như thế nào?", model="test-model", embed_model=None)
    assert result["answer"].startswith("Lo âu có thể")
    assert result["citations"]
    assert "<think>" not in result["answer"]


def test_cli_shows_only_final_answer_even_when_internal_sources_exist(monkeypatch, capsys):
    from types import SimpleNamespace
    import run

    class FakeAgent:
        def process_query_stream(self, **_kwargs):
            yield {"type": "citations", "citations": [{"book_title": "Sách nội bộ", "page_num": 4, "score": 1}]}
            yield {"type": "token", "content": "Câu trả lời hoàn chỉnh."}
            yield {"type": "done", "citations": []}

    monkeypatch.setattr(run, "PsychologyAgent", FakeAgent)
    run.cmd_query(SimpleNamespace(question="Một câu hỏi", model="test", embed_model="none", top_k=2))
    output = capsys.readouterr().out
    assert "Câu trả lời hoàn chỉnh." in output
    assert "Sách nội bộ" not in output
    assert "NGUỒN ĐỐI CHIẾU" not in output
