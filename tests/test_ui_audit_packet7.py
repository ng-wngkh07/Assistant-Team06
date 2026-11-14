"""Comprehensive behavioral regressions for UI-AUDIT-20261111-07.

Verifies:
1. Parser recognizes question prefixes with decorative bullet prefixes (e.g. \\uf076Câu 7:)
   while preserving numbering, order, code declarations, and citations.
2. Parser omits standalone decorative ornaments (e.g. \\uf0e8 arrows) and running slide/page
   footers (e.g. 'Dữ liệu kiểu con trỏ (nâng cao)') from question/answer text.
3. Dependent questions (e.g. Câu 8 asking about array[0][0] declared in Câu 7) preserve
   verifiable source context from prior code declarations.
4. If a question is dependent and its context cannot be established, it is kept in an
   unavailable/review state instead of being emitted as a supposedly self-contained exercise.
5. Standalone fully specified questions remain completely unchanged.
6. Mathematical operators, code statements, brackets, and non-Latin/Vietnamese characters
   are preserved faithfully without aggressive noise stripping.
7. PDF worksheet export maps known Wingdings/Symbol PUA glyphs (\\uf076, \\uf0e8, etc.) to
   standard readable Unicode (•, →, etc.) and contains ZERO \\x00 (NUL) bytes across all pages.
8. PDF worksheet includes clearly labeled source context section for dependent questions.
9. Stale generator_version ('source_exercise_v1') rows in planner_worksheets are preserved
   in the database but rejected from download/reuse after generator bump to 'source_exercise_v2'.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from app.document_lookup import DocumentLookup
from app.exercise_pdf import (
    PUA_GLYPH_MAP,
    _normalize_item,
    clean_for_pdf_export,
    generate_exercise_worksheet_pdf,
)
from app.learning import (
    LearningService,
    _clean_question_prefix,
    _find_declared_identifiers,
    _find_referenced_dependencies,
    _strip_trailing_ornaments_and_footer,
)
from app.planner_topic_bridge import (
    CURRENT_WORKSHEET_GENERATOR_VERSION,
    _connect,
    add_weekly_lesson,
    create_or_update_worksheet,
    get_worksheet_pdf,
    is_valid_worksheet,
    list_weekly_lessons,
)
from app.server import app


# ============================================================================
# 1. PARSER EXTRACTION: BULLET PREFIX, FOOTER STRIPPING & LINKED CONTEXT
# ============================================================================


def test_clean_question_prefix_removes_bullets_preserves_numbering():
    """Leading decorative bullets (PUA glyphs, bullets, dashes) are stripped from question prefix."""
    raw_q7 = "\uf076Câu 7: Giả sử ta khai báo mảng array 3 chiều: int array[2][3][4]."
    clean_q7 = _clean_question_prefix(raw_q7)
    assert clean_q7.startswith("Câu 7: Giả sử ta khai báo mảng array 3 chiều:")
    assert "\uf076" not in clean_q7

    # Other bullet variants
    assert _clean_question_prefix("• Câu 1: Con trỏ là gì?").startswith("Câu 1:")
    assert _clean_question_prefix("▪ Bài 2: Nhập mảng").startswith("Bài 2:")
    assert _clean_question_prefix("- Exercise 3: Explain").startswith("Exercise 3:")


def test_strip_trailing_ornaments_and_slide_footer():
    """Standalone layout ornament arrows and slide footers are stripped from question end."""
    q_with_footer = (
        "Câu 8: Cho biết array[0][0] có nghĩa là gì?\n"
        "\uf0e8\n"
        "Dữ liệu kiểu con trỏ (nâng cao)"
    )
    cleaned = _strip_trailing_ornaments_and_footer(
        q_with_footer,
        clean_title="KTLT C14 ConTroNangCao",
        recurring_footers={"Dữ liệu kiểu con trỏ (nâng cao)"},
    )
    assert cleaned == "Câu 8: Cho biết array[0][0] có nghĩa là gì?"
    assert "\uf0e8" not in cleaned
    assert "con trỏ (nâng cao)" not in cleaned


def test_extraction_of_real_source_shaped_linked_questions():
    """Extracting a real slide chunk containing bullet prefix, code declaration in Q7,

    trailing arrow, Q8 referencing array[0][0], and slide footer.
    Both questions are recognized in order, footers are removed, and Q8 binds Q7's declaration.
    """
    chunk_text = (
        "VC\n&\nBB\n39\n39\nBài tập\n"
        "\uf076Câu 7: Giả sử ta khai báo mảng array 3 chiều: \n"
        "int array[2][3][4]. Cho biết cấu trúc của mảng \n"
        "này đối với trình biên dịch C.\n"
        "\uf0e8\n"
        "Câu 8: Cho biết array[0][0] có nghĩa là gì?\n"
        "\uf0e8\n"
        "Dữ liệu kiểu con trỏ (nâng cao)"
    )
    chunks = [
        {"id": 63149, "page_num": 39, "chunk_index": 0, "text": chunk_text},
        # Another chunk with same footer to establish recurring status
        {"id": 63150, "page_num": 40, "chunk_index": 0, "text": "Bài tập\nDữ liệu kiểu con trỏ (nâng cao)"},
    ]
    doc = {
        "id": 123,
        "filename": "cslt/tieng Viet/KTLT_C14_ConTroNangCao.pdf",
        "clean_title": "KTLT C14 ConTroNangCao",
    }

    mock_dl = MagicMock(spec=DocumentLookup)
    mock_dl.pdf_path.return_value = Path("/fake/path.pdf")
    ls = LearningService(document_lookup=mock_dl, practice_service=MagicMock())

    exercises, answers, ans_counts = ls._extract_exercises_and_answers(chunks, doc)

    assert len(exercises) == 2
    ex7, ex8 = exercises[0], exercises[1]

    # Q7 checks
    assert ex7["id"] == "7"
    assert ex7["question"].startswith("Câu 7: Giả sử ta khai báo mảng array 3 chiều:")
    assert "int array[2][3][4]" in ex7["question"]
    assert "\uf076" not in ex7["question"]
    assert "\uf0e8" not in ex7["question"]
    assert "Dữ liệu kiểu con trỏ (nâng cao)" not in ex7["question"]
    assert ex7["source"]["page_num"] == 39

    # Q8 checks
    assert ex8["id"] == "8"
    assert ex8["question"] == "Câu 8: Cho biết array[0][0] có nghĩa là gì?"
    assert "\uf0e8" not in ex8["question"]
    assert "Dữ liệu kiểu con trỏ (nâng cao)" not in ex8["question"]
    # Verifiable source context is bound to Q8
    assert ex8["source_context"] is not None
    assert "int array[2][3][4]" in ex8["source_context"]
    assert ex8["source"]["source_context"] == ex8["source_context"]
    assert not ex8.get("is_dependent_unresolved")


def test_standalone_question_remains_unchanged():
    """A standalone, fully specified question retains its exact content without spurious context."""
    chunk_text = (
        "Bài tập\n"
        "Bài 1: Viết chương trình nhập xuất mảng một chiều các số thực."
    )
    chunks = [{"id": 1, "page_num": 130, "chunk_index": 0, "text": chunk_text}]
    doc = {"id": 125, "filename": "book-c.pdf", "clean_title": "book-c"}

    mock_dl = MagicMock(spec=DocumentLookup)
    mock_dl.pdf_path.return_value = Path("/fake/path.pdf")
    ls = LearningService(document_lookup=mock_dl, practice_service=MagicMock())

    exercises, answers, ans_counts = ls._extract_exercises_and_answers(chunks, doc)
    assert len(exercises) == 1
    assert exercises[0]["question"] == "Bài 1: Viết chương trình nhập xuất mảng một chiều các số thực."
    assert exercises[0]["source_context"] is None
    assert not exercises[0].get("is_dependent_unresolved")


def test_unresolved_dependent_question_kept_in_review_state():
    """If a question uses an array/variable without a declaration anywhere in the chunk/premise,

    it is marked unresolved and excluded from self-contained exercises.
    """
    chunk_text = (
        "Bài tập\n"
        "Câu 99: Cho biết array_x[0][0] có giá trị bao nhiêu?"
    )
    chunks = [{"id": 2, "page_num": 50, "chunk_index": 0, "text": chunk_text}]
    doc = {"id": 100, "filename": "test.pdf", "clean_title": "test"}

    mock_dl = MagicMock(spec=DocumentLookup)
    mock_dl.pdf_path.return_value = None
    ls = LearningService(document_lookup=mock_dl, practice_service=MagicMock())

    exercises, answers, ans_counts = ls._extract_exercises_and_answers(chunks, doc)
    assert len(exercises) == 1
    assert exercises[0]["is_dependent_unresolved"] is True
    assert exercises[0]["source_context"] is None


def test_preserves_mathematical_operators_and_code():
    """Mathematical operators (+, -, *, /, ==, &, [, ], {, }) and C code are preserved faithfully."""
    chunk_text = (
        "Bài tập\n"
        "Câu 9: Xét xem biểu thức so sánh nào sau đây đúng:\n"
        "a. array[0][0] == &array[0][0][0];\n"
        "b. array[0][1] == array[0][0][1];\n"
        "c. array[0][1] == &array[0][1][0];"
    )
    chunks = [{"id": 3, "page_num": 40, "chunk_index": 0, "text": chunk_text}]
    doc = {"id": 123, "filename": "test.pdf", "clean_title": "test"}

    mock_dl = MagicMock(spec=DocumentLookup)
    mock_dl.pdf_path.return_value = None
    ls = LearningService(document_lookup=mock_dl, practice_service=MagicMock())

    exercises, _, _ = ls._extract_exercises_and_answers(chunks, doc)
    assert len(exercises) == 1
    q = exercises[0]["question"]
    assert "array[0][0] == &array[0][0][0];" in q
    assert "==" in q
    assert "&" in q
    assert "[" in q and "]" in q


# ============================================================================
# 2. PDF EXPORT: PUA GLYPH NORMALIZATION, NO NUL, AND CONTEXT RENDERING
# ============================================================================


def test_clean_for_pdf_export_normalizes_pua_and_strips_nul():
    """Known PUA glyphs map to readable Unicode; stray NUL bytes and unmapped PUA are cleaned."""
    text_with_pua = "Câu 7: \uf076 bullet and \uf0e8 arrow and \x00 nul"
    cleaned = clean_for_pdf_export(text_with_pua)
    assert "•" in cleaned
    assert "→" in cleaned
    assert "\x00" not in cleaned
    assert "\uf076" not in cleaned
    assert "\uf0e8" not in cleaned


def test_pdf_worksheet_export_contains_no_nul_bytes_and_renders_context():
    """Generated PDF worksheet contains zero NUL (\\x00) bytes across all pages,

    renders readable Unicode arrows/bullets, and includes the source context block.
    """
    questions = [
        {
            "question": "Câu 7: Giả sử ta khai báo mảng array 3 chiều:\nint array[2][3][4]. Cho biết cấu trúc của mảng này đối với trình biên dịch C.",
            "source": {
                "title": "KTLT C14 ConTroNangCao",
                "page_num": 39,
                "snippet": "Câu 7: Giả sử ta khai báo mảng array 3 chiều...",
            },
            "answer": None,
            "answer_status": "missing",
        },
        {
            "question": "Câu 8: Cho biết array[0][0] có nghĩa là gì?",
            "source_context": "Giả sử ta khai báo mảng array 3 chiều: int array[2][3][4]. Cho biết cấu trúc của mảng này đối với trình biên dịch C.",
            "source": {
                "title": "KTLT C14 ConTroNangCao",
                "page_num": 39,
                "snippet": "Câu 8: Cho biết array[0][0] có nghĩa là gì?",
            },
            "answer": None,
            "answer_status": "missing",
        },
    ]

    pdf_bytes = generate_exercise_worksheet_pdf(
        topic="Mảng",
        course="Kỹ thuật lập trình",
        difficulty="medium",
        title="Bài tập tự học 1h30p: Mảng",
        questions=questions,
    )

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) >= 2

    full_pdf_text = ""
    for page_idx, page in enumerate(doc):
        page_text = page.get_text()
        # ABSOLUTE RULE: Zero NUL bytes
        assert "\x00" not in page_text, f"NUL byte found on page {page_idx + 1}"
        full_pdf_text += page_text + "\n"

    # Verifiable source context is preserved in the rendered PDF
    assert "Ngữ cảnh từ tài liệu nguồn" in full_pdf_text
    assert "int array[2][3][4]" in full_pdf_text
    assert "Câu 8: Cho biết array[0][0] có nghĩa là gì?" in full_pdf_text

    # Missing answer status is explicit
    assert "Chưa có đáp án chính thức trong tài liệu nguồn" in full_pdf_text
    # Citation is preserved
    assert "KTLT C14 ConTroNangCao" in full_pdf_text
    assert "Trang 39" in full_pdf_text


def test_pdf_worksheet_with_decorative_pua_glyphs_renders_readable_unicode():
    """If questions or choices contain PUA glyphs (\\uf0e8, \\uf076), PDF export renders them

    as readable Unicode (→, •) without NUL characters.
    """
    questions = [
        {
            "question": "Chọn hướng chuyển đổi đúng: \uf0e8 A sang B \uf076",
            "choices": [
                {"label": "A", "text": "Bước 1 \uf0e8 Bước 2"},
                {"label": "B", "text": "Bước 2 \uf0e8 Bước 3"},
            ],
            "source": {"title": "Tài liệu", "page_num": 1, "snippet": "Trích đoạn"},
            "answer": "A",
            "answer_status": "source_document",
        }
    ]

    pdf_bytes = generate_exercise_worksheet_pdf(
        topic="Kỹ thuật",
        questions=questions,
    )

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    for page in doc:
        txt = page.get_text()
        assert "\x00" not in txt
        full_text += txt + "\n"
    assert "→" in full_text
    assert "•" in full_text


# ============================================================================
# 3. COMPATIBILITY & GENERATOR VERSION BUMP TO source_exercise_v2
# ============================================================================


def test_generator_version_is_v2():
    """CURRENT_WORKSHEET_GENERATOR_VERSION is bumped to source_exercise_v2."""
    assert CURRENT_WORKSHEET_GENERATOR_VERSION == "source_exercise_v2"


def test_stale_v1_worksheets_retained_in_db_but_blocked_from_reuse(tmp_path):
    """Old source_exercise_v1 rows remain stored in the DB but is_valid_worksheet and

    get_worksheet_pdf return False/None to prevent serving stale broken PDF output.
    """
    db_path = tmp_path / "version_compat.db"
    legacy_ws_id = "ws_legacy_v1_test"

    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS planner_worksheets (
                worksheet_id TEXT PRIMARY KEY,
                course TEXT,
                topic TEXT,
                lesson_title TEXT,
                questions_json TEXT,
                pdf_blob BLOB,
                created_at TEXT,
                generator_version TEXT DEFAULT ''
            )
            """
        )
        # Insert a source_exercise_v1 row
        conn.execute(
            """
            INSERT INTO planner_worksheets (
                worksheet_id, course, topic, lesson_title, questions_json, pdf_blob, created_at, generator_version
            ) VALUES (?, 'KTLT', 'Mảng', 'Mảng', ?, ?, '2026-11-10T00:00:00Z', 'source_exercise_v1')
            """,
            (
                legacy_ws_id,
                json.dumps([{"question": "Old question with NUL glyphs"}]),
                b"%PDF-old-v1-blob",
            ),
        )
        conn.commit()

    with _connect(db_path) as conn:
        # Row must be preserved in DB (do not delete owner data)
        row = conn.execute("SELECT * FROM planner_worksheets WHERE worksheet_id = ?", (legacy_ws_id,)).fetchone()
        assert row is not None
        assert row["generator_version"] == "source_exercise_v1"
        assert bytes(row["pdf_blob"]) == b"%PDF-old-v1-blob"

        # Blocked from is_valid_worksheet
        assert is_valid_worksheet(conn, legacy_ws_id) is False

    # Blocked from get_worksheet_pdf (returns None -> 404 in endpoint)
    assert get_worksheet_pdf(db_path, legacy_ws_id) is None


