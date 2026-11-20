import sys
import pytest
from app.rag_agent import PsychologyAgent, weak_keyword_evidence, repetitive_answer


def test_weak_keyword_match_does_not_count_as_evidence():
    assert weak_keyword_evidence("xyzqwerty-no-such-concept", [{"text": "The concept is described here."}])  # nosec B101
    assert not weak_keyword_evidence("hệ thống nhanh chậm", [{"text": "Hệ thống 1 vận hành nhanh; hệ thống 2 chậm."}])  # nosec B101


def test_repeated_citation_answer_is_rejected():
    assert repetitive_answer("[S1] [S2] " * 20, 2)  # nosec B101
    assert not repetitive_answer("Hai nguồn bổ sung cho nhau [S1] [S2].", 2)  # nosec B101


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
    assert result["answer"] == "Hệ thống 1 xử lý nhanh."  # nosec B101
    assert "[S1]" not in result["answer"]  # nosec B101
    assert len(result["citations"]) == 1  # nosec B101


def test_unknown_source_id_is_rejected():
    agent = PsychologyAgent(searcher=StubSearcher(), ollama=StubOllama("Thông tin không có nguồn [S9]."), translator=StubTranslator())
    result = agent.process_query_sync("Hệ thống 1 là gì?", embed_model=None, model="test-ollama")
    assert "chưa có đủ thông tin" in result["answer"]  # nosec B101
    assert result["citations"] == []  # nosec B101


def test_combined_source_id_is_normalized():
    agent = PsychologyAgent(searcher=StubSearcher(), ollama=StubOllama("Có liên hệ [S1, S1]."), translator=StubTranslator())
    result = agent.process_query_sync("Hệ thống 1 là gì?", embed_model=None, model="test-ollama")
    assert "không đề cập đủ thông tin" not in result["answer"]  # nosec B101
    assert "[S1" not in result["answer"]  # nosec B101


@pytest.mark.skipif(sys.platform != "darwin", reason="Native Mac MLX deployment")
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
    assert result["answer"] == "Hệ thống 1 xử lý nhanh."  # nosec B101


def test_qa_prompt_preserves_scope_and_clarifies_compare_and_tables():
    from app.qa_prompts import MLX_SOURCE_PROMPT, qa_system_prompt_matches

    assert qa_system_prompt_matches(MLX_SOURCE_PROMPT) is True  # nosec B101
    assert "S1" in MLX_SOURCE_PROMPT and "S2" in MLX_SOURCE_PROMPT  # nosec B101
    assert "<...>" in MLX_SOURCE_PROMPT  # nosec B101


def test_compare_requires_citations_covering_all_admitted_concepts(monkeypatch, tmp_path):
    import json
    import app.model_resolver as model_resolver
    import app.rag_agent as rag
    from test_study_agent_c_f_g import NoTranslation, RecordingSearcher, make_chunk

    (tmp_path / "config.json").write_text('{"max_position_embeddings":8192}', encoding="utf-8")
    monkeypatch.setattr(model_resolver.sys, "platform", "darwin")
    monkeypatch.setattr(model_resolver, "MLX_9B_DIR", tmp_path)
    monkeypatch.setattr(rag, "get_exact_token_counter", lambda *args: lambda messages: 200)

    c1 = {**make_chunk("AVLNodes* rotateLeft(AVLNodes* x) { return x; }", doc_id=34, page_num=2), "chunk_id": 1}
    c2 = {**make_chunk("AVLNodes* rotateRight(AVLNodes* x) { return x; }", doc_id=34, page_num=3), "chunk_id": 2}

    class CompareModel:
        def __init__(self):
            self.calls = []

        def chat_stream(self, **kwargs):
            self.calls.append(kwargs)
            self.last_done_reason = "stop"
            if len(self.calls) == 1:
                yield json.dumps(
                    {
                        "claims": [
                            {"text": "rotateLeft xoay trái.", "source_ids": ["S1"], "evidence_lines": [1, 1]},
                            {"text": "rotateRight xoay phải.", "source_ids": ["S1"], "evidence_lines": [1, 1]},
                        ]
                    },
                    ensure_ascii=False,
                )
            else:
                yield json.dumps(
                    {
                        "claims": [
                            {"text": "rotateLeft xoay trái.", "source_ids": ["S1"], "evidence_lines": [1, 1]},
                            {"text": "rotateRight xoay phải.", "source_ids": ["S2"], "evidence_lines": [1, 1]},
                        ]
                    },
                    ensure_ascii=False,
                )

    model = CompareModel()
    res = PsychologyAgent(
        searcher=RecordingSearcher([c1, c2]),
        ollama=model,
        translator=NoTranslation(),
    ).process_query_sync(
        "So sánh rotateLeft và rotateRight.",
        embed_model=None,
        model="qwen3.5-9b-4bit",
        processing_level="standard",
        answer_mode="compare",
        isolate_context=True,
    )
    assert res["response_kind"] == "answer"  # nosec B101
    assert res["generation_calls"] == 2  # nosec B101
    assert {c.get("id") for c in res["citations"]} == {"S1", "S2"}  # nosec B101
    assert {c.get("page_num") for c in res["citations"]} == {2, 3}  # nosec B101


