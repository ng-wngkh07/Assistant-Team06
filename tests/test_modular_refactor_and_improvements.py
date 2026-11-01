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


def _check(condition: bool, msg: str = "Assertion failed") -> None:
    if not condition:
        raise AssertionError(msg)


def test_yc03_resolve_processing_level_defaults_to_standard():
    _check(resolve_processing_level(None, "Giải thích chi tiết và so sánh các mô hình") == "standard")
    _check(resolve_processing_level("auto", "Câu hỏi phức tạp") == "standard")
    _check(resolve_processing_level("standard", "Câu hỏi thường") == "standard")
    _check(resolve_processing_level("careful", "Câu hỏi kỹ") == "careful")


def test_yc03_topic_tree_hierarchy():
    _check(len(TOPIC_TREE) >= 5)
    _check("Trí nhớ và nhận thức" in CHILD_TO_PARENT)
    hierarchy = resolve_topic_hierarchy("Trí nhớ và nhận thức")
    _check(hierarchy is not None)
    _check(hierarchy["parent_id"] == "psychology_behavioral")
    _check(hierarchy["topic"] == "Trí nhớ và nhận thức")

    inferred = infer_query_topic_hierarchy("Tâm lý học nhận thức và trí nhớ dài hạn")
    _check(inferred is not None)
    _check("psychology_behavioral" in inferred["parent_ids"])

    catalog = get_topic_tree_catalog()
    _check(any(node["parent_id"] == "psychology_behavioral" and len(node["children"]) >= 5 for node in catalog))


def test_yc07_registration_hold_retains_partial_entries():
    ocr_lines = [
        {"text": "Môn học đã đăng ký trong kỳ", "x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.14},
        {"text": "Kết quả đăng ký môn học", "x0": 0.1, "y0": 0.15, "x1": 0.5, "y1": 0.19},
        {"text": "PSY1051 - Tâm lý học đại cương", "x0": 0.1, "y0": 0.25, "x1": 0.6, "y1": 0.29},
        {"text": "INT1008 - Nhập môn lập trình", "x0": 0.1, "y0": 0.35, "x1": 0.6, "y1": 0.39},
        {"text": "Lịch học: Chưa biết", "x0": 0.1, "y0": 0.45, "x1": 0.5, "y1": 0.49},
    ]
    res = classify_registration_hold(ocr_lines)
    _check(res is not None)
    _check(res["classification"] == "incomplete_registration")
    _check(res["entries"] == [])
    _check(len(res["partial_entries"]) >= 2)
    _check(any(item.get("course_code") == "PSY1051" for item in res["partial_entries"]))
    _check(any("missing_fields" in item for item in res["partial_entries"]))


def test_yc08_find_tesseract_executable_returns_str_or_none():
    exe = _find_tesseract_executable()
    _check(exe is None or isinstance(exe, str))


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
    _check(len(slides) == 1)
    _check("Tâm lý học nhận thức" in slides[0][0])

    png_bytes = render_office_page_png(pptx_path, page_num=1)
    _check(png_bytes is not None)
    _check(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
    _check(render_office_page_png(pptx_path, page_num=2) is None)


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

    _check(hasattr(ls, "clean_fts_query"))
    _check(hasattr(ss, "normalize_unit_vector"))
    _check(hasattr(ms, "StudyMaterialError"))
    _check(hasattr(qv, "evaluate_question_difficulty"))
    _check(hasattr(qg, "build_quiz_prompt"))
    _check(hasattr(fg, "build_flashcards_prompt"))
    _check(hasattr(rqa, "extract_query_facets"))
    _check(hasattr(rev, "has_relevant_evidence"))
    _check(hasattr(rcr, "parse_citations"))
    _check(hasattr(srv, "extract_timetable"))
    _check(hasattr(srv, "upload_and_generate_materials"))


def test_security_alerts_remediation(tmp_path: Path):
    from defusedxml.common import DTDForbidden
    from app.answer_format import clean_answer
    from app.knowledge_pack import _enumerate_physical_pages
    from app.vision_ocr import VisionOCRManager

    # 1. Path injection guard in vision_ocr
    mgr = VisionOCRManager()
    valid_key = "a" * 64
    resolved = mgr._validate_cache_path(valid_key)
    _check(resolved.name == f"{valid_key}.json")

    rejected = False
    try:
        mgr._validate_cache_path("../etc/passwd")
    except ValueError:
        rejected = True
    _check(rejected, "Expected ValueError on path traversal key")

    # 2. ReDoS-safe _SOURCE_SECTION in answer_format
    adversarial_tail = "Nội dung chính [S1].\n\nNguồn tham khảo:\n" + ("- [S1] " + " " * 200 + "\n") * 20
    cleaned = clean_answer(adversarial_tail)
    _check("Nguồn tham khảo" not in cleaned)
    _check("Nội dung chính." in cleaned)

    # 3. defusedxml DTD rejection in knowledge_pack._enumerate_physical_pages
    bad_pptx = tmp_path / "malicious.pptx"
    with ZipFile(bad_pptx, "w") as zf:
        zf.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0"?><!DOCTYPE root><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
    dtd_blocked = False
    try:
        _enumerate_physical_pages(bad_pptx)
    except DTDForbidden:
        dtd_blocked = True
    _check(dtd_blocked, "Expected DTDForbidden on PPTX with DOCTYPE")

    # 4. Dependency manifests patched against Dependabot alerts
    root = Path(__file__).resolve().parents[1]
    req_txt = (root / "requirements.txt").read_text(encoding="utf-8")
    req_mlx = (root / "requirements-mlx.txt").read_text(encoding="utf-8")
    _check("pillow>=13.0.0" in req_txt)
    _check("certifi>=2026.10.18" in req_txt)
    _check("certifi>=2026.10.18" in req_mlx)
    _check("filelock>=4.2.1" in req_mlx)
