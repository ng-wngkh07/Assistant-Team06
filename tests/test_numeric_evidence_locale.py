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
