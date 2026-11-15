import json
from decimal import Decimal
import pytest

from app.rag_agent import (
    extract_numeric_literals,
    extract_unparsed_numeric_tokens,
    PsychologyAgent,
)


class DummySearcher:
    def __init__(self, text: str):
        self.text = text

    def search_hybrid(self, **kwargs):
        return [{
            "book_title": "Synthetic Course Notes",
            "filename": "synthetic_notes.md",
            "page_num": 1,
            "chunk_index": 0,
            "text": self.text,
            "safe_text": self.text,
            "hybrid_score": 1.0,
            "semantic_score": 1.0,
        }]


class DummyModel:
    def __init__(self, claim_text: str):
        self.claim_text = claim_text
        self.calls = 0

    def chat_stream(self, **kwargs):
        self.calls += 1
        yield json.dumps({
            "claims": [{
                "text": self.claim_text,
                "source_ids": ["S1"],
                "evidence_lines": [1, 1],
            }]
        }, ensure_ascii=False)


class DummyTranslator:
    def get_cached(self, chunks):
        return {}


# ---------------------------------------------------------------------------
# Unit tests for extract_numeric_literals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # Both locale directions (dot and comma)
    ("Giá trị đạt 0.6.", {Decimal("0.6")}),
    ("Giá trị đạt 0,6.", {Decimal("0.6")}),
    ("Đo được 12.34 cm.", {Decimal("12.34")}),
    ("Đo được 12,34 cm.", {Decimal("12.34")}),
    ("Hệ số là .75 đơn vị.", {Decimal("0.75")}),
    ("Tỉ lệ bằng 0,006.", {Decimal("0.006")}),

    # Unequal values
    ("Kết quả 0.6.", {Decimal("0.6")}),
    ("Kết quả 0,7.", {Decimal("0.7")}),
    ("Độ lệch 2.5.", {Decimal("2.5")}),
    ("Độ lệch 2.6.", {Decimal("2.6")}),

    # Sign changes and unary signs
    ("Nhiệt độ bằng -2.5 độ.", {Decimal("-2.5")}),
    ("Nhiệt độ bằng 2.5 độ.", {Decimal("2.5")}),
    ("Nhiệt độ là -2,5 độ.", {Decimal("-2.5")}),
    ("Độ cao +3.5 m.", {Decimal("3.5")}),
    ("Ban đầu -10 điểm.", {Decimal("-10")}),

    # Signed factors after operators / multiplication / subtraction
    ("H=-2×0.5×log₂(0.5)=1 bit.", {Decimal("-2"), Decimal("0.5"), Decimal("1")}),
    ("y = 3 * -2.5", {Decimal("3"), Decimal("-2.5")}),
    ("y = 3 × -2.5", {Decimal("3"), Decimal("-2.5")}),
    ("Hiệu là 3 - -2.5", {Decimal("3"), Decimal("-2.5")}),
    ("Trong ngoặc (-2.5)", {Decimal("-2.5")}),

    # Binary subtraction and algebraic variables
    ("Phép trừ 5 - 2 = 3", {Decimal("5"), Decimal("2"), Decimal("3")}),
    ("Biểu thức (5-2)/3=1", {Decimal("5"), Decimal("2"), Decimal("3"), Decimal("1")}),
    ("Hàm f(x) = x - 2.5", {Decimal("2.5")}),
    ("Mã mẫu COVID-19", {Decimal("19")}),

    # Scientific equivalence and exponent changes
    ("Giá trị 6e-3.", {Decimal("0.006")}),
    ("Giá trị 6E-3.", {Decimal("0.006")}),
    ("Giá trị 6e+3.", {Decimal("6000")}),
    ("Giá trị 1.5e2.", {Decimal("150")}),
    ("Giá trị 1,5e2.", {Decimal("150")}),
    ("Giá trị 0.006.", {Decimal("0.006")}),

    # Lists with and without whitespace
    ("Các số gồm 1, 2, 3.", {Decimal("1"), Decimal("2"), Decimal("3")}),
    ("Dãy [2,4,6,8,10]", {Decimal("2"), Decimal("4"), Decimal("6"), Decimal("8"), Decimal("10")}),

    # Adjacent punctuation
    ("(0.6)", {Decimal("0.6")}),
    ("[0,6]", {Decimal("0.6")}),
    ("{0.6}", {Decimal("0.6")}),
    ("0.6.", {Decimal("0.6")}),
    ("0.6,", {Decimal("0.6")}),
    ("0.6;", {Decimal("0.6")}),
    ("\"0.6\"", {Decimal("0.6")}),
    ("'0.6'", {Decimal("0.6")}),
    ("0.6!", {Decimal("0.6")}),
    ("0.6?", {Decimal("0.6")}),

    # Ambiguous grouping and malformed representations (must not infer values)
    ("Số lượng 1,000 người.", set()),
    ("Tổng cộng 1,000,000 đồng.", set()),
    ("Tổng 1.000.000 đồng.", set()),
    ("Phiên bản 1.2.3.", set()),
    ("Token 6e.", set()),
    ("Mã 123abc.", set()),
    ("Biến abc123.", set()),
])
def test_numeric_literal_extraction(text, expected):
    assert extract_numeric_literals(text) == expected


