"""Regressions for an inconsistent prime-list example observed in the live app."""

import pytest

from app.rag_agent import PsychologyAgent
from app.source_consistency import differing_value_reference_signatures, inconsistent_prime_extraction_example
from app.study_materials import StudyMaterialError, StudyMaterialManager


def example(result: str, *, input_values: str = "2 4 5 7 9") -> str:
    return (
        "Extract prime numbers from the sequence.\n"
        "Read values from INPUT.TXT.\nWrite results to OUTPUT.TXT.\n"
        f"INPUT.TXT\n{input_values}\nOUTPUT.TXT\n{result}\n"
    )


def test_detects_missing_prime_and_included_composite():
    assert inconsistent_prime_extraction_example(example("2 5 9"))


@pytest.mark.parametrize("text", [
    example("7 2 5"),  # Order can vary; multiplicity cannot.
    example("2 2 5", input_values="2 2 4 5"),
    example("2 5", input_values="2 4 9999999967"),  # Bounded check.
    "Prime numbers are useful.\nINPUT.TXT\n2 4 5\nOUTPUT.TXT\n2 5\n",
    "Extract prime numbers.\nRead INPUT.TXT.\nWrite OUTPUT.TXT.\nINPUT.TXT\n2 4 5\nOUTPUT.TXT\n2, 5\n",
    "Extract prime numbers.\nINPUT.TXT\n2 4 5\nOUTPUT.TXT\n2 5\n",  # Roles unstated.
])
def test_does_not_claim_inconsistency_for_valid_or_unsupported_layouts(text):
    assert not inconsistent_prime_extraction_example(text)


def test_duplicate_prime_omission_is_inconsistent():
    assert inconsistent_prime_extraction_example(example("2 5", input_values="2 2 4 5"))


@pytest.mark.parametrize("text,question,expected", [
    ("void read_number(int value);\nvoid read_number(int &value)\n{\n}",
     "Khai báo và định nghĩa read_number khác nhau thế nào?", True),
    ("void read_number(int &value);\nvoid read_number(int &value)\n{\n}",
     "Khai báo và định nghĩa read_number khác nhau thế nào?", False),
    ("void read_number(int value);\nvoid read_number(int &value);\n"
     "void read_number(int &value)\n{\n}",
     "Khai báo và định nghĩa read_number khác nhau thế nào?", False),
    ("void read_number(int value);\nvoid read_number(int &value)\n{\n}",
     "Hàm read_number nhận dữ liệu như thế nào?", False),
    ("void read_number(int value);\nvoid read_number(int &value)\n{\n}",
     "Compare the declaration and definition of another_function", False),
    ("void read_number(const int value);\nvoid read_number(int &value)\n{\n}",
     "Compare the declaration and definition of read_number", False),
])
def test_signature_comparison_is_scoped_to_supported_named_examples(text, question, expected):
    assert differing_value_reference_signatures(text, question) is expected


def test_signature_difference_is_reported_before_unsupported_model_claims():
    text = (
        "Khai báo và định nghĩa hàm trong ví dụ nhiều tệp:\n"
        "void read_number(int value);\nvoid read_number(int &value)\n{\n}"
    )

    class Searcher:
        def source_page_chunks(self, doc_id, page_num, limit):
            return [{
                "doc_id": doc_id, "page_num": page_num, "book_title": "Reference",
                "filename": "reference.pdf", "chunk_index": 0, "text": text, "safe_text": text,
                "semantic_score": 0.7,
            }]

    class Model:
        calls = 0

        def chat_stream(self, **kwargs):
            self.calls += 1
            yield "Unsupported effect [S1]."

    model = Model()
    agent = PsychologyAgent(searcher=Searcher(), ollama=model, translator=object())
    result = agent.process_query_sync(
        "Khai báo và định nghĩa read_number khác nhau thế nào?",
        source_page=(1, 1), model="test-ollama", embed_model=None,
    )
    assert result["insufficient_reason"] == "source_signature_difference_unverified"
    assert "truyền giá trị" in result["answer"]
    assert "tham chiếu" in result["answer"]
    assert "có thể" in result["answer"]
    assert model.calls == 0


def test_inconsistent_example_is_withheld_before_model_generation():
    class Searcher:
        def source_page_chunks(self, doc_id, page_num, limit):
            text = example("2 5 9")
            return [{
                "doc_id": doc_id, "page_num": page_num, "book_title": "Reference",
                "filename": "reference.pdf", "chunk_index": 0, "text": text, "safe_text": text,
            }]

    class Model:
        calls = 0

        def chat_stream(self, **kwargs):
            self.calls += 1
            yield "Unreliable result [S1]."

    model = Model()
    agent = PsychologyAgent(searcher=Searcher(), ollama=model, translator=object())
    result = agent.process_query_sync(
        "Extract prime numbers from the sequence", source_page=(1, 1), model="test-ollama", embed_model=None,
    )
    assert result["insufficient_reason"] == "source_example_inconsistent"
    assert result["citations"] == []
    assert "không khớp" in result["answer"]
    assert model.calls == 0


@pytest.mark.parametrize("result,blocked", [("2 5 9", True), ("2 5 7", False)])
def test_library_study_materials_check_inconsistent_example_before_generation(monkeypatch, result, blocked):
    manager = StudyMaterialManager()
    monkeypatch.setattr(manager, "inspect_library_document", lambda **kwargs: {
        "doc_id": 1, "filename": "reference.pdf", "total_pages": 2,
        "pages": [
            {"page_num": 1, "text": "Introduction."},
            {"page_num": 2, "text": example(result)},
        ],
    })
    monkeypatch.setattr(manager, "_unverified_visual_pages", lambda *args: [])
    calls = []
    monkeypatch.setattr(manager, "generate_materials_from_pages", lambda extracted, **kwargs: calls.append(extracted))

    if blocked:
        with pytest.raises(StudyMaterialError, match="trang 2.*không khớp"):
            manager.generate_from_library_document(doc_id=1, page_from=2, page_to=2)
        assert not calls
    else:
        manager.generate_from_library_document(doc_id=1, page_from=2, page_to=2)
        assert len(calls) == 1
