"""Unit and contract tests for practice review correctness, audit-before-verdict schema, and prompt parity."""
import json
import pytest

from app.practice import (
    PracticeService,
    build_practice_review_item,
    build_practice_review_prompt,
    practice_review_schema,
)
from app.multitask_training_data import (
    SourceContext,
    build_practice_verify_invalid,
    build_practice_verify_supported,
)


def sample_context():
    text = "Tìm kiếm nhị phân chia đôi không gian tìm kiếm tại mỗi bước."
    return SourceContext(
        domain="programming",
        doc_id=1,
        filename="test.pdf",
        file_hash="0" * 64,
        page_num=1,
        chunk_id=10,
        span_start=0,
        span_end=len(text),
        passage=text,
    )


def sample_question():
    return {
        "id": "q1",
        "question": "Tìm kiếm nhị phân chia đôi không gian tìm kiếm ở bước nào?",
        "choices": [
            {"label": "A", "text": "Mỗi bước"},
            {"label": "B", "text": "Chỉ bước đầu"},
            {"label": "C", "text": "Chỉ bước cuối"},
            {"label": "D", "text": "Không bao giờ"},
        ],
        "answer": "A",
        "explanation": "Nguồn nêu trực tiếp rằng thuật toán chia đôi không gian tìm kiếm tại mỗi bước.",
        "source": {"chunk_id": 10, "snippet": "chia đôi không gian tìm kiếm tại mỗi bước"},
    }


def sample_valid_check():
    return {
        "index": 1,
        "input_audit": "Đề bài nêu rõ câu hỏi và không thiếu biến ban đầu.",
        "independent_solution": "Theo nguồn, chia đôi tại mỗi bước nên đáp án là Mỗi bước.",
        "choice_comparison": "Kết quả Mỗi bước khớp duy nhất với A; B, C, D sai.",
        "explanation_audit": "Giải thích chuẩn xác theo nguồn, không có khẳng định ngoài nguồn.",
        "supported": True,
        "explanation_supported": True,
        "unique_answer": True,
        "qualifiers_preserved": True,
        "difficulty_matches": True,
        "verified_answer": "A",
    }


def test_practice_review_schema_structure_and_bounds():
    """Verify schema properties, required audit fields, and bounded string lengths."""
    schema = practice_review_schema(count=1, difficulty="medium")
    check_props = schema["properties"]["checks"]["items"]["properties"]
    required = schema["properties"]["checks"]["items"]["required"]

    for audit_field in ("input_audit", "independent_solution", "choice_comparison", "explanation_audit"):
        assert audit_field in check_props, f"Missing {audit_field} in check properties"
        assert audit_field in required, f"Missing {audit_field} in required check fields"
        assert check_props[audit_field]["type"] == "string"
        assert check_props[audit_field]["maxLength"] <= 1000

    for bool_field in ("supported", "explanation_supported", "unique_answer", "qualifiers_preserved", "difficulty_matches"):
        assert bool_field in check_props
        assert bool_field in required
        assert check_props[bool_field]["type"] == "boolean"

    assert "verified_answer" in check_props
    assert "verified_answer" in required
    assert check_props["verified_answer"]["type"] == "string"


def test_practice_review_schema_difficulty_optional():
    """When difficulty is None, difficulty_matches is not required."""
    schema = practice_review_schema(count=1, difficulty=None)
    required = schema["properties"]["checks"]["items"]["required"]
    assert "difficulty_matches" not in required


def test_pure_prompt_producer_parity_between_runtime_and_multitask():
    """Ensure runtime and training preparation use identical prompt producer and strings."""
    q = sample_question()
    passage = "Thuật toán chia đôi không gian tìm kiếm tại mỗi bước."
    q_dict = {
        "question": q["question"],
        "choices": {c["label"]: c["text"] for c in q["choices"]},
        "answer": q["answer"],
        "explanation": q["explanation"],
        "evidence_quote": q["source"]["snippet"],
        "verifier_check": sample_valid_check(),
        "difficulty": "medium",
    }

    item = build_practice_review_item(q_dict, passage, index=1)
    prompt_from_producer = build_practice_review_prompt([item], difficulty="medium")

    # In multitask training data:
    ctx = sample_context()
    ctx.passage = passage
    messages, _ = build_practice_verify_supported(ctx, "Kiểm định", q_dict, difficulty="medium")
    multitask_prompt = messages[1]["content"]

    assert prompt_from_producer == multitask_prompt
    assert "input_audit:" in prompt_from_producer
    assert "independent_solution:" in prompt_from_producer
    assert "choice_comparison:" in prompt_from_producer
    assert "explanation_audit:" in prompt_from_producer
    assert "Độ khó bắt buộc: medium" in prompt_from_producer