def test_scientific_constituent_fragments_not_extracted():
    # 6e-3 should yield 0.006, never 6 or 3 as constituent fragments
    extracted = extract_numeric_literals("Thông số 6e-3.")
    assert extracted == {Decimal("0.006")}
    assert Decimal("6") not in extracted
    assert Decimal("3") not in extracted


def test_locale_equivalence_value_comparison():
    # In Decimal comparison, 0.6 and 0,6 evaluate identically
    set_dot = extract_numeric_literals("0.6")
    set_comma = extract_numeric_literals("0,6")
    assert set_dot == set_comma == {Decimal("0.6")}
    assert set_dot.issubset(set_comma)
    assert set_comma.issubset(set_dot)


def test_scientific_equivalence_value_comparison():
    # 6e-3 and 0.006 evaluate identically in Decimal
    set_sci = extract_numeric_literals("6e-3")
    set_dec = extract_numeric_literals("0.006")
    set_comma = extract_numeric_literals("0,006")
    assert set_sci == set_dec == set_comma == {Decimal("0.006")}
    assert set_sci.issubset(set_dec)
    assert set_dec.issubset(set_sci)


def test_sign_change_inequality():
    set_neg = extract_numeric_literals("Nhiệt độ bằng -2.5.")
    set_pos = extract_numeric_literals("Nhiệt độ bằng 2.5.")
    assert set_neg == {Decimal("-2.5")}
    assert set_pos == {Decimal("2.5")}
    assert not set_pos.issubset(set_neg)
    assert not set_neg.issubset(set_pos)


# ---------------------------------------------------------------------------
# Pipeline integration tests with PsychologyAgent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source_text,claim_text,expected_kind", [
    # Locale direction: dot in source, comma in claim -> accepted
    ("Kết quả tính toán là 0.6.", "Kết quả tính toán là 0,6.", "answer"),
    # Locale direction: comma in source, dot in claim -> accepted
    ("Kết quả tính toán là 0,6.", "Kết quả tính toán là 0.6.", "answer"),
    # Unequal values: dot in source, different comma in claim -> rejected
    ("Kết quả tính toán là 0.6.", "Kết quả tính toán là 0,7.", "generation_failed"),
    # Sign change: negative in source, positive in claim -> rejected
    ("Giá trị suy ra bằng -2.5.", "Giá trị suy ra bằng 2.5.", "generation_failed"),
    # Scientific equivalence: 6e-3 in source, 0.006 in claim -> accepted
    ("Giá trị suy ra bằng 6e-3.", "Giá trị suy ra bằng 0.006.", "answer"),
    # Exponent change / constituent fragment: 6e-3 in source, 6 in claim -> rejected
    ("Giá trị suy ra bằng 6e-3.", "Giá trị suy ra bằng 6.", "generation_failed"),
])
def test_pipeline_evidence_numeric_boundary(monkeypatch, source_text, claim_text, expected_kind):
    monkeypatch.setattr("app.rag_agent.context_capacity", lambda m: 8192)
    monkeypatch.setattr("app.rag_agent.get_exact_token_counter", lambda *a: lambda messages: 100)

    model = DummyModel(claim_text)
    agent = PsychologyAgent(
        searcher=DummySearcher(source_text),
        ollama=model,
        translator=DummyTranslator(),
    )
    result = agent.process_query_sync(
        "Giá trị cần tìm bằng bao nhiêu?",
        model="qwen3.5-9b-4bit",
        embed_model=None,
        isolate_context=True,
        processing_level="standard",
    )
    assert result["response_kind"] == expected_kind, result
    if expected_kind == "answer":
        assert len(result["citations"]) == 1
        assert model.calls == 1
    else:
        assert len(result["citations"]) == 0