def test_new_worksheet_is_created_with_v2(tmp_path):
    """Newly generated worksheets carry generator_version source_exercise_v2."""
    db_path = tmp_path / "v2_create.db"

    mock_ls = MagicMock()
    mock_ls.exercises.return_value = {
        "items": [
            {
                "question": "Câu 1: int a[10];",
                "source": {"title": "C Book", "page_num": 5, "snippet": "int a[10];"},
                "answer": None,
                "answer_status": "missing",
            }
        ]
    }

    res = create_or_update_worksheet(
        db_path,
        course="C Programming",
        lesson_title="Arrays",
        classified_topic="Mảng",
        question_count=1,
        learning_service=mock_ls,
    )
    ws_id = res["worksheet_id"]
    assert ws_id is not None

    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM planner_worksheets WHERE worksheet_id = ?", (ws_id,)).fetchone()
        assert row is not None
        assert row["generator_version"] == "source_exercise_v2"
        assert is_valid_worksheet(conn, ws_id) is True

    record = get_worksheet_pdf(db_path, ws_id)
    assert record is not None
    assert record["worksheet_id"] == ws_id
    assert len(record["pdf_bytes"]) > 0


# ============================================================================
# 4. PACKET07 EDGE PROBE COUNTEREXAMPLE REGRESSIONS
# ============================================================================


