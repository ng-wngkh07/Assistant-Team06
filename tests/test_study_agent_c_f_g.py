import json
import re
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import ANSWER_MODE_MAX_TOKENS
from app.dialogue import DialogueGate, build_context
from app.history import HistoryStore
from app.rag_agent import PsychologyAgent
from app.searcher import HybridSearcher
from app import server


ROOT = Path(__file__).resolve().parents[1]
QA_CASES = json.loads((ROOT / "tests/fixtures/qa/study_agent_c_f_g_cases.json").read_text(encoding="utf-8"))["cases"]


class RecordingSearcher:
    def __init__(self, chunks=None, error=None):
        self.chunks = chunks or []
        self.error = error
        self.calls = []

    def search_hybrid(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return [dict(chunk) for chunk in self.chunks]

    def source_page_chunks(self, doc_id, page_num, limit=12):
        self.calls.append({"document_id": doc_id, "page_num": page_num, "limit": limit})
        if self.error:
            raise self.error
        return [dict(chunk) for chunk in self.chunks]


class RecordingModel:
    def __init__(self, answer="Câu trả lời có căn cứ [S1]."):
        self.answer = answer
        self.calls = []

    def chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        yield self.answer


class NoTranslation:
    def translate(self, _chunks):
        return {}


def make_chunk(text, semantic_score=0.9, doc_id=41, page_num=12):
    return {
        "doc_id": doc_id,
        "book_title": "Tài liệu kiểm thử",
        "filename": "study.pdf",
        "page_num": page_num,
        "chunk_index": 0,
        "text": text,
        "safe_text": text,
        "semantic_score": semantic_score,
    }


def test_three_answer_modes_share_retrieval_and_enforce_distinct_prompt_guidance():
    query = "Ánh xạ tuyến tính cần điều kiện gì?"
    source = "Ánh xạ tuyến tính f: V → W thỏa f(u+v)=f(u)+f(v) và f(αu)=αf(u), với u,v thuộc V và α thuộc trường vô hướng."
    calls = []
    for mode, expected in [
        ("quick", "Tóm tắt nhanh"),
        ("steps", "Giải thích từng bước"),
        ("compare", "So sánh"),
    ]:
        searcher = RecordingSearcher([make_chunk(source)])
        model = RecordingModel()
        agent = PsychologyAgent(searcher=searcher, ollama=model, translator=NoTranslation())
        result = agent.process_query_sync(query, embed_model=None, model="test-ollama", answer_mode=mode)
        prompt = "\n".join(message["content"] for message in model.calls[0]["messages"])
        assert expected.casefold() in prompt.casefold()
        assert "f(u+v)=f(u)+f(v)" in prompt and "α thuộc trường vô hướng" in prompt
        assert model.calls[0]["num_predict"] == ANSWER_MODE_MAX_TOKENS[mode]
        assert result["answer_mode"] == mode
        assert len(result["citations"]) == 1
        calls.append(searcher.calls[0]["query"])
    assert calls == [calls[0]] * 3


@pytest.mark.parametrize("case_id", ["compare_one_sided_source", "steps_incomplete_procedure"])
def test_dataset_cases_keep_mode_specific_limits_explicit(case_id):
    case = next(item for item in QA_CASES if item["id"] == case_id)
    from app.rag_agent import answer_mode_instruction

    actual = answer_mode_instruction(case["mode"])
    assert all(fragment.casefold() in actual.casefold() for fragment in case["expected_prompt_fragments"])
    source = " ".join(case["source_chunks"])
    assert all(evidence in source for evidence in case["expected_evidence"])
    assert all(condition in source for condition in case["critical_conditions"])


@pytest.mark.parametrize(
    ("case_id", "source_page", "source_document_id", "chunks", "error"),
    [
        ("library_no_results", None, None, [], None),
        ("document_low_relevance", None, 41, [make_chunk("Ghi chú về cây xanh và tưới nước.", semantic_score=0.0)], None),
        ("selected_page_empty", (41, 12), 41, [], None),
        ("selected_page_unrelated_results", (41, 12), 41, [make_chunk("Ghi chú về cây xanh và tưới nước.", semantic_score=0.0)], None),
        ("comparison_partial_evidence", None, None, [make_chunk("Hệ thống 1 hoạt động nhanh và tự động trong nhiều tình huống.", semantic_score=0.0)], None),
        ("retrieval_technical_error", None, None, [], RuntimeError("database unavailable")),
    ],
)
def test_insufficient_retrieval_states_are_scoped_and_distinct(case_id, source_page, source_document_id, chunks, error):
    case = next(item for item in QA_CASES if item["id"] == case_id)
    searcher = RecordingSearcher(chunks=chunks, error=error)
    agent = PsychologyAgent(searcher=searcher, ollama=RecordingModel(), translator=NoTranslation())
    result = agent.process_query_sync(
        case["query"], embed_model=None, model="test-ollama",
        source_page=source_page, source_document_id=source_document_id,
    )
    assert result["search_scope"] == case["scope"]
    if error:
        assert result["response_kind"] == "technical_error"
        assert result["error"]
        assert case["expected_message_fragment"] in result["error"]
    else:
        assert result["response_kind"] == "insufficient_evidence"
        assert result["insufficient_reason"] == case["expected_reason"]
        assert case["expected_message_fragment"] in result["answer"]
        assert result["citations"] == []
        assert [item["action"] for item in result["suggested_actions"]] == case["expected_actions"]


def test_document_id_is_forwarded_to_retrieval_and_old_citations_are_not_evidence():
    searcher = RecordingSearcher([make_chunk("Quasar là một vật thể thiên văn.")])
    model = RecordingModel()
    agent = PsychologyAgent(searcher=searcher, ollama=model, translator=NoTranslation())
    agent.process_query_sync("Quasar là gì?", embed_model=None, model="test-ollama", source_document_id=41)
    assert searcher.calls[0]["document_id"] == 41

    case = next(item for item in QA_CASES if item["id"] == "previous_citation_is_not_current_evidence")
    messages, meta = build_context(
        "system", case["query"], [case["current_source"]],
        [{"role": "user", "content": "Câu hỏi cũ"}, {"role": "assistant", "content": case["prior_answer"]}],
    )
    prompt = messages[0]["content"]
    assert case["expected_history_marker"] in prompt
    assert case["forbidden_current_source_id"] not in prompt
    assert meta["source_ids"] == ["S1"]


def test_partial_comparison_in_document_scope_offers_actual_widening_action():
    agent = PsychologyAgent(
        searcher=RecordingSearcher([make_chunk("Hệ thống 1 hoạt động nhanh và tự động.", semantic_score=0.0)]),
        ollama=RecordingModel(), translator=NoTranslation(),
    )
    result = agent.process_query_sync(
        "Hệ thống 1 và Hệ thống 2 khác nhau thế nào?", embed_model=None, model="test-ollama",
        source_document_id=41,
    )
    assert result["insufficient_reason"] == "partial_evidence"
    assert [item["action"] for item in result["suggested_actions"]] == [
        "focus_single_concept", "expand_library", "rephrase_query",
    ]


def test_answer_citation_parser_rejects_malformed_ids_even_beside_a_valid_id():
    agent = PsychologyAgent(
        searcher=RecordingSearcher([make_chunk("Hệ thống 1 xử lý nhanh.")]),
        ollama=RecordingModel("Hệ thống 1 xử lý nhanh [S1]. Khẳng định khác [Sx]."),
        translator=NoTranslation(),
    )
    result = agent.process_query_sync("Hệ thống 1 là gì?", embed_model=None, model="test-ollama")
    assert result["citations"] == []
    assert "chưa có đủ thông tin" in result["answer"]


def test_answer_mode_token_ceiling_and_truncated_generation_are_reported():
    class TruncatedModel(RecordingModel):
        last_done_reason = "length"

        def chat_stream(self, **kwargs):
            self.calls.append(kwargs)
            yield " ".join(["Ý"] * 1500) + " [S1]"

    model = TruncatedModel()
    agent = PsychologyAgent(
        searcher=RecordingSearcher([make_chunk("Quasar là một vật thể thiên văn.")]),
        ollama=model, translator=NoTranslation(),
    )
    result = agent.process_query_sync("Quasar là gì?", embed_model=None, model="test-ollama", answer_mode="quick")
    assert model.calls[0]["num_predict"] == ANSWER_MODE_MAX_TOKENS["quick"]
    assert result["is_truncated"] is True


def test_isolated_new_topic_does_not_put_old_turns_into_model_context():
    searcher = RecordingSearcher([make_chunk("Trí nhớ là khả năng lưu giữ thông tin.")])
    model = RecordingModel()
    agent = PsychologyAgent(searcher=searcher, ollama=model, translator=NoTranslation())
    agent.process_query_sync(
        "Trí nhớ là gì?", chat_history=[
            {"role": "user", "content": "private prior topic"},
            {"role": "assistant", "content": "private prior answer [S8]"},
        ], embed_model=None, model="test-ollama", isolate_context=True,
    )
    prompt = "\n".join(message["content"] for message in model.calls[0]["messages"])
    assert "private prior topic" not in prompt
    assert "private prior answer" not in prompt
    assert "[S8]" not in prompt


def test_document_scoped_hybrid_search_does_not_return_other_documents(tmp_path):
    path = tmp_path / "scope.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE chunks (
            id INTEGER PRIMARY KEY, doc_id INTEGER, book_title TEXT, filename TEXT,
            page_num INTEGER, chunk_index INTEGER, text TEXT, embedding BLOB)""")
        db.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id')")
        rows = [
            (1, 41, "Alpha", "alpha.pdf", 1, 0, "Quasar belongs to the alpha document.", None),
            (2, 42, "Beta", "beta.pdf", 2, 0, "Quasar belongs to the beta document.", None),
        ]
        db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        db.executemany("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", [(row[0], row[6]) for row in rows])

    searcher = HybridSearcher(path)
    selected = searcher.search_hybrid("quasar", top_k=5, embed_model=None, document_id=41)
    all_docs = searcher.search_hybrid("quasar", top_k=5, embed_model=None)
    assert selected and {item["doc_id"] for item in selected} == {41}
    assert {item["doc_id"] for item in all_docs} == {41, 42}


def test_session_turn_round_trip_keeps_answer_mode_and_verified_scope(tmp_path):
    store = HistoryStore(tmp_path / "history.db")
    session_id = store.create_session()["id"]
    store.add_turn(
        session_id, "So sánh A và B", "Chưa đủ căn cứ.", [], "mock",
        answer_mode="compare", search_scope={"kind": "document", "document_id": 41},
    )
    turn = store.get_session(session_id)["turns"][0]
    assert turn["answer_mode"] == "compare"
    assert turn["search_scope"] == {"kind": "document", "document_id": 41}


@pytest.mark.parametrize(("alias", "canonical"), [
    ("summary", "quick"), ("step_by_step", "steps"), ("comparison", "compare"),
])
def test_legacy_answer_mode_aliases_are_canonical_before_processing(alias, canonical):
    assert server.ChatRequest(query="Câu hỏi", answer_mode=alias).answer_mode == canonical


@pytest.mark.parametrize("streaming", [False, True])
def test_chat_api_propagates_scope_and_persists_selected_mode(tmp_path, monkeypatch, streaming):
    store = HistoryStore(tmp_path / "history.db")
    monkeypatch.setattr(server, "history_store", store)
    monkeypatch.setattr(server, "dialogue_gate", DialogueGate(tmp_path / "dialogue.lock"))
    monkeypatch.setattr(server, "gpu_coordinator", SimpleNamespace(
        check_inference_allowed=lambda: (True, ""), acquire_for_inference=nullcontext,
    ))
    monkeypatch.setattr(server.document_lookup, "document", lambda _document_id: {"total_pages": 20})
    seen = {}

    def search_scope(kwargs):
        return {"kind": "page", "document_id": kwargs["source_page"][0], "page_num": kwargs["source_page"][1]} if kwargs.get("source_page") else {"kind": "document", "document_id": kwargs["source_document_id"]}

    if streaming:
        def response(**kwargs):
            seen.update(kwargs)
            yield {"type": "token", "content": "Bằng chứng đã được truy xuất lại."}
            yield {"type": "done", "citations": [], "search_scope": search_scope(kwargs)}
        monkeypatch.setattr(server.agent, "process_query_stream", response)
        route = "/api/chat/stream"
    else:
        def response(**kwargs):
            seen.update(kwargs)
            return {"answer": "Bằng chứng đã được truy xuất lại.", "citations": [], "search_scope": search_scope(kwargs)}
        monkeypatch.setattr(server.agent, "process_query_sync", response)
        route = "/api/chat"

    with TestClient(server.app) as client:
        result = client.post(route, json={
            "query": "Quasar là gì?", "answer_mode": "steps", "source_document_id": 41,
        })
        assert result.status_code == 200
        if streaming:
            done = next(json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ") and '"type": "done"' in line)
            session_id = done["session_id"]
        else:
            session_id = result.json()["session_id"]
        assert seen["answer_mode"] == "steps"
        assert seen["source_document_id"] == 41
        assert "source_page" not in seen
        turn = client.get(f"/api/sessions/{session_id}").json()["turns"][0]
        assert turn["answer_mode"] == "steps"
        assert turn["search_scope"] == {"kind": "document", "document_id": 41}

        # A selected page is a narrower current-request scope and is persisted as such.
        payload = {"query": "Quasar là gì?", "answer_mode": "compare", "source_document_id": 41, "source_page_num": 12, "session_id": session_id}
        second = client.post(route, json=payload)
        assert second.status_code == 200
        session = client.get(f"/api/sessions/{session_id}").json()
        assert session["turns"][1]["answer_mode"] == "compare"
        assert session["turns"][1]["search_scope"] == {"kind": "page", "document_id": 41, "page_num": 12}


def test_ui_exposes_three_modes_and_real_recovery_controls():
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "static/app.js").read_text(encoding="utf-8")
    assert all(f'value="{mode}"' in html for mode in ("quick", "steps", "compare"))
    assert "answer_mode: answerModeSelect" in js
    assert "submitRecoveryQuery(query, \"document\", searchScope)" in js
    assert "submitRecoveryQuery(query, \"library\", searchScope)" in js
    assert "suggestedActions: event.suggested_actions" in js
    assert "focus_single_concept: focusQuestionForEdit" in js
    assert "btn-resume-last-question" in html and "resume-source-list" in html
    assert "question.slice(0, 500)" in js
    assert "citation from" not in js.lower()