# ---------------------------------------------------------------------------
# Unit tests for extract_unparsed_numeric_tokens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Số lượng 1,000 người.", {"1,000"}),
    ("Tổng cộng 1,000,000 đồng.", {"1,000,000"}),
    ("Tổng 1.000.000 đồng.", {"1.000.000"}),
    ("Phiên bản 1.2.3.", {"1.2.3"}),
    ("Token 6e.", {"6e"}),
    ("Token 7e+.", {"7e+"}),
    ("Token 7e-.", {"7e-"}),
    ("Giá trị đạt 0.6.", set()),
    ("Giá trị gồm 0.6 và 1,000.", {"1,000"}),
])
def test_unparsed_numeric_token_extraction(text, expected):
    assert extract_unparsed_numeric_tokens(text) == expected


# ---------------------------------------------------------------------------
# Pipeline integration tests for unparsed and mixed numeric tokens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source_text,claim_text,expected_kind", [
    # Unknown-only unsupported claims (empty-set subset must not pass)
    ("Giá trị bằng 0.6.", "Giá trị bằng 1,000.", "generation_failed"),
    ("Giá trị bằng 0.6.", "Giá trị bằng 1.2.3.", "generation_failed"),
    ("Giá trị bằng 0.6.", "Giá trị bằng 6e.", "generation_failed"),
    ("Giá trị bằng 0.6.", "Giá trị bằng 7e+.", "generation_failed"),

    # Mixed recognized + unsupported unknown tokens (valid number must not mask unknown)
    ("Giá trị bằng 0.6.", "Giá trị gồm 0.6 và 1,000.", "generation_failed"),
    ("Giá trị bằng 0.6.", "Giá trị gồm 0.6 và 1.2.3.", "generation_failed"),
    ("Giá trị bằng 0.6.", "Giá trị gồm 0.6 và 6e.", "generation_failed"),
    ("Giá trị bằng 0.6.", "Giá trị gồm 0.6 và 7e+.", "generation_failed"),

    # Source-backed identical unknown representation (preserved without guessing)
    ("Số lượng là 1,000 người.", "Số lượng là 1,000 người.", "answer"),
    ("Phiên bản hệ thống là 1.2.3.", "Phiên bản hệ thống là 1.2.3.", "answer"),
    ("Ký hiệu mã là 6e.", "Ký hiệu mã là 6e.", "answer"),
    ("Ký hiệu mã là 7e+.", "Ký hiệu mã là 7e+.", "answer"),

    # Source-backed mixed recognized + unknown representation
    ("Tỉ lệ 0.6 và số lượng 1,000.", "Tỉ lệ 0.6 và số lượng 1,000.", "answer"),
    ("Tỉ lệ 0.6 và phiên bản 1.2.3.", "Tỉ lệ 0.6 và phiên bản 1.2.3.", "answer"),
])
def test_pipeline_evidence_unparsed_boundary(monkeypatch, source_text, claim_text, expected_kind):
    monkeypatch.setattr("app.rag_agent.context_capacity", lambda m: 8192)
    monkeypatch.setattr("app.rag_agent.get_exact_token_counter", lambda *a: lambda messages: 100)

    model = DummyModel(claim_text)
    agent = PsychologyAgent(
        searcher=DummySearcher(source_text),
        ollama=model,
        translator=DummyTranslator(),
    )
    result = agent.process_query_sync(
        "Giá trị cần tìm bằng bao nhiêu?",
        model="qwen3.5-9b-4bit",
        embed_model=None,
        isolate_context=True,
        processing_level="standard",
    )
    assert result["response_kind"] == expected_kind, result
    if expected_kind == "answer":
        assert len(result["citations"]) == 1
        assert model.calls == 1
    else:
        assert len(result["citations"]) == 0


