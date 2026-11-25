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


def _bank_test_deps(passages, page_quality=None):
    class BankSearcher:
        def __init__(self, items):
            self._items = items
        def source_page_chunks(self, doc_id, page_num, limit=6):
            return [dict(item) for item in self._items]
        def _attach_page_quality_metadata(self, items):
            if page_quality is not None:
                for item in items:
                    item["page_quality"] = dict(page_quality)

    class BankDocLookup:
        def document(self, doc_id):
            return {"id": doc_id, "clean_title": "Toán rời rạc", "filename": "toan.pdf"}
        def inspect_document_status(self, doc_id):
            return {"fresh": True, "doc": self.document(doc_id)}
        def pdf_path(self, filename):
            return None

    return BankSearcher(passages), BankDocLookup()


def _wire_fake_practice_llm(service, generated_batches):
    gen_calls = []
    verify_calls = []

    def fake_generate(passages, batch_size, model, difficulty="medium", purpose="practice",
                      weak_topics=None, exclude_questions=None, timeout=None):
        idx = min(len(gen_calls), len(generated_batches) - 1)
        gen_calls.append({
            "batch_size": batch_size,
            "model": model,
            "difficulty": difficulty,
            "exclude_questions": list(exclude_questions or []),
        })
        return json.dumps({"questions": generated_batches[idx]})

    def fake_verify(questions, passages_by_id, model, difficulty=None, timeout=None, stop_after=None):
        verify_calls.append({"count": len(questions), "model": model, "difficulty": difficulty})
        return [dict(q) for q in questions]

    service._generate_text = fake_generate
    service._verify_questions = fake_verify
    return gen_calls, verify_calls


def test_verified_practice_bank_reuses_exact_chunk_without_llm_and_resets_answers(tmp_path):
    db_file = tmp_path / "bank_reuse.db"
    quote = "Ngăn xếp hoạt động theo nguyên tắc vào sau ra trước (LIFO) với hai phép toán cơ bản là push và pop."
    passage_item = {"chunk_id": 21, "doc_id": 1, "page_num": 4, "text": quote, "filename": "toan.pdf"}
    q1 = {
        "question": "Ngăn xếp hoạt động theo nguyên tắc nào trong đoạn trích?",
        "choices": {"A": "LIFO", "B": "FIFO", "C": "Ngẫu nhiên", "D": "Ưu tiên"},
        "answer": "A",
        "explanation": "Nguồn nêu rõ ngăn xếp hoạt động theo nguyên tắc vào sau ra trước (LIFO).",
        "source_chunk_id": 21,
        "evidence_quote": quote,
    }
    searcher, doc_lookup = _bank_test_deps([passage_item])
    service1 = PracticeService(searcher, doc_lookup, db_path=db_file, enable_verified_bank=True)
    gen_calls1, verify_calls1 = _wire_fake_practice_llm(service1, [[q1]])

    res1 = service1.generate(1, 4, count=1, model="test-model")
    assert len(gen_calls1) == 1
    assert len(verify_calls1) == 1
    assert "cached_from_bank" not in res1
    assert "answer" not in res1["questions"][0]
    assert "snippet" not in res1["questions"][0]["source"]

    submitted = service1.answer(res1["practice_id"], res1["questions"][0]["id"], "A")
    assert submitted["is_correct"] is True

    # Simulate fresh service/restart against the same SQLite DB
    service2 = PracticeService(searcher, doc_lookup, db_path=db_file, enable_verified_bank=True)
    gen_calls2, verify_calls2 = _wire_fake_practice_llm(service2, [[q1]])
    res2 = service2.generate(1, 4, count=1, model="test-model")

    assert gen_calls2 == []
    assert verify_calls2 == []
    assert res2["practice_id"] != res1["practice_id"]
    assert res2["cached_from_bank"] == 1
    assert "answer" not in res2["questions"][0]
    assert "snippet" not in res2["questions"][0]["source"]
    assert service2.get_session(res2["practice_id"])["answers"] == {}
    assert service2.answer(res2["practice_id"], res2["questions"][0]["id"], "A")["is_correct"] is True


def test_verified_practice_bank_prefers_unanswered_and_supports_refresh_bypass(tmp_path):
    db_file = tmp_path / "bank_priority.db"
    quote = "Hàng đợi hoạt động theo nguyên tắc vào trước ra trước (FIFO), trong đó enqueue thêm vào cuối và dequeue lấy ở đầu."
    passage_item = {"chunk_id": 22, "doc_id": 1, "page_num": 5, "text": quote, "filename": "toan.pdf"}
    q1 = {
        "question": "Hàng đợi hoạt động theo nguyên tắc nào?",
        "choices": {"A": "FIFO", "B": "LIFO", "C": "FILO", "D": "Cây nhị phân"},
        "answer": "A",
        "explanation": "Nguồn nêu rõ hàng đợi hoạt động theo nguyên tắc FIFO.",
        "source_chunk_id": 22,
        "evidence_quote": quote,
    }
    q2 = {
        "question": "Thao tác dequeue trên hàng đợi thực hiện ở vị trí nào?",
        "choices": {"A": "Lấy ở đầu", "B": "Thêm vào đầu", "C": "Thêm vào giữa", "D": "Lấy ở cuối"},
        "answer": "A",
        "explanation": "Nguồn nêu rõ thao tác dequeue lấy ở đầu hàng đợi.",
        "source_chunk_id": 22,
        "evidence_quote": quote,
    }
    q3 = {
        "question": "Thao tác enqueue trên hàng đợi thực hiện việc gì?",
        "choices": {"A": "Thêm vào cuối", "B": "Xóa ở đầu", "C": "Đảo ngược", "D": "Sắp xếp"},
        "answer": "A",
        "explanation": "Nguồn nêu rõ thao tác enqueue thêm vào cuối hàng đợi.",
        "source_chunk_id": 22,
        "evidence_quote": quote,
    }
    searcher, doc_lookup = _bank_test_deps([passage_item])
    service1 = PracticeService(searcher, doc_lookup, db_path=db_file, enable_verified_bank=True)
    _wire_fake_practice_llm(service1, [[q1, q2]])
    res_pair = service1.generate(1, 5, count=2, model="test-model")
    service1.answer(res_pair["practice_id"], "q1", "A")

    service2 = PracticeService(searcher, doc_lookup, db_path=db_file, enable_verified_bank=True)
    gen_calls2, verify_calls2 = _wire_fake_practice_llm(service2, [[q3]])
    reused_single = service2.generate(1, 5, count=1, model="test-model")
    assert gen_calls2 == []
    assert verify_calls2 == []
    assert reused_single["cached_from_bank"] == 1
    assert reused_single["questions"][0]["question"] == q2["question"]

    refreshed = service2.generate(1, 5, count=1, model="test-model", reuse_verified_bank=False)
    assert len(gen_calls2) == 1
    assert "cached_from_bank" not in refreshed
    assert refreshed["questions"][0]["question"] == q3["question"]


def test_verified_practice_bank_invalidates_on_drift_and_fails_closed_on_needs_review(tmp_path):
    db_file = tmp_path / "bank_invalidation.db"
    quote_v1 = "Đệ quy trực tiếp xảy ra khi một hàm gọi lại chính nó trong phần thân định nghĩa."
    quote_v2 = "Đệ quy gián tiếp xảy ra khi hàm A gọi hàm B và hàm B gọi ngược lại hàm A."
    q1 = {
        "question": "Đệ quy trực tiếp xảy ra trong điều kiện nào?",
        "choices": {"A": "Hàm gọi chính nó", "B": "Hai hàm độc lập", "C": "Chỉ dùng vòng lặp", "D": "Không có điều kiện dừng"},
        "answer": "A",
        "explanation": "Nguồn nêu rõ đệ quy trực tiếp xảy ra khi một hàm gọi lại chính nó.",
        "source_chunk_id": 23,
        "evidence_quote": quote_v1,
    }
    q2 = {
        "question": "Khi nào xuất hiện đệ quy gián tiếp giữa hai hàm A và B?",
        "choices": {"A": "A gọi B rồi B gọi A", "B": "A chỉ gọi chính A", "C": "B chỉ trả về hằng", "D": "Cả hai hàm rỗng"},
        "answer": "A",
        "explanation": "Nguồn nêu rõ hàm A gọi hàm B và hàm B gọi ngược lại hàm A.",
        "source_chunk_id": 23,
        "evidence_quote": quote_v2,
    }
    passage_v1 = {"chunk_id": 23, "doc_id": 1, "page_num": 6, "text": quote_v1, "filename": "toan.pdf"}
    s1, d1 = _bank_test_deps([passage_v1])
    svc1 = PracticeService(s1, d1, db_path=db_file, enable_verified_bank=True)
    _wire_fake_practice_llm(svc1, [[q1]])
    svc1.generate(1, 6, count=1, model="model-a")

    # enforce_difficulty=True must NOT reuse enforce_difficulty=False entry
    gen_diff, _ = _wire_fake_practice_llm(svc1, [[q1]])
    svc1.generate_from_passages([dict(passage_v1)], count=1, model="model-a", enforce_difficulty=True)
    assert len(gen_diff) == 1

    # Different model must NOT reuse model-a entry
    gen_model, _ = _wire_fake_practice_llm(svc1, [[q1]])
    svc1.generate(1, 6, count=1, model="model-b")
    assert len(gen_model) == 1

    # Altered passage text invalidates SHA-256 chunk hash
    passage_v2 = {"chunk_id": 23, "doc_id": 1, "page_num": 6, "text": quote_v2, "filename": "toan.pdf"}
    s2, d2 = _bank_test_deps([passage_v2])
    svc2 = PracticeService(s2, d2, db_path=db_file, enable_verified_bank=True)
    gen_drift, _ = _wire_fake_practice_llm(svc2, [[q2]])
    res_drift = svc2.generate(1, 6, count=1, model="model-a")
    assert len(gen_drift) == 1
    assert "cached_from_bank" not in res_drift
    assert res_drift["questions"][0]["question"] == q2["question"]

    # Legacy sessions without verified_bank metadata must never be reused
    legacy_db = tmp_path / "legacy_only.db"
    s_leg, d_leg = _bank_test_deps([passage_v1])
    svc_leg = PracticeService(s_leg, d_leg, db_path=legacy_db, enable_verified_bank=True)
    with svc_leg._db_connection() as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-id", 9999999999.0, 1, 1, "medium", "practice", "legacy",
                None, "careful",
                json.dumps([{
                    "id": "q1",
                    "question": q1["question"],
                    "choices": [{"label": k, "text": v} for k, v in q1["choices"].items()],
                    "answer": q1["answer"],
                    "explanation": q1["explanation"],
                    "source": {"doc_id": 1, "chunk_id": 23, "page_num": 6, "snippet": quote_v1},
                }]),
                "{}",
                "{}",
            ),
        )
        conn.commit()
    gen_leg, _ = _wire_fake_practice_llm(svc_leg, [[q1]])
    svc_leg.generate(1, 6, count=1, model="model-a")
    assert len(gen_leg) == 1

    # needs_review with unrepresented formula/diagram MUST still fail closed before bank lookup
    s_flagged, d_flagged = _bank_test_deps(
        [passage_v1],
        page_quality={"needs_review": True, "warnings": ["unrepresented_formula_or_diagram_suspected"]},
    )
    svc_flagged = PracticeService(s_flagged, d_flagged, db_path=db_file, enable_verified_bank=True)
    with pytest.raises(PracticeGenerationError, match="thiếu công thức"):
        svc_flagged.generate(1, 6, count=1, model="model-a")


def test_verified_practice_bank_partial_hit_only_generates_remaining(tmp_path):
    db_file = tmp_path / "bank_partial.db"
    quote = "Cây nhị phân tìm kiếm thỏa điều kiện khóa ở cây con trái nhỏ hơn nút gốc và khóa ở cây con phải lớn hơn nút gốc."
    passage_item = {"chunk_id": 24, "doc_id": 1, "page_num": 7, "text": quote, "filename": "toan.pdf"}
    q1 = {
        "question": "Các khóa ở cây con trái của cây nhị phân tìm kiếm có quan hệ gì với nút gốc?",
        "choices": {"A": "Nhỏ hơn nút gốc", "B": "Lớn hơn nút gốc", "C": "Luôn bằng 0", "D": "Bất kỳ"},
        "answer": "A",
        "explanation": "Nguồn nêu rõ khóa ở cây con trái nhỏ hơn nút gốc.",
        "source_chunk_id": 24,
        "evidence_quote": quote,
    }
    q2 = {
        "question": "Các khóa ở cây con phải của cây nhị phân tìm kiếm có quan hệ gì với nút gốc?",
        "choices": {"A": "Lớn hơn nút gốc", "B": "Nhỏ hơn nút gốc", "C": "Bằng âm vô cực", "D": "Không so sánh"},
        "answer": "A",
        "explanation": "Nguồn nêu rõ khóa ở cây con phải lớn hơn nút gốc.",
        "source_chunk_id": 24,
        "evidence_quote": quote,
    }
    searcher, doc_lookup = _bank_test_deps([passage_item])
    svc1 = PracticeService(searcher, doc_lookup, db_path=db_file, enable_verified_bank=True)
    _wire_fake_practice_llm(svc1, [[q1]])
    svc1.generate(1, 7, count=1, model="test-model")

    svc2 = PracticeService(searcher, doc_lookup, db_path=db_file, enable_verified_bank=True)
    gen_calls, _ = _wire_fake_practice_llm(svc2, [[q2]])
    res = svc2.generate(1, 7, count=2, model="test-model")
    assert len(gen_calls) == 1
    assert gen_calls[0]["batch_size"] == 1
    assert q1["question"] in gen_calls[0]["exclude_questions"]
    assert res["count"] == 2
    assert res["cached_from_bank"] == 1
    assert [q["question"] for q in res["questions"]] == [q1["question"], q2["question"]]


def test_learning_routes_refresh_flag_forwarding_respects_spies():
    from app.routes.learning_routes import (
        LearningGenerateRequest,
        PracticeGenerateRequest,
        _bank_refresh_kwargs,
    )

    class LegacySpy:
        def generate(self, topic, count=3):
            return {}

    def bank_aware_generate(topic, count=3, reuse_verified_bank=None):
        return {}

    assert PracticeGenerateRequest(source_document_id=1, source_page_num=2).refresh is False
    assert PracticeGenerateRequest(source_document_id=1, source_page_num=2, refresh=True).refresh is True
    assert LearningGenerateRequest(topic="Toán").refresh is False
    assert LearningGenerateRequest(topic="Toán", refresh=True).refresh is True

    assert _bank_refresh_kwargs(bank_aware_generate, False) == {}
    assert _bank_refresh_kwargs(bank_aware_generate, True) == {"reuse_verified_bank": False}
    assert _bank_refresh_kwargs(LegacySpy().generate, True) == {}


def test_practice_verification_phase_gets_dedicated_timeout_after_slow_generation(monkeypatch):
    """Slow primary generation (e.g. 135s on 9B) must not starve verification to 45s and fail HTTP 422."""
    import subprocess
    from types import SimpleNamespace

    clock = SimpleNamespace(now=1000.0)
    batch = [
        {
            **QUESTION,
            "question": f"Câu hỏi độc lập {i} về điều kiện A trong nguồn?",
            "choices": {**QUESTION["choices"], "B": f"Điều kiện A được nêu rõ trong nguồn ({i})."},
        }
        for i in range(1, 4)
    ]
    responses = [
        {"questions": batch},
        {
            "checks": [
                {
                    "index": idx,
                    **AUDIT_FIELDS,
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "verified_answer": "B",
                }
                for idx in range(1, 4)
            ]
        },
    ]
    service, _, _ = make_service([])
    timeouts = []

    def timed_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        seconds = 135.0 if max_tokens == 1800 else 75.0
        timeouts.append(timeout)
        if timeout is not None and timeout < seconds:
            raise subprocess.TimeoutExpired("fixture-native-inference", timeout)
        clock.now += seconds
        return json.dumps(responses.pop(0), ensure_ascii=False)

    monkeypatch.setattr("app.practice.time.monotonic", lambda: clock.now)
    monkeypatch.setattr(service, "_chat_complete", timed_chat)
    result = service.generate_from_passages([passage()], count=3, model="test-model")
    assert result["count"] == 3
    assert result["complete"] is True
    assert len(timeouts) == 2
    assert timeouts[1] >= 120.0


