"""Regression for a real page whose exponent was lost in indexed text."""

import pytest

from app.rag_agent import PsychologyAgent


class PageSearcher:
    def __init__(self, needs_review, warnings):
        self.needs_review = needs_review
        self.warnings = warnings

    def source_page_chunks(self, doc_id, page_num, limit):
        return [{
            "doc_id": doc_id,
            "book_title": "Reference",
            "filename": "reference.pdf",
            "page_num": page_num,
            "chunk_index": 0,
            "text": "A collection contains elements and has a defined size.",
            "safe_text": "A collection contains elements and has a defined size.",
            "page_quality": {"needs_review": self.needs_review, "warnings": self.warnings},
        }]


class Model:
    def __init__(self):
        self.calls = 0

    def chat_stream(self, **kwargs):
        self.calls += 1
        yield "A collection contains elements [S1]."


class Translator:
    pass


def answer(needs_review, warnings):
    model = Model()
    agent = PsychologyAgent(searcher=PageSearcher(needs_review, warnings), ollama=model, translator=Translator())
    result = agent.process_query_sync(
        "Trình bày nội dung trang này", source_page=(1, 1), model="test-ollama", embed_model=None,
    )
    return result, model.calls


def test_unreviewed_potentially_incomplete_formula_does_not_reach_model():
    result, calls = answer(True, ["unrepresented_formula_or_diagram_suspected"])
    assert result["response_kind"] == "insufficient_evidence"
    assert result["insufficient_reason"] == "source_visual_content_unverified"
    assert result["citations"] == []
    assert "trang tài liệu gốc" in result["answer"]
    assert calls == 0


@pytest.mark.parametrize("needs_review,warnings", [
    (False, ["unrepresented_formula_or_diagram_suspected"]),
    (True, ["vni_legacy_encoding_detected"]),
    (False, []),
])
def test_reviewed_or_other_quality_warning_does_not_trigger_formula_guard(needs_review, warnings):
    result, calls = answer(needs_review, warnings)
    assert result["insufficient_reason"] != "source_visual_content_unverified"
    assert calls > 0