def test_substantive_repeated_condition_preserved_not_treated_as_footer():
    """Two question chunks ending with substantive condition (Giả sử n > 0.) preserve that condition."""
    chunk1 = {"id": 1, "page_num": 1, "chunk_index": 0, "text": "Câu 1: Tính tổng của dãy.\nGiả sử n > 0."}
    chunk2 = {"id": 2, "page_num": 2, "chunk_index": 0, "text": "Câu 2: Tính tổng của dãy.\nGiả sử n > 0."}
    doc = {"id": 999, "filename": "math.pdf", "clean_title": "math"}
    ls = LearningService(document_lookup=MagicMock(spec=DocumentLookup), practice_service=MagicMock())
    exs, _, _ = ls._extract_exercises_and_answers([chunk1, chunk2], doc)

    assert len(exs) == 2
    assert "Giả sử n > 0." in exs[0]["question"]
    assert "Giả sử n > 0." in exs[1]["question"]


def test_meaningful_last_sentence_preserved_not_dropped_by_phrase_prefix():
    """Meaningful technical sentence ('Dữ liệu kiểu int có 4 byte.') is preserved."""
    raw = "Câu 1: Xác định kích thước.\nDữ liệu kiểu int có 4 byte."
    clean_sentence = _strip_trailing_ornaments_and_footer(raw)
    assert "Dữ liệu kiểu int có 4 byte." in clean_sentence
    assert clean_sentence == raw