def test_written_number_words_in_quote_substantiate_claim_digits(monkeypatch):
    from app.rag_evidence_validator import extract_quote_written_numbers

    assert extract_quote_written_numbers("mỗi người bị giam một năm tù hoặc hai năm tù") >= {1.0, 2.0}
    assert extract_quote_written_numbers("giam giữ năm năm tù nhưng nếu cả hai chối tội thì chỉ bị một năm") >= {1.0, 5.0}
    assert extract_quote_written_numbers("tất cả bị giam ten years và không ai chịu zero years") >= {0.0, 10.0}
    # Ambiguous Vietnamese phrases containing 'một' / 'không' must NOT emit bogus numerals
    assert extract_quote_written_numbers("một số trường hợp không chỉ có một phần nhỏ mà không những thế") == set()

    monkeypatch.setattr("app.rag_agent.context_capacity", lambda m: 8192)
    monkeypatch.setattr("app.rag_agent.get_exact_token_counter", lambda *a: lambda messages: 100)

    source_text = "Nếu cả hai cùng im lặng thì mỗi người chỉ chịu một năm tù; nếu một người khai và người kia im lặng thì người im lặng chịu hai năm tù."
    claim_text = "Nếu cùng im lặng, mỗi người chịu 1 năm tù; nếu chỉ một người im lặng, người đó chịu 2 năm tù."
    model = DummyModel(claim_text)
    agent = PsychologyAgent(
        searcher=DummySearcher(source_text),
        ollama=model,
        translator=DummyTranslator(),
    )
    result = agent.process_query_sync(
        "Kết quả án tù trong từng trường hợp là gì?",
        model="qwen3.5-9b-4bit",
        embed_model=None,
        isolate_context=True,
        processing_level="standard",
    )
    assert result["response_kind"] == "answer", result
    assert len(result["citations"]) == 1
    assert model.calls == 1, "Written number words in source quote must not trigger repair pass"


def test_ocr_glued_vietnamese_math_numbers_in_quote_substantiate_claim_digits(monkeypatch):
    from app.rag_evidence_validator import extract_quote_ocr_glued_numbers

    glued = "1.Kiểm tra0∈W:nếu0∉W,dừng; nếu0∈W,tiếp. x0∈W, u1=2, H2O, W1,W2"
    assert extract_quote_ocr_glued_numbers("1.Kiểm tra0∈W:nếu0∉W,dừng; nếu0∈W,tiếp.") == {Decimal("0")}
    # Must never extract subscripted math variables, source IDs, or chemical formulas
    assert extract_quote_ocr_glued_numbers("x0∈W, u1=2, H2O, CO2, W1,W2, S1") == set()

    monkeypatch.setattr("app.rag_agent.context_capacity", lambda m: 8192)
    monkeypatch.setattr("app.rag_agent.get_exact_token_counter", lambda *a: lambda messages: 100)

    source_text = "1.Kiểm tra0∈W:nếu0∉W,dừng;nếu0∈W,tiếp."
    claim_text = "1. Kiểm tra 0∈W: nếu 0∉W thì dừng; nếu 0∈W thì tiếp tục."
    model = DummyModel(claim_text)
    agent = PsychologyAgent(
        searcher=DummySearcher(source_text),
        ollama=model,
        translator=DummyTranslator(),
    )
    result = agent.process_query_sync(
        "Trình bày phương pháp kiểm tra không gian con W.",
        model="qwen3.5-9b-4bit",
        embed_model=None,
        isolate_context=True,
        processing_level="standard",
    )
    assert result["response_kind"] == "answer", result
    assert len(result["citations"]) == 1
    assert model.calls == 1, f"Expected 1 call on glued quote 'nếu0∉W', got {model.calls}"


def test_numeric_mismatch_feedback_names_missing_digits_lines_and_peer_sources(monkeypatch):
    monkeypatch.setattr("app.rag_agent.context_capacity", lambda m: 8192)
    monkeypatch.setattr("app.rag_agent.get_exact_token_counter", lambda *a: lambda messages: 100)

    class TwoChunkSearcher:
        def search_hybrid(self, **kwargs):
            return [
                {
                    "book_title": "Sách A",
                    "filename": "book_a.pdf",
                    "page_num": 38,
                    "chunk_index": 0,
                    "text": "Khai báo = 0 là thuần ảo.\nLớp không có thân nên là abstract class, phản bội chịu 5 năm tù.",
                    "safe_text": "Khai báo = 0 là thuần ảo.\nLớp không có thân nên là abstract class, phản bội chịu 5 năm tù.",
                    "hybrid_score": 1.0,
                    "semantic_score": 0.9,
                },
                {
                    "book_title": "Sách A",
                    "filename": "book_a.pdf",
                    "page_num": 51,
                    "chunk_index": 0,
                    "text": "Nếu cả hai cùng chối tội thì mỗi người chỉ chịu 1 năm tù.",
                    "safe_text": "Nếu cả hai cùng chối tội thì mỗi người chỉ chịu 1 năm tù.",
                    "hybrid_score": 0.9,
                    "semantic_score": 0.85,
                },
            ]

    class SpyRepairModel:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        def chat_stream(self, **kwargs):
            self.calls += 1
            msgs = kwargs.get("messages") or []
            self.prompts.append(msgs[-1]["content"] if msgs else "")
            if self.calls == 1:
                yield json.dumps({
                    "claims": [{
                        "text": "Khai báo = 0 là abstract class, chịu 5 năm thay vì 1 năm.",
                        "source_ids": ["S1"],
                        "evidence_lines": [2, 2],
                    }]
                }, ensure_ascii=False)
            else:
                yield json.dumps({
                    "claims": [
                        {"text": "Khai báo = 0 là thuần ảo, lớp là abstract class và phản bội chịu 5 năm tù.", "source_ids": ["S1"], "evidence_lines": [1, 2]},
                        {"text": "Nếu cả hai cùng chối tội thì mỗi người chỉ chịu 1 năm tù.", "source_ids": ["S2"], "evidence_lines": [1, 1]},
                    ]
                }, ensure_ascii=False)

    model = SpyRepairModel()
    agent = PsychologyAgent(
        searcher=TwoChunkSearcher(),
        ollama=model,
        translator=DummyTranslator(),
    )
    result = agent.process_query_sync(
        "Tại sao phản bội 5 năm thay vì 1 năm và = 0 nghĩa là gì?",
        model="qwen3.5-9b-4bit",
        embed_model=None,
        isolate_context=True,
        processing_level="standard",
    )
    assert result["response_kind"] == "answer", result
    assert model.calls == 2
    repair_prompt = model.prompts[1]
    assert "Thiếu số: 0, 1" in repair_prompt
    assert "S1" in repair_prompt and "L1" in repair_prompt
    assert "S2" in repair_prompt