def test_qa_repeated_pure_abstention_claims_resolve_to_insufficient_evidence(monkeypatch, tmp_path):
    import json
    import app.model_resolver as model_resolver
    import app.rag_agent as rag
    from test_study_agent_c_f_g import NoTranslation, RecordingSearcher, make_chunk

    (tmp_path / "config.json").write_text('{"max_position_embeddings":8192}', encoding="utf-8")
    monkeypatch.setattr(model_resolver.sys, "platform", "darwin")
    monkeypatch.setattr(model_resolver, "MLX_9B_DIR", tmp_path)
    monkeypatch.setattr(rag, "get_exact_token_counter", lambda *args: lambda messages: 200)

    chunks = [
        {**make_chunk("Thuyết tích hợp đặc trưng phân tích vật thể theo thuộc tính sớm.", doc_id=6, page_num=10), "chunk_id": 1},
        {**make_chunk("Tri giác tổ chức nhóm theo quy luật gần nhau và tương đồng.", doc_id=6, page_num=11), "chunk_id": 2},
        {**make_chunk("Nhận dạng thành phần dựa trên các geon không gian cơ bản.", doc_id=6, page_num=12), "chunk_id": 3},
    ]

    class AbstainLoopModel:
        def __init__(self):
            self.calls = 0

        def chat_stream(self, **kwargs):
            self.calls += 1
            self.last_done_reason = "stop"
            if self.calls == 1:
                yield json.dumps({"claims": []})
            else:
                yield json.dumps(
                    {
                        "claims": [
                            {"text": "Đoạn trích không mô tả phương pháp kiểm chuẩn thang đo nhân cách MMPI-2.", "source_ids": [f"S{i}"], "evidence_lines": [1, 1]}
                            for i in (1, 2, 3)
                        ]
                    },
                    ensure_ascii=False,
                )

    res = PsychologyAgent(
        searcher=RecordingSearcher(chunks),
        ollama=AbstainLoopModel(),
        translator=NoTranslation(),
    ).process_query_sync(
        "Đoạn trích có mô tả phương pháp kiểm chuẩn thang đo nhân cách MMPI-2 không?",
        embed_model=None,
        model="qwen3.5-9b-4bit",
        processing_level="standard",
        isolate_context=True,
    )
    assert res["response_kind"] == "insufficient_evidence"  # nosec B101
    assert res["citations"] == []  # nosec B101


def test_qa_mixed_refusal_and_substantive_claim_stays_answer(monkeypatch, tmp_path):
    import json
    import app.model_resolver as model_resolver
    import app.rag_agent as rag
    from test_study_agent_c_f_g import NoTranslation, RecordingSearcher, make_chunk

    (tmp_path / "config.json").write_text('{"max_position_embeddings":8192}', encoding="utf-8")
    monkeypatch.setattr(model_resolver.sys, "platform", "darwin")
    monkeypatch.setattr(model_resolver, "MLX_9B_DIR", tmp_path)
    monkeypatch.setattr(rag, "get_exact_token_counter", lambda *args: lambda messages: 200)

    chunks = [
        {
            **make_chunk(
                "Abstract Factory là mẫu khởi tạo cho phép thay toàn bộ một họ sản phẩm trong khi Client vẫn chỉ phụ thuộc vào abstraction, nhờ đa hình factory để chọn họ sản phẩm.",
                doc_id=9,
                page_num=1,
            ),
            "chunk_id": 10,
        }
    ]

    class SingleOutputModel:
        def __init__(self, payload: dict):
            self.payload = payload

        def chat_stream(self, **kwargs):
            self.last_done_reason = "stop"
            yield json.dumps(self.payload, ensure_ascii=False)

    mixed_res = PsychologyAgent(
        searcher=RecordingSearcher(chunks),
        ollama=SingleOutputModel(
            {
                "claims": [
                    {
                        "text": "Đoạn trích không hướng dẫn cài đặt Singleton bằng enum; Abstract Factory giải quyết vấn đề bằng cách cho phép thay toàn bộ một họ sản phẩm trong khi Client vẫn chỉ phụ thuộc vào abstraction.",
                        "source_ids": ["S1"],
                        "evidence_lines": [1, 1],
                    }
                ]
            }
        ),
        translator=NoTranslation(),
    ).process_query_sync(
        "Abstract Factory giải quyết vấn đề gì và đoạn trích có hướng dẫn cài đặt Singleton bằng enum không?",
        embed_model=None,
        model="qwen3.5-9b-4bit",
        processing_level="standard",
        isolate_context=True,
    )
    assert mixed_res["response_kind"] == "answer"  # nosec B101
    assert len(mixed_res["citations"]) == 1  # nosec B101

    pure_res = PsychologyAgent(
        searcher=RecordingSearcher(chunks),
        ollama=SingleOutputModel(
            {
                "claims": [
                    {
                        "text": "Đoạn trích không mô tả cách cài đặt Singleton bằng enum; tài liệu chỉ đề cập Abstract Factory.",
                        "source_ids": ["S1"],
                        "evidence_lines": [1, 1],
                    }
                ]
            }
        ),
        translator=NoTranslation(),
    ).process_query_sync(
        "Đoạn trích có mô tả Singleton enum không?",
        embed_model=None,
        model="qwen3.5-9b-4bit",
        processing_level="standard",
        isolate_context=True,
    )
    assert pure_res["response_kind"] == "insufficient_evidence"  # nosec B101
    assert pure_res["citations"] == []  # nosec B101