def test_verify_questions_fails_closed_on_missing_or_blank_audit_fields(monkeypatch):
    """If model omits or blanks out any audit field, _verify_questions must reject the question."""
    service = PracticeService(None, None)
    q = sample_question()
    passage_dict = {10: {"text": "chia đôi không gian", "safe_text": "chia đôi không gian"}}

    for missing_field in ("input_audit", "independent_solution", "choice_comparison", "explanation_audit"):
        bad_check = sample_valid_check()
        del bad_check[missing_field]
        monkeypatch.setattr(
            service,
            "_chat_complete",
            lambda *args, **kwargs: json.dumps({"checks": [bad_check]}),
        )
        verified = service._verify_questions([q], passage_dict, "test-model", difficulty="medium")
        assert len(verified) == 0, f"Expected rejection when {missing_field} is missing"

    for blank_field in ("input_audit", "independent_solution", "choice_comparison", "explanation_audit"):
        bad_check = sample_valid_check()
        bad_check[blank_field] = "   "
        monkeypatch.setattr(
            service,
            "_chat_complete",
            lambda *args, **kwargs: json.dumps({"checks": [bad_check]}),
        )
        verified = service._verify_questions([q], passage_dict, "test-model", difficulty="medium")
        assert len(verified) == 0, f"Expected rejection when {blank_field} is whitespace"


def test_verify_questions_fails_closed_on_failed_boolean_gates(monkeypatch):
    """If any boolean gate is False or verified_answer differs, question is rejected."""
    service = PracticeService(None, None)
    q = sample_question()
    passage_dict = {10: {"text": "chia đôi không gian", "safe_text": "chia đôi không gian"}}

    for bool_gate in ("supported", "explanation_supported", "unique_answer", "qualifiers_preserved", "difficulty_matches"):
        bad_check = sample_valid_check()
        bad_check[bool_gate] = False
        monkeypatch.setattr(
            service,
            "_chat_complete",
            lambda *args, **kwargs: json.dumps({"checks": [bad_check]}),
        )
        verified = service._verify_questions([q], passage_dict, "test-model", difficulty="medium")
        assert len(verified) == 0, f"Expected rejection when {bool_gate} is False"

    # Mismatched answer
    bad_check = sample_valid_check()
    bad_check["verified_answer"] = "B"
    monkeypatch.setattr(
        service,
        "_chat_complete",
        lambda *args, **kwargs: json.dumps({"checks": [bad_check]}),
    )
    verified = service._verify_questions([q], passage_dict, "test-model", difficulty="medium")
    assert len(verified) == 0, "Expected rejection when verified_answer != question answer"


def test_verify_questions_accepts_valid_audit_check(monkeypatch):
    """When all audit fields and boolean gates pass, question is accepted."""
    service = PracticeService(None, None)
    q = sample_question()
    passage_dict = {10: {"text": "chia đôi không gian", "safe_text": "chia đôi không gian"}}
    good_check = sample_valid_check()

    monkeypatch.setattr(
        service,
        "_chat_complete",
        lambda *args, **kwargs: json.dumps({"checks": [good_check]}),
    )
    verified = service._verify_questions([q], passage_dict, "test-model", difficulty="medium")
    assert len(verified) == 1
    assert verified[0]["id"] == "q1"


def test_multitask_training_data_validates_malformed_audit_fields():
    """Multitask training data builder rejects non-string or whitespace audit fields if present."""
    ctx = sample_context()
    q_dict = {
        "question": "Câu hỏi?",
        "choices": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "answer": "A",
        "explanation": "Giải thích",
        "evidence_quote": "chia đôi không gian",
        "verifier_check": {
            **sample_valid_check(),
            "input_audit": "   ",  # whitespace only
        },
    }
    with pytest.raises(ValueError, match="input_audit.*must be a non-empty string"):
        build_practice_verify_supported(ctx, "Kiểm định", q_dict)

    q_dict["verifier_check"]["input_audit"] = 12345
    with pytest.raises(ValueError, match="input_audit.*must be a non-empty string"):
        build_practice_verify_supported(ctx, "Kiểm định", q_dict)


