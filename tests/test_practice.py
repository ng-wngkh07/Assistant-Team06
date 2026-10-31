import json
import sqlite3
import sys
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
    "source_chunk_id": 1,
    "evidence_quote": "Trong điều kiện A, hiện tượng X có thể xảy ra",
}
AUDIT_FIELDS = {
    "input_audit": "Đề bài nêu rõ điều kiện A và hiện tượng X.",
    "independent_solution": "Trong điều kiện A, hiện tượng X có thể xảy ra.",
    "choice_comparison": "Khớp với B, loại A, C, D.",
    "explanation_audit": "Giải thích chuẩn xác theo nguồn.",
}
VALIDATION = {
    "checks": [{
        "index": 1,
        **AUDIT_FIELDS,
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


def test_practice_rejects_page_with_unverified_visual_content_before_generation(monkeypatch):
    source = passage()
    source["page_quality"] = {
        "needs_review": True,
        "warnings": ["unrepresented_formula_or_diagram_suspected"],
    }
    service, _, ollama = make_service(passages=[source])
    monkeypatch.setattr(service, "_generate_text", lambda *args, **kwargs: pytest.fail("model called"))

    with pytest.raises(PracticeGenerationError, match="trang tài liệu gốc"):
        service.generate(1, 4, count=1, source_chunk_id=17)
    assert not ollama.prompts


def test_practice_attaches_quality_to_direct_learning_passages(monkeypatch):
    service, searcher, _ = make_service()
    source = passage()

    def attach(items):
        items[0]["page_quality"] = {
            "needs_review": True,
            "warnings": ["unrepresented_formula_or_diagram_suspected"],
        }

    monkeypatch.setattr(searcher, "_attach_page_quality_metadata", attach, raising=False)
    monkeypatch.setattr(service, "_generate_text", lambda *args, **kwargs: pytest.fail("model called"))
    with pytest.raises(PracticeGenerationError, match="trang tài liệu gốc"):
        service.generate_from_passages([source], count=1)


@pytest.mark.parametrize("quality", [
    {"needs_review": False, "warnings": ["unrepresented_formula_or_diagram_suspected"]},
    {"needs_review": True, "warnings": ["another_warning"]},
])
def test_practice_keeps_generating_when_visual_warning_does_not_require_review(quality):
    source = passage()
    source["page_quality"] = quality
    service, _, _ = make_service([{"questions": [QUESTION]}, VALIDATION], [source])

    result = service.generate(1, 4, count=1, model="test-model")
    assert len(result["questions"]) == 1


def test_generation_maps_short_prompt_handles_to_exact_database_sources(monkeypatch):
    """Real 9B returned ordinal 1/4 for five-digit IDs; handles are server-owned."""
    service, _, _ = make_service([])
    captured = []
    def chat(prompt, *args, **kwargs):
        captured.append(prompt)
        return json.dumps({'questions': [{**QUESTION, 'source_chunk_id': 2},
                                         {**QUESTION, 'source_chunk_id': 999}]})
    monkeypatch.setattr(service, '_chat_complete', chat)
    raw = service._generate_text([passage(82910), passage(83063)], 2, 'test-model')
    questions = json.loads(raw)['questions']
    assert [q['source_chunk_id'] for q in questions] == [83063]
    assert '<passage id="1"' in captured[0] and '<passage id="2"' in captured[0]
    assert '<passage id="82910"' not in captured[0]


def test_generation_decoder_binds_exact_quotes_to_their_visible_source(monkeypatch):
    """The live model paraphrased tables into fabricated 'verbatim' quotes."""
    service, _, _ = make_service([])
    first = passage(82910, text='Only a closed door and an engaged lock allow operation. Override bypasses heat only.')
    second = passage(83063, text='A private member cannot be accessed directly outside its class. Public methods provide access.')
    observed = []
    def chat(prompt, *args, **kwargs):
        observed.append(kwargs['response_schema'])
        return json.dumps({'questions': []})
    monkeypatch.setattr(service, '_chat_complete', chat)
    service._generate_text([first, second], 2, 'test-model')
    constraints = observed[0]['properties']['questions']['items']['allOf'][0]['anyOf']
    assert len(constraints) == 2
    for handle, (constraint, source) in enumerate(zip(constraints, [first, second]), 1):
        properties = constraint['properties']
        assert properties['source_chunk_id'] == {'const': handle}
        quotes = properties['evidence_quote']['enum']
        assert quotes and all(20 <= len(q) <= 240 for q in quotes)
        assert all(service._normalized(q) in service._normalized(source['text']) for q in quotes)
        other = second if handle == 1 else first
        assert all(service._normalized(q) not in service._normalized(other['text']) for q in quotes)


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
    assert '<passage id="1"' in ollama.prompts[0]
    assert '<passage id="2"' not in ollama.prompts[0]
    assert result["questions"][0]["source"]["chunk_id"] == 17


def test_page_scope_sends_at_most_six_passages_to_the_generator():
    passages = [passage(chunk_id) for chunk_id in range(17, 24)]
    service, searcher, ollama = make_service([{"questions": [QUESTION]}, VALIDATION], passages)
    service.generate(1, 4, count=1, model="test-model")
    assert searcher.calls == [(1, 4, 6)]
    assert '<passage id="6"' in ollama.prompts[0]
    assert '<passage id="7"' not in ollama.prompts[0]


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


@pytest.mark.skipif(sys.platform != "darwin", reason="Native Mac MLX deployment")
def test_practice_routes_mlx_model_to_trained_client(monkeypatch):
    class MockTrainedClient:
        def __init__(self):
            self.last_done_reason = "stop"
        def chat_complete(self, messages, **kwargs):
            return "fake-mlx-output"

    monkeypatch.setattr("app.practice.TrainedModelClient", MockTrainedClient)
    service = PracticeService(None, None, ollama=None)
    for model_name in ("qwen3.5-9b-4bit", "mlx-9b", "qwen3.5:9b"):
        res = service._chat_complete("test prompt", model=model_name, max_tokens=100)
        assert res == "fake-mlx-output"


@pytest.mark.parametrize("invalid_count", [0, 11, -1, 2.5, "3", True, False, None])
def test_practice_count_validation(invalid_count):
    service, _, _ = make_service()
    with pytest.raises(PracticeGenerationError, match="Số câu phải là số nguyên từ 1 đến 10"):
        service.generate_from_passages([passage()], count=invalid_count, model="test-model")


@pytest.mark.parametrize("invalid_diff", ["extreme", "super-hard", "", "1", None])
def test_practice_difficulty_validation(invalid_diff):
    service, _, _ = make_service()
    with pytest.raises(PracticeGenerationError, match="Độ khó không hợp lệ"):
        service.generate_from_passages([passage()], count=3, difficulty=invalid_diff, model="test-model")


def test_practice_batching_and_honest_partial_warning():
    q1 = {**QUESTION, "question": "Câu hỏi kiểm tra số 1 về hiện tượng X?"}
    q2 = {
        **QUESTION,
        "question": "Câu hỏi kiểm tra số 2 về hiện tượng X?",
        "choices": {
            "A": "Hiện tượng X hoàn toàn độc lập.",
            "B": "Hiện tượng X phụ thuộc điều kiện A theo nguồn.",
            "C": "Hiện tượng X không xác định.",
            "D": "Hiện tượng X xảy ra ngẫu nhiên.",
        },
        "answer": "B",
    }
    batch1_gen = {"questions": [q1, q2]}
    batch1_val = {
        "checks": [
            {"index": 1, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "difficulty_matches": True, "verified_answer": "B"},
            {"index": 2, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "difficulty_matches": True, "verified_answer": "B"},
        ]
    }
    q3 = {**QUESTION, "question": "Câu hỏi kiểm tra số 3 về hiện tượng X?"}
    batch2_gen = {"questions": [q3]}
    batch2_val = {
        "checks": [
            {"index": 1, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "difficulty_matches": False, "verified_answer": "B"},
        ]
    }
    service, _, ollama = make_service([batch1_gen, batch1_val, batch2_gen, batch2_val])
    result = service.generate_from_passages([passage()], count=5, difficulty="hard", model="test-model", enforce_difficulty=True)
    assert result["requested_count"] == 5
    assert result["difficulty"] == "hard"
    assert result["count"] == 2
    assert result["complete"] is False
    assert "2/5 câu" in result["warning"]


def test_practice_recovers_from_truncated_batch_and_preserves_earlier_questions():
    """Truncated/failed batch attempt must not discard earlier accepted questions; should retry smaller batch."""
    q1 = {**QUESTION, "question": "Câu 1 đạt kiểm định về hiện tượng X?"}
    batch1_gen = {"questions": [q1]}
    batch1_val = {
        "checks": [
            {"index": 1, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"},
        ]
    }
    # Batch 2 simulates truncated/invalid model response that triggers PracticeGenerationError
    batch2_gen = "INVALID TRUNCATED JSON {questions: ["
    # Batch 3 recovers and yields second valid question
    q2 = {
        **QUESTION,
        "question": "Câu 2 đạt kiểm định sau khi phục hồi?",
        "choices": {
            "A": "Hiện tượng X độc lập.",
            "B": "Hiện tượng X xảy ra có điều kiện.",
            "C": "Hiện tượng X vô định.",
            "D": "Hiện tượng X không xảy ra.",
        },
        "answer": "B",
    }
    batch3_gen = {"questions": [q2]}
    batch3_val = {
        "checks": [
            {"index": 1, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"},
        ]
    }
    service, _, _ = make_service([batch1_gen, batch1_val, batch2_gen, batch3_gen, batch3_val])
    result = service.generate_from_passages([passage()], count=2, model="test-model", allow_retry=True)
    assert result["count"] == 2
    assert result["complete"] is True
    assert len(result["questions"]) == 2
    assert result["questions"][0]["question"] == "Câu 1 đạt kiểm định về hiện tượng X?"
    assert result["questions"][1]["question"] == "Câu 2 đạt kiểm định sau khi phục hồi?"


def test_practice_fast_perfect_count_10():
    """Verify requesting 10 questions works in 4 perfect batches (3+3+3+1) within max_attempts bounds."""
    batches = []
    checks = []
    for i in range(1, 11):
        q = {
            **QUESTION,
            "question": f"Câu hỏi số {i} về hiện tượng X trong điều kiện A?",
            "choices": {
                "A": f"Hiện tượng X luôn xảy ra phương án {i}.",
                "B": f"Hiện tượng X có thể xảy ra phương án {i}.",
                "C": f"Hiện tượng X không thể xảy ra phương án {i}.",
                "D": f"Hiện tượng X ngẫu nhiên phương án {i}.",
            },
            "answer": "B",
        }
        batches.append(q)

    responses = [
        {"questions": batches[0:3]},
        {"checks": [{"index": j, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"} for j in range(1, 4)]},
        {"questions": batches[3:6]},
        {"checks": [{"index": j, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"} for j in range(1, 4)]},
        {"questions": batches[6:9]},
        {"checks": [{"index": j, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"} for j in range(1, 4)]},
        {"questions": batches[9:10]},
        {"checks": [{"index": 1, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"}]},
    ]
    service, _, _ = make_service(responses)
    result = service.generate_from_passages([passage()], count=10, model="test-model")
    assert result["count"] == 10
    assert result["complete"] is True
    assert result["requested_count"] == 10
    assert len(result["questions"]) == 10


def test_practice_deadline_bounding_raises_when_no_questions(monkeypatch):
    """When elapsed time exceeds deadline and 0 questions are produced, raises PracticeGenerationError."""
    import time
    fake_now = 1000.0
    def mock_monotonic():
        nonlocal fake_now
        val = fake_now
        fake_now += 200.0  # simulates > 180s elapsed
        return val

    monkeypatch.setattr(time, "monotonic", mock_monotonic)
    service, _, _ = make_service([{"questions": [QUESTION]}, VALIDATION])
    with pytest.raises(PracticeGenerationError, match="Quá thời gian tạo câu hỏi"):
        service.generate_from_passages([passage()], count=3, model="test-model")


def test_five_questions_allow_two_realistic_generation_and_review_batches(monkeypatch):
    """A fixed 180s total must not silently cap a five-question request to one batch."""
    import subprocess
    from types import SimpleNamespace
    clock = SimpleNamespace(now=1000.)
    questions = []
    for i in range(5):
        question = {**QUESTION, 'question': f'Câu hỏi độc lập số {i + 1} về điều kiện A?',
                    'choices': {**QUESTION['choices'], 'B': f'Điều kiện A là căn cứ được nêu, cách diễn đạt {i + 1}.'}}
        questions.append(question)
    responses = []
    for batch in (questions[:3], questions[3:]):
        responses.extend([{'questions': batch}, {'checks': [
            {'index': index, **AUDIT_FIELDS, 'supported': True, 'explanation_supported': True,
             'unique_answer': True, 'qualifiers_preserved': True, 'verified_answer': 'B'}
            for index in range(1, len(batch) + 1)]}])
    service, _, _ = make_service([])
    calls = []
    def timed_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        seconds = 100. if max_tokens == 1800 else 20.
        calls.append(timeout)
        if timeout < seconds:
            raise subprocess.TimeoutExpired('fixture-native-inference', timeout)
        clock.now += seconds
        return json.dumps(responses.pop(0), ensure_ascii=False)
    monkeypatch.setattr('app.practice.time.monotonic', lambda: clock.now)
    monkeypatch.setattr(service, '_chat_complete', timed_chat)
    result = service.generate_from_passages([passage()], count=5, model='test-model')
    assert result['count'] == 5 and result['complete'] is True
    assert len(calls) == 4 and all(0 < timeout <= 180 for timeout in calls)


def test_practice_timeout_preserves_verified_partial_questions(monkeypatch):
    """When timeout occurs on attempt 2 after attempt 1 produced a verified question, partial set is returned."""
    import subprocess
    q1 = {**QUESTION, "question": "Câu 1 kiểm tra thành công về hiện tượng X?"}
    batch1_gen = {"questions": [q1]}
    batch1_val = {
        "checks": [
            {"index": 1, **AUDIT_FIELDS, "supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "B"},
        ]
    }
    service, _, ollama = make_service([batch1_gen, batch1_val])
    original_generate = service._generate_text
    call_count = 0

    def mock_generate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return original_generate(*args, **kwargs)
        raise subprocess.TimeoutExpired(cmd="chat", timeout=5.0)

    monkeypatch.setattr(service, "_generate_text", mock_generate)
    result = service.generate_from_passages([passage()], count=3, model="test-model", allow_retry=True)
    assert result["count"] == 1
    assert result["requested_count"] == 3
    assert result["complete"] is False
    assert "1/3 câu" in result["warning"]


def test_practice_busy_error_is_reraised_immediately(monkeypatch):
    """GPUBusyError or DialogueBusyError must re-raise immediately without swallow or batch=1 retry."""
    from app.gpu_lock import GPUBusyError
    service, _, _ = make_service([])
    def mock_generate(*args, **kwargs):
        raise GPUBusyError("GPU đang bận phục vụ tác vụ khác")

    monkeypatch.setattr(service, "_generate_text", mock_generate)
    with pytest.raises(GPUBusyError, match="GPU đang bận"):
        service.generate_from_passages([passage()], count=3, model="test-model")