def test_unknown_pua_not_silently_deleted():
    """Unknown PUA characters (e.g. \\ue123) are not silently dropped in clean_for_pdf_export."""
    pua_clean = clean_for_pdf_export("f(x) = x\ue123y")
    assert "\ue123" in pua_clean
    assert pua_clean == "f(x) = x\ue123y"


def test_partially_unresolved_dependencies_marked_unresolved():
    """Câu 1 declaring only int a[3] followed by Câu 2 using a[0] + b[0] leaves b undefined.

    All detected dependencies must be satisfied by bound context; otherwise is_dependent_unresolved=True.
    """
    chunk_dep = {"id": 3, "page_num": 10, "chunk_index": 0, "text": "Câu 1: Cho int a[3];\nCâu 2: Tính a[0] + b[0]?"}
    doc = {"id": 999, "filename": "test.pdf", "clean_title": "test"}
    ls = LearningService(document_lookup=MagicMock(spec=DocumentLookup), practice_service=MagicMock())
    exs_dep, _, _ = ls._extract_exercises_and_answers([chunk_dep], doc)

    assert len(exs_dep) == 2
    assert exs_dep[1]["is_dependent_unresolved"] is True

    # If both a and b are declared, both dependencies are satisfied
    chunk_full = {"id": 4, "page_num": 11, "chunk_index": 0, "text": "Câu 1: Cho int a[3]; int b[3];\nCâu 2: Tính a[0] + b[0]?"}
    exs_full, _, _ = ls._extract_exercises_and_answers([chunk_full], doc)
    assert len(exs_full) == 2
    assert exs_full[1]["is_dependent_unresolved"] is False
    assert "int a[3]" in exs_full[1]["source_context"]
    assert "int b[3]" in exs_full[1]["source_context"]

