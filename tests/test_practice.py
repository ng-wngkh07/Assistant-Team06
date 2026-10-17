import json
import sqlite3
from pathlib import Path

import pytest

from app.practice import PracticeGenerationError, PracticeService


PASSAGE = "Trong điều kiện A, hiện tượng X có thể xảy ra; tài liệu không khẳng định nó luôn xảy ra."
QUESTION = {
    "question": "Theo tài liệu, hiện tượng X trong điều kiện A như thế nào?",
    "choices": {
        "A": "Hiện tượng X luôn xảy ra.",
        "B": "Hiện tượng X có thể xảy ra.",
        "C": "Hiện tượng X không thể xảy ra.",
        "D": "Hiện tượng X xảy ra trong mọi điều kiện.",
    },
    "answer": "B",
    "explanation": "Nguồn chỉ nói hiện tượng có thể xảy ra trong điều kiện A.",
    "source_chunk_id": 17,
    "evidence_quote": "Trong điều kiện A, hiện tượng X có thể xảy ra",
}
VALIDATION = {
    "checks": [{
        "index": 1,
        "supported": True,
        "explanation_supported": True,
        "unique_answer": True,
        "qualifiers_preserved": True,
        "verified_answer": "B",
    }]
}


class FakeSearcher:
    def __init__(self, passages):
        self.passages = passages
        self.calls = []

    def source_page_chunks(self, doc_id, page_num, limit=12):
        self.calls.append((doc_id, page_num, limit))
        return [item for item in self.passages if item["doc_id"] == doc_id and item["page_num"] == page_num][:limit]


class FakeOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def chat_complete(self, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        self.last_done_reason = "stop"
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


class FakeDocumentLookup:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def document(self, doc_id):
        return {"id": doc_id, "filename": "course.pdf", "clean_title": "Tài liệu môn học", "total_pages": 10}

    def pdf_path(self, filename):
        return Path("course.pdf") if filename.endswith(".pdf") else None

    def connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db


def passage(chunk_id=17, page=4, text=PASSAGE):
    return {
        "chunk_id": chunk_id,
        "doc_id": 1,
        "book_title": "Tài liệu môn học",
        "filename": "course.pdf",
        "page_num": page,
        "chunk_index": chunk_id,
        "text": text,
        "safe_text": text,
    }


def make_service(responses=None, passages=None, db_path=None):
    ollama = FakeOllama(responses or [])
    searcher = FakeSearcher([passage()] if passages is None else passages)
    service = PracticeService(searcher, FakeDocumentLookup(db_path), ollama=ollama)
    return service, searcher, ollama


def test_answers_are_withheld_until_answer_submission_and_source_is_specific():
    service, _, _ = make_service([{"questions": [QUESTION]}, VALIDATION])

    result = service.generate(1, 4, count=1, model="test-model")

    public_question = result["questions"][0]
    assert set(public_question) == {"id", "question", "choices", "source"}
    assert "answer" not in public_question
    assert "explanation" not in public_question
    assert "snippet" not in public_question["source"]
    assert public_question["source"]["chunk_id"] == 17
    assert public_question["source"]["page_num"] == 4

    feedback = service.answer(result["practice_id"], public_question["id"], "B")
    assert feedback["is_correct"] is True
    assert feedback["correct_answer"] == "B"
    assert feedback["explanation"] == QUESTION["explanation"]
    assert feedback["source"]["snippet"] == QUESTION["evidence_quote"]
    assert feedback["source"]["pdf_url"].endswith("/1/pdf#page=4")


def test_unsure_answer_still_returns_feedback_only_after_submission():
    service, _, _ = make_service([{"questions": [QUESTION]}, VALIDATION])
    result = service.generate(1, 4, count=1, model="test-model")
    feedback = service.answer(result["practice_id"], result["questions"][0]["id"], None)
    assert feedback["is_correct"] is False
    assert feedback["submitted_answer"] is None
    assert feedback["correct_answer"] == "B"


def test_question_without_an_exact_source_quote_is_rejected_before_verification():
    unsupported = {**QUESTION, "evidence_quote": "A sentence that does not occur in the selected passage."}
    service, _, ollama = make_service([{"questions": [unsupported]}])
    with pytest.raises(PracticeGenerationError, match="[Cc]hưa tạo được câu hỏi"):
        service.generate(1, 4, count=1, model="test-model")
    assert len(ollama.prompts) == 1


def test_empty_selected_page_reports_that_the_source_is_insufficient():
    service, _, ollama = make_service([{"questions": []}], passages=[])
    with pytest.raises(PracticeGenerationError, match="chưa có đoạn văn bản"):
        service.generate(1, 4, count=1, model="test-model")
    assert not ollama.prompts


@pytest.mark.parametrize("check", [
    {"supported": False, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"},
    {"supported": True, "explanation_supported": False, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"},
    {"supported": True, "unique_answer": False, "qualifiers_preserved": True, "verified_answer": "B"},
    {"supported": True, "unique_answer": True, "qualifiers_preserved": False, "verified_answer": "B"},
    {"supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "A"},
])
def test_unsupported_ambiguous_or_overstated_questions_are_rejected(check):
    service, _, _ = make_service([{"questions": [QUESTION]}, {"checks": [{"index": 1, **check}]}])
    with pytest.raises(PracticeGenerationError, match="[Kk]hông có câu hỏi"):
        service.generate(1, 4, count=1, model="test-model")


def test_exact_selected_chunk_is_the_only_source_sent_to_model():
    other = passage(18, text="Đoạn khác không thuộc lựa chọn của người học.")
    service, searcher, ollama = make_service([{"questions": [QUESTION]}, VALIDATION], [passage(), other])
    result = service.generate(1, 4, count=1, model="test-model", source_chunk_id=17)
    assert searcher.calls == [(1, 4, 1000)]
    assert '<passage id="17"' in ollama.prompts[0]
    assert '<passage id="18"' not in ollama.prompts[0]
    assert result["questions"][0]["source"]["chunk_id"] == 17


def test_page_scope_sends_at_most_six_passages_to_the_generator():
    passages = [passage(chunk_id) for chunk_id in range(17, 24)]
    service, searcher, ollama = make_service([{"questions": [QUESTION]}, VALIDATION], passages)
    service.generate(1, 4, count=1, model="test-model")
    assert searcher.calls == [(1, 4, 6)]
    assert '<passage id="22"' in ollama.prompts[0]
    assert '<passage id="23"' not in ollama.prompts[0]


def test_example_extraction_returns_original_source_text_and_ocr_warning(tmp_path):
    db_path = tmp_path / "learning.db"
    original = "Bài tập 2.3 — Tính đạo hàm của hàm số f(x) = x².\nGiữ nguyên dấu và xuống dòng."
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY, is_scanned INTEGER, status TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, book_title TEXT,
              filename TEXT, page_num INTEGER, chunk_index INTEGER, text TEXT);
        """)
        db.execute("INSERT INTO documents VALUES(1, 1, 'ocr_partial_reviewed')")
        db.execute("INSERT INTO chunks VALUES(17, 1, 'Tài liệu môn học', 'course.pdf', 4, 0, ?)", (original,))
        db.execute("INSERT INTO chunks VALUES(18, 1, 'Tài liệu môn học', 'course.pdf', 5, 0, 'Bài tập: tích phân')")
        db.execute("INSERT INTO chunks VALUES(20, 1, 'Tài liệu môn học', 'course.pdf', 4, 1, 'Bài tập: tích phân')")
        db.execute("INSERT INTO chunks VALUES(19, 1, 'Tài liệu môn học', 'course.pdf', 4, 1, 'Đoạn lý thuyết không có marker')")
    service, _, _ = make_service(passages=[], db_path=db_path)

    found = service.list_example_exercises(1, 4, 4, topic="đạo hàm")

    assert len(found) == 1
    item = found[0]
    assert item["text"] == original
    assert item["origin"] == "source_document"
    assert item["title"] == "Tài liệu môn học"
    assert item["page_num"] == 4
    assert item["pdf_url"].endswith("/1/pdf#page=4")
    assert item["needs_source_check"] is True
    assert item["source_warning"]


def test_example_page_range_is_checked_against_document():
    service, _, _ = make_service()
    with pytest.raises(PracticeGenerationError, match="khoảng trang hợp lệ"):
        service.list_example_exercises(1, 2, 11)


def test_example_extraction_returns_empty_when_source_has_no_exercises(tmp_path):
    db_path = tmp_path / "learning.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY, is_scanned INTEGER, status TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, book_title TEXT,
              filename TEXT, page_num INTEGER, chunk_index INTEGER, text TEXT);
        """)
        db.execute("INSERT INTO documents VALUES(1, 0, 'indexed')")
        db.execute(
            "INSERT INTO chunks VALUES(17, 1, 'Tài liệu môn học', 'course.pdf', 4, 0, ?)",
            ("Đoạn lý thuyết không có bài tập hoặc câu hỏi.",),
        )
    service, _, _ = make_service(passages=[], db_path=db_path)
    assert service.list_example_exercises(1, 4, 4) == []


def test_practice_routes_mlx_model_to_trained_client(monkeypatch):
    class MockTrainedClient:
        def __init__(self):
            self.last_done_reason = "stop"
        def chat_complete(self, messages, **kwargs):
            return "fake-mlx-output"

    monkeypatch.setattr("app.practice.TrainedModelClient", MockTrainedClient)
    service = PracticeService(None, None, ollama=None)
    for model_name in ("qwen2.5-3b-4bit", "local", "tam-ly-mlx", "mlx-3b"):
        res = service._chat_complete("test prompt", model=model_name, max_tokens=100)
        assert res == "fake-mlx-output"