def test_multitask_training_data_rejects_missing_verifier_check():
    """Examples without authored verifier_check must raise ValueError."""
    ctx = sample_context()
    q_dict = {
        "question": "Câu hỏi?",
        "choices": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "answer": "A",
        "explanation": "Giải thích",
        "evidence_quote": "chia đôi không gian",
    }
    with pytest.raises(ValueError, match="requires authored verifier_check"):
        build_practice_verify_supported(ctx, "Kiểm định", q_dict)

    with pytest.raises(ValueError, match="requires authored verifier_check"):
        build_practice_verify_invalid(ctx, "Kiểm định", q_dict)


def test_reject_auditless_response(monkeypatch):
    """When checks response omits all mandatory audit fields, question must be rejected."""
    service = PracticeService(None, None)
    q = sample_question()
    passage_dict = {10: {"text": "chia đôi không gian", "safe_text": "chia đôi không gian"}}
    auditless_check = {
        "index": 1,
        "supported": True,
        "explanation_supported": True,
        "unique_answer": True,
        "qualifiers_preserved": True,
        "difficulty_matches": True,
        "verified_answer": "A",
    }
    monkeypatch.setattr(
        service,
        "_chat_complete",
        lambda *args, **kwargs: json.dumps({"checks": [auditless_check]}),
    )
    verified = service._verify_questions([q], passage_dict, "test-model", difficulty="medium")
    assert len(verified) == 0


@pytest.mark.parametrize("field", ["input_audit", "independent_solution", "choice_comparison", "explanation_audit"])
def test_reject_overlong_audit(monkeypatch, field):
    """When an audit field exceeds 1000 characters, question must be rejected."""
    service = PracticeService(None, None)
    q = sample_question()
    passage_dict = {10: {"text": "chia đôi không gian", "safe_text": "chia đôi không gian"}}
    overlong_check = sample_valid_check()
    overlong_check[field] = "x" * 1001
    monkeypatch.setattr(
        service,
        "_chat_complete",
        lambda *args, **kwargs: json.dumps({"checks": [overlong_check]}),
    )
    verified = service._verify_questions([q], passage_dict, "test-model", difficulty="medium")
    assert len(verified) == 0


@pytest.mark.parametrize("builder", [build_practice_verify_supported, build_practice_verify_invalid])
@pytest.mark.parametrize("field", ["input_audit", "independent_solution", "choice_comparison", "explanation_audit"])
def test_preparation_rejects_labels_missing_required_audits(builder, field):
    """Training builders must reject verifier_check missing any mandatory audit field."""
    ctx = sample_context()
    base_check = sample_valid_check() if builder is build_practice_verify_supported else {
        **sample_valid_check(),
        "supported": False,
        "verified_answer": "",
    }
    del base_check[field]
    q_dict = {
        "question": "Câu hỏi?",
        "choices": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "answer": "A",
        "explanation": "Giải thích",
        "evidence_quote": "chia đôi không gian",
        "verifier_check": base_check,
    }
    with pytest.raises(ValueError, match=f"verifier_check missing required key {field}"):
        builder(ctx, "Kiểm định", q_dict)


@pytest.mark.parametrize("builder", [build_practice_verify_supported, build_practice_verify_invalid])
@pytest.mark.parametrize("field", ["input_audit", "independent_solution", "choice_comparison", "explanation_audit"])
def test_preparation_rejects_overlong_audit_fields(builder, field):
    """Training builders must reject verifier_check with audit fields exceeding 1000 characters."""
    ctx = sample_context()
    base_check = sample_valid_check() if builder is build_practice_verify_supported else {
        **sample_valid_check(),
        "supported": False,
        "verified_answer": "",
    }
    base_check[field] = "x" * 1001
    q_dict = {
        "question": "Câu hỏi?",
        "choices": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "answer": "A",
        "explanation": "Giải thích",
        "evidence_quote": "chia đôi không gian",
        "verifier_check": base_check,
    }
    with pytest.raises(ValueError, match="exceeds 1000 characters"):
        builder(ctx, "Kiểm định", q_dict)
