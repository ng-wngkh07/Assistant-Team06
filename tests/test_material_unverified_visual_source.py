"""Regression for a real library page whose extracted exponent was flattened."""

import json
import sqlite3

import pytest

from app.study_materials import StudyMaterialError, StudyMaterialManager


@pytest.fixture
def library_page_quality(tmp_path, monkeypatch):
    import app.config as config

    database = tmp_path / "library.db"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE corpus_page_quality "
            "(filename TEXT, page_num INTEGER, needs_review INTEGER, warnings_json TEXT)"
        )
        db.executemany(
            "INSERT INTO corpus_page_quality VALUES (?, ?, ?, ?)",
            [
                ("reference.pdf", 2, 1, json.dumps(["unrepresented_formula_or_diagram_suspected"])),
                ("reference.pdf", 3, 0, json.dumps(["unrepresented_formula_or_diagram_suspected"])),
                ("another.pdf", 1, 1, json.dumps(["unrepresented_formula_or_diagram_suspected"])),
            ],
        )
    monkeypatch.setattr(config, "DB_PATH", database)
    manager = StudyMaterialManager()
    monkeypatch.setattr(manager, "inspect_library_document", lambda **kwargs: {
        "doc_id": 1, "filename": "reference.pdf", "total_pages": 3,
        "pages": [{"page_num": n, "text": "Source content."} for n in range(1, 4)],
    })
    calls = []

    def generate(extracted, **kwargs):
        calls.append(extracted.extracted_pages)
        return {"pages": extracted.extracted_pages}

    monkeypatch.setattr(manager, "generate_materials_from_pages", generate)
    return manager, calls


@pytest.mark.parametrize("first,last", [(2, 2), (1, 3)])
def test_unreviewed_visual_content_blocks_generation(library_page_quality, first, last):
    manager, calls = library_page_quality
    with pytest.raises(StudyMaterialError, match="trang 2.*tài liệu gốc"):
        manager.generate_from_library_document(doc_id=1, page_from=first, page_to=last)
    assert not calls


@pytest.mark.parametrize("page", [1, 3])
def test_other_pages_and_reviewed_warnings_do_not_block(library_page_quality, page):
    manager, calls = library_page_quality
    result = manager.generate_from_library_document(doc_id=1, page_from=page, page_to=page)
    assert result["pages"][0]["page_num"] == page
    assert len(calls) == 1