def test_mlx_practice_verification_keeps_full_240s_budget(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    clock = SimpleNamespace(now=200.0)
    batch = [
        {
            **QUESTION,
            "question": f"Câu {idx} về điều kiện A trong nguồn?",
            "choices": {**QUESTION["choices"], "B": f"Điều kiện A được nêu rõ trong nguồn ({idx})."},
        }
        for idx in range(1, 5)
    ]
    responses = [
        {"questions": batch},
        {
            "checks": [
                {
                    "index": idx,
                    "source_basis": QUESTION["evidence_quote"],
                    "input_audit": "Source supplies direct fact.",
                    "independent_solution": "B matches the quoted statement.",
                    "choice_comparison": "Only B matches; A,C,D diverge.",
                    "explanation_audit": "Explanation matches source.",
                    "matching_choices": "B",
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "verified_answer": "B",
                    "difficulty_matches": True,
                }
                for idx in range(1, 4)
            ]
        },
    ]
    service, _, _ = make_service([])
    timeouts = []

    def timed_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        seconds = 155.0 if max_tokens == 1800 else (45.0 if max_tokens <= 1000 else 135.0)
        timeouts.append(timeout)
        if timeout is not None and timeout < seconds:
            raise subprocess.TimeoutExpired("fixture-mlx-inference", timeout)
        clock.now += seconds
        return json.dumps(responses.pop(0), ensure_ascii=False)

    monkeypatch.setattr("app.model_resolver.is_mlx_model", lambda _model=None: True)
    monkeypatch.setattr("app.practice.time.monotonic", lambda: clock.now)
    monkeypatch.setattr(service, "_chat_complete", timed_chat)
    result = service.generate_from_passages([passage()], count=3, model="qwen3.5-9b-4bit")
    assert result["count"] == 3
    assert result["complete"] is True
    assert len(timeouts) == 2
    assert timeouts[1] >= 240.0


def test_topic_bayes_medium_end_to_end_via_learning_route_and_durable_grading(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from fractions import Fraction
    import shutil
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app import server
    from app.document_lookup import DocumentLookup
    from app.indexer import KnowledgeIndexer
    from app.learning import LearningService
    from app.searcher import HybridSearcher

    src = tmp_path / "src"
    src.mkdir()
    fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "documents"
    shutil.copy2(fixtures_dir / "extended_probability.md", src / "extended_probability.md")
    other_md = sorted(p for p in fixtures_dir.glob("extended_*.md") if p.name != "extended_probability.md")[0]
    shutil.copy2(other_md, src / other_md.name)
    kb_db = tmp_path / "kb.db"
    prac_db = tmp_path / "practice.db"
    KnowledgeIndexer(kb_db, src).index_all(ocr=False, embed_model=None)
    lookup = DocumentLookup(kb_db, src)
    searcher = HybridSearcher(kb_db)
    practice = PracticeService(searcher, lookup, db_path=prac_db, enable_verified_bank=True)
    learning = LearningService(document_lookup=lookup, practice_service=practice, searcher=searcher)

    review_calls = []

    def review_only_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        review_calls.append(prompt)
        assert "Dữ liệu cần kiểm định:\n" in prompt
        items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
        assert len(items) == 1
        item = items[0]
        assert "P(B)>0" in item["source"] and "P(A|B)=P(B|A)P(A)/P(B)" in item["source"]
        import re as _re
        m = _re.search(r"P\(A\)\s*=\s*([0-9.]+),\s*P\(B\|A\)\s*=\s*([0-9.]+)\s+và\s+P\(B\)\s*=\s*([0-9.]+)", item["question"])
        assert m is not None
        expected = (Fraction(m.group(2)) * Fraction(m.group(1))) / Fraction(m.group(3))
        matching = [lbl for lbl, txt in item["choices"].items() if Fraction(txt) == expected]
        assert len(matching) == 1
        ans = matching[0]
        return json.dumps({
            "checks": [{
                "index": 1,
                "source_basis": (item.get("evidence_quote") or item["source"])[:120],
                "input_audit": "Question provides all inputs P(A), P(B|A), P(B)>0.",
                "independent_solution": f"P(A|B) = ({m.group(2)} * {m.group(1)}) / {m.group(3)} = {float(expected):g}.",
                "choice_comparison": f"Only {ans} matches exact quotient.",
                "explanation_audit": "Formula and P(B)>0 bound to source; arithmetic verified.",
                "matching_choices": ans,
                "supported": True,
                "explanation_supported": True,
                "unique_answer": True,
                "qualifiers_preserved": True,
                "difficulty_matches": True,
                "verified_answer": ans,
            }]
        }, ensure_ascii=False)

    monkeypatch.setenv("PRACTICE_DB_PATH", str(prac_db))
    monkeypatch.setattr(server, "learning_service", learning, raising=False)
    monkeypatch.setattr(server, "practice_service", practice, raising=False)
    monkeypatch.setattr(server, "dialogue_gate", SimpleNamespace(acquire=lambda: nullcontext()))
    monkeypatch.setattr(server, "gpu_coordinator", SimpleNamespace(acquire_for_inference=lambda: nullcontext()))
    client = TestClient(server.app)

    baseline_calls = []
    with monkeypatch.context() as base_ctx:
        base_ctx.setattr("app.worked_practice.source_bayes_proposals", lambda *a, **kw: None)
        base_ctx.setattr(practice, "_chat_complete", lambda *a, **kw: baseline_calls.append(a[0]) or json.dumps({"questions": []}))
        baseline_res = client.post("/api/learning/generate", json={"topic": "Xác suất Bayes", "count": 1, "difficulty": "medium"})
        assert baseline_res.status_code == 422
        assert len(baseline_calls) == 1

    monkeypatch.setattr(practice, "_chat_complete", review_only_chat)

    res = client.post("/api/learning/generate", json={"topic": "Xác suất Bayes", "count": 1, "difficulty": "medium"})
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["complete"] is True
    assert len(review_calls) == 1
    pub_q = body["questions"][0]
    assert "answer" not in pub_q and "explanation" not in pub_q
    assert "snippet" not in pub_q["source"]
    assert pub_q["source"]["page_num"] == 4

    sess = client.get(f"/api/practice/session/{body['practice_id']}").json()
    assert sess["questions"][0]["id"] == pub_q["id"]
    assert sess["answers"] == {}

    stored_q = practice.get_session_questions_with_answers(body["practice_id"])[0]
    grade = client.post("/api/practice/answer", json={
        "practice_id": body["practice_id"],
        "question_id": pub_q["id"],
        "answer": stored_q["answer"],
    })
    assert grade.status_code == 200
    graded = grade.json()
    assert graded["is_correct"] is True
    assert graded["source"]["page_num"] == 4
    assert "P(B)>0" in graded["source"]["snippet"]
    assert "P(A|B)=P(B|A)P(A)/P(B)" in graded["source"]["snippet"]

    res_cached = client.post("/api/learning/generate", json={"topic": "Xác suất Bayes", "count": 1, "difficulty": "medium"})
    assert res_cached.status_code == 200
    assert res_cached.json()["cached_from_bank"] == 1
    assert len(review_calls) == 1


def test_topic_bayes_medium_respects_fail_closed_review_gate(tmp_path, monkeypatch):
    bayes_text = "Nếu P(B)>0 thì P(A|B)=P(B|A)P(A)/P(B). Mẫu số P(B) phải dương."
    src = passage(chunk_id=4, text=bayes_text)
    service = PracticeService(FakeSearcher([src]), FakeDocumentLookup(), db_path=tmp_path / "prac.db")
    calls = []

    def reject_review(prompt, *args, **kwargs):
        calls.append(prompt)
        return json.dumps({
            "checks": [{
                "index": 1,
                **AUDIT_FIELDS,
                "supported": False,
                "explanation_supported": True,
                "unique_answer": False,
                "qualifiers_preserved": True,
                "difficulty_matches": False,
                "verified_answer": "",
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", reject_review)
    with pytest.raises(PracticeGenerationError, match="đạt kiểm tra nguồn"):
        service.generate_from_passages(
            [src],
            count=1,
            difficulty="medium",
            model="test-model",
            allow_retry=True,
            enforce_difficulty=True,
        )
    assert len(calls) == 1


def test_topic_bayes_partial_count_3_falls_through_once_and_returns_partial(tmp_path, monkeypatch):
    from app.worked_practice import source_bayes_proposals

    bayes_text = "Nếu P(B)>0 thì P(A|B)=P(B|A)P(A)/P(B). Mẫu số P(B) phải dương."
    src = passage(chunk_id=4, text=bayes_text)
    service = PracticeService(FakeSearcher([src]), FakeDocumentLookup(), db_path=tmp_path / "prac.db")
    gen_prompts = []
    review_prompts = []
    expected_ans = source_bayes_proposals([src], 1)[0]["answer"]

    def fake_chat_exact(prompt, model, max_tokens, timeout=None, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            review_prompts.append(prompt)
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": items[0]["evidence_quote"],
                    **AUDIT_FIELDS,
                    "matching_choices": expected_ans,
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "difficulty_matches": True,
                    "verified_answer": expected_ans,
                }]
            }, ensure_ascii=False)
        gen_prompts.append(prompt)
        return json.dumps({"questions": []})

    monkeypatch.setattr(service, "_chat_complete", fake_chat_exact)
    result = service.generate_from_passages(
        [src],
        count=3,
        difficulty="medium",
        model="test-model",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert result["count"] == 1
    assert result["requested_count"] == 3
    assert result["complete"] is False
    assert "1/3 câu" in result["warning"]
    assert len(review_prompts) == 1
    assert len(gen_prompts) == 2


def test_single_passage_empty_output_does_not_repeat_identical_calls_while_changed_evidence_retries(tmp_path, monkeypatch):
    tampered = passage(chunk_id=4, text="Nếu P(B) >= 0 thì P(A|B)=P(B|A)P(A)/P(B).")
    service = PracticeService(FakeSearcher([tampered]), FakeDocumentLookup(), db_path=tmp_path / "prac.db")
    calls = []

    def always_empty(prompt, *args, **kwargs):
        calls.append(prompt)
        return json.dumps({"questions": []})

    monkeypatch.setattr(service, "_chat_complete", always_empty)
    with pytest.raises(PracticeGenerationError, match="Chưa tạo được câu hỏi"):
        service.generate_from_passages([tampered], count=1, difficulty="medium", model="test-model", allow_retry=True)
    assert len(calls) == 1

    second = passage(chunk_id=5, text="Điều kiện độc lập có ý nghĩa khác với xung khắc.")
    calls.clear()
    with pytest.raises(PracticeGenerationError, match="Chưa tạo được câu hỏi"):
        service.generate_from_passages([tampered, second], count=1, difficulty="medium", model="test-model", allow_retry=True)
    assert len(calls) == 2


def test_retry_skips_identical_rejected_candidate_but_accepts_changed_candidate(tmp_path, monkeypatch):
    src = passage(chunk_id=17, text=PASSAGE)
    q_bad = {**QUESTION, "question": "Trong điều kiện A, hiện tượng X có thể dẫn tới kết quả nào khác chưa nêu?"}
    q_good = {**QUESTION, "question": "Theo đoạn trích, hiện tượng X trong điều kiện A có thể xảy ra không?"}

    svc1 = PracticeService(FakeSearcher([src]), FakeDocumentLookup(), db_path=tmp_path / "prac1.db")
    gen_calls1 = []
    review_calls1 = []

    def chat_repeated_bad(prompt, *args, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            review_calls1.append(prompt)
            return json.dumps({"checks": [{"index": 1, **AUDIT_FIELDS, "supported": False, "explanation_supported": False, "unique_answer": False, "qualifiers_preserved": True, "verified_answer": ""}]})
        gen_calls1.append(prompt)
        return json.dumps({"questions": [{**q_bad, "source_chunk_id": 1}]})

    monkeypatch.setattr(svc1, "_chat_complete", chat_repeated_bad)
    with pytest.raises(PracticeGenerationError, match="Không có câu hỏi"):
        svc1.generate_from_passages([src], count=1, difficulty="medium", model="test-model", allow_retry=True)
    assert len(review_calls1) == 1
    assert len(gen_calls1) == 2

    svc2 = PracticeService(FakeSearcher([src]), FakeDocumentLookup(), db_path=tmp_path / "prac2.db")
    gen_outputs = iter([
        json.dumps({"questions": [{**q_bad, "source_chunk_id": 1}]}),
        json.dumps({"questions": [{**q_good, "source_chunk_id": 1}]}),
    ])

    def chat_changed_good(prompt, *args, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            ok = items[0]["question"] == q_good["question"]
            return json.dumps({"checks": [{
                "index": 1,
                **AUDIT_FIELDS,
                "supported": ok,
                "explanation_supported": ok,
                "unique_answer": ok,
                "qualifiers_preserved": True,
                "verified_answer": "B" if ok else "",
            }]})
        return next(gen_outputs)

    monkeypatch.setattr(svc2, "_chat_complete", chat_changed_good)
    res = svc2.generate_from_passages([src], count=1, difficulty="medium", model="test-model", allow_retry=True)
    assert res["count"] == 1
    assert res["questions"][0]["question"] == q_good["question"]


def test_native_count_1_uses_statement_planning_and_requires_question(monkeypatch):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    sources = [passage(31 + i, text=f"The {name} is an object defined by its explicitly stated rule when evaluated.")
               for i, name in enumerate(["widget", "gadget"])]
    service, _, _ = make_service()
    seen = []

    def respond(prompt, *args, **kwargs):
        seen.append((prompt, kwargs))
        quotes = kwargs["response_schema"]["properties"]["questions"]["items"]["allOf"][0]["anyOf"]
        return json.dumps({"questions": [dict(QUESTION, source_chunk_id=1, evidence_quote=quotes[0]["properties"]["evidence_quote"]["enum"][0])]})

    monkeypatch.setattr(service, "_chat_complete", respond)
    out = json.loads(service._generate_text(sources, 1, "qwen3.5-9b-4bit", timeout=20))
    assert len(out["questions"]) == 1
    assert seen[0][1]["response_schema"]["properties"]["questions"]["minItems"] == 1
    assert seen[0][1]["response_schema"]["properties"]["questions"]["items"]["properties"]["question"]["pattern"] == "^Theo đoạn trích, "


def test_statement_passages_retry_rotation_easy_prompt_and_kv_prefix(monkeypatch):
    import app.model_resolver as resolver
    from app.practice import _statement_passages

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    text = (
        "Khái niệm cốt lõi:\n"
        "Trí nhớ ngắn hạn là hệ thống lưu giữ tạm thời một lượng thông tin nhỏ trong vài giây khi xử lý.\n"
        "Trí nhớ dài hạn lưu trữ kiến thức ổn định hơn vì quá trình củng cố diễn ra liên tục sau học tập."
    )
    src = passage(41, text=text)
    first = _statement_passages([src], 1, 1400, offset=0)
    assert len(first) == 2
    assert "Khái niệm cốt lõi:" not in first[0]["text"]

    # offset=1 rotates away from first candidate, and exclude_quotes drops it completely
    rotated = _statement_passages([src], 1, 1400, offset=1)
    assert rotated[0]["text"] != first[0]["text"]
    excluded = _statement_passages([src], 1, 1400, exclude_quotes={first[0]["text"]})
    assert all(item["text"] != first[0]["text"] for item in excluded)

    service, _, _ = make_service()
    calls = []

    def respond(prompt, model, max_tokens, *args, **kwargs):
        calls.append((prompt, max_tokens, kwargs))
        quotes = kwargs["response_schema"]["properties"]["questions"]["items"]["allOf"][0]["anyOf"]
        return json.dumps({
            "questions": [dict(
                QUESTION,
                source_chunk_id=1,
                evidence_quote=quotes[0]["properties"]["evidence_quote"]["enum"][0],
            )]
        })

    monkeypatch.setattr(service, "_chat_complete", respond)
    service._generate_text([src], 1, "qwen3.5-9b-4bit", difficulty="easy", timeout=20)
    assert len(calls) == 1
    prompt, max_tokens, _kwargs = calls[0]
    assert "mức easy (nhận biết/định nghĩa trực tiếp" in prompt
    assert "\n\nPassage được chọn:\n<passage id=\"1\">" in prompt
    assert max_tokens == 900


def test_mlx_allow_retry_extends_deadline_and_returns_partial_instead_of_short_timeout(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr("app.practice.time.monotonic", lambda: clock.now)

    src = passage(chunk_id=55, text=PASSAGE)
    service = PracticeService(FakeSearcher([src]), FakeDocumentLookup(), db_path=tmp_path / "prac_mlx_deadline.db")
    calls = []

    def slow_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        calls.append((max_tokens, timeout))
        if "Dữ liệu cần kiểm định:\n" in prompt:
            clock.now += 150.0
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": QUESTION["evidence_quote"],
                    **AUDIT_FIELDS,
                    "matching_choices": "B",
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "verified_answer": "B",
                }]
            }, ensure_ascii=False)
        if len(calls) == 1:
            clock.now += 100.0  # Elapsed becomes 250s after verify -> remaining = 20s (<35s) of 270s MLX deadline
            return json.dumps({"questions": [{**QUESTION, "source_chunk_id": 1}]}, ensure_ascii=False)
        raise AssertionError("Should return partial verified set instead of issuing a starved <35s retry")

    monkeypatch.setattr(service, "_chat_complete", slow_chat)
    result = service.generate_from_passages([src], count=3, model="qwen3.5-9b-4bit", allow_retry=True)
    assert result["count"] == 1
    assert result["complete"] is False
    assert "1/3 câu" in result["warning"]
    assert calls[0][1] == 180.0
    assert len(calls) == 2


def test_mlx_easy_uses_source_cloze_compact_review_and_reuses_verified_bank(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    long_passage = (
        "Trong sinh học tế bào, ty thể là bào quan thực hiện quá trình hô hấp hiếu khí để tổng hợp ATP cho mọi hoạt động sống. "
        "Khi màng trong ty thể bị tổn thương, chuỗi truyền electron bị gián đoạn và hiệu suất tạo năng lượng giảm mạnh."
    )
    src = passage(chunk_id=77, text=long_passage)
    prac_db = tmp_path / "prac_easy_cloze.db"
    service = PracticeService(FakeSearcher([src]), FakeDocumentLookup(), db_path=prac_db, enable_verified_bank=True)
    prompts_seen = []

    def fake_cloze_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        prompts_seen.append((prompt, max_tokens, kwargs.get("context_prefix_marker")))
        if "Dữ liệu cần kiểm định:\n" in prompt:
            assert "source_basis" in prompt and "difficulty_matches" not in prompt
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            assert len(items) == 1
            item = items[0]
            ans_letter = next(k for k, v in item["choices"].items() if v == "hô hấp hiếu khí")
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": "ty thể là bào quan thực hiện quá trình hô hấp hiếu khí để tổng hợp ATP",
                    "choice_comparison": f"{ans_letter} đúng nguyên văn; các phương án còn lại sai quá trình sinh học.",
                    "matching_choices": ans_letter,
                    "supported": True,
                    "qualifiers_preserved": True,
                    "unique_answer": True,
                    "verified_answer": ans_letter,
                }]
            }, ensure_ascii=False)
        assert "<passages>" in prompt and "\n<task>\n" in prompt
        return json.dumps({
            "questions": [{
                "format": "source_cloze",
                "evidence_id": "Q1",
                "answer": "hô hấp hiếu khí",
                "distractors": ["quang hợp kỵ khí", "lên men rượu", "phân bào giảm nhiễm"],
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", fake_cloze_chat)
    res1 = service.generate_from_passages(
        [src], count=1, difficulty="easy", model="qwen3.5-9b-4bit", allow_retry=True, enforce_difficulty=True
    )
    assert res1["count"] == 1
    assert res1["complete"] is True
    q = res1["questions"][0]
    assert q["question"].startswith("Chọn cụm điền vào trích đoạn:\n")
    assert "[…]" in q["question"]
    assert len(q["question"]) <= 600
    assert service._sessions[res1["practice_id"]]["questions"][0]["format"] == "source_cloze"
    assert "\n<task>\n" in prompts_seen[0][0]
    assert "\nDữ liệu cần kiểm định:\n" in prompts_seen[1][0]

    # Immediate repeat hits verified bank with zero additional model calls
    prompts_seen.clear()
    res2 = service.generate_from_passages(
        [src], count=1, difficulty="easy", model="qwen3.5-9b-4bit", allow_retry=True, enforce_difficulty=True
    )
    assert res2["cached_from_bank"] == 1
    assert res2["questions"][0]["question"] == q["question"]
    assert service._sessions[res2["practice_id"]]["questions"][0]["format"] == "source_cloze"
    assert prompts_seen == []


def test_easy_cloze_focuses_excerpt_and_runs_one_shot_repeated_answer_repair(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    english_slide = (
        "Array\nLinked Lists\nOrdered Linked List\nStack\nQueue\n"
        "Dynamically allocated data structures form a linear chain of connected items. "
        "Each node stores a data payload and links that node directly to its successor.\n"
        + ("Additional discussion about pointer management and a node structure across later segments. " * 6)
    )
    src = passage(chunk_id=88, text=english_slide)
    service = PracticeService(FakeSearcher([src]), FakeDocumentLookup(), db_path=tmp_path / "prac_easy_repair.db")
    gen_prompts = []

    def repair_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            item = items[0]
            ans_letter = next(k for k, v in item["choices"].items() if v == "linear chain")
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": "Dynamically allocated data structures form a linear chain of connected items.",
                    "choice_comparison": f"{ans_letter} matches the exact phrase.",
                    "matching_choices": ans_letter,
                    "supported": True,
                    "qualifiers_preserved": True,
                    "unique_answer": True,
                    "verified_answer": ans_letter,
                }]
            }, ensure_ascii=False)
        gen_prompts.append(prompt)
        if len(gen_prompts) == 1:
            # Repeated word inside the excerpt triggers 1-shot KV repair
            return json.dumps({
                "questions": [{
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "node",
                    "distractors": ["array", "queue", "stack"],
                }]
            }, ensure_ascii=False)
        assert "Chọn lại cụm answer KHÔNG bị lặp" in prompt and '"node"' in prompt
        # Chosen term appears outside excerpt in later segments, but is unique inside the focused excerpt
        return json.dumps({
            "questions": [{
                "format": "source_cloze",
                "evidence_id": "Q1",
                "answer": "linear chain",
                "distractors": ["fixed table", "binary heap", "hash bucket"],
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", repair_chat)
    res = service.generate_from_passages(
        [src], count=1, difficulty="easy", model="qwen3.5-9b-4bit", allow_retry=True, enforce_difficulty=True
    )
    assert res["count"] == 1
    assert len(gen_prompts) == 2
    # Sidebar stack was trimmed from the focused excerpt sent to the cloze generator
    assert "Array\nLinked Lists\n" not in gen_prompts[0]


def test_deterministic_prefilter_rejects_defective_candidates_before_model_review(tmp_path, monkeypatch):
    from app.practice import _deterministic_prefilter_reject_reason

    src_text = "Trong điều kiện A, hiện tượng X có thể xảy ra nếu đủ tham số."
    valid_q = {
        "question": "Theo tài liệu, hiện tượng X trong điều kiện A như thế nào?",
        "choices": {
            "A": "Luôn xảy ra",
            "B": "Có thể xảy ra",
            "C": "Không thể xảy ra",
            "D": "Đảo ngược tham số",
        },
        "answer": "B",
        "explanation": "Không chọn A vì nguồn nêu có thể; đáp án đúng là B.",
        "evidence_quote": src_text,
        "source": {"chunk_id": 1, "doc_id": 1, "page_num": 1, "title": "Doc"},
    }
    assert _deterministic_prefilter_reject_reason(valid_q, src_text) is None

    math_var_q = dict(valid_q, explanation="Ta chọn a = 2 theo quy tắc; đáp án đúng là B.")
    assert _deterministic_prefilter_reject_reason(math_var_q, src_text) is None

    leak_q = dict(valid_q, question="Dựa vào <passage id=\"1\"> hãy chọn đáp án?")
    assert _deterministic_prefilter_reject_reason(leak_q, src_text) == "prefilter_prompt_leakage"

    meta_q = dict(valid_q, choices={**valid_q["choices"], "D": "Tất cả các đáp án trên đều đúng"})
    assert _deterministic_prefilter_reject_reason(meta_q, src_text) == "prefilter_meta_choice"

    placeholder_q = dict(valid_q, choices={**valid_q["choices"], "C": "..."})
    assert _deterministic_prefilter_reject_reason(placeholder_q, src_text) == "prefilter_placeholder_choice"

    contradict_q = dict(valid_q, explanation="Vì vậy đáp án đúng là C.", answer="B")
    assert _deterministic_prefilter_reject_reason(contradict_q, src_text) == "prefilter_explanation_answer_mismatch"

    service = PracticeService(FakeSearcher([passage(chunk_id=1, text=src_text)]), FakeDocumentLookup(), db_path=tmp_path / "prac_pf.db")
    chat_calls = []
    monkeypatch.setattr(service, "_chat_complete", lambda *a, **kw: chat_calls.append(a[0]) or "{}")
    assert service._verify_questions([leak_q, meta_q, placeholder_q, contradict_q], {1: passage(chunk_id=1, text=src_text)}, "test-model") == []
    assert chat_calls == []


def test_verify_questions_preserves_study_material_review_on_four_candidate_split(tmp_path, monkeypatch):
    import app.model_resolver as resolver
    import app.practice_proof as proof_mod

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    compact_calls = []
    orig_compact = proof_mod.compact_medium_review_prompt

    def spy_compact(items):
        compact_calls.append(len(items))
        return orig_compact(items)

    monkeypatch.setattr(proof_mod, "compact_medium_review_prompt", spy_compact)
    quote = "Khi tham số alpha đạt ngưỡng chuẩn thì phản ứng bảo toàn năng lượng."
    passages_map = {
        i: passage(chunk_id=i, text=f"{quote} Đoạn nguồn {i}.") for i in range(1, 5)
    }
    candidates = [
        {
            "question": f"Theo tài liệu, trường hợp {i} khi alpha đạt ngưỡng chuẩn thì sao?",
            "choices": {"A": "Sai 1", "B": "Bảo toàn năng lượng", "C": "Sai 2", "D": "Sai 3"},
            "answer": "B",
            "explanation": "Áp dụng quy tắc đoạn trích.",
            "evidence_quote": quote,
            "source": {"chunk_id": i, "doc_id": 1, "page_num": i, "title": "Doc"},
        }
        for i in range(1, 5)
    ]
    service = PracticeService(FakeSearcher([]), FakeDocumentLookup(), db_path=tmp_path / "prac_split.db")

    def fake_verify_chat(prompt, *args, **kwargs):
        items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
        return json.dumps({
            "checks": [
                {
                    "index": it["index"],
                    "source_basis": quote,
                    **AUDIT_FIELDS,
                    "matching_choices": "B",
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "difficulty_matches": True,
                    "verified_answer": "B",
                }
                for it in items
            ]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", fake_verify_chat)
    verified = service._verify_questions(
        candidates,
        passages_map,
        "qwen3.5-9b-4bit",
        difficulty="medium",
        stop_after=3,
        study_material_review=True,
    )
    assert len(verified) == 3
    assert compact_calls == [3]

    # When batch 1 only accepts 1 item, recursive batch 2 must ALSO retain compact_medium_review_prompt
    compact_calls.clear()
    call_idx = {"n": 0}

    def partial_verify_chat(prompt, *args, **kwargs):
        call_idx["n"] += 1
        items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
        return json.dumps({
            "checks": [
                {
                    "index": it["index"],
                    "source_basis": quote,
                    **AUDIT_FIELDS,
                    "matching_choices": "B" if call_idx["n"] > 1 or it["index"] == 1 else "",
                    "supported": call_idx["n"] > 1 or it["index"] == 1,
                    "explanation_supported": True,
                    "unique_answer": call_idx["n"] > 1 or it["index"] == 1,
                    "qualifiers_preserved": True,
                    "difficulty_matches": True,
                    "verified_answer": "B" if call_idx["n"] > 1 or it["index"] == 1 else "",
                }
                for it in items
            ]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", partial_verify_chat)
    verified2 = service._verify_questions(
        candidates,
        passages_map,
        "qwen3.5-9b-4bit",
        difficulty="medium",
        stop_after=3,
        study_material_review=True,
    )
    assert len(verified2) == 2
    assert compact_calls == [3, 1]


def test_select_hard_passages_ranks_multi_rule_density_and_rotates_safely():
    from app.practice import _score_hard_passage_density, _select_hard_passages

    low = {
        "chunk_id": 10,
        "page_num": 1,
        "text": "Chương này giới thiệu khái quát lịch sử ngành học trong thế kỷ trước. Nội dung gồm nhiều ví dụ minh họa quen thuộc.",
    }
    medium_p = {
        "chunk_id": 11,
        "page_num": 2,
        "text": "Khi hệ thống đạt trạng thái ổn định thì sai số giảm dần theo thời gian. Mục tiêu chung là đảm bảo độ tin cậy cơ bản.",
    }
    high = {
        "chunk_id": 12,
        "page_num": 3,
        "text": (
            "Nếu điều kiện biên f(0) = 0 thỏa mãn thì công thức tích phân cho ra nghiệm duy nhất. "
            "Do đó khi chuyển sang bước hai, thuật toán kiểm tra P(x) >= 0; ngược lại quy trình dừng ngay. "
            "So với phương pháp trực tiếp, cách tính từng bước giảm sai số đáng kể."
        ),
    }
    extra1 = {"chunk_id": 13, "page_num": 4, "text": "Khi điều kiện k = 1 đúng thì suy ra nghiệm T = n + 1."}
    extra2 = {"chunk_id": 14, "page_num": 5, "text": "Giới thiệu chung về tài liệu tham khảo cuối chương."}

    assert _score_hard_passage_density(high["text"]) > _score_hard_passage_density(low["text"])
    selected0 = _select_hard_passages([low, medium_p, high, extra1, extra2], count=2, offset=0)
    assert len(selected0) == 3
    assert selected0[0]["chunk_id"] == 12
    assert selected0[0]["text"] == high["text"]

    selected1 = _select_hard_passages([low, medium_p, high, extra1, extra2], count=2, offset=1)
    assert selected1[0]["chunk_id"] != 12
    assert len({p["chunk_id"] for p in selected1}) == 3


def test_learning_generate_stream_emits_public_progress_complete_and_maps_errors(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app import learning as learning_mod, server

    class StreamLookup(FakeDocumentLookup):
        def search_topic_chunks(self, topic, limit=8):
            return [passage(chunk_id=1, text=PASSAGE)]

    prac_db = tmp_path / "prac_stream.db"
    practice = PracticeService(
        FakeSearcher([passage(chunk_id=1, text=PASSAGE)]),
        StreamLookup(),
        db_path=prac_db,
        enable_verified_bank=True,
    )
    learning = learning_mod.LearningService(StreamLookup(), practice)
    monkeypatch.setattr(learning, "_select_topic_passages", lambda topic, **kw: [passage(chunk_id=1, text=PASSAGE)])

    def fake_chat(prompt, *args, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": QUESTION["evidence_quote"],
                    **AUDIT_FIELDS,
                    "matching_choices": "B",
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "difficulty_matches": True,
                    "verified_answer": "B",
                }]
            }, ensure_ascii=False)
        return json.dumps({"questions": [QUESTION]}, ensure_ascii=False)

    monkeypatch.setattr(practice, "_chat_complete", fake_chat)
    monkeypatch.setenv("PRACTICE_DB_PATH", str(prac_db))
    monkeypatch.setattr(server, "learning_service", learning, raising=False)
    monkeypatch.setattr(server, "practice_service", practice, raising=False)
    monkeypatch.setattr(server, "dialogue_gate", SimpleNamespace(acquire=lambda: nullcontext()))
    monkeypatch.setattr(server, "gpu_coordinator", SimpleNamespace(acquire_for_inference=lambda: nullcontext()))
    client = TestClient(server.app)

    def parse_sse(text: str):
        events = []
        for block in text.split("\n\n"):
            data_lines = [ln[5:].lstrip() for ln in block.splitlines() if ln.startswith("data:")]
            if data_lines:
                events.append(json.loads("\n".join(data_lines)))
        return events

    res = client.post("/api/learning/generate/stream", json={"topic": "Trí nhớ", "count": 1, "difficulty": "medium"})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    events = parse_sse(res.text)
    stages = [e["stage"] for e in events if e.get("type") == "progress"]
    assert stages == ["generating", "verifying", "questions"]
    assert events[-1]["type"] == "complete"
    assert events[-1]["session"]["count"] == 1
    for ev in events:
        if ev.get("type") == "progress":
            for q_pub in ev.get("questions") or []:
                assert "answer" not in q_pub
                assert "explanation" not in q_pub
                assert "snippet" not in q_pub.get("source", {})

    # Repeat hits verified bank and emits bank_reused
    res2 = client.post("/api/learning/generate/stream", json={"topic": "Trí nhớ", "count": 1, "difficulty": "medium"})
    events2 = parse_sse(res2.text)
    assert any(e.get("type") == "progress" and e.get("stage") == "bank_reused" for e in events2)
    assert events2[-1]["type"] == "complete"
    assert events2[-1]["session"]["cached_from_bank"] == 1

    # Error mapping in SSE stream
    monkeypatch.setattr(learning, "generate", lambda *a, **kw: (_ for _ in ()).throw(PracticeGenerationError("Chưa tạo được câu hỏi đạt chuẩn.")))
    res_err = client.post("/api/learning/generate/stream", json={"topic": "Trí nhớ", "count": 1, "difficulty": "medium"})
    assert res_err.status_code == 200
    events_err = parse_sse(res_err.text)
    assert events_err == [{"type": "error", "status": 422, "detail": "Chưa tạo được câu hỏi đạt chuẩn."}]


def test_hard_batch_backoff_switches_to_single_item_on_mlx_zero_verified(tmp_path, monkeypatch):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    src_text = (
        "Nếu điều kiện f(0) = 0 thỏa mãn và đạo hàm liên tục thì hệ thống đạt nghiệm duy nhất. "
        "Do đó khi chuyển sang bước kế tiếp ta kiểm tra P(x) >= 0 để xác nhận ổn định."
    )
    src = passage(chunk_id=1, text=src_text)
    service = PracticeService(FakeSearcher([src]), FakeDocumentLookup(), db_path=tmp_path / "prac_hard_backoff.db")
    gen_requests = []

    def fake_chat(prompt, *args, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            if len(gen_requests) == 1:
                return json.dumps({
                    "checks": [{
                        "index": it["index"],
                        "source_basis": src_text[:120],
                        **AUDIT_FIELDS,
                        "matching_choices": "",
                        "supported": False,
                        "explanation_supported": True,
                        "unique_answer": False,
                        "qualifiers_preserved": True,
                        "difficulty_matches": False,
                        "verified_answer": "",
                    } for it in items]
                }, ensure_ascii=False)
            return json.dumps({
                "checks": [{
                    "index": it["index"],
                    "source_basis": src_text[:120],
                    **AUDIT_FIELDS,
                    "matching_choices": "B",
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "difficulty_matches": True,
                    "verified_answer": "B",
                } for it in items]
            }, ensure_ascii=False)

        gen_requests.append(prompt)
        if len(gen_requests) == 1:
            return json.dumps({
                "questions": [{
                    "source_chunk_id": 1,
                    "evidence_quote": src_text[:120],
                    "question": f"Câu hard lô đầu số {i} khi điều kiện thỏa mãn?",
                    "explanation": "Áp dụng hai bước từ nguồn.",
                    "choices": {"A": "Sai 1", "B": f"Đáp án lô {i}", "C": "Sai 2", "D": "Sai 3"},
                    "answer": "B",
                } for i in range(1, 4)]
            }, ensure_ascii=False)
        return json.dumps({
            "questions": [{
                "source_chunk_id": 1,
                "evidence_quote": src_text[:120],
                "question": f"Câu hard đơn lẻ lượt {len(gen_requests)} kết hợp hai điều kiện?",
                "explanation": "Áp dụng từng bước từ nguồn.",
                "choices": {"A": "Sai 1", "B": f"Kết luận đúng {len(gen_requests)}", "C": "Sai 2", "D": "Sai 3"},
                "answer": "B",
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", fake_chat)
    res = service.generate_from_passages(
        [src],
        count=3,
        difficulty="hard",
        model="qwen3.5-9b-4bit",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert "Tạo đủ 3 câu hỏi" in gen_requests[0]
    assert "Tạo đủ 1 câu hỏi" in gen_requests[1]
    assert res["count"] >= 1


def test_collect_verified_bank_questions_falls_back_to_shared_practice_db(tmp_path, monkeypatch):
    import app.practice as practice_mod

    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    acct_db = tmp_path / "acct_practice.db"
    monkeypatch.setattr(practice_mod, "DATA_DIR", shared_dir)

    src = passage(chunk_id=1, text=PASSAGE)
    shared_service = PracticeService(
        FakeSearcher([src]),
        FakeDocumentLookup(),
        db_path=shared_dir / "practice.db",
        enable_verified_bank=True,
    )
    monkeypatch.setattr(
        shared_service,
        "_chat_complete",
        lambda prompt, *a, **k: json.dumps(
            {"checks": [{**VALIDATION["checks"][0], "difficulty_matches": True}]}
            if "Dữ liệu cần kiểm định:\n" in prompt
            else {"questions": [{**QUESTION, "source_chunk_id": 1}]},
            ensure_ascii=False,
        ),
    )
    seeded = shared_service.generate_from_passages(
        [src], count=1, difficulty="medium", model="test-model", enforce_difficulty=True
    )
    assert seeded["count"] == 1

    # Account-scoped service points to empty acct_db, must read & validate from shared DATA_DIR/practice.db
    monkeypatch.setenv("PRACTICE_DB_PATH", str(acct_db))
    acct_service = PracticeService(
        FakeSearcher([src]),
        FakeDocumentLookup(),
        db_path=None,
        enable_verified_bank=True,
    )
    monkeypatch.setattr(
        acct_service,
        "_chat_complete",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should reuse shared bank without calling model")),
    )
    reused = acct_service.generate_from_passages(
        [src], count=1, difficulty="medium", model="test-model", enforce_difficulty=True
    )
    assert reused["cached_from_bank"] == 1
    assert reused["questions"][0]["question"] == QUESTION["question"]

    # Changed passage hash rejects shared bank entry (fail-closed)
    changed_src = passage(chunk_id=1, text=PASSAGE + " Nội dung nguồn đã đổi.")
    assert acct_service._collect_verified_bank_questions(
        {1: changed_src}, count=1, model="test-model", difficulty="medium", enforce_difficulty=True
    ) == []


def test_idle_bank_seeder_seeds_dedicated_topics_when_idle_and_skips_busy():
    from contextlib import nullcontext
    from app.practice import IdleBankSeeder

    calls = []

    class FakeLearning:
        def topics(self):
            return {
                "topics": [
                    {"id": "Đạo hàm", "label": "Đạo hàm", "coverage": "dedicated"},
                    {"id": "Nhắc thoáng", "label": "Nhắc thoáng", "coverage": "mention_only"},
                ]
            }

        def generate(self, **kwargs):
            calls.append(kwargs)
            return {"questions": [{"id": "q1"}]}

    class FakeGPU:
        def __init__(self):
            self.busy = True

        def is_busy(self):
            return self.busy

        def acquire_for_inference(self):
            return nullcontext()

    class FakeGate:
        def acquire(self, timeout=0.0):
            return nullcontext()

        def get_status(self):
            return {"active_requests": 0, "queued_requests": 0}

    gpu = FakeGPU()
    seeder = IdleBankSeeder(
        learning_service=FakeLearning(),
        gpu_coordinator=gpu,
        dialogue_gate=FakeGate(),
        model="qwen3.5-9b-4bit",
        max_topics_per_cycle=4,
    )
    busy_res = seeder.run_once()
    assert busy_res == {"seeded": 0, "skipped_busy": True}
    assert calls == []

    gpu.busy = False
    idle_res = seeder.run_once()
    assert idle_res == {"seeded": 2, "skipped_busy": False}
    assert [(c["topic"], c["difficulty"], c["count"]) for c in calls] == [
        ("Đạo hàm", "easy", 1),
        ("Đạo hàm", "medium", 1),
    ]


def test_easy_cloze_repair_notes_for_incompatible_distractors_and_superstring(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    slide_text = (
        "Trong phân tích điều kiện nghiệm thực, phương trình bậc hai có hai nghiệm phân biệt khi biệt thức D > 0. "
        "Cấu trúc biểu diễn dữ liệu phổ biến là danh sách liên kết đơn với các nút nối tiếp nhau."
    )

    # Case 1: incompatible_distractors repair note
    src1 = passage(chunk_id=201, text=slide_text)
    svc1 = PracticeService(FakeSearcher([src1]), FakeDocumentLookup(), db_path=tmp_path / "prac_incompat.db")
    cloze_prompts1 = []

    def incompat_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            item = items[0]
            ans_letter = next(k for k, v in item["choices"].items() if v == "hai nghiệm phân biệt")
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": "phương trình bậc hai có hai nghiệm phân biệt khi biệt thức D > 0.",
                    "choice_comparison": f"{ans_letter} khớp nội dung nguồn.",
                    "matching_choices": ans_letter,
                    "supported": True,
                    "qualifiers_preserved": True,
                    "unique_answer": True,
                    "verified_answer": ans_letter,
                }]
            }, ensure_ascii=False)
        cloze_prompts1.append(prompt)
        if len(cloze_prompts1) == 1:
            return json.dumps({
                "questions": [{
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "D > 0",
                    "distractors": ["D != 0", "D < 0", "D = 0"],
                }]
            }, ensure_ascii=False)
        assert "thuộc miền giá trị hay điều kiện RỜI NHAU" in prompt
        return json.dumps({
            "questions": [{
                "format": "source_cloze",
                "evidence_id": "Q1",
                "answer": "hai nghiệm phân biệt",
                "distractors": ["nghiệm kép duy nhất", "vô nghiệm trên tập thực", "ba nghiệm phức độc lập"],
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(svc1, "_chat_complete", incompat_chat)
    res1 = svc1.generate_from_passages(
        [src1], count=1, difficulty="easy", model="qwen3.5-9b-4bit", allow_retry=True, enforce_difficulty=True
    )
    assert res1["count"] == 1
    assert len(cloze_prompts1) == 2

    # Case 2: superstring_choice repair note
    src2 = passage(chunk_id=202, text=slide_text)
    svc2 = PracticeService(FakeSearcher([src2]), FakeDocumentLookup(), db_path=tmp_path / "prac_super.db")
    cloze_prompts2 = []

    def super_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            item = items[0]
            ans_letter = next(k for k, v in item["choices"].items() if v == "danh sách liên kết đơn")
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": "Cấu trúc biểu diễn dữ liệu phổ biến là danh sách liên kết đơn với các nút nối tiếp nhau.",
                    "choice_comparison": f"{ans_letter} khớp cụm.",
                    "matching_choices": ans_letter,
                    "supported": True,
                    "qualifiers_preserved": True,
                    "unique_answer": True,
                    "verified_answer": ans_letter,
                }]
            }, ensure_ascii=False)
        cloze_prompts2.append(prompt)
        if len(cloze_prompts2) == 1:
            return json.dumps({
                "questions": [{
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "danh sách liên kết đơn",
                    "distractors": ["danh sách", "cây nhị phân", "ngăn xếp"],
                }]
            }, ensure_ascii=False)
        assert "chuỗi con hoặc chứa trọn một lựa chọn khác" in prompt
        return json.dumps({
            "questions": [{
                "format": "source_cloze",
                "evidence_id": "Q1",
                "answer": "danh sách liên kết đơn",
                "distractors": ["bảng băm động", "cây nhị phân cân bằng", "hàng đợi ưu tiên"],
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(svc2, "_chat_complete", super_chat)
    res2 = svc2.generate_from_passages(
        [src2], count=1, difficulty="easy", model="qwen3.5-9b-4bit", allow_retry=True, enforce_difficulty=True
    )
    assert res2["count"] == 1
    assert len(cloze_prompts2) == 2


def test_generate_from_passages_easy_rotates_and_retries_cloze_on_second_attempt(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    p1 = passage(
        chunk_id=301,
        text="Chương mở đầu giới thiệu các ký hiệu cơ bản dùng xuyên suốt toàn bộ tài liệu học tập này.",
    )
    p2 = passage(
        chunk_id=302,
        text="Hàng đợi ưu tiên cho phép lấy ra phần tử có độ ưu tiên cao nhất trong thời gian logarit.",
    )
    service = PracticeService(FakeSearcher([p1, p2]), FakeDocumentLookup(), db_path=tmp_path / "prac_cloze_rot.db")
    cloze_leading_ids = []

    def rotate_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            item = items[0]
            ans_letter = next(k for k, v in item["choices"].items() if v == "Hàng đợi ưu tiên")
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": "Hàng đợi ưu tiên cho phép lấy ra phần tử có độ ưu tiên cao nhất trong thời gian logarit.",
                    "choice_comparison": f"{ans_letter} chính xác.",
                    "matching_choices": ans_letter,
                    "supported": True,
                    "qualifiers_preserved": True,
                    "unique_answer": True,
                    "verified_answer": ans_letter,
                }]
            }, ensure_ascii=False)
        if '"format": "source_cloze"' in prompt or "điền cụm ngắn vào chỗ trống" in prompt:
            q1_section = prompt.split('evidence_id="Q1">', 1)[1].split("</passage>", 1)[0]
            leading = 301 if "ký hiệu cơ bản" in q1_section else 302
            cloze_leading_ids.append(leading)
            if leading == 301:
                return json.dumps({"questions": []}, ensure_ascii=False)
            return json.dumps({
                "questions": [{
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "Hàng đợi ưu tiên",
                    "distractors": ["Ngăn xếp liên kết", "Bảng băm tuyến tính", "Đồ thị vô hướng"],
                }]
            }, ensure_ascii=False)
        # Fallback statement generation on attempt 0 returns no candidates so attempt 1 runs
        return json.dumps({"questions": []}, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", rotate_chat)
    res = service.generate_from_passages(
        [p1, p2], count=1, difficulty="easy", model="qwen3.5-9b-4bit", allow_retry=True, enforce_difficulty=True
    )
    assert res["count"] == 1
    assert cloze_leading_ids == [301, 302]
    assert res["questions"][0]["source"]["chunk_id"] == 302


def test_build_practice_generation_prompt_hard_includes_two_step_guidance_and_scaled_bounds():
    from app.practice import build_practice_generation_prompt

    passages = [
        {
            "chunk_id": 11,
            "page_num": 5,
            "text": "Quy tắc 1: Nếu hệ số góc dương thì hàm tăng. Quy tắc 2: Khi biên dương thì cực đại đạt ở biên phải.",
        }
    ]
    hard_prompt = build_practice_generation_prompt(passages, count=1, difficulty="hard")
    medium_prompt = build_practice_generation_prompt(passages, count=1, difficulty="medium")

    assert "Bước 1: ... Bước 2: ..." in hard_prompt
    assert "question tối đa 65 từ, mỗi lựa chọn tối đa 20 từ, explanation tối đa 60 từ" in hard_prompt
    assert "question tối đa 45 từ, mỗi lựa chọn tối đa 15 từ, explanation tối đa 35 từ" in medium_prompt


def test_generate_from_passages_easy_continues_cloze_and_prioritizes_unused_passages(monkeypatch, tmp_path):
    import re
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    topics_and_answers = [
        ("Ngăn xếp tuyến tính", "lưu trữ theo cơ chế LIFO chuẩn mực", ["cơ chế FIFO", "cây cân bằng", "bảng băm"]),
        ("Hàng đợi vòng", "phân phối tác vụ theo thứ tự FIFO", ["ngăn xếp đơn", "đồ thị đầy đủ", "hàm băm"]),
        ("Danh sách liên kết", "liên kết các phần tử qua con trỏ động", ["mảng kích thước tĩnh", "tập hợp rời", "ma trận thưa"]),
        ("Cây nhị phân tìm kiếm", "duy trì nhánh trái nhỏ hơn nút gốc", ["nhánh trái lớn hơn", "đồ thị chu trình", "hàng đợi kép"]),
        ("Bảng băm mở", "tra cứu khóa trong thời gian trung bình O(1)", ["thời gian O(n log n)", "sắp xếp nổi bọt", "cây khung cực tiểu"]),
        ("Đệ quy đuôi", "tối ưu khung ngăn xếp gọi hàm cuối", ["vòng lặp vô hạn", "mảng hai chiều", "bộ đếm nguyên thủy"]),
    ]
    passages_list = [
        passage(
            chunk_id=401 + idx,
            text=f"Nội dung học thuật mục {idx + 1}: {title} giúp hệ thống {ans} trong cấu trúc dữ liệu.",
        )
        for idx, (title, ans, _) in enumerate(topics_and_answers)
    ]
    service = PracticeService(FakeSearcher(passages_list), FakeDocumentLookup(), db_path=tmp_path / "prac_multi_cloze.db")
    seen_q1_cids_per_attempt = []

    def multi_cloze_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            checks = []
            for idx, item in enumerate(items, 1):
                q_text = item["question"]
                matched = next(
                    (title, ans)
                    for title, ans, _ in topics_and_answers
                    if title in q_text
                )
                ans_letter = next(k for k, v in item["choices"].items() if v == matched[1])
                checks.append({
                    "index": idx,
                    "source_basis": f"{matched[0]} giúp hệ thống {matched[1]} trong cấu trúc dữ liệu.",
                    "choice_comparison": f"{ans_letter} chính xác.",
                    "matching_choices": ans_letter,
                    "supported": True,
                    "qualifiers_preserved": True,
                    "unique_answer": True,
                    "verified_answer": ans_letter,
                })
            return json.dumps({"checks": checks}, ensure_ascii=False)

        assert '"format": "source_cloze"' in prompt
        # Extract evidence_id -> chunk index shown in this prompt
        blocks = re.findall(r'<passage page="\d+" evidence_id="(Q\d+)">\s*(.*?)\s*</passage>', prompt, re.DOTALL)
        q1_cid = next(401 + idx for idx, (title, _, _) in enumerate(topics_and_answers) if title in blocks[0][1])
        seen_q1_cids_per_attempt.append(q1_cid)
        out = []
        # Return 2 items per attempt across attempts 0, 1, 2 (testing continuation past attempt 1!)
        for ev_id, block_txt in blocks[:2]:
            _, ans, dists = next(
                item for item in topics_and_answers if item[0] in block_txt
            )
            out.append({
                "format": "source_cloze",
                "evidence_id": ev_id,
                "answer": ans,
                "distractors": dists,
            })
        return json.dumps({"questions": out}, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", multi_cloze_chat)
    res = service.generate_from_passages(
        passages_list,
        count=5,
        difficulty="easy",
        model="qwen3.5-9b-4bit",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert res["count"] == 5
    assert len(seen_q1_cids_per_attempt) == 3
    # Every returned question came from a distinct passage; unused passages prioritized cleanly
    returned_cids = [q["source"]["chunk_id"] for q in res["questions"]]
    assert len(set(returned_cids)) == 5
    assert res["complete"] is True


def test_extract_contiguous_cloze_window_narrows_repeated_span_and_recovers_easy_candidate(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    multi_sent = (
        "Mạng nơ-ron học thông qua cơ chế cập nhật trọng số từ sai số dự đoán. "
        "Khi tăng số vòng lặp vừa phải, quá trình củng cố dài hạn giúp trí nhớ ổn định và bền vững hơn. "
        "Nhiều thí nghiệm về trí nhớ lặp lại đều quan sát sự thay đổi ở giai đoạn khởi đầu."
    )
    window = PracticeService._extract_contiguous_cloze_window(
        multi_sent, "củng cố dài hạn", max_chars=460, allow_narrow_unique=False
    )
    assert window is not None
    repeated_text = (
        "Thói quen tốt cần củng cố dài hạn để duy trì đều đặn mỗi ngày. "
        "Khi luyện tập đúng cách, cơ chế củng cố dài hạn giúp hình thành phản xạ tự động rất bền vững. "
        "Giai đoạn ban đầu thường cần nhắc nhở rõ ràng."
    )
    assert PracticeService._extract_contiguous_cloze_window(
        repeated_text, "củng cố dài hạn", max_chars=460, allow_narrow_unique=False
    ) is None
    narrowed = PracticeService._extract_contiguous_cloze_window(
        repeated_text, "củng cố dài hạn", max_chars=460, allow_narrow_unique=True
    )
    assert narrowed is not None and len(narrowed) < len(repeated_text)
    assert "phản xạ tự động" in narrowed or "mỗi ngày" in narrowed
    same_sentence_dup = "Khái niệm củng cố dài hạn lặp lại cụm củng cố dài hạn trong cùng một câu duy nhất."
    assert PracticeService._extract_contiguous_cloze_window(
        same_sentence_dup, "củng cố dài hạn", max_chars=460, allow_narrow_unique=True
    ) is None

    service = PracticeService(
        FakeSearcher([passage(chunk_id=501, text=repeated_text)]),
        FakeDocumentLookup(),
        db_path=tmp_path / "prac_narrow_cloze.db",
    )
    prompts = []

    def cloze_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        prompts.append(prompt)
        if len(prompts) == 1:
            # First miss triggers span_not_in_excerpt 1-shot repair
            return json.dumps({
                "questions": [{
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "cụm không tồn tại",
                    "distractors": ["phản xạ tạm thời", "trí nhớ giác quan", "quá trình ức chế"],
                }]
            }, ensure_ascii=False)
        # Second response repeats answer in full excerpt across two sentences -> recovered by narrow unique window
        return json.dumps({
            "questions": [{
                "format": "source_cloze",
                "evidence_id": "Q1",
                "answer": "củng cố dài hạn",
                "distractors": ["ức chế chủ động", "quên tự nhiên", "mất trí nhớ ngắn"],
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", cloze_chat)
    pas = passage(chunk_id=501, text=repeated_text)
    cands = service._generate_easy_cloze_candidates(
        [pas],
        1,
        "qwen3.5-9b-4bit",
        lambda p, cid, q: {"chunk_id": cid, "page_num": 1, "snippet": q},
    )
    assert len(prompts) == 2
    assert "NGUYÊN VĂN" in prompts[1]
    assert len(cands) == 1
    assert cands[0]["format"] == "source_cloze"
    assert "[…]" in cands[0]["question"]


def test_hard_quote_options_expand_to_420_and_score_deprioritizes_exercise_lists():
    from app.practice import (
        _practice_quote_options,
        _score_hard_passage_density,
        _select_hard_passages,
        practice_generation_schema,
    )

    theory = {
        "chunk_id": 601,
        "page_num": 10,
        "text": (
            "Nếu điều kiện biên ban đầu f(0) = 0 thỏa mãn trên đoạn đóng thì phương trình tích phân cho nghiệm duy nhất và liên tục theo tham số. "
            "Khi chuyển sang bước lặp thứ hai, thuật toán kiểm tra sai số P(x) <= eps và chỉ cập nhật nghiệm khi cả hai điều kiện hội tụ đều đạt yêu cầu. "
            "Do đó tốc độ hội tụ tăng gấp đôi so với phép lặp đơn thông thường trên tập dữ liệu lớn."
        ),
    }
    exercise_list = {
        "chunk_id": 602,
        "page_num": 25,
        "text": (
            "4.TF When the head pointer points to nullptr, the linked list is considered empty and traversal terminates immediately.\n"
            "5.TF If the condition holds, write an algorithm to compute the sum of all nodes when traversing from head."
        ),
    }

    assert _score_hard_passage_density(theory["text"]) > _score_hard_passage_density(exercise_list["text"])
    selected = _select_hard_passages([exercise_list, theory], count=1, offset=0)
    assert selected[0]["chunk_id"] == 601

    default_quotes = _practice_quote_options(theory, max_chars=1400)
    assert default_quotes and all(20 <= len(q) <= 240 for q in default_quotes)

    hard_quotes = _practice_quote_options(theory, max_chars=1400, max_quote_chars=420)
    assert any(240 < len(q) <= 420 for q in hard_quotes)

    schema_hard = practice_generation_schema(1, 1, [theory], max_quote_chars=420)
    item_props = schema_hard["properties"]["questions"]["items"]["properties"]
    assert item_props["evidence_quote"]["maxLength"] == 420


def test_easy_cloze_unescapes_html_operators_and_prioritizes_prose_over_cover_slide(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    cover_slide = passage(
        chunk_id=701,
        text="Inst. Nguyễn Minh Huy\nIntroduction to Programming\n1\nFundamentals of Programming - Nguyễn Minh Huy",
    )
    code_prose_raw = (
        "Khi dùng con trỏ trong C++, phép gán p = &n; lưu địa chỉ của biến n vào biến con trỏ p. "
        "Sau đó toán tử giải tham chiếu *p cho phép đọc và ghi trực tiếp giá trị tại ô nhớ đó."
    )
    code_prose = {
        **passage(chunk_id=702, text=code_prose_raw),
        "safe_text": code_prose_raw.replace("&", "&amp;"),
    }
    service = PracticeService(
        FakeSearcher([cover_slide, code_prose]),
        FakeDocumentLookup(),
        db_path=tmp_path / "prac_easy_html.db",
    )
    seen_prompts = []

    def html_cloze_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        seen_prompts.append(prompt)
        return json.dumps({
            "questions": [{
                "format": "source_cloze",
                "evidence_id": "Q1",
                "answer": "p = &n;",
                "distractors": ["p = *n;", "p = n;", "p == &n;"],
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", html_cloze_chat)
    cands = service._generate_easy_cloze_candidates(
        [cover_slide, code_prose],
        1,
        "qwen3.5-9b-4bit",
        lambda p, cid, q: {"chunk_id": cid, "page_num": 1, "snippet": q},
    )
    assert len(seen_prompts) == 1
    assert 'evidence_id="Q1">\nKhi dùng con trỏ' in seen_prompts[0]
    assert len(cands) == 1
    assert cands[0]["source"]["chunk_id"] == 702
    assert any(c["text"] == "p = &n;" for c in cands[0]["choices"])


def test_hard_scoring_deprioritizes_programming_challenges_and_enforces_min_quote_chars():
    from app.practice import (
        _practice_quote_options,
        _score_hard_passage_density,
        _select_hard_passages,
    )

    theory = {
        "chunk_id": 710,
        "page_num": 12,
        "text": (
            "Notice the use of nextNode instead of previousNode when unlinking a node cleanly. "
            "The nextNode pointer holds the position of the following element so it stays reachable after deleting nodePtr. "
            "If you delete nodePtr before saving nextNode, the chain to remaining elements is permanently lost."
        ),
    }
    challenge = {
        "chunk_id": 711,
        "page_num": 88,
        "text": (
            "8. List Template\n\n"
            "Create a list class template based on the linked list class when the head condition is met. "
            "Test the class in a driver program and verify every insertion step."
        ),
    }
    modify_prog = {
        "chunk_id": 712,
        "page_num": 89,
        "text": (
            "Modify the program you wrote for Programming Challenge 7 so that it validates input positions.\n"
            "Write a function that traverses when nullptr is encountered."
        ),
    }

    assert _score_hard_passage_density(theory["text"]) > _score_hard_passage_density(challenge["text"])
    assert _score_hard_passage_density(theory["text"]) > _score_hard_passage_density(modify_prog["text"])
    ranked = _select_hard_passages([challenge, modify_prog, theory], count=1, offset=0)
    assert ranked[0]["chunk_id"] == 710

    hard_quotes = _practice_quote_options(theory, max_chars=1400, max_quote_chars=420, min_quote_chars=140)
    assert hard_quotes and all(140 <= len(q) <= 420 for q in hard_quotes)


def test_easy_cloze_narrows_to_230_and_deprioritizes_glued_ocr(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    glued = passage(
        chunk_id=801,
        text=(
            "Bộnhớmáy tính gồm nhiều ô nhớ vật lý đánh địa chỉ tuần tự. "
            "Ngôn ngữ C/C++ không tựđộng thu hồi bộnhớđộng sau khi cấp phát."
        ),
    )
    long_clean = passage(
        chunk_id=802,
        text=(
            "Ngữ cảnh mở đầu dài để kéo tổng độ dài trích đoạn vượt ngưỡng 240 ký tự khi không thu hẹp khung câu. "
            "Theo tài liệu thiên văn cổ, người Maya quen dùng đơn vị số lượng tỷ để ghi chép chu kỳ dài. "
            "Nhận xét bổ sung phía sau cung cấp thêm bối cảnh lịch sử về cách đo lường thời gian cổ đại. "
            "Đoạn kết tiếp nối giúp toàn văn vượt xa hai trăm ba mươi ký tự trong cùng một trích đoạn."
        ),
    )
    service = PracticeService(
        FakeSearcher([glued, long_clean]),
        FakeDocumentLookup(),
        db_path=tmp_path / "prac_cloze_230.db",
    )
    seen_prompts = []

    def cloze_230_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        seen_prompts.append(prompt)
        return json.dumps({
            "questions": [{
                "format": "source_cloze",
                "evidence_id": "Q1",
                "answer": "đơn vị số lượng tỷ",
                "distractors": ["đơn vị khối lượng tấn", "đơn vị độ dài dặm", "đơn vị thời gian giây"],
            }]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", cloze_230_chat)
    cands = service._generate_easy_cloze_candidates(
        [glued, long_clean],
        1,
        "qwen3.5-9b-4bit",
        lambda p, cid, q: {"chunk_id": cid, "page_num": 1, "snippet": q},
    )
    assert seen_prompts and 'evidence_id="Q1">\nNgữ cảnh mở đầu dài' in seen_prompts[0]
    assert len(cands) == 1
    assert cands[0]["source"]["chunk_id"] == 802
    assert len(cands[0]["evidence_quote"]) <= 230


def test_score_hard_passage_density_penalizes_lowercase_fragments():
    from app.practice import _score_hard_passage_density, _select_hard_passages

    fragment = {
        "chunk_id": 811,
        "page_num": 5,
        "text": (
            "[NỘI DUNG NGUỒN ĐÃ ĐỐI CHIẾU ẢNH; KÝ HIỆU CHUẨN HÓA]\n"
            "n of times that the needle intersects a line is the ratio. "
            "If this condition holds, therefore compute step two from the previous formula."
        ),
    }
    complete = {
        "chunk_id": 812,
        "page_num": 6,
        "text": (
            "If event A and event B satisfy Bayes rule, we update posterior odds cleanly. "
            "When conditions fail, fall back to the prior probability bound."
        ),
    }
    assert _score_hard_passage_density(complete["text"]) > _score_hard_passage_density(fragment["text"])
    ranked = _select_hard_passages([fragment, complete], count=1, offset=0)
    assert ranked[0]["chunk_id"] == 812


def test_hard_generation_oversamples_pairs_and_passes_retry_hint_on_difficulty_mismatch(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    p1 = passage(
        chunk_id=821,
        text=(
            "Quy tắc Cramer: nếu Δ≠0 thì hệ có nghiệm duy nhất; nếu Δ=0 và tồn tại Δ_i≠0 thì hệ vô nghiệm. "
            "Ngoài ra nếu mọi Δ_i=0 khi Δ=0 thì cần khảo sát thêm theo hạng ma trận."
        ),
    )
    p2 = passage(
        chunk_id=822,
        text=(
            "Ma trận vuông A khả nghịch khi và chỉ khi det(A)≠0; khi đó nghiệm hệ AX=B là X=A⁻¹B. "
            "Nếu hạng ma trận mở rộng lớn hơn hạng ma trận hệ số thì hệ phương trình vô nghiệm."
        ),
    )
    service = PracticeService(
        FakeSearcher([p1, p2]),
        FakeDocumentLookup(),
        db_path=tmp_path / "prac_hard_retry_hint.db",
    )
    gen_calls = []
    q1 = {
        "source_chunk_id": 821,
        "evidence_quote": "nếu Δ≠0 thì hệ có nghiệm duy nhất; nếu Δ=0 và tồn tại Δ_i≠0 thì hệ vô nghiệm.",
        "question": "Cho hệ AX=B có Δ=0 và Δ_1=5. Kết luận nào đúng?",
        "explanation": "Vì Δ=0 và Δ_1≠0 nên hệ vô nghiệm.",
        "choices": {"A": "Nghiệm duy nhất", "B": "Vô nghiệm", "C": "Vô số nghiệm", "D": "Không xác định"},
        "answer": "B",
    }
    q2 = {
        "source_chunk_id": 822,
        "evidence_quote": "Ma trận vuông A khả nghịch khi và chỉ khi det(A)≠0; khi đó nghiệm hệ AX=B là X=A⁻¹B.",
        "question": "Nếu det(A)≠0, phát biểu nào đúng về tính khả nghịch của A và nghiệm hệ AX=B?",
        "explanation": "Bước 1: det(A)≠0 nên A khả nghịch. Bước 2: Nghiệm là X=A⁻¹B.",
        "choices": {
            "A": "A khả nghịch và nghiệm X=A⁻¹B",
            "B": "A khả nghịch và hệ vô nghiệm",
            "C": "A không khả nghịch và X=A⁻¹B",
            "D": "A không khả nghịch và hệ vô nghiệm",
        },
        "answer": "A",
    }

    def fake_gen(passages_arg, gen_count, model, **kwargs):
        gen_calls.append({"gen_count": gen_count, "retry_hint": kwargs.get("retry_hint")})
        item = q1 if len(gen_calls) == 1 else q2
        return json.dumps({"questions": [item]}, ensure_ascii=False)

    def fake_verify(candidates, passage_by_id, model, difficulty=None, timeout=None, stop_after=None, study_material_review=False, reject_reasons_out=None):
        cand = candidates[0]
        if "Δ=0" in cand["question"]:
            if reject_reasons_out is not None:
                reject_reasons_out.add("difficulty_mismatch")
            return []
        return [cand]

    monkeypatch.setattr(service, "_generate_text", fake_gen)
    monkeypatch.setattr(service, "_verify_questions", fake_verify)

    res = service.generate_from_passages(
        [p1, p2],
        count=1,
        difficulty="hard",
        model="qwen3.5-9b-4bit",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert res["count"] == 1
    assert gen_calls[0]["gen_count"] == 1
    assert gen_calls[0]["retry_hint"] is None
    assert gen_calls[1]["retry_hint"] is not None
    assert "2 BƯỚC" in gen_calls[1]["retry_hint"]


def test_extract_supporting_evidence_normalizes_pua_bullets_while_staying_contiguous():
    slide_passage = (
        "\uf0a7Con trỏ NULL là con trỏ không trỏ vào đâu cả.\n"
        "\uf0a7Khác với con trỏ chưa được khởi tạo.\n"
        "Ví dụ khai báo int *p = nullptr;"
    )
    reviewer_basis = "Con trỏ NULL là con trỏ không trỏ vào đâu cả.\nKhác với con trỏ chưa được khởi tạo."
    matched = PracticeService.extract_supporting_evidence(slide_passage, reviewer_basis)
    assert matched == "Con trỏ NULL là con trỏ không trỏ vào đâu cả. Khác với con trỏ chưa được khởi tạo."

    # Non-contiguous or altered words must still fail closed.
    assert PracticeService.extract_supporting_evidence(slide_passage, "Con trỏ NULL trỏ vào đâu") == ""
    assert PracticeService.extract_supporting_evidence(slide_passage, "Con trỏ chưa được") != ""


def test_hard_native_review_schema_and_witness_align_to_420_chars():
    from app.practice_proof import native_grounded_review_schema, native_review_witness_is_bound

    hard_schema = native_grounded_review_schema(1, "hard")
    medium_schema = native_grounded_review_schema(1, "medium")
    none_schema = native_grounded_review_schema(1, None)

    assert hard_schema["properties"]["checks"]["items"]["properties"]["source_basis"]["maxLength"] == 420
    assert medium_schema["properties"]["checks"]["items"]["properties"]["source_basis"]["maxLength"] == 240
    assert none_schema["properties"]["checks"]["items"]["properties"]["source_basis"]["maxLength"] == 240

    long_hard_source = (
        "Nếu hàm f(x) liên tục trên đoạn đóng [a, b] và khả vi trên khoảng mở (a, b) với đạo hàm đổi dấu từ dương sang âm tại điểm c "
        "thì f(c) đạt cực đại địa phương trên khoảng chứa c; đồng thời nếu giá trị biên f(b) vượt mọi điểm dừng bên trong khoảng mở "
        "thì cực đại toàn cục trên đoạn đóng [a, b] đạt ngay tại điểm biên phải b theo định lý Weierstrass."
    )
    assert 240 < len(long_hard_source) <= 420
    check = {
        "source_basis": long_hard_source,
        "matching_choices": "B",
        "unique_answer": True,
        "verified_answer": "B",
        "supported": True,
    }
    assert native_review_witness_is_bound(check, long_hard_source) is True


def test_mlx_medium_topic_practice_retains_fourth_backup_and_uses_compact_review(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    passages_list = [
        passage(
            chunk_id=900 + i,
            text=f"Khi điều kiện ngưỡng thứ {i} thỏa mãn thì đầu ra hệ thống bảo toàn thông lượng chuẩn ở mức {i}.",
        )
        for i in range(1, 7)
    ]
    service = PracticeService(
        FakeSearcher(passages_list),
        FakeDocumentLookup(),
        db_path=tmp_path / "prac_med_compact.db",
    )
    verify_calls = []

    def fake_gen(passages_arg, gen_count, model, **kwargs):
        all_qs = [
            {
                "source_chunk_id": 900 + j,
                "evidence_quote": passages_list[j - 1]["text"],
                "question": f"Theo đoạn trích, trường hợp {j} khi điều kiện ngưỡng thỏa mãn cho kết quả nào?",
                "explanation": f"Đoạn trích nêu đầu ra bảo toàn thông lượng ở mức {j}.",
                "choices": {
                    "A": f"Mất thông lượng {j}",
                    "B": f"Bảo toàn thông lượng ở mức {j}",
                    "C": f"Đảo chiều {j}",
                    "D": f"Bằng không {j}",
                },
                "answer": "B",
            }
            for j in range(1, 7)
        ]
        excluded = set(kwargs.get("exclude_questions") or [])
        available = [item for item in all_qs if item["question"] not in excluded]
        return json.dumps({"questions": available[:max(4, gen_count)]}, ensure_ascii=False)

    orig_verify = service._verify_questions

    def spy_verify(candidates, passage_by_id, model, difficulty=None, timeout=None, stop_after=None, study_material_review=False, reject_reasons_out=None):
        verify_calls.append({
            "candidate_count": len(candidates),
            "stop_after": stop_after,
            "study_material_review": study_material_review,
        })
        return orig_verify(
            candidates, passage_by_id, model, difficulty=difficulty, timeout=timeout,
            stop_after=stop_after, study_material_review=study_material_review, reject_reasons_out=reject_reasons_out,
        )

    review_prompts = []

    def fake_review_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        review_prompts.append(prompt)
        items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
        assert all("evidence_quote" not in it for it in items)
        return json.dumps({
            "checks": [
                {
                    "index": it["index"],
                    "source_basis": it["source"],
                    **AUDIT_FIELDS,
                    "matching_choices": "B",
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "difficulty_matches": True,
                    "verified_answer": "B",
                }
                for it in items
            ]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_generate_text", fake_gen)
    monkeypatch.setattr(service, "_verify_questions", spy_verify)
    monkeypatch.setattr(service, "_chat_complete", fake_review_chat)

    res = service.generate_from_passages(
        passages_list,
        count=4,
        difficulty="medium",
        model="qwen3.5-9b-4bit",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert res["count"] == 4
    assert verify_calls[0]["candidate_count"] == 4
    assert verify_calls[0]["stop_after"] == 3
    assert verify_calls[0]["study_material_review"] is True
    assert review_prompts and "Kiểm định độc lập từng câu trắc nghiệm mức medium" in review_prompts[0]


def test_easy_cloze_oversamples_plus_one_when_count_at_least_two_to_absorb_python_drop(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    p1 = passage(
        chunk_id=951,
        text="Cây nhị phân cân bằng duy trì chiều cao logarit khi thực hiện phép xoay tại các nút mất cân bằng.",
    )
    p2 = passage(
        chunk_id=952,
        text="Hàng đợi vòng sử dụng mảng cố định kết hợp phép chia lấy dư để tận dụng vùng nhớ đầu mảng.",
    )
    p3 = passage(
        chunk_id=953,
        text="Bảng băm xử lý đụng độ bằng phương pháp nối kết hoặc thăm dò tuyến tính theo chỉ số ô nhớ.",
    )
    service = PracticeService(FakeSearcher([p1, p2, p3]), FakeDocumentLookup(), db_path=tmp_path / "prac_cloze_plus1.db")
    calls = []

    def cloze_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        schema = kwargs["response_schema"]
        calls.append(schema["properties"]["questions"]["maxItems"])
        assert "Tạo 3 câu chọn cụm điền khuyết" in prompt
        return json.dumps({
            "questions": [
                {
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "cụm không có trong trích đoạn",
                    "distractors": ["cây nhị phân", "cây khung", "cây quyết định"],
                },
                {
                    "format": "source_cloze",
                    "evidence_id": "Q2",
                    "answer": "phép chia lấy dư",
                    "distractors": ["phép nhân ma trận", "phép dịch bit trái", "phép lấy căn bậc hai"],
                },
                {
                    "format": "source_cloze",
                    "evidence_id": "Q3",
                    "answer": "thăm dò tuyến tính",
                    "distractors": ["tìm kiếm nhị phân", "sắp xếp trộn", "duyệt theo chiều sâu"],
                },
            ]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", cloze_chat)
    cands = service._generate_easy_cloze_candidates(
        [p1, p2, p3],
        2,
        "qwen3.5-9b-4bit",
        lambda p, cid, q: {"chunk_id": cid, "page_num": 1, "snippet": q},
    )
    assert calls == [3]
    assert len(cands) == 2
    assert [c["source"]["chunk_id"] for c in cands] == [952, 953]


def test_mlx_medium_tail_batch_oversamples_plus_one_with_stop_after(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    passages_list = [
        passage(
            chunk_id=960 + i,
            text=f"Theo quy tắc thứ {i}, khi bộ đệm đầy thì hệ thống tự động chuyển sang hàng đợi phụ {i}.",
        )
        for i in range(1, 6)
    ]
    service = PracticeService(
        FakeSearcher(passages_list),
        FakeDocumentLookup(),
        db_path=tmp_path / "prac_med_tail_oversample.db",
    )
    gen_counts = []
    verify_calls = []

    def make_q(j):
        return {
            "source_chunk_id": 960 + j,
            "evidence_quote": passages_list[j - 1]["text"],
            "question": f"Theo đoạn trích, khi bộ đệm đầy ở trường hợp {j} thì hệ thống xử lý ra sao?",
            "explanation": f"Đoạn trích nêu hệ thống chuyển sang hàng đợi phụ {j}.",
            "choices": {
                "A": f"Hủy dữ liệu {j}",
                "B": f"Chuyển sang hàng đợi phụ {j}",
                "C": f"Tăng kích thước ổ cứng {j}",
                "D": f"Ngắt kết nối ngay {j}",
            },
            "answer": "B",
        }

    def fake_gen(passages_arg, gen_count, model, **kwargs):
        gen_counts.append(gen_count)
        excluded = set(kwargs.get("exclude_questions") or [])
        avail = [make_q(j) for j in range(1, 6) if make_q(j)["question"] not in excluded]
        return json.dumps({"questions": avail[:gen_count]}, ensure_ascii=False)

    def fake_verify(candidates, passage_by_id, model, difficulty=None, timeout=None, stop_after=None, study_material_review=False, reject_reasons_out=None):
        verify_calls.append({"len": len(candidates), "stop_after": stop_after, "study_material_review": study_material_review})
        if len(verify_calls) == 2:
            # First candidate on the remaining tail fails; backup 2nd candidate succeeds
            return [candidates[1]]
        return list(candidates[: stop_after or len(candidates)])

    monkeypatch.setattr(service, "_generate_text", fake_gen)
    monkeypatch.setattr(service, "_verify_questions", fake_verify)

    res = service.generate_from_passages(
        passages_list,
        count=4,
        difficulty="medium",
        model="qwen3.5-9b-4bit",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert res["count"] == 4
    # First batch (remaining=4, batch_size=3 -> gen 3+1); tail batch (remaining=1 -> oversample gen 2!)
    assert gen_counts == [3, 2]
    assert verify_calls == [
        {"len": 3, "stop_after": None, "study_material_review": True},
        {"len": 2, "stop_after": 1, "study_material_review": True},
    ]


def test_idle_bank_seeder_prioritizes_dedicated_doc_count_and_multi_round_depth():
    from contextlib import nullcontext
    from app.practice import IdleBankSeeder

    calls = []

    class FakeLearning:
        def topics(self):
            return {
                "topics": [
                    {"id": "T1", "label": "Chủ đề 1 doc", "coverage": "dedicated", "dedicated_document_count": 1},
                    {"id": "T2", "label": "Chủ đề 5 doc", "coverage": "dedicated", "dedicated_document_count": 5},
                ]
            }

        def generate(self, **kwargs):
            calls.append(kwargs)
            return {"questions": [{"id": f"q{i}"} for i in range(kwargs["count"])]}

    class IdleGPU:
        def is_busy(self):
            return False

        def acquire_for_inference(self):
            return nullcontext()

    class IdleGate:
        def acquire(self, timeout=0.0):
            return nullcontext()

        def get_status(self):
            return {"active_requests": 0, "queued_requests": 0}

    seeder = IdleBankSeeder(
        learning_service=FakeLearning(),
        gpu_coordinator=IdleGPU(),
        dialogue_gate=IdleGate(),
        model="qwen3.5-9b-4bit",
        max_topics_per_cycle=2,
        target_per_topic=2,
    )
    res1 = seeder.run_once()
    assert res1 == {"seeded": 2, "skipped_busy": False}
    assert [(c["topic"], c["difficulty"], c["count"]) for c in calls] == [
        ("Chủ đề 5 doc", "easy", 1),
        ("Chủ đề 5 doc", "medium", 1),
    ]
    # Cycle 2 covers remaining dedicated topic (breadth first) before returning for depth 2
    res2 = seeder.run_once()
    assert res2 == {"seeded": 2, "skipped_busy": False}
    assert [(c["topic"], c["difficulty"], c["count"]) for c in calls[2:]] == [
        ("Chủ đề 1 doc", "easy", 1),
        ("Chủ đề 1 doc", "medium", 1),
    ]
    # Cycle 3 increments depth to count=2 on highest-priority dedicated topic
    res3 = seeder.run_once()
    assert res3 == {"seeded": 2, "skipped_busy": False}
    assert [(c["topic"], c["difficulty"], c["count"]) for c in calls[4:]] == [
        ("Chủ đề 5 doc", "easy", 2),
        ("Chủ đề 5 doc", "medium", 2),
    ]


def test_selected_page_practice_rejects_ultra_thin_page_and_enables_retry_on_mlx(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    thin = {**passage(chunk_id=31, page=1, text="Chương 1\n1"), "doc_id": 2}
    thin_service = PracticeService(
        FakeSearcher([thin]), FakeDocumentLookup(), db_path=tmp_path / "prac_thin.db"
    )
    monkeypatch.setattr(
        thin_service,
        "_generate_text",
        lambda *a, **kw: pytest.fail("Should reject ultra-thin page before invoking model"),
    )
    with pytest.raises(PracticeGenerationError, match="quá ít nội dung văn bản"):
        thin_service.generate(2, 1, count=2, model="qwen3.5-9b-4bit")

    rich = {
        **passage(
            chunk_id=32,
            page=17,
            text="Thuật toán sắp xếp nhanh chia dãy theo chốt và đệ quy hai nửa trong thời gian trung bình O(n log n).",
        ),
        "doc_id": 2,
    }
    rich_service = PracticeService(
        FakeSearcher([rich]), FakeDocumentLookup(), db_path=tmp_path / "prac_mlx_retry.db"
    )
    captured = {}

    def fake_gen_from_passages(passages_arg, **kwargs):
        captured.update(kwargs)
        return {"practice_id": "ok", "count": 2, "questions": []}

    monkeypatch.setattr(rich_service, "generate_from_passages", fake_gen_from_passages)
    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: str(model).startswith("qwen"))

    rich_service.generate(2, 17, count=2, model="qwen3.5-9b-4bit")
    assert captured["allow_retry"] is True
    rich_service.generate(2, 17, count=2, model="remote-ollama-model")
    assert captured["allow_retry"] is False


def test_verify_questions_hard_split_handles_stop_after_none(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: str(model).startswith("qwen"))
    p1 = passage(
        chunk_id=971,
        text="Nếu biến cố B có xác suất dương P(B) > 0 và A độc lập với B thì P(A|B) = P(A).",
    )
    p2 = passage(
        chunk_id=972,
        text="Nếu A và B xung khắc và đều có xác suất dương thì P(A hoặc B) = P(A) + P(B).",
    )
    service = PracticeService(FakeSearcher([p1, p2]), FakeDocumentLookup(), db_path=tmp_path / "prac_hard_stop_none.db")
    candidates = [
        {
            "id": "q1",
            "question": "Theo đoạn trích, nếu A độc lập với B và P(B) > 0 thì giá trị P(A|B) bằng gì?",
            "choices": [
                {"label": "A", "text": "P(A)"},
                {"label": "B", "text": "P(B)"},
                {"label": "C", "text": "P(A) + P(B)"},
                {"label": "D", "text": "0"},
            ],
            "answer": "A",
            "explanation": "Áp dụng điều kiện độc lập và P(B) > 0 suy ra P(A|B) = P(A).",
            "evidence_quote": p1["text"],
            "source": {"chunk_id": 971, "page_num": 1, "snippet": p1["text"]},
        },
        {
            "id": "q2",
            "question": "Theo đoạn trích, nếu A và B xung khắc với xác suất dương thì P(A hoặc B) bằng gì?",
            "choices": [
                {"label": "A", "text": "P(A) + P(B)"},
                {"label": "B", "text": "P(A) * P(B)"},
                {"label": "C", "text": "P(A) - P(B)"},
                {"label": "D", "text": "1 - P(A)"},
            ],
            "answer": "A",
            "explanation": "Hai biến cố xung khắc nên cộng xác suất thành phần.",
            "evidence_quote": p2["text"],
            "source": {"chunk_id": 972, "page_num": 1, "snippet": p2["text"]},
        },
    ]

    import app.practice as practice_mod

    class FakeClient:
        last_done_reason = "stop"
        last_diagnostics = {}

        def chat_complete(self, messages, system_prompt="", temperature=0.0, max_tokens=1000, **kwargs):
            prompt = messages[0]["content"]
            assert "Dữ liệu cần kiểm định:\n" in prompt
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            assert len(items) == 1
            item = items[0]
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "input_audit": "Đủ dữ kiện đầu vào.",
                    "source_basis": item["source"],
                    "independent_solution": "Đáp án A suy ra trực tiếp từ quy tắc.",
                    "choice_comparison": "Chỉ A đúng.",
                    "explanation_audit": "Giải thích khớp nguồn.",
                    "matching_choices": "A",
                    "supported": True,
                    "explanation_supported": True,
                    "unique_answer": True,
                    "qualifiers_preserved": True,
                    "difficulty_matches": True,
                    "verified_answer": "A",
                }]
            }, ensure_ascii=False)

    monkeypatch.setattr(practice_mod, "TrainedModelClient", lambda *a, **kw: FakeClient())
    verified = service._verify_questions(
        candidates,
        {971: p1, 972: p2},
        model="qwen3.5-9b-4bit",
        difficulty="hard",
        stop_after=None,
    )
    assert len(verified) == 2


def test_select_focused_cloze_excerpt_preserves_wrapped_first_line_and_strips_page_number():
    raw = (
        "Chapter 3 Linked Lists\n"
        "Một danh sách liên kết đơn lưu trữ phần tử rời rạc trong bộ nhớ và kết nối\n"
        "chúng bằng con trỏ kế tiếp theo đúng thứ tự duyệt tuyến tính.\n"
        "24"
    )
    excerpt = PracticeService._select_focused_cloze_excerpt(raw, max_chars=260)
    assert excerpt is not None
    assert excerpt.startswith("Một danh sách liên kết đơn lưu trữ")
    assert not excerpt.rstrip().endswith("24")


def test_extract_contiguous_cloze_window_ignores_abbreviations_and_page_citations():
    raw = (
        "Theo tài liệu (p. 123), hàm băm ánh xạ khóa về chỉ số mảng rất nhanh. "
        "Sau đó tra cứu trực tiếp ở ô nhớ tương ứng."
    )
    quote = PracticeService._extract_contiguous_cloze_window(raw, "chỉ số mảng", max_chars=96)
    assert quote is not None
    assert quote.startswith("Theo tài liệu (p. 123),")
    assert "Sau đó tra cứu" not in quote


def test_easy_cloze_returns_req_count_and_repairs_shape(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    p1 = passage(
        chunk_id=981,
        text="Cây nhị phân cân bằng duy trì chiều cao logarit khi thực hiện phép xoay tại các nút mất cân bằng.",
    )
    p2 = passage(
        chunk_id=982,
        text="Hàng đợi vòng sử dụng mảng cố định kết hợp phép chia lấy dư để tận dụng vùng nhớ đầu mảng.",
    )
    p3 = passage(
        chunk_id=983,
        text="Bảng băm xử lý đụng độ bằng phương pháp nối kết hoặc thăm dò tuyến tính theo chỉ số ô nhớ.",
    )
    service = PracticeService(FakeSearcher([p1, p2, p3]), FakeDocumentLookup(), db_path=tmp_path / "prac_cloze_req.db")
    prompts = []

    def cloze_repair_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps({
                "questions": [{
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "cân",
                    "distractors": ["chỉ số", "ô nhớ"],
                }]
            }, ensure_ascii=False)
        return json.dumps({
            "questions": [
                {
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "chiều cao logarit",
                    "distractors": ["độ phức tạp bậc hai", "bộ nhớ tĩnh", "kích thước cố định"],
                },
                {
                    "format": "source_cloze",
                    "evidence_id": "Q2",
                    "answer": "phép chia lấy dư",
                    "distractors": ["phép nhân ma trận", "phép dịch bit trái", "phép lấy căn bậc hai"],
                },
                {
                    "format": "source_cloze",
                    "evidence_id": "Q3",
                    "answer": "thăm dò tuyến tính",
                    "distractors": ["tìm kiếm nhị phân", "sắp xếp trộn", "duyệt theo chiều sâu"],
                },
            ]
        }, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", cloze_repair_chat)
    cands = service._generate_easy_cloze_candidates(
        [p1, p2, p3],
        2,
        "qwen3.5-9b-4bit",
        lambda p, cid, q: {"chunk_id": cid, "page_num": 1, "snippet": q},
    )
    assert len(prompts) == 2
    assert "3 distractors" in prompts[1] or "distractors" in prompts[1]
    assert len(cands) == 3


def test_generate_from_passages_easy_skips_statement_on_first_empty_cloze_and_allows_weak_topics(monkeypatch, tmp_path):
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    p1 = passage(
        chunk_id=991,
        text="Chương mở đầu trình bày ký hiệu quy ước tổng quát trong giáo trình lập trình cấu trúc.",
    )
    p2 = passage(
        chunk_id=992,
        text="Danh sách liên kết sử dụng con trỏ kế tiếp để nối các nút cấp phát động rời rạc.",
    )
    service = PracticeService(FakeSearcher([p1, p2]), FakeDocumentLookup(), db_path=tmp_path / "prac_cloze_weak.db")
    statement_prompts = []

    def flow_chat(prompt, model, max_tokens, timeout=None, **kwargs):
        if "Dữ liệu cần kiểm định:\n" in prompt:
            items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
            item = items[0]
            ans_letter = next(k for k, v in item["choices"].items() if v == "con trỏ kế tiếp")
            return json.dumps({
                "checks": [{
                    "index": 1,
                    "source_basis": p2["text"],
                    "choice_comparison": f"{ans_letter} chính xác.",
                    "matching_choices": ans_letter,
                    "supported": True,
                    "qualifiers_preserved": True,
                    "unique_answer": True,
                    "verified_answer": ans_letter,
                }]
            }, ensure_ascii=False)
        if '"format": "source_cloze"' in prompt or "điền cụm ngắn vào chỗ trống" in prompt:
            q1_section = prompt.split('evidence_id="Q1">', 1)[1].split("</passage>", 1)[0]
            if "ký hiệu quy ước" in q1_section:
                return json.dumps({"questions": []}, ensure_ascii=False)
            return json.dumps({
                "questions": [{
                    "format": "source_cloze",
                    "evidence_id": "Q1",
                    "answer": "con trỏ kế tiếp",
                    "distractors": ["mảng hai chiều", "ngăn xếp tĩnh", "bảng băm kép"],
                }]
            }, ensure_ascii=False)
        statement_prompts.append(prompt)
        return json.dumps({"questions": []}, ensure_ascii=False)

    monkeypatch.setattr(service, "_chat_complete", flow_chat)
    res = service.generate_from_passages(
        [p1, p2],
        count=1,
        difficulty="easy",
        purpose="review",
        weak_topics=["con trỏ"],
        model="qwen3.5-9b-4bit",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert res["count"] == 1
    assert statement_prompts == []
    assert "[…]" in res["questions"][0]["question"]
    assert res["questions"][0]["source"]["chunk_id"] == 992


def test_rotate_choices_balances_all_a_outputs_and_preserves_letter_references(monkeypatch, tmp_path):
    import app.practice as practice_mod
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    passages_list = [
        passage(
            chunk_id=1001 + i,
            text=f"Theo quy tắc thứ {i}, khi tải vượt ngưỡng T_{i} thì hệ thống tự động chia tải qua nút phụ {i} nhằm tránh nghẽn.",
        )
        for i in range(4)
    ]
    service = PracticeService(FakeSearcher(passages_list), FakeDocumentLookup(), db_path=tmp_path / "prac_rot_ad.db")

    class FakeBalancedClient:
        last_done_reason = "stop"
        last_diagnostics = {}

        def chat_complete(self, messages, system_prompt="", temperature=0.0, max_tokens=1000, **kwargs):
            prompt = messages[0]["content"]
            if "Dữ liệu cần kiểm định:\n" in prompt:
                items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
                return json.dumps({
                    "checks": [{
                        "index": it["index"],
                        "input_audit": "Đủ dữ kiện đầu vào.",
                        "source_basis": it["source"],
                        "independent_solution": "Đáp án đúng khớp với quy tắc phân tải trong đoạn.",
                        "choice_comparison": "Chỉ 1 phương án khớp.",
                        "explanation_audit": "Giải thích khớp nguồn.",
                        "matching_choices": next(
                            lbl for lbl, txt in it["choices"].items() if txt.startswith("Tự động chia tải")
                        ),
                        "supported": True,
                        "explanation_supported": True,
                        "unique_answer": True,
                        "qualifiers_preserved": True,
                        "difficulty_matches": True,
                        "verified_answer": next(
                            lbl for lbl, txt in it["choices"].items() if txt.startswith("Tự động chia tải")
                        ),
                    } for it in items]
                }, ensure_ascii=False)
            return json.dumps({
                "questions": [{
                    "source_chunk_id": idx + 1,
                    "evidence_quote": passages_list[idx]["text"],
                    "question": f"Theo đoạn trích, hệ thống xử lý thế nào khi tải vượt ngưỡng T_{idx}?",
                    "explanation": "Khi tải vượt ngưỡng trong quy tắc, hệ thống tự động chia tải qua nút phụ.",
                    "choices": {
                        "A": f"Tự động chia tải qua nút phụ {idx}",
                        "B": f"Ngắt toàn bộ kết nối mạng ở nút {idx}",
                        "C": f"Xóa dữ liệu hàng đợi tại nút {idx}",
                        "D": f"Khóa bộ nhớ đệm của nút {idx}",
                    },
                    "answer": "A",
                } for idx in range(4)]
            }, ensure_ascii=False)

    monkeypatch.setattr(practice_mod, "TrainedModelClient", lambda *a, **kw: FakeBalancedClient())
    res = service.generate_from_passages(
        passages_list,
        count=4,
        difficulty="medium",
        model="qwen3.5-9b-4bit",
        allow_retry=False,
        enforce_difficulty=False,
    )
    saved_questions = service._sessions[res["practice_id"]]["questions"]
    answers = [q["answer"] for q in saved_questions]
    assert len(set(answers)) >= 3

    explicit_choices = [{"label": lbl, "text": f"Option {lbl}"} for lbl in "ABCD"]
    kept_choices, kept_ans = PracticeService._rotate_choices_if_safe(
        explicit_choices, "A", "Phương án A đúng vì thỏa mãn điều kiện biên.", target_idx=2
    )
    assert kept_ans == "A"
    assert kept_choices == explicit_choices


def test_statement_passages_medium_avoids_bare_definitions_and_dangling_pronouns():
    from app.practice import _statement_passages

    bare_def_only = passage(
        chunk_id=1010,
        text=(
            "Bảng băm là một cấu trúc dữ liệu lưu cặp khóa và giá trị trong bộ nhớ chính. "
            "Hàm băm là hàm ánh xạ khóa thành chỉ số nguyên tương ứng."
        ),
    )
    assert _statement_passages([bare_def_only], 2, 1400, difficulty="medium") == [bare_def_only]

    mixed = passage(
        chunk_id=1011,
        text=(
            "Việc sử dụng kỹ năng đó giúp hoàn thành báo cáo đúng hạn hơn rất nhiều. "
            "Ngân sách là bảng dự toán thu chi hàng năm của một tổ chức doanh nghiệp. "
            "Khi bảng băm có hệ số tải vượt ngưỡng cho phép, hệ thống phải mở rộng mảng để giảm xung đột khóa."
        ),
    )
    picked = _statement_passages([mixed], 1, 1400, difficulty="medium", topic_terms=["bảng băm"])
    assert len(picked) >= 1
    assert "bảng băm" in picked[0]["text"].casefold()
    assert all("kỹ năng đó" not in item["text"].casefold() for item in picked)


def test_build_practice_generation_prompt_keeps_invariant_kv_prefix_across_counts():
    from app.practice import build_practice_generation_prompt

    passages_list = [passage(chunk_id=1020, text=PASSAGE)]
    p1 = build_practice_generation_prompt(passages_list, count=1, difficulty="medium")
    p3 = build_practice_generation_prompt(passages_list, count=3, difficulty="medium")
    assert p1.split("\n\nPassage được chọn:\n", 1)[0] == p3.split("\n\nPassage được chọn:\n", 1)[0]
    assert "Tạo đủ 3 câu hỏi" in p3


def test_generate_from_passages_excludes_accepted_quotes_prioritizes_unused_and_recovers_batch_size(monkeypatch, tmp_path):
    passages_list = [
        passage(chunk_id=1030 + i, text=f"Đoạn số {i}: Khi tín hiệu đầu vào tăng vượt mức chuẩn {i}, bộ điều khiển chuyển sang pha ổn định {i}.")
        for i in range(4)
    ]
    service = PracticeService(FakeSearcher(passages_list), FakeDocumentLookup(), db_path=tmp_path / "prac_unused_recover.db")
    seen_calls = []

    def tracking_gen(passages_arg, req_count, model, difficulty="medium", purpose="practice",
                     weak_topics=None, exclude_questions=None, timeout=None,
                     attempt_offset=0, exclude_quotes=None, retry_hint=None):
        seen_calls.append({
            "cids": [p["chunk_id"] for p in passages_arg],
            "count": req_count,
            "exclude_quotes": set(exclude_quotes or ()),
        })
        if len(seen_calls) == 1:
            return json.dumps({"questions": []}, ensure_ascii=False)
        selected = passages_arg[:req_count]
        return json.dumps({
            "questions": [{
                "source_chunk_id": p["chunk_id"],
                "evidence_quote": p["text"],
                "question": f"Theo đoạn trích {p['chunk_id']}, điều gì xảy ra khi tín hiệu vượt chuẩn?",
                "explanation": f"Bộ điều khiển chuyển pha ổn định theo đoạn {p['chunk_id']}.",
                "choices": {"A": "Sai 1", "B": f"Chuyển pha ổn định {p['chunk_id']}", "C": "Sai 2", "D": "Sai 3"},
                "answer": "B",
            } for p in selected]
        }, ensure_ascii=False)

    def tracking_verify(candidates, passage_map, model, difficulty=None, **kwargs):
        return candidates

    monkeypatch.setattr(service, "_generate_text", tracking_gen)
    monkeypatch.setattr(service, "_verify_questions", tracking_verify)
    res = service.generate_from_passages(
        passages_list,
        count=4,
        difficulty="medium",
        model="local-test",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert res["count"] == 4
    # Call 1: batch=3 returned empty -> temporary step-down to batch=2 on Call 2 -> recovered to remaining=2 on Call 3
    assert seen_calls[0]["count"] == 3
    assert seen_calls[1]["count"] == 2
    assert seen_calls[2]["count"] == 2
    # Call 3 must only receive unused passages (none of the chunk_ids accepted in Call 2) and must exclude accepted quotes
    accepted_cids_after_2 = {1030, 1031}
    if set(seen_calls[1]["cids"][:2]) != accepted_cids_after_2:
        accepted_cids_after_2 = set(seen_calls[1]["cids"][:2])
    assert set(seen_calls[2]["cids"]).isdisjoint(accepted_cids_after_2)
    assert len(seen_calls[2]["exclude_quotes"]) >= 2


def test_hard_mlx_uses_max_batch_2_scales_attempts_and_adds_empty_retry_hint(monkeypatch, tmp_path):
    import app.practice as practice_mod
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    passages_list = [
        passage(
            chunk_id=1100 + i,
            text=(
                f"Quy tắc {i}A: Nếu tải vượt mức M_{i} thì chuyển sang hàng đợi dự phòng Q_{i}. "
                f"Quy tắc {i}B: Chỉ khi Q_{i} chưa đầy dưới 80% thì gói tin mới được ghi nhận hợp lệ."
            ),
        )
        for i in range(6)
    ]
    service = PracticeService(FakeSearcher(passages_list), FakeDocumentLookup(), db_path=tmp_path / "prac_hard_mlx.db")
    gen_logs = []

    class FakeHardBatchClient:
        last_done_reason = "stop"
        last_diagnostics = {}

        def chat_complete(self, messages, system_prompt="", temperature=0.0, max_tokens=1000, **kwargs):
            prompt = messages[0]["content"]
            if "Dữ liệu cần kiểm định:\n" in prompt:
                items = json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
                return json.dumps({
                    "checks": [{
                        "index": it["index"],
                        "input_audit": "Đủ dữ kiện đầu vào.",
                        "source_basis": it["source"],
                        "independent_solution": "Bước 1 kiểm tra M; Bước 2 kiểm tra Q < 80%.",
                        "choice_comparison": "Chỉ B thỏa cả hai điều kiện.",
                        "explanation_audit": "Đúng nguồn hai bước.",
                        "matching_choices": "B",
                        "supported": True,
                        "explanation_supported": True,
                        "unique_answer": True,
                        "qualifiers_preserved": True,
                        "difficulty_matches": True,
                        "verified_answer": "B",
                    } for it in items]
                }, ensure_ascii=False)
            schema = kwargs["response_schema"]
            max_items = schema["properties"]["questions"]["maxItems"]
            gen_logs.append((max_items, prompt))
            if len(gen_logs) <= 3:
                return json.dumps({"questions": []}, ensure_ascii=False)
            quotes_any = schema["properties"]["questions"]["items"]["allOf"][0]["anyOf"]
            built = []
            for idx, entry in enumerate(quotes_any[:max_items]):
                q_str = entry["properties"]["evidence_quote"]["enum"][0]
                built.append({
                    "source_chunk_id": idx + 1,
                    "evidence_quote": q_str,
                    "question": f"Theo đoạn trích {len(gen_logs)}_{idx}, khi vượt M và Q ở mức 60% thì kết quả nào đúng?",
                    "explanation": "Bước 1 xét vượt M để chuyển sang Q; Bước 2 Q ở 60% < 80% nên được ghi nhận.",
                    "choices": {
                        "A": f"Loại bỏ gói tin ngay {len(gen_logs)}_{idx}",
                        "B": f"Chuyển sang Q và ghi nhận hợp lệ {len(gen_logs)}_{idx}",
                        "C": f"Tăng ngưỡng Q lên 95% {len(gen_logs)}_{idx}",
                        "D": f"Ghi trực tiếp không kiểm tra Q {len(gen_logs)}_{idx}",
                    },
                    "answer": "B",
                })
            return json.dumps({"questions": built}, ensure_ascii=False)

    monkeypatch.setattr(practice_mod, "TrainedModelClient", lambda *a, **kw: FakeHardBatchClient())
    res = service.generate_from_passages(
        passages_list,
        count=6,
        difficulty="hard",
        model="qwen3.5-9b-4bit",
        allow_retry=True,
        enforce_difficulty=True,
    )
    assert res["count"] == 6
    assert all(max_items <= 2 for max_items, _p in gen_logs)
    assert "tương tác" in gen_logs[1][1].casefold() or "trống" in gen_logs[1][1].casefold()


def test_select_hard_passages_keeps_unused_ahead_of_used_chunk_ids():
    from app.practice import _select_hard_passages

    used_high = passage(
        chunk_id=1120,
        text=(
            "Nếu điều kiện 1 đúng thì chuyển bước 2; nếu không thì dừng ngay. "
            "Khi thuật toán lặp while kiểm tra nullptr ở mỗi nút, phải cập nhật con trỏ trước khi xóa."
        ),
    )
    unused_lower = passage(
        chunk_id=1121,
        text="Nếu bộ đệm vượt giới hạn thì ghi nhận tràn hàng đợi trên đoạn này.",
    )
    ranked = _select_hard_passages([used_high, unused_lower], count=1, used_chunk_ids={1120})
    assert ranked[0]["chunk_id"] == 1121


def test_practice_quote_options_strips_review_banner_and_sidebar_toc_noise():
    from app.practice import _practice_quote_options

    noisy = {
        "chunk_id": 1130,
        "page_num": 24,
        "text": (
            "[BẢN CHỮ CHƯA QUA KIỂM ĐỊNH ĐẦY ĐỦ: cần kiểm tra công thức trên trang gốc]\n"
            "24 Linked Lists\n"
            "24\n"
            "Khi nút đầu bằng rỗng thì phép xóa không làm thay đổi cấu trúc liên kết và trả về ngay lập tức."
        ),
    }
    quotes = _practice_quote_options(noisy, 1400, max_quote_chars=420, min_quote_chars=90)
    assert quotes
    assert all("CHƯA QUA KIỂM ĐỊNH" not in q for q in quotes)
    assert all("24 Linked Lists" not in q for q in quotes)
    assert any(len(q) >= 90 for q in quotes)


def test_generate_text_hard_enables_multistep_proposals_with_weak_topics(monkeypatch):
    import app.model_resolver as resolver
    import app.worked_practice as worked_mod

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    seen = []

    def spy_multistep(passages_arg, count_arg, **kwargs):
        seen.append((count_arg, len(passages_arg)))
        return [{
            "source_chunk_id": passages_arg[0]["chunk_id"],
            "evidence_quote": passages_arg[0]["text"],
            "question": "Bài toán 2 bước xác suất có điều kiện với yếu điểm?",
            "explanation": "Bước 1 tính trung gian; Bước 2 suy ra kết quả cuối.",
            "choices": {"A": "0.1", "B": "0.2", "C": "0.3", "D": "0.4"},
            "answer": "B",
        }]

    monkeypatch.setattr(worked_mod, "source_hard_multistep_proposals", spy_multistep)
    service, _, _ = make_service()
    p = passage(chunk_id=1140, text=PASSAGE)
    raw = service._generate_text([p], 1, "qwen3.5-9b-4bit", difficulty="hard", weak_topics=["xác suất"])
    assert len(seen) == 1
    assert json.loads(raw)["questions"][0]["source_chunk_id"] == 1140


def test_sentence_spans_and_cloze_window_preserve_soft_pdf_line_wraps():
    wrapped = (
        "Mở đầu bị cắt cụt khá dài với nhiều chi tiết thừa trước đó để phần câu mở đầu vượt hẳn ngưỡng gộp với câu thứ hai trong cùng cửa sổ hai trăm ba mươi ký tự. "
        "Proxy cũng\ngiữ interface nhưng chủ yếu kiểm soát quyền truy cập "
        "thay vì tích lũy các\ntrách nhiệm tùy chọn.\n"
        "III. OBSERVER:\n1. Khái quát: Observer gửi thông báo cập nhật."
    )
    quote = PracticeService._extract_contiguous_cloze_window(wrapped, "trách nhiệm tùy chọn", max_chars=230)
    assert quote is not None
    assert quote.startswith("Proxy cũng\ngiữ interface")
    assert "trách nhiệm tùy chọn." in quote

    wrapped_end = (
        "Mục lục mở đầu phần hai về các mẫu khởi tạo đối tượng rất dài dòng để tổng kích thước vượt ngưỡng hai trăm ba mươi ký tự. "
        "Abstract Factory là một mẫu thiết kế khởi tạo, cung cấp interface tạo họ đối\n"
        "tượng liên quan mà không yêu cầu Client gọi tên lớp cụ\nthể."
    )
    quote2 = PracticeService._extract_contiguous_cloze_window(wrapped_end, "Abstract Factory", max_chars=230)
    assert quote2 is not None
    assert quote2.endswith("lớp cụ\nthể.")


def test_extract_supporting_evidence_normalizes_typographic_quotes_without_bridging_ellipsis():
    src = "Ông: “Có một số hiện tượng\u00a0phi\u2013khoa học không thể bỏ qua.”"
    matched = PracticeService.extract_supporting_evidence(
        src, 'Ông: "Có một số hiện tượng phi-khoa học không thể bỏ qua."'
    )
    assert matched
    assert "hiện tượng" in matched
    assert PracticeService.extract_supporting_evidence(
        src, 'Ông: "Có một số... không thể bỏ qua."'
    ) == ""


def test_medium_retry_passes_rejected_question_feedback_on_single_passage(monkeypatch, tmp_path):
    import app.practice as practice_mod
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    p = passage(
        chunk_id=1150,
        text="An ordered linked list arranges elements in a specific order, such as ascending or descending, based on element values.",
    )
    service = PracticeService(FakeSearcher([p]), FakeDocumentLookup(), db_path=tmp_path / "prac_med_retry.db")
    prompts = []

    class FakeMediumRetryMLX:
        last_done_reason = "stop"

        def chat_complete(self, messages, **kwargs):
            self.last_done_reason = "stop"
            user_prompt = messages[-1]["content"]
            prompts.append(user_prompt)
            if "Dữ liệu cần kiểm định:\n" in user_prompt:
                is_first = "5, 2, 8" in user_prompt
                return json.dumps({
                    "checks": [{
                        "index": 1,
                        "input_audit": "Checks ordering." if not is_first else "Missing ascending vs descending.",
                        "source_basis": "An ordered linked list arranges elements in a specific order, such as ascending or descending",
                        "independent_solution": "Needs explicit direction." if is_first else "Arranged based on element values.",
                        "supported": not is_first,
                        "qualifiers_preserved": True,
                        "choice_comparison": "Only A matches." if not is_first else "Undetermined.",
                        "matching_choices": "" if is_first else "A",
                        "unique_answer": not is_first,
                        "verified_answer": "" if is_first else "A",
                        "explanation_audit": "Valid." if not is_first else "Assumed ascending.",
                        "explanation_supported": not is_first,
                        "difficulty_matches": not is_first,
                    }]
                })
            gen_calls = [pr for pr in prompts if "Dữ liệu cần kiểm định:\n" not in pr]
            if len(gen_calls) == 1:
                return json.dumps({
                    "questions": [{
                        "source_chunk_id": 1,
                        "evidence_quote": "An ordered linked list arranges elements in a specific order, such as ascending or descending",
                        "question": "Theo đoạn trích, với dãy 5, 2, 8 thì phần tử đầu của danh sách có thứ tự là gì?",
                        "explanation": "Xếp tăng dần được 2.",
                        "choices": {"A": "2", "B": "5", "C": "8", "D": "1"},
                        "answer": "A",
                    }]
                }, ensure_ascii=False)
            assert "dãy 5, 2, 8" in user_prompt
            return json.dumps({
                "questions": [{
                    "source_chunk_id": 1,
                    "evidence_quote": "An ordered linked list arranges elements in a specific order, such as ascending or descending, based on element values.",
                    "question": "Theo đoạn trích, nếu dùng danh sách liên kết sắp xếp tăng dần theo giá trị, tiêu chí quyết định vị trí phần tử là gì?",
                    "explanation": "Nguồn nêu thứ tự dựa trên giá trị phần tử.",
                    "choices": {"A": "Giá trị của phần tử", "B": "Ngẫu nhiên", "C": "Kích thước con trỏ", "D": "Địa chỉ bộ nhớ"},
                    "answer": "A",
                }]
            }, ensure_ascii=False)

    monkeypatch.setattr(practice_mod, "TrainedModelClient", lambda *a, **kw: FakeMediumRetryMLX())
    res = service.generate_from_passages(
        [p], count=1, model="qwen3.5-9b-4bit", difficulty="medium", allow_retry=True, enforce_difficulty=True
    )
    assert res["count"] == 1
    assert "tiêu chí quyết định" in res["questions"][0]["question"]


def test_hard_count1_mlx_caps_consecutive_zero_verified_rounds(monkeypatch, tmp_path):
    import app.practice as practice_mod
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    passages_list = [
        passage(chunk_id=1160 + idx, text=f"Nếu điều kiện {idx} thỏa mãn thì áp dụng quy tắc phụ thuộc hai bước {idx} cho bài toán phức tạp.")
        for idx in range(5)
    ]
    service = PracticeService(FakeSearcher(passages_list), FakeDocumentLookup(), db_path=tmp_path / "prac_hard_cap.db")
    gen_count = 0

    class FakeEmptyHardMLX:
        last_done_reason = "stop"

        def chat_complete(self, messages, **kwargs):
            nonlocal gen_count
            self.last_done_reason = "stop"
            gen_count += 1
            return json.dumps({"questions": []})

    monkeypatch.setattr(practice_mod, "TrainedModelClient", lambda *a, **kw: FakeEmptyHardMLX())
    with pytest.raises(PracticeGenerationError):
        service.generate_from_passages(
            passages_list, count=1, model="qwen3.5-9b-4bit", difficulty="hard", allow_retry=True, enforce_difficulty=True
        )
    assert gen_count == 3


def test_generate_from_passages_completes_clipped_sentence_quote_for_review(monkeypatch, tmp_path):
    import app.practice as practice_mod
    import app.model_resolver as resolver

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    full_sentence = (
        "The elements in an ordered linked list are arranged in a specific "
        "order, such as ascending or descending, based on the values of the elements."
    )
    p = passage(chunk_id=1170, text=f"Ordered Linked List\n{full_sentence}")
    service = PracticeService(FakeSearcher([p]), FakeDocumentLookup(), db_path=tmp_path / "prac_clip_quote.db")

    class FakeClipQuoteMLX:
        last_done_reason = "stop"

        def chat_complete(self, messages, **kwargs):
            self.last_done_reason = "stop"
            user_prompt = messages[-1]["content"]
            if "Dữ liệu cần kiểm định:\n" in user_prompt:
                return json.dumps({
                    "checks": [{
                        "index": 1,
                        "input_audit": "Yêu cầu tìm phần tử đầu khi sắp xếp giảm dần.",
                        "source_basis": full_sentence,
                        "independent_solution": "Dãy [10, 3, 7] giảm dần thành [10, 7, 3]; phần tử đầu là 10.",
                        "supported": True,
                        "qualifiers_preserved": True,
                        "choice_comparison": "Chỉ A (10) đúng; B, C, D sai.",
                        "matching_choices": "A",
                        "unique_answer": True,
                        "verified_answer": "A",
                        "explanation_audit": "Giải thích đúng theo quy tắc giảm dần.",
                        "explanation_supported": True,
                        "difficulty_matches": True,
                    }]
                }, ensure_ascii=False)
            return json.dumps({
                "questions": [{
                    "source_chunk_id": 1,
                    "evidence_quote": "The elements in an ordered linked list are arranged in a specific",
                    "question": "Dữ kiện giả định: Danh sách liên kết có thứ tự chứa [10, 3, 7]. Nếu sắp xếp giảm dần, phần tử đầu là bao nhiêu?",
                    "explanation": "Khi sắp xếp giảm dần theo giá trị, phần tử lớn nhất là 10 đứng đầu.",
                    "choices": {"A": "10", "B": "3", "C": "7", "D": "0"},
                    "answer": "A",
                }]
            }, ensure_ascii=False)

    monkeypatch.setattr(practice_mod, "TrainedModelClient", lambda *a, **kw: FakeClipQuoteMLX())
    res = service.generate_from_passages(
        [p], count=1, model="qwen3.5-9b-4bit", difficulty="medium", allow_retry=False, enforce_difficulty=True
    )
    assert res["count"] == 1
    feedback = service.answer(res["practice_id"], res["questions"][0]["id"], "A")
    assert feedback["source"]["snippet"] == full_sentence


def test_prefilter_rejects_stem_answer_leak_pure_recall_and_bounds_hard_mlx_deadline(monkeypatch, tmp_path):
    import app.model_resolver as resolver
    import app.practice as practice_mod
    from app.practice import _deterministic_prefilter_reject_reason

    src_text = "Trong bài toán hai bước, kết quả âm làm phép toán không hợp lệ; mutex khóa vùng găng cho một luồng."
    stem_leak_q = {
        "question": "Theo đoạn trích, tính toán nào là sai do kết quả âm?",
        "choices": {"A": "Tính toán có kết quả âm", "B": "Tính toán tổng hai số dương", "C": "Phép nhân ma trận vuông", "D": "Đổi dấu phương trình"},
        "answer": "A",
        "explanation": "Đáp án đúng là A vì kết quả âm.",
    }
    assert _deterministic_prefilter_reject_reason(stem_leak_q, src_text, difficulty="medium") == "prefilter_stem_answer_leak"  # nosec B101

    clause_leak_q = {
        "question": "Khi hai luồng cùng truy cập biến chia sẻ, cơ chế nào dùng để đảm bảo chỉ một luồng được phép truy cập tại một thời điểm?",
        "choices": {"A": "Tăng số luồng song song", "B": "Xóa bộ nhớ đệm", "C": "Đảm bảo chỉ một luồng truy cập tại một thời điểm", "D": "Chia đôi danh sách"},
        "answer": "C",
        "explanation": "Đáp án đúng là C.",
    }
    assert _deterministic_prefilter_reject_reason(clause_leak_q, src_text, difficulty="medium") == "prefilter_stem_answer_leak"  # nosec B101

    recall_q = {
        "question": "Theo đoạn trích, khái niệm chuẩn hóa dữ liệu là gì?",
        "choices": {"A": "Chuẩn hóa", "B": "Nén dữ liệu", "C": "Sắp xếp", "D": "Tìm kiếm"},
        "answer": "A",
        "explanation": "Chuẩn hóa dữ liệu là bước xử lý chính.",
    }
    assert _deterministic_prefilter_reject_reason(recall_q, src_text, difficulty="medium") == "prefilter_recall_stem_not_medium_or_hard"  # nosec B101
    assert _deterministic_prefilter_reject_reason(recall_q, src_text, difficulty="easy") is None  # nosec B101

    vague_q = {
        "question": "Theo đoạn trích, khi sử dụng phải lưu ý điều kiện gì?",
        "choices": {"A": "Kiểm tra con trỏ null", "B": "Giải phóng mảng", "C": "Gọi hàm đệ quy", "D": "Ép kiểu"},
        "answer": "A",
        "explanation": "Đáp án đúng là A.",
    }
    assert _deterministic_prefilter_reject_reason(vague_q, src_text, difficulty="medium") == "prefilter_recall_stem_not_medium_or_hard"  # nosec B101
    cond_recall_q = {
        "question": "Theo đoạn trích, điều kiện cần để bệnh nhân bắt đầu giảm lo lắng là gì?",
        "choices": {"A": "Đối diện an toàn", "B": "Tránh né hoàn toàn", "C": "Uống thuốc ngủ", "D": "Ngưng điều trị"},
        "answer": "A",
        "explanation": "Đáp án đúng là A.",
    }
    assert _deterministic_prefilter_reject_reason(cond_recall_q, src_text, difficulty="medium") == "prefilter_recall_stem_not_medium_or_hard"  # nosec B101

    monkeypatch.setattr(resolver, "is_mlx_model", lambda model: True)
    p = passage(chunk_id=1201, text="Nếu điều kiện kép thỏa mãn thì áp dụng bước kiểm tra thứ nhất rồi mới tính bước hai.")
    service = PracticeService(FakeSearcher([p]), FakeDocumentLookup(), db_path=tmp_path / "prac_deadline_cap.db")
    clock = iter([0.0, 225.0])
    monkeypatch.setattr(practice_mod.time, "monotonic", lambda: next(clock, 225.0))
    with pytest.raises(PracticeGenerationError, match=r"\(220s\)"):
        service.generate_from_passages(
            [p], count=3, model="qwen3.5-9b-4bit", difficulty="hard", allow_retry=True, enforce_difficulty=True
        )
