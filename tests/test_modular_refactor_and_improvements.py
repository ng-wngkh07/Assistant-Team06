"""Unit tests for YC-03, YC-05, YC-06, YC-07, YC-08, PPTX/DOCX preview, and SRP module splits."""

from pathlib import Path
from zipfile import ZipFile

from app.reasoning_policy import resolve_processing_level
from app.slide_preview import extract_pptx_slide_texts, render_office_page_png
from app.timetable_table import (
    _find_tesseract_executable,
    classify_registration_hold,
)
from app.topic_tree import (
    CHILD_TO_PARENT,
    TOPIC_TREE,
    get_topic_tree_catalog,
    infer_query_topic_hierarchy,
    resolve_topic_hierarchy,
)


def test_yc03_resolve_processing_level_defaults_to_standard():
    assert resolve_processing_level(None, "Giải thích chi tiết và so sánh các mô hình") == "standard"
    assert resolve_processing_level("auto", "Câu hỏi phức tạp") == "standard"
    assert resolve_processing_level("standard", "Câu hỏi thường") == "standard"
    assert resolve_processing_level("careful", "Câu hỏi kỹ") == "careful"


def test_yc03_topic_tree_hierarchy():
    assert len(TOPIC_TREE) >= 5
    assert "Trí nhớ và nhận thức" in CHILD_TO_PARENT
    hierarchy = resolve_topic_hierarchy("Trí nhớ và nhận thức")
    assert hierarchy is not None
    assert hierarchy["parent_id"] == "psychology_behavioral"
    assert hierarchy["topic"] == "Trí nhớ và nhận thức"

    inferred = infer_query_topic_hierarchy("Tâm lý học nhận thức và trí nhớ dài hạn")
    assert inferred is not None
    assert "psychology_behavioral" in inferred["parent_ids"]

    catalog = get_topic_tree_catalog()
    assert any(node["parent_id"] == "psychology_behavioral" and len(node["children"]) >= 5 for node in catalog)


def test_yc07_registration_hold_retains_partial_entries():
    ocr_lines = [
        {"text": "Môn học đã đăng ký trong kỳ", "x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.14},
        {"text": "Kết quả đăng ký môn học", "x0": 0.1, "y0": 0.15, "x1": 0.5, "y1": 0.19},
        {"text": "PSY1051 - Tâm lý học đại cương", "x0": 0.1, "y0": 0.25, "x1": 0.6, "y1": 0.29},
        {"text": "INT1008 - Nhập môn lập trình", "x0": 0.1, "y0": 0.35, "x1": 0.6, "y1": 0.39},
        {"text": "Lịch học: Chưa biết", "x0": 0.1, "y0": 0.45, "x1": 0.5, "y1": 0.49},
    ]
    res = classify_registration_hold(ocr_lines)
    assert res is not None
    assert res["classification"] == "incomplete_registration"
    assert res["entries"] == []
    assert len(res["partial_entries"]) >= 2
    assert any(item.get("course_code") == "PSY1051" for item in res["partial_entries"])
    assert any("missing_fields" in item for item in res["partial_entries"])


def test_yc08_find_tesseract_executable_returns_str_or_none():
    exe = _find_tesseract_executable()
    assert exe is None or isinstance(exe, str)


def test_pptx_slide_preview_renders_valid_png(tmp_path: Path):
    pptx_path = tmp_path / "sample_deck.pptx"
    pres_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
        "</p:presentation>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
        'Target="slides/slide1.xml"/>'
        "</Relationships>"
    )
    slide_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>"
        "Slide 1: Tâm lý học nhận thức và trí nhớ"
        "</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    with ZipFile(pptx_path, "w") as zf:
        zf.writestr("ppt/presentation.xml", pres_xml)
        zf.writestr("ppt/_rels/presentation.xml.rels", rels_xml)
        zf.writestr("ppt/slides/slide1.xml", slide_xml)

    slides = extract_pptx_slide_texts(pptx_path)
    assert len(slides) == 1
    assert "Tâm lý học nhận thức" in slides[0][0]

    png_bytes = render_office_page_png(pptx_path, page_num=1)
    assert png_bytes is not None
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert render_office_page_png(pptx_path, page_num=2) is None


def test_srp_modules_importable_and_reexported():
    import app.flashcard_generator as fg
    import app.lexical_search as ls
    import app.material_storage as ms
    import app.question_validator as qv
    import app.quiz_generator as qg
    import app.rag_citation_repair as rcr
    import app.rag_evidence_validator as rev
    import app.rag_query_analyzer as rqa
    import app.semantic_search as ss
    import app.server as srv

    assert hasattr(ls, "clean_fts_query")
    assert hasattr(ss, "normalize_unit_vector")
    assert hasattr(ms, "StudyMaterialError")
    assert hasattr(qv, "evaluate_question_difficulty")
    assert hasattr(qg, "build_quiz_prompt")
    assert hasattr(fg, "build_flashcards_prompt")
    assert hasattr(rqa, "extract_query_facets")
    assert hasattr(rev, "has_relevant_evidence")
    assert hasattr(rcr, "parse_citations")
    assert hasattr(srv, "extract_timetable")
    assert hasattr(srv, "upload_and_generate_materials")
