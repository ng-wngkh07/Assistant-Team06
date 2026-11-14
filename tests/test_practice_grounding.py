"""Regression tests for grounding v3, coherent evidence binding, and review contracts."""

import json
import pytest

from app.practice import (
    PracticeService,
    _demands_unbound_certainty,
    _requires_explicit_causal_evidence,
    _practice_quote_options,
    build_practice_review_item,
    build_practice_review_prompt,
    practice_review_schema,
)
from app.practice_adapter import (
    SYSTEM_PROMPT,
    build_compact_review_prompt,
    load_practice_adapter,
    request_matches_scope,
)


class MockOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.last_done_reason = "stop"

    def chat_complete(self, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def test_worked_generation_requires_concrete_calculation_in_explanation():
    text = ('Worked Examples\n1. f(x) = 2^2016\n'
            'Solution: the derivative is 0 since f is constant.\n'
            '2. f(x) = (1/16)x^4\nSolution: the derivative is (1/4)x^3.\n'
            '3. f(x) = x^2(5-2x)\nSolution: 2x(5-2x)-2x^2 = -6x^2+10x.')
    service = PracticeService(None, None)
    prompts = []
    service._chat_complete = lambda prompt, *args, **kwargs: (prompts.append(prompt), '{"questions": []}')[1]
    service._generate_text([dict(chunk_id=77, page_num=1, safe_text=text, text=text)], 3, 'test')
    assert 'explanation phải nêu phép biến đổi cụ thể và biểu thức kết quả cuối' in prompts[0]


@pytest.mark.parametrize('source,question,blocked', [
    ('X relates to Y, and this can be hard to accept.', 'Why is X hard to accept?', True),
    ('Owning the gold is terrifying.', 'Tại sao việc sở hữu vàng đáng sợ?', True),
    ('Some gold is hidden because culture has no place for it.', 'Vì sao một phần vàng bị giấu?', False),
    ('The derivative is zero since the function is constant.', 'Why is the derivative zero?', False),
    ('Nhiệt độ cao gây ra sự thay đổi.', 'Nguyên nhân của sự thay đổi là gì?', False),
    ('X relates to Y, and this can be hard to accept.', 'X liên quan đến điều gì?', False),
])
def test_causal_admission_requires_observable_source_relation(source, question, blocked):
    assert _requires_explicit_causal_evidence(source, question) is blocked


def test_causal_admission_does_not_borrow_relation_from_surrounding_context():
    quote = 'X relates to Y, and this can be hard to accept.'
    source = quote + ' Other people refuse Z because it is expensive.'
    question = dict(question='Why is X hard to accept?', evidence_quote=quote,
                    choices=dict(zip('ABCD', ['Y', 'Z', 'Cost', 'Unknown'])), answer='A',
                    explanation='Because X relates to Y.', source={'chunk_id': 1})
    service = PracticeService(None, None)
    service._chat_complete = lambda *args, **kwargs: pytest.fail('Unverified causal relation reached review')
    assert service._verify_questions([question], {1: {'safe_text': source}}, 'test') == []


@pytest.mark.parametrize('source,question,blocked', [
    ('Some traits are hidden by culture.', 'Điều gì sẽ xảy ra với phẩm chất của cá nhân đó?', True),
    ('Some traits are hidden by culture.', 'Phẩm chất đó có thể sẽ xảy ra điều gì?', False),
    ('Cooling may reduce the rate.', 'What will happen to the rate?', True),
    ('Cooling may reduce the rate.', 'What might happen to the rate?', False),
    ('Nếu cửa mở thì máy luôn dừng.', 'Khi cửa mở, điều gì sẽ xảy ra?', False),
    ('Một số học viên có thể tiến bộ.', 'Học viên này chắc chắn đạt kết quả nào?', True),
    ('Some students improve.', 'Nguồn mô tả nhóm học viên nào?', False),
])
def test_qualified_source_does_not_license_unqualified_guarantees(source, question, blocked):
    assert _demands_unbound_certainty(source, question) is blocked


def test_certainty_guard_rejects_before_model_inference():
    source = 'Some traits are hidden by culture.'
    question = dict(question='Điều gì sẽ xảy ra với phẩm chất của cá nhân đó?',
                    evidence_quote=source, choices=dict(zip('ABCD', ['Hidden', 'Gone', 'Revealed', 'Absent'])),
                    answer='A', explanation='It is always hidden.', source={'chunk_id': 1})
    service = PracticeService(None, None)
    service._chat_complete = lambda *args, **kwargs: pytest.fail('Unqualified guarantee reached model')
    assert service._verify_questions([question], {1: {'safe_text': source}}, 'test') == []


def test_quote_options_selects_coherent_spans_and_excludes_exercise_headers():
    passage = {
        "text": (
            "Bài tập vận dụng\n"
            "Trong điều kiện A, hiện tượng X có thể xảy ra. "
            "Tài liệu không khẳng định hiện tượng X luôn luôn xảy ra trong mọi hoàn cảnh.\n"
            "Worked Examples\n"
            "Hiệu suất nhiệt động lực học của động cơ là tỷ số giữa công sinh ra và nhiệt lượng nhận vào."
        ),
        "safe_text": (
            "Bài tập vận dụng\n"
            "Trong điều kiện A, hiện tượng X có thể xảy ra. "
            "Tài liệu không khẳng định hiện tượng X luôn luôn xảy ra trong mọi hoàn cảnh.\n"
            "Worked Examples\n"
            "Hiệu suất nhiệt động lực học của động cơ là tỷ số giữa công sinh ra và nhiệt lượng nhận vào."
        ),
    }
    quotes = _practice_quote_options(passage, max_chars=1400)
    # Standalone exercise headers must be excluded
    assert "Bài tập vận dụng" not in quotes
    assert "Worked Examples" not in quotes
    # Coherent self-contained sentences and combinations must be present
    assert any("Trong điều kiện A, hiện tượng X có thể xảy ra" in q for q in quotes)
    assert any("Tài liệu không khẳng định hiện tượng X luôn luôn xảy ra" in q for q in quotes)
    assert any("Hiệu suất nhiệt động lực học" in q for q in quotes)
    # Every quote must be bounded and in the original text
    for q in quotes:
        assert 20 <= len(q) <= 240
        assert q.casefold() in passage["text"].casefold()


def test_supporting_evidence_extraction_isolates_citation_and_fails_closed_when_unrelated():
    passage_text = (
        "Đạo hàm của hàm hợp:\n"
        "(f(g(x)))' = f'(g(x)) * g'(x) với mọi hàm f và g khả vi.\n\n"
        "Quy tắc lũy thừa tổng quát:\n"
        "Đạo hàm của x^r là r * x^(r-1) với số thực r bất kỳ.\n\n"
        "Bài tập 1:\n"
        "1. Cho f(x) = 2^2016. Lời giải: đạo hàm là 0 vì f là hàm hằng."
    )
    # When quoting the exercise in Section 3, supporting evidence must NOT include the general power rule in Section 2
    exercise_quote = "1. Cho f(x) = 2^2016. Lời giải: đạo hàm là 0 vì f là hàm hằng."
    supporting = PracticeService.extract_supporting_evidence(passage_text, exercise_quote)
    assert "Quy tắc lũy thừa tổng quát" not in supporting
    assert "(f(g(x)))'" not in supporting
    assert "f là hàm hằng" in supporting

    # When quote is not in passage, extract_supporting_evidence returns empty string
    missing_quote = "Quy tắc hoàn toàn không có trong tài liệu này"
    assert PracticeService.extract_supporting_evidence(passage_text, missing_quote) == ""


def test_verify_questions_fails_closed_on_unrelated_citation_even_if_document_has_answer():
    passage_text = (
        "Quy tắc lũy thừa tổng quát:\n"
        "Đạo hàm của x^r là r * x^(r-1) với số thực r bất kỳ.\n\n"
        "Bài tập bổ sung:\n"
        "1. Cho f(x) = 2^2016. Lời giải: đạo hàm là 0 vì f là hàm hằng."
    )
    # A question asking about the power rule but citing the unrelated exercise snippet
    unrelated_question = {
        "id": "q1",
        "question": "Theo quy tắc lũy thừa tổng quát, đạo hàm của x^r là gì?",
        "choices": {
            "A": "r * x^(r-1)",
            "B": "x^(r-1)",
            "C": "r * x^r",
            "D": "0",
        },
        "answer": "A",
        "explanation": "Dùng g(u)=u^r và h(x)=x để suy ra quy tắc lũy thừa.",
        "source": {"chunk_id": 158, "snippet": "1. Cho f(x) = 2^2016. Lời giải: đạo hàm là 0 vì f là hàm hằng."},
        "evidence_quote": "1. Cho f(x) = 2^2016. Lời giải: đạo hàm là 0 vì f là hàm hằng.",
    }
    service = PracticeService(None, None)
    # Verifier sees only the supporting evidence (the exercise snippet), which lacks the power rule
    # It must fail closed (supported=False)
    unsupported_check = {
        "index": 1,
        "input_audit": "Question hỏi quy tắc lũy thừa của x^r.",
        "independent_solution": "Đoạn trích nguồn chỉ là bài tập f(x)=2^2016, không chứa quy tắc lũy thừa tổng quát x^r.",
        "choice_comparison": "Không thể chứng minh đáp án A từ đoạn trích nguồn được cung cấp.",
        "explanation_audit": "Explanation lập luận vòng quanh và trích dẫn không liên quan đến quy tắc cần chứng minh.",
        "supported": False,
        "explanation_supported": False,
        "unique_answer": False,
        "qualifiers_preserved": True,
        "verified_answer": "",
    }
    prompts = []
    def mock_chat(prompt, *args, **kwargs):
        prompts.append(prompt)
        return json.dumps({"checks": [unsupported_check]})

    service._chat_complete = mock_chat
    passage_dict = {158: {"chunk_id": 158, "text": passage_text, "safe_text": passage_text}}
    verified = service._verify_questions([unrelated_question], passage_dict, "test-model")
    assert len(verified) == 0
    # Confirm prompt only received the supporting evidence for the exercise, not the full 3-paragraph passage
    assert "Quy tắc lũy thừa tổng quát" not in prompts[0]
    assert "1. Cho f(x) = 2^2016" in prompts[0]


def test_missing_temperature_honest_refusal_preserves_qualifiers():
    carnot_passage = "Hiệu suất của động cơ nhiệt chạy theo chu trình Carnot:\nη = 1−T₂/T₁"
    carnot_question = {
        "question": "Động cơ Carnot có T₁=600 K. Dùng công thức nguồn, hiệu suất số cụ thể là bao nhiêu?",
        "choices": {"A": "40%", "B": "60%", "C": "100%", "D": "20%"},
        "answer": "A",
        "explanation": "Lấy T₂=360 K nên η=40%.",
        "source": {"chunk_id": 139},
        "evidence_quote": "Hiệu suất của động cơ nhiệt chạy theo chu trình Carnot:\nη = 1−T₂/T₁",
    }
    # Gold contract for honest refusal on missing required input:
    # supported=False, unique_answer=False, verified_answer="", but qualifiers_preserved=True!
    honest_refusal_check = {
        "index": 1,
        "input_audit": "Question thiếu nhiệt độ nguồn lạnh T₂ bắt buộc; không tự bù số từ explanation.",
        "independent_solution": "Không đủ dữ kiện để tính hiệu suất số cụ thể do question thiếu T₂.",
        "choice_comparison": "Không thể xác định đáp án số duy nhất khi thiếu T₂.",
        "explanation_audit": "Explanation tự ý bịa thêm T₂=360 K không có trong question để tính.",
        "supported": False,
        "explanation_supported": False,
        "unique_answer": False,
        "qualifiers_preserved": True,  # Must be True on honest refusal
        "verified_answer": "",
    }
    service = PracticeService(None, None)
    service._chat_complete = lambda *args, **kwargs: json.dumps({"checks": [honest_refusal_check]})
    passage_dict = {139: {"chunk_id": 139, "text": carnot_passage, "safe_text": carnot_passage}}
    verified = service._verify_questions([carnot_question], passage_dict, "test-model")
    # Missing required inputs must be rejected from serving
    assert len(verified) == 0


def test_height_null_guard_explanation_audit_identifies_execution_order_without_hallucinating():
    height_passage = "int getHeight(AVLNodes* x) { if(x==nullptr)return 0; return x->height; }"
    height_question = {
        "question": "Theo getHeight, nếu x là nullptr thì hàm trả về giá trị nào?",
        "choices": {"A": "-1", "B": "1", "C": "Không xác định", "D": "0"},
        "answer": "D",
        "explanation": "Nhánh if(x==nullptr) trả về 0 vì mã luôn trả về x->height trước khi kiểm tra nullptr.",
        "source": {"chunk_id": 34},
        "evidence_quote": "int getHeight(AVLNodes* x) { if(x==nullptr)return 0; return x->height; }",
    }
    # Faithful check identifies the execution order reversal without claiming null checks are unnecessary
    faithful_check = {
        "index": 1,
        "input_audit": "Question hỏi giá trị trả về khi x là nullptr; mã nguồn đầy đủ dữ kiện.",
        "independent_solution": "Khi x==nullptr, nhánh if trả về 0 ngay trước lệnh return x->height.",
        "choice_comparison": "Kết quả 0 khớp duy nhất với lựa chọn D.",
        "explanation_audit": "Explanation sai vì đảo ngược thứ tự thực hiện: mã kiểm tra nullptr trước khi đọc x->height.",
        "supported": True,
        "explanation_supported": False,  # Rejected due to false explanation claim
        "unique_answer": True,
        "qualifiers_preserved": True,
        "verified_answer": "D",
    }
    service = PracticeService(None, None)
    service._chat_complete = lambda *args, **kwargs: json.dumps({"checks": [faithful_check]})
    passage_dict = {34: {"chunk_id": 34, "text": height_passage, "safe_text": height_passage}}
    verified = service._verify_questions([height_question], passage_dict, "test-model")
    # Rejected because explanation_supported is False
    assert len(verified) == 0


def test_arithmetic_intermediate_and_final_correctness_enforced():
    heat_passage = "Hiệu suất của động cơ nhiệt:\nη = A′/Q₁ = 1−Q′₂/Q₁\nQ₁ = Q′₂ + A′"
    # When intermediate arithmetic is wrong (A' = 1000 - 650 = 450 J instead of 350 J)
    bad_intermediate_check = {
        "index": 1,
        "input_audit": "Question cho Q₁=1000 J và Q′₂=650 J; đầy đủ dữ kiện để tính A′ và η.",
        "independent_solution": "A′ = 1000 - 650 = 350 J; η = 350 / 1000 = 35%.",
        "choice_comparison": "Khớp duy nhất với A (350 J và 35%).",
        "explanation_audit": "Explanation tính sai bước trung gian A′=450 J dù kết luận η=35%.",
        "supported": True,
        "explanation_supported": False,  # Must fail on arithmetic error
        "unique_answer": True,
        "qualifiers_preserved": True,
        "verified_answer": "A",
    }
    service = PracticeService(None, None)
    service._chat_complete = lambda *args, **kwargs: json.dumps({"checks": [bad_intermediate_check]})
    heat_question = {
        "question": "Dùng các công thức trong nguồn với Q₁=1000 J và Q′₂=650 J. Công A′ và hiệu suất η là bao nhiêu?",
        "choices": {"A": "350 J và 35%", "B": "650 J và 65%", "C": "350 J và 65%", "D": "1650 J và 35%"},
        "answer": "A",
        "explanation": "A′=1000−650=450 J; η=35%.",
        "source": {"chunk_id": 139},
        "evidence_quote": "η = A′/Q₁ = 1−Q′₂/Q₁",
    }
    passage_dict = {139: {"chunk_id": 139, "text": heat_passage, "safe_text": heat_passage}}
    verified = service._verify_questions([heat_question], passage_dict, "test-model")
    assert len(verified) == 0


def test_native_review_three_questions_bounded_tokens_and_compact_sources(monkeypatch):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    passage_text = "Nhiệt động lực học nghiên cứu sự truyền năng lượng và biến đổi giữa nhiệt và công."
    questions = [
        {
            "id": f"q{i}",
            "question": f"Câu hỏi {i} về nhiệt động lực học?",
            "choices": {"A": "Đúng", "B": "Sai", "C": "Không rõ", "D": "Khác"},
            "answer": "A",
            "explanation": f"Giải thích câu {i}.",
            "source": {"chunk_id": 10},
            "evidence_quote": "Nhiệt động lực học nghiên cứu sự truyền năng lượng",
        }
        for i in range(1, 4)
    ]
    service = PracticeService(None, None)
    token_limits = []
    def mock_chat(prompt, model, max_tokens, **kwargs):
        token_limits.append(max_tokens)
        for question in questions:
            assert question['question'] in prompt
            assert question['explanation'] in prompt
        assert kwargs['response_schema']['properties']['checks']['minItems'] == 3
        assert kwargs['response_schema']['properties']['checks']['maxItems'] == 3
        checks = [
            {
                "index": i,
                "source_basis": "Nhiệt động lực học nghiên cứu sự truyền năng lượng",
                "matching_choices": "A",
                "input_audit": f"Đủ dữ kiện câu {i}.",
                "independent_solution": f"Tự giải câu {i}.",
                "choice_comparison": "Khớp A.",
                "explanation_audit": f"Giải thích chuẩn câu {i}.",
                "supported": True,
                "explanation_supported": True,
                "unique_answer": True,
                "qualifiers_preserved": True,
                "verified_answer": "A",
            }
            for i in range(1, kwargs['response_schema']['properties']['checks']['maxItems'] + 1)
        ]
        return json.dumps({"checks": checks})

    service._chat_complete = mock_chat
    passage_dict = {10: {"chunk_id": 10, "text": passage_text, "safe_text": passage_text}}
    verified = service._verify_questions(questions, passage_dict, "test-model")
    assert len(verified) == 3
    assert len(token_limits) == 1
    assert 0 < token_limits[0] <= 1500


def test_adapter_gate_preserves_disabled_state_when_stage_is_qualified():
    from app.config import DATA_DIR
    model_dir = DATA_DIR / "evaluation" / "fake-model"
    # Registry has stage 'native_qualified'
    # require_active=True must return None, ensuring adapter remains inactive
    adapter = load_practice_adapter(model_dir, require_active=True)
    assert adapter is None


def test_same_paragraph_unrelated_rule_exclusion_exact_and_normalized():
    """Defect 1 Reproduction: Exact cited evidence must prove the question.
    Extracting evidence for control group must NOT include independent variable definition
    even when in the same paragraph (<=500 chars) or when whitespace/case differs.
    """
    passage = "A control group is not exposed to the experimental treatment. The independent variable is manipulated by the researcher."
    quote = "A control group is not exposed to the experimental treatment."

    # 1. Exact match
    supporting = PracticeService.extract_supporting_evidence(passage, quote)
    assert supporting == quote
    assert "independent variable" not in supporting
    assert "manipulated by the researcher" not in supporting

    # 2. Whitespace-differing quote
    quote_ws = "A   control group  is not exposed to the experimental treatment.  "
    supporting_ws = PracticeService.extract_supporting_evidence(passage, quote_ws)
    assert "independent variable" not in supporting_ws
    assert "control group" in supporting_ws

    # 3. Case-differing quote
    quote_case = "a control group is not exposed to the experimental treatment."
    supporting_case = PracticeService.extract_supporting_evidence(passage, quote_case)
    assert "independent variable" not in supporting_case
    assert "control group" in supporting_case

    # 4. HTML-escaped code operator matching (e.g. &gt; vs >)
    code_passage = "int getHeight(AVLNodes* x) { if(x==nullptr)return 0; return x-&gt;height; }"
    code_quote = "return x->height;"
    supporting_code = PracticeService.extract_supporting_evidence(code_passage, code_quote)
    assert "return x" in supporting_code
    assert "getHeight" not in supporting_code


def test_verify_questions_fails_closed_on_unbound_citation_without_fallback():
    """Defect 2 Reproduction: Real passage defines control group; snippet defines independent variable.
    _verify_questions must fail closed on unbound citation and MUST NOT call the model with
    the invented rule as trusted supporting source.
    """
    real_passage = "The source only defines the control group."
    invented_snippet = "The independent variable is manipulated by the researcher."

    unbound_question = {
        "id": "q_unbound",
        "question": "What is the independent variable?",
        "choices": {"A": "Manipulated by researcher", "B": "Not manipulated", "C": "Control", "D": "None"},
        "answer": "A",
        "explanation": "Directly from the definition of independent variable.",
        "source": {"chunk_id": 99, "snippet": invented_snippet},
        "evidence_quote": invented_snippet,
    }

    service = PracticeService(None, None)
    chat_calls = []
    service._chat_complete = lambda prompt, *args, **kwargs: (chat_calls.append(prompt), '{"checks": []}')[1]

    passage_dict = {99: {"chunk_id": 99, "text": real_passage, "safe_text": real_passage}}
    verified = service._verify_questions([unbound_question], passage_dict, "test-model")

    # Must fail closed immediately: no verified question and no chat call with the invented rule
    assert len(verified) == 0
    assert len(chat_calls) == 0


def test_surrounding_context_retains_conditions_for_restriction_checks():
    """Surrounding context must be extracted to check for omitted conditions without supplying uncited rules."""
    passage = "Under low oxygen conditions, pathway B is activated. In normal oxygen, pathway B remains completely dormant."
    quote = "pathway B is activated."

    context = PracticeService.extract_surrounding_context(passage, quote)
    assert "Under low oxygen conditions" in context
    assert "pathway B is activated" in context

    # Supporting evidence itself remains strictly the bound quote
    evidence = PracticeService.extract_supporting_evidence(passage, quote)
    assert evidence == "pathway B is activated."
    assert "normal oxygen" not in evidence


def test_submitted_structured_payload_distinguishes_source_from_context():
    """Submitted review payload distinguishes cited rule evidence ('source') from condition context ('context')."""
    passage = "Quy tắc: trong điều kiện chuẩn, phản ứng sinh ra chất Y. Trong điều kiện khác, không sinh ra chất Y."
    quote = "trong điều kiện chuẩn, phản ứng sinh ra chất Y."

    question = {
        "id": "q_cond",
        "question": "Trong điều kiện chuẩn, phản ứng sinh ra chất nào?",
        "choices": {"A": "Chất Y", "B": "Chất X", "C": "Chất Z", "D": "Không có"},
        "answer": "A",
        "explanation": "Quy tắc nêu rõ phản ứng sinh ra chất Y.",
        "source": {"chunk_id": 55, "snippet": quote},
        "evidence_quote": quote,
    }

    submitted_prompts = []
    service = PracticeService(None, None)
    valid_check = {
        "index": 1,
        "input_audit": "Đủ dữ kiện điều kiện chuẩn.",
        "independent_solution": "Phản ứng sinh ra chất Y.",
        "choice_comparison": "Khớp A.",
        "explanation_audit": "Giải thích chuẩn xác.",
        "supported": True,
        "explanation_supported": True,
        "unique_answer": True,
        "qualifiers_preserved": True,
        "verified_answer": "A",
    }
    service._chat_complete = lambda prompt, *args, **kwargs: (submitted_prompts.append(prompt), json.dumps({"checks": [valid_check]}))[1]

    passage_dict = {55: {"chunk_id": 55, "text": passage, "safe_text": passage}}
    verified = service._verify_questions([question], passage_dict, "test-model")

    assert len(verified) == 1
    assert len(submitted_prompts) == 1
    prompt = submitted_prompts[0]

    # Verify submitted payload structure
    marker = "Dữ liệu cần kiểm định:\n"
    payload_json = json.loads(prompt.rsplit(marker, 1)[1])
    assert len(payload_json) == 1
    item = payload_json[0]
    assert item["source"] == quote
    assert "context" in item
    assert "Trong điều kiện khác" in item["context"]
    assert "Kiểm định độc lập từng câu từ source và question" in prompt


def test_valid_evidence_acceptance():
    """A question with faithfully bound evidence and correct explanation is accepted."""
    passage = "Quang hợp là quá trình biến đổi năng lượng ánh sáng thành năng lượng hóa học."
    quote = "Quang hợp là quá trình biến đổi năng lượng ánh sáng thành năng lượng hóa học."

    question = {
        "id": "q_photo",
        "question": "Quang hợp biến đổi năng lượng ánh sáng thành dạng năng lượng nào?",
        "choices": {"A": "Hóa học", "B": "Cơ học", "C": "Nhiệt năng", "D": "Điện năng"},
        "answer": "A",
        "explanation": "Đoạn trích nêu trực tiếp biến thành năng lượng hóa học.",
        "source": {"chunk_id": 77, "snippet": quote},
        "evidence_quote": quote,
    }

    service = PracticeService(None, None)
    valid_check = {
        "index": 1,
        "input_audit": "Đủ dữ kiện khái niệm quang hợp.",
        "independent_solution": "Ánh sáng thành năng lượng hóa học.",
        "choice_comparison": "Khớp A (Hóa học).",
        "explanation_audit": "Giải thích đúng theo nguồn.",
        "supported": True,
        "explanation_supported": True,
        "unique_answer": True,
        "qualifiers_preserved": True,
        "verified_answer": "A",
    }
    service._chat_complete = lambda *args, **kwargs: json.dumps({"checks": [valid_check]})
    passage_dict = {77: {"chunk_id": 77, "text": passage, "safe_text": passage}}
    verified = service._verify_questions([question], passage_dict, "test-model")
    assert len(verified) == 1
    assert verified[0]["id"] == "q_photo"


def test_review_indices_are_dense_after_unbound_citation_is_skipped():
    quote = 'Quang hợp biến đổi năng lượng ánh sáng thành năng lượng hóa học.'
    valid = {'id': 'valid', 'question': 'Quang hợp tạo dạng năng lượng nào?',
             'choices': {'A': 'Hóa học', 'B': 'Cơ học', 'C': 'Điện', 'D': 'Âm thanh'},
             'answer': 'A', 'explanation': quote, 'source': {'chunk_id': 1, 'snippet': quote}}
    invalid = {**valid, 'id': 'invalid', 'source': {'chunk_id': 1, 'snippet': 'Không có câu này trong nguồn.'}}
    service = PracticeService(None, None)
    def capture(prompt, model, max_tokens, **kwargs):
        records = json.loads(prompt.split('Dữ liệu cần kiểm định:\n')[1])
        assert [item['index'] for item in records] == [1]
        assert kwargs['response_schema']['properties']['checks']['items']['properties']['index']['maximum'] == 1
        return json.dumps({'checks': [{'index': 1,
            'input_audit': 'Đủ dữ kiện khái niệm.', 'independent_solution': 'Năng lượng hóa học.',
            'choice_comparison': 'Chỉ A khớp.', 'explanation_audit': 'Đúng nguồn.',
            'supported': True, 'explanation_supported': True, 'unique_answer': True,
            'qualifiers_preserved': True, 'verified_answer': 'A'}]})
    service._chat_complete = capture
    assert service._verify_questions([invalid, valid], {1: {'safe_text': quote}}, 'test') == [valid]


def test_quote_options_include_adjacent_problem_and_worked_solution_lines():
    text = 'Worked Examples\n1. Let f(x) = 2^2016. Find its derivative.\nSolution: f is constant, so its derivative is zero.\n2. Let g(x) = x^2.'
    options = _practice_quote_options({'text': text, 'safe_text': text}, 1400)
    assert '1. Let f(x) = 2^2016. Find its derivative. Solution: f is constant, so its derivative is zero.' in options
    assert all('Worked Examples' not in item for item in options)


def test_generation_binds_evidence_before_writing_and_solves_before_answer_key():
    from app.practice import practice_generation_schema
    properties = list(practice_generation_schema(3, 1)['properties']['questions']['items']['properties'])
    assert properties[:2] == ['source_chunk_id', 'evidence_quote']
    assert properties.index('explanation') < properties.index('choices') < properties.index('answer')


def test_quote_options_exclude_unsolved_exercise_prompts_but_keep_worked_evidence():
    text = '1. f(x) = 2^2016\nSolution: the derivative is 0 since f is constant.\n9. Using the previous problem, show that the derivative of x^r is rx^(r−1) for any real number r.'
    options = _practice_quote_options({'text': text, 'safe_text': text}, 1400)
    assert not any(item.startswith('Using the previous problem') or item.startswith('9. Using') for item in options)
    assert '1. f(x) = 2^2016 Solution: the derivative is 0 since f is constant.' in options


def test_generation_uses_distinct_complete_worked_examples_with_original_source_ids():
    from app.practice import _worked_example_passages
    text = 'Worked Examples\n1. f(x) = 2^2016\nSolution: the derivative is 0 since f is constant.\n2. f(x) = (1/16)x^4\nSolution: using the power rule, f\'(x) = (1/4)x^3\n3. f(x) = sin x cos x\nSolution: the derivative is cos(2x).'
    source = {'chunk_id': 77, 'page_num': 3, 'text': text, 'safe_text': text}
    selected = _worked_example_passages([source], 3, 1400)
    assert len(selected) == 3
    assert [item['chunk_id'] for item in selected] == [77, 77, 77]
    assert all('Solution:' in item['text'] for item in selected)
    assert all(_practice_quote_options(item, 1400) == [item['generation_evidence']] for item in selected)
    assert len({item['text'] for item in selected}) == 3
    assert all(' '.join(item['text'].split()) in ' '.join(text.split()) for item in selected)
    assert _worked_example_passages([source], 4, 1400) == [source]
    theory = {'chunk_id': 1, 'text': 'A rule with an important condition.', 'safe_text': 'A rule with an important condition.'}
    assert _worked_example_passages([theory], 3, 1400) == [theory]


def test_worked_example_plan_does_not_force_incomplete_or_general_sources():
    from app.practice import _worked_example_passages, practice_generation_schema
    text = '1. Find the derivative of the function.\nSolution:'
    source = {'chunk_id': 1, 'text': text, 'safe_text': text}
    sources = [source]
    assert _worked_example_passages(sources, 1, 1400) is sources
    ordinary = practice_generation_schema(3, 1)['properties']['questions']
    planned = practice_generation_schema(3, 3, require_count=True)['properties']['questions']
    assert 'minItems' not in ordinary
    assert planned['minItems'] == planned['maxItems'] == 3


def test_constrained_native_review_completes_audits_before_verdicts():
    fields = list(practice_review_schema(1, None)['properties']['checks']['items']['properties'])
    assert fields.index('input_audit') < fields.index('independent_solution') < fields.index('choice_comparison') < fields.index('explanation_audit')
    assert all(fields.index('explanation_audit') < fields.index(key) for key in
               ('supported', 'explanation_supported', 'unique_answer', 'qualifiers_preserved', 'verified_answer'))


def test_grounded_reviewer_does_not_receive_proposed_answer_key():
    from app.practice import build_grounded_review_prompt
    item = {'index': 1, 'source': 'A source rule.', 'question': 'What follows?',
            'choices': {'A': 'One', 'B': 'Two', 'C': 'Three', 'D': 'Four'},
            'proposed_answer': 'C', 'explanation': 'Proposed reasoning.'}
    prompt = build_grounded_review_prompt([item])
    sent = json.loads(prompt.split('Dữ liệu cần kiểm định:\n')[1])[0]
    assert 'proposed_answer' not in sent
    assert sent == {key: value for key, value in item.items() if key != 'proposed_answer'}
    assert item['proposed_answer'] == 'C'


def test_wrapped_definition_plan_preserves_exact_quotes_and_original_source_ids():
    from app.practice import _statement_passages
    text = ('Punishment is a penalty given in an attempt to\n'
            'decrease the occurrence of a certain behaviour.\n'
            'Shaping is a process in which reinforcers are\n'
            "used to guide an organism's actions toward a\n"
            'desired behaviour.\n'
            'Successive approximations are behaviours\n'
            'that are incrementally closer to the overall desired\n'
            'action.')
    source = {'chunk_id': 77, 'page_num': 197, 'text': text, 'safe_text': text}
    selected = _statement_passages([source], 3, 1400)
    assert len(selected) == 3
    assert [item['chunk_id'] for item in selected] == [77, 77, 77]
    assert {item['generation_evidence'] for item in selected} == {
        'Punishment is a penalty given in an attempt to decrease the occurrence of a certain behaviour.',
        "Shaping is a process in which reinforcers are used to guide an organism's actions toward a desired behaviour.",
        'Successive approximations are behaviours that are incrementally closer to the overall desired action.'}
    assert all(PracticeService.extract_supporting_evidence(text, item['generation_evidence']) for item in selected)


def test_statement_plan_preserves_unresolved_sources_and_ignores_questions_and_fragments():
    from app.practice import _statement_passages
    sources = [{'chunk_id': 77, 'page_num': 1, 'text':
                'Incomplete trailing text.\nFind the derivative of the following function.\n'
                'What is the meaning of this deliberately lengthy question?\n'
                'A statement that stops before providing its required condition'}]
    assert _statement_passages(sources, 3, 1400) is sources