def test_numeric_mismatch_feedback_warns_not_to_copy_query_number_when_peer_sources_also_have_it():
    from decimal import Decimal
    from app.rag_evidence_validator import format_numeric_mismatch_feedback

    feedback = format_numeric_mismatch_feedback(
        "S3",
        {Decimal("1"), Decimal("5")},
        {Decimal("1")},
        set(),
        set(),
        {
            "S1": "Hai người nhận tội, mỗi người lĩnh án 5 năm tù giam.",
            "S2": "Nếu đồng bọn nhận tội thì ngồi tù 5 năm.",
            "S3": "Cho dù anh không nhận tội chúng tôi cũng có thể phán anh án 1 năm tù giam.",
        },
        use_source_lines=True,
        query="Tại sao hai tên tội phạm chịu 5 năm tù thay vì cùng chối tội để chỉ chịu 1 năm?",
    )

    assert "Thiếu số: 5" in feedback
    assert "S1" in feedback and "S2" in feedback
    assert "câu hỏi" in feedback
    assert "KHÔNG chép" in feedback


def test_numeric_mismatch_feedback_prefers_complete_peer_and_forbids_question_echo():
    from decimal import Decimal
    from app.rag_evidence_validator import format_numeric_mismatch_feedback

    feedback = format_numeric_mismatch_feedback(
        "S3",
        {Decimal("1"), Decimal("5")},
        {Decimal("1")},
        set(),
        set(),
        {
            "S1": "Nếu cả hai cùng phản bội thì mỗi người ngồi tù 5 năm.",
            "S2": "Nếu cả hai giữ im lặng thì mỗi người tù 1 năm; nếu cùng phản bội thì 5 năm.",
            "S3": "Cho dù anh không nhận tội chúng tôi cũng có thể phán anh án 1 năm tù giam.",
        },
        use_source_lines=True,
        query="Tại sao hai tên tội phạm chịu 5 năm tù thay vì cùng chối tội để chỉ chịu 1 năm?",
    )

    assert "Thiếu số: 5" in feedback
    assert "S2" in feedback
    assert "S1" not in feedback
    assert "KHÔNG lặp lại" in feedback
    assert "KHÔNG chép" in feedback
    assert "BỎ hoàn toàn" in feedback


def test_numeric_mismatch_feedback_calls_out_same_page_sibling_chunks():
    from app.rag_evidence_validator import format_numeric_mismatch_feedback

    feedback = format_numeric_mismatch_feedback(
        "S7",
        {Decimal("5")},
        set(),
        set(),
        set(),
        {
            "S1": "Nếu cả hai cùng phản bội thì mỗi người nhận án 5 năm tù giam.",
            "S6": "Nội dung chương khác hoàn toàn không liên quan.",
            "S7": "Cân bằng Nash được coi là cân bằng phi hợp tác giữa hai bên.",
        },
        use_source_lines=True,
        query="Tại sao hai tên trộm chịu án 5 năm tù?",
        source_meta={"S1": ("doc:22", 48), "S6": ("doc:22", 143), "S7": ("doc:22", 48)},
    )

    assert "CÙNG TRANG" in feedback
    assert "48" in feedback
    assert "S1" in feedback
    assert "S6" not in feedback
