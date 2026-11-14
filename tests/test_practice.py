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
CAUSAL_PASSAGE = 'Trong điều kiện A, hiện tượng X có thể xảy ra vì quá trình Y.'
CAUSAL_QUESTION = dict(QUESTION, question='Vì sao hiện tượng X có thể xảy ra trong điều kiện A?',
                       evidence_quote=CAUSAL_PASSAGE,
                       choices={'A': 'Vì quá trình Z.', 'B': 'Vì quá trình Y.',
                                'C': 'Vì quá trình T.', 'D': 'Vì quá trình H.'},
                       explanation='Theo nguồn, nguyên nhân là quá trình Y.')
AUDIT_FIELDS = {
    "input_audit": "Đề bài nêu rõ điều kiện A và hiện tượng X.",
    "independent_solution": "Trong điều kiện A, hiện tượng X có thể xảy ra.",
    "choice_comparison": "Khớp với B, loại A, C, D.",
    "explanation_audit": "Giải thích chuẩn xác theo nguồn.",
}
VALIDATION = {
    "checks": [{
        "index": 1,
        "source_basis": QUESTION['evidence_quote'],
        "matching_choices": "B",
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


@pytest.mark.parametrize('count,difficulty,compact', [(1, None, True), (2, None, True), (3, None, True), (1, "medium", True), (4, None, False)])
def test_native_small_review_uses_grounded_base_without_adapter(monkeypatch, count, difficulty, compact):
    import app.model_resolver as resolver
    import app.practice_adapter as profile
    from app.model_resolver import DEFAULT_CHAT_MODEL
    service, _, _ = make_service()
    prompts = []
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    monkeypatch.setattr(profile, 'active_practice_adapter', lambda model: None)
    def respond(prompt, *args, **kwargs):
        prompts.append(prompt)
        fields = kwargs['response_schema']['properties']['checks']['items']['properties']
        if 'missing' in fields:
            return json.dumps({'checks': [dict(index=i, asked='Fixture question', source_says='Fixture source',
                                               missing=[], supported=True) for i in range(1, count + 1)]})
        return '{"checks": []}'
    monkeypatch.setattr(service, '_chat_complete', respond)
    questions = [dict(QUESTION, source={'chunk_id': 17}) for _ in range(count)]
    service._verify_questions(questions, {17: passage()}, DEFAULT_CHAT_MODEL, difficulty=difficulty)
    assert len(prompts) == 1
    assert ('source_basis: Copy a contiguous exact source substring' in prompts[-1]) is compact
    if compact:
        from app.practice_proof import native_grounded_review_prompt
        for prompt in prompts:
            items = json.JSONDecoder().raw_decode(prompt.split('Dữ liệu cần kiểm định:\n', 1)[1])[0]
            assert prompt == native_grounded_review_prompt(items, difficulty=difficulty)
            assert len(items) == count and [item['index'] for item in items] == list(range(1, count + 1))
            assert all('proposed_answer' not in item and 'choices' in item and 'explanation' in item for item in items)


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


@pytest.mark.parametrize('checks', [
    [],
    [{'index': True, 'asked': 'Question', 'source_says': 'Source', 'missing': [], 'supported': True}],
    [{'index': 2, 'asked': 'Question', 'source_says': 'Source', 'missing': [], 'supported': True}],
    [{'index': 1, 'asked': '', 'source_says': 'Source', 'missing': [], 'supported': True}],
    [{'index': 1, 'asked': 'Question', 'source_says': 'Source', 'missing': ['Required input'], 'supported': True}],
    [{'index': 1, 'asked': 'Question', 'source_says': 'Source', 'missing': [], 'supported': False}],
    [{'index': 1, 'asked': 'Question', 'source_says': 'Source', 'missing': [], 'supported': True}] * 2,
])
def test_native_causal_review_rejects_incomplete_or_contradictory_checks(monkeypatch, checks):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    service, _, _ = make_service()
    calls = []
    def respond(prompt, *args, **kwargs):
        calls.append(prompt)
        return json.dumps({'checks': checks})
    monkeypatch.setattr(service, '_chat_complete', respond)
    causal = dict(CAUSAL_QUESTION, source={'chunk_id': 17})
    assert service._verify_questions([causal], {17: passage(text=CAUSAL_PASSAGE)}, 'test') == []
    assert len(calls) == 1


def test_native_reserve_remaps_index_after_batch_and_shares_deadline(monkeypatch):
    import app.model_resolver as resolver
    import app.practice as practice
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    ticks = iter([100.0, 100.0, 100.0, 103.0, 103.0])
    monkeypatch.setattr(practice.time, 'monotonic', lambda: next(ticks))
    questions = [dict(QUESTION, question=f'Fixture question {i}?', source={'chunk_id': 17+i}) for i in range(4)]
    service, _, _ = make_service()
    calls = []
    def respond(prompt, *args, **kwargs):
        calls.append((prompt, kwargs))
        items = json.loads(prompt.split('Dữ liệu cần kiểm định:\n')[1])
        return json.dumps({'checks': [dict(VALIDATION['checks'][0], index=item['index'],
            explanation_supported=item['question'] != questions[0]['question']) for item in items]})
    monkeypatch.setattr(service, '_chat_complete', respond)
    assert service._verify_questions(questions, {17+i: passage(17+i) for i in range(4)},
                                     'test', timeout=20, stop_after=3) == questions[1:]
    assert calls[0][1]['timeout'] == 20
    assert calls[1][1]['timeout'] == 17
    records = json.JSONDecoder().raw_decode(calls[1][0].split('Dữ liệu cần kiểm định:\n', 1)[1])[0]
    assert len(records) == 1 and records[0]['index'] == 1 and records[0]['question'] == questions[3]['question']
    assert 'proposed_answer' not in records[0]
    assert calls[1][1]['response_schema']['properties']['checks']['minItems'] == 1


def test_native_causal_admission_still_requires_full_four_audit_review(monkeypatch):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    question = dict(CAUSAL_QUESTION, source={'chunk_id': 17})
    service, _, _ = make_service()
    calls = []
    def respond(prompt, *args, **kwargs):
        calls.append((prompt, kwargs))
        return json.dumps(VALIDATION)
    monkeypatch.setattr(service, '_chat_complete', respond)
    assert service._verify_questions([question], {17: passage(text=CAUSAL_PASSAGE)}, 'test', timeout=20) == [question]
    assert len(calls) == 1 and calls[0][1]['timeout'] == 20
    full_schema = calls[0][1]['response_schema']['properties']['checks']['items']['properties']
    assert set(AUDIT_FIELDS) <= full_schema.keys()
    item = json.JSONDecoder().raw_decode(calls[0][0].split('Dữ liệu cần kiểm định:\n', 1)[1])[0][0]
    assert item['source'] == CAUSAL_PASSAGE and 'proposed_answer' not in item



def test_native_isolated_reviews_stop_at_shared_deadline_without_accepting_unreviewed(monkeypatch):
    import app.model_resolver as resolver
    import app.practice as practice
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    ticks = iter([100.0, 100.0, 100.0, 121.0])
    monkeypatch.setattr(practice.time, 'monotonic', lambda: next(ticks))
    service, _, _ = make_service()
    calls = []
    monkeypatch.setattr(service, '_chat_complete', lambda *args, **kwargs: calls.append(kwargs) or json.dumps(VALIDATION))
    first = dict(QUESTION, source={'chunk_id': 17})
    second = dict(QUESTION, question='Second fixture question?', source={'chunk_id': 17})
    assert service._verify_questions([first, second], {17: passage()}, 'test', timeout=20) == [first]
    assert len(calls) == 1 and calls[0]['timeout'] == 20


def test_native_statement_proposals_batch_bounded_reserve_without_repeated_startup(monkeypatch):
    import app.model_resolver as resolver
    import app.practice as practice
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    sources = [passage(31 + i, text=f'The {name} is an object described by its explicitly stated property in this source.')
               for i, name in enumerate(['widget', 'gadget', 'device', 'sensor'])]
    service, _, _ = make_service()
    calls = []
    def respond(prompt, *args, **kwargs):
        calls.append((prompt, kwargs))
        props = kwargs['response_schema']['properties']['questions']
        quotes = props['items']['allOf'][0]['anyOf']
        return json.dumps({'questions': [dict(QUESTION, source_chunk_id=i+1,
            evidence_quote=entry['properties']['evidence_quote']['enum'][0]) for i,entry in enumerate(quotes)]})
    monkeypatch.setattr(service, '_chat_complete', respond)
    result = json.loads(service._generate_text(sources, 3, 'test', timeout=20))
    assert [q['source_chunk_id'] for q in result['questions']] == [31, 32, 33, 34]
    assert len(calls) == 1 and calls[0][1]['timeout'] == 20
    assert calls[0][0].count('<passage id=') == 4
    assert calls[0][1]['response_schema']['properties']['questions']['maxItems'] == 4
    assert calls[0][1]['response_schema']['properties']['questions']['minItems'] == 4
    assert all(kwargs['response_schema']['properties']['questions']['items']['properties']['question']['pattern'] == '^Theo đoạn trích, '
               for _, kwargs in calls)


def test_native_reserve_reviews_every_accepted_question_and_stops_after_three(monkeypatch):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    service, _, _ = make_service()
    calls = []
    def respond(prompt, *args, **kwargs):
        calls.append((prompt, kwargs))
        items = json.loads(prompt.split('Dữ liệu cần kiểm định:\n')[1])
        return json.dumps({'checks': [dict(VALIDATION['checks'][0], index=item['index'],
            explanation_supported=item['question'] != questions[0]['question']) for item in items]})
    monkeypatch.setattr(service, '_chat_complete', respond)
    questions = [dict(QUESTION, question=f'Fixture question number {i}?', source={'chunk_id': 17+i}) for i in range(4)]
    sources = {17+i:passage(17+i) for i in range(4)}
    result = service._verify_questions(questions, sources, 'test', timeout=180, stop_after=3)
    assert result == questions[1:] and len(calls) == 2
    assert all(set(AUDIT_FIELDS) <= kwargs['response_schema']['properties']['checks']['items']['properties'].keys() for _,kwargs in calls)
    calls.clear()
    result = service._verify_questions(questions, sources, 'test', timeout=180, stop_after=2)
    assert result == questions[1:3] and len(calls) == 1


@pytest.mark.parametrize('reject_first', [False, True])
def test_three_question_request_can_use_reviewed_reserve_without_exposing_keys(monkeypatch, reject_first):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    sources = [passage(17+i) for i in range(4)]
    service, _, _ = make_service(passages=sources)
    proposals = [dict(QUESTION, question=f'Fixture question number {i}?', source_chunk_id=17+i) for i in range(4)]
    monkeypatch.setattr(service, '_generate_text', lambda *args, **kwargs: json.dumps({'questions': proposals}))
    calls = []
    def review(prompt, *args, **kwargs):
        calls.append(kwargs)
        items = json.loads(prompt.split('Dữ liệu cần kiểm định:\n')[1])
        return json.dumps({'checks': [dict(VALIDATION['checks'][0], index=item['index'],
            explanation_supported=not (reject_first and item['question'] == proposals[0]['question'])) for item in items]})
    monkeypatch.setattr(service, '_chat_complete', review)
    result = service.generate_from_passages(sources, count=3, model='test')
    assert result['complete'] is True and result['count'] == result['requested_count'] == 3
    expected = proposals[1:] if reject_first else proposals[:3]
    assert [q['question'] for q in result['questions']] == [q['question'] for q in expected]
    assert len(calls) == (2 if reject_first else 1)
    assert all('answer' not in q and 'explanation' not in q for q in result['questions'])


def test_remote_three_question_review_preserves_full_output_budget(monkeypatch):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: False)
    service, _, _ = make_service()
    calls = []
    monkeypatch.setattr(service, '_chat_complete', lambda *args, **kwargs: calls.append((args, kwargs)) or '{"checks": []}')
    service._verify_questions([dict(QUESTION, source={'chunk_id': 17}) for _ in range(3)], {17: passage()}, 'test')
    assert len(calls) == 1 and calls[0][0][2] >= 1500
    assert calls[0][1]['response_schema']['properties']['checks']['minItems'] == 3


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


def test_practice_generation_prompt_distinguishes_source_rules_and_forbids_explanation_overreach():
    from app.practice import build_practice_generation_prompt
    prompt = build_practice_generation_prompt([passage()], count=1)
    assert "Phân biệt rạch ròi giữa quy tắc có trong passage với quy ước hoặc dữ kiện do câu hỏi tự đặt ra" in prompt
    assert "Tuyệt đối không khẳng định trong explanation rằng passage/tài liệu nêu một quy ước, định nghĩa hay quy tắc khi passage không hề chứa quy tắc đó" in prompt


def test_practice_review_prompt_guides_honest_input_and_explanation_audits():
    from app.practice import build_practice_review_prompt
    item = {"index": 1, "source": "Passage.", "question": "Question?", "choices": {"A": "1", "B": "2", "C": "3", "D": "4"}, "proposed_answer": "A", "explanation": "Expl.", "evidence_quote": "Passage."}
    prompt = build_practice_review_prompt(item)
    assert "Phân biệt dữ kiện câu hỏi với quy tắc nguồn: câu hỏi hỏi giá trị hàm/đạo hàm tại điểm số (như f'(-7))" in prompt
    assert "Đọc chính xác explanation, không bịa ra thiếu sót mà explanation đã nêu rõ" in prompt


def test_practice_verification_rejects_explanation_attributing_absent_rules_to_source():
    syntax_only_passage = passage(
        chunk_id=126,
        text="Truy xuất phần tử của mảng.\nCú pháp:\n<Tên mảng>[<Chỉsố>]\n"
    )
    overreaching_question = {
        "question": "Cho mảng int a[10], đề bài quy ước chỉ số bắt đầu từ 0. Cú pháp truy xuất phần tử thứ 5 là gì?",
        "choices": {"A": "a[4]", "B": "a[5]", "C": "a(4)", "D": "a.5"},
        "answer": "A",
        "explanation": "Theo tài liệu, mảng đánh chỉ số từ 0 nên phần tử thứ 5 là a[4].",
        "source": {"chunk_id": 126},
        "evidence_quote": "<Tên mảng>[<Chỉsố>]",
    }
    # When explanation audit detects overreach (attributing absent zero-based indexing rule to passage with only syntax),
    # explanation_supported must be False.
    rejected_check = {
        "index": 1,
        "input_audit": "Đề bài cho mảng a[10], quy ước chỉ số từ 0 và hỏi phần tử thứ 5; đủ dữ kiện.",
        "independent_solution": "Tài liệu chỉ nêu cú pháp <Tên mảng>[<Chỉsố>].",
        "choice_comparison": "a[4] phù hợp với quy ước C, khớp A.",
        "explanation_audit": "Explanation sai vì khẳng định tài liệu quy định chỉ số từ 0 trong khi tài liệu chỉ nêu cú pháp truy xuất.",
        "supported": True,
        "explanation_supported": False,
        "unique_answer": True,
        "qualifiers_preserved": True,
        "verified_answer": "A",
    }
    service, _, _ = make_service([{"checks": [rejected_check]}])
    verified = service._verify_questions([overreaching_question], {126: syntax_only_passage}, "test-model")
    assert verified == []


def test_practice_verification_accepts_honest_audit_for_constant_derivative():
    derivative_passage = passage(
        chunk_id=158,
        text="1. f(x) = 2^2016\nSolution: the derivative is 0 since f is constant."
    )
    derivative_question = {
        "question": "Cho f(x)=2^2016. Theo lời giải trong nguồn, giá trị f'(-7) là bao nhiêu?",
        "choices": {"A": "2^2016", "B": "0", "C": "2016·2^2015", "D": "-7"},
        "answer": "B",
        "explanation": "f(x)=2^2016 là hàm hằng nên f'(x)=0 với mọi x, f'(-7)=0.",
        "source": {"chunk_id": 158},
        "evidence_quote": "the derivative is 0 since f is constant.",
    }
    honest_check = {
        "index": 1,
        "input_audit": "Question nêu rõ hàm f(x)=2^2016 và yêu cầu tính f'(-7); đủ dữ kiện.",
        "independent_solution": "f là hàm hằng nên đạo hàm luôn bằng 0, f'(-7)=0.",
        "choice_comparison": "Kết quả 0 khớp duy nhất với lựa chọn B.",
        "explanation_audit": "Explanation giải đúng theo quy tắc đạo hàm hàm hằng của nguồn.",
        "supported": True,
        "explanation_supported": True,
        "unique_answer": True,
        "qualifiers_preserved": True,
        "verified_answer": "B",
    }
    service, _, _ = make_service([{"checks": [honest_check]}])
    verified = service._verify_questions([derivative_question], {158: derivative_passage}, "test-model")
    assert len(verified) == 1
    assert verified[0]["answer"] == "B"


@pytest.mark.parametrize('accept_review', [True, False])
def test_generated_duplicate_distractors_are_repaired_before_source_review(monkeypatch, accept_review):
    from app.math_choices import equivalent_choice_pair
    quote = "For f(x) = 2x^2, the power rule gives f'(x) = 4x."
    item = dict(QUESTION, question="For f(x) = 2x^2, what is its derivative?",
                choices={'A': '2*2x', 'B': '4x', 'C': '4x+1', 'D': '2x'},
                explanation="The cited power rule gives 4x.", answer='B',
                source_chunk_id=17, evidence_quote=quote)
    service, _, _ = make_service(passages=[passage(text=quote)])
    monkeypatch.setattr(service, '_generate_text', lambda *args, **kwargs: json.dumps({'questions': [item]}))
    def review(prompt, *args, **kwargs):
        records = json.loads(prompt.split('Dữ liệu cần kiểm định:\n')[1])
        record = records[0]
        assert record['source'] == record['evidence_quote'] == quote
        assert record['explanation'] == item['explanation']
        assert record['choices']['B'] == '4x'
        choices = [{'label': key, 'text': text} for key, text in record['choices'].items()]
        assert equivalent_choice_pair(choices) is None
        return json.dumps({'checks': [{**VALIDATION['checks'][0], 'explanation_supported': accept_review}]})
    monkeypatch.setattr(service, '_chat_complete', review)
    if accept_review:
        result = service.generate(1, 4, count=1, model='test-model')
        assert result['complete'] is True
        assert result['questions'][0]['choices'][1]['text'] == '4x'
    else:
        with pytest.raises(PracticeGenerationError, match='đạt kiểm tra'):
            service.generate(1, 4, count=1, model='test-model')
