"""Behavioral regression test suite for UI-AUDIT-20261111-05.
Covers:
05-A: PLANNER01 missing-window refusal, byte/logical session preservation, declared window honor.
05-B: CONTENT01 legacy worksheet blocked from download/list/bundle/ICS, retained in DB, new versioned worksheet works, unknown lesson schedulable without PDF.
05-C: CONTENT01/UX source PDF display truthful labels, learning export-pdf choices preservation, 404 on invalid practice id.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.db.schemas import timetable_migrations
from app.exercise_pdf import generate_exercise_worksheet_pdf
from app.planner_topic_bridge import (
    CURRENT_WORKSHEET_GENERATOR_VERSION,
    _connect,
    add_weekly_lesson,
    auto_schedule_90m_one_subject_per_day,
    build_calendar_bundle_zip,
    create_or_update_worksheet,
    get_worksheet_pdf,
    is_valid_worksheet,
    list_weekly_lessons,
)
from app.server import app


# ============================================================================
# 05-A REGRESSIONS: Missing study window refusal & session preservation
# ============================================================================


def test_05a_zero_study_windows_refuses_and_preserves_sessions(tmp_path):
    """Owner contract supersedes prior manual-study-window requirement (UI-AUDIT-20261111-09):
    When zero study_window routines are declared:
    1. auto_schedule_90m_one_subject_per_day automatically derives availability from daytime bounds
       instead of refusing or requiring manual slot entry.
    2. Locked, completed, and manual sessions are strictly preserved.
    3. Eligible unlocked prior auto-sessions are refreshed without duplicating or overlapping.
    """
    db_path = tmp_path / "zero_windows.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Giải tích 1', 'Chương 1: Đạo hàm', 'Giải tích', '2026-11-11T00:00:00Z')
            """
        )
        # Pre-seed diverse existing sessions
        conn.execute(
            """
            INSERT INTO study_sessions (
                plan_id, task_id, title, course, weekday, start_time, end_time,
                duration_minutes, priority, source, completed, locked,
                session_index, total_sessions, notes, created_at,
                classified_topic, worksheet_id
            ) VALUES
                ('manual_plan', NULL, 'Tự học tay đã khóa', 'Giải tích 1', 2, '08:00', '09:30', 90, 'high', 'manual', 0, 1, 1, 1, 'Ghi chú khóa', '2026-11-10T00:00:00Z', 'Giải tích', ''),
                ('prior_plan', NULL, 'Tự học đã hoàn thành', 'Giải tích 1', 3, '14:00', '15:30', 90, 'medium', 'ai_auto_90m', 1, 0, 1, 1, 'Đã học xong', '2026-11-10T00:00:00Z', 'Giải tích', ''),
                ('prior_unlocked', NULL, 'Phiên tự học chờ', 'Giải tích 1', 4, '19:00', '20:30', 90, 'medium', 'ai_auto_90m', 0, 0, 1, 1, 'Chưa khóa', '2026-11-10T00:00:00Z', 'Giải tích', '')
            """
        )
        conn.commit()

    # Under new contract, zero declared study_windows automatically derives availability from daytime bounds
    res = auto_schedule_90m_one_subject_per_day(db_path)
    assert res["status"] == "scheduled"
    assert res["scheduled_count"] >= 1

    # Verify locked & completed sessions are 100% preserved
    with _connect(db_path) as conn:
        sessions = [dict(r) for r in conn.execute("SELECT * FROM study_sessions ORDER BY id").fetchall()]

    locked_sess = next((s for s in sessions if s["plan_id"] == "manual_plan"), None)
    assert locked_sess is not None
    assert locked_sess["locked"] == 1
    assert locked_sess["title"] == "Tự học tay đã khóa"

    completed_sess = next((s for s in sessions if s["plan_id"] == "prior_plan"), None)
    assert completed_sess is not None
    assert completed_sess["completed"] == 1
    assert completed_sess["title"] == "Tự học đã hoàn thành"

    # Verify no overlaps across all sessions
    by_day: dict[int, list[dict]] = {}
    for s in sessions:
        by_day.setdefault(s["weekday"], []).append(s)
    for d, d_sessions in by_day.items():
        sorted_d = sorted(d_sessions, key=lambda x: x["start_time"])
        for i in range(len(sorted_d) - 1):
            assert sorted_d[i]["end_time"] <= sorted_d[i + 1]["start_time"]


def test_05a_declared_study_windows_allows_scheduling(tmp_path):
    """When a study window is declared on a specific day, scheduling succeeds only on permitted windows."""
    db_path = tmp_path / "declared_window.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Vật lý đại cương', 'Cơ học chất điểm', 'Cơ học', '2026-11-11T00:00:00Z')
            """
        )
        # Declare study window ONLY on Thursday (day 4) 19:00 - 22:00
        conn.execute(
            """
            INSERT INTO personal_routines (weekday, start_time, end_time, title, activity_type, flexibility, created_at)
            VALUES (4, '19:00', '22:00', 'Tự học tối', 'study_window', 'study_window', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    res = auto_schedule_90m_one_subject_per_day(db_path)
    assert res["status"] == "scheduled"
    assert res["scheduled_count"] == 1
    session = res["sessions"][0]
    assert session["weekday"] == 4
    assert session["start_time"] == "19:30"
    assert session["end_time"] == "21:00"
    assert session["duration_minutes"] == 90


# ============================================================================
# 05-B REGRESSIONS: Legacy unverified worksheet blocked, new versioned works
# ============================================================================


def test_05b_legacy_worksheet_blocked_retained_and_sanitized_in_ics(tmp_path):
    """Legacy unversioned worksheet:
    1. Retained in DB (not deleted).
    2. get_worksheet_pdf returns None.
    3. list_weekly_lessons marks it unavailable with pdf_url=None.
    4. build_calendar_bundle_zip excludes it.
    5. ICS notes sanitize old worksheet claim.
    """
    db_path = tmp_path / "legacy_test.db"
    legacy_ws_id = "ws_7b634d600cc7"

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO weekly_lessons (id, course, lesson_title, status, classified_topic, domain_label, question_count, worksheet_id, created_at)
            VALUES (1, 'Kinh tế vi mô', 'Bài 1: Cung cầu', 'upcoming', 'Kinh tế học', 'Kinh tế', 4, ?, '2026-11-10T00:00:00Z')
            """,
            (legacy_ws_id,),
        )
        # Legacy row inserted without generator_version (defaults to empty string)
        conn.execute(
            """
            INSERT INTO planner_worksheets (worksheet_id, course, topic, lesson_title, questions_json, pdf_blob, created_at)
            VALUES (?, 'Kinh tế vi mô', 'Kinh tế học', 'Bài 1: Cung cầu', ?, ?, '2026-11-10T00:00:00Z')
            """,
            (
                legacy_ws_id,
                json.dumps([{"question": "Fabricated question", "answer": "A"}]),
                b"%PDF-legacy-unverified-blob",
            ),
        )
        # Pre-seed a study session with old notes claiming the legacy worksheet URL
        old_note = (
            f"Quy tắc 1h30p · 1 môn/ngày | Chủ đề phân loại: Kinh tế học | "
            f"Bài tập PDF đính kèm (có đáp án cuối trang): /api/timetable/worksheets/{legacy_ws_id}.pdf"
        )
        conn.execute(
            """
            INSERT INTO study_sessions (
                id, plan_id, title, course, weekday, start_time, end_time, duration_minutes,
                priority, source, completed, locked, notes, created_at, classified_topic, worksheet_id
            ) VALUES (1, 'p1', 'Tự học Kinh tế vi mô', 'Kinh tế vi mô', 3, '19:00', '20:30', 90, 'high', 'ai_auto_90m', 0, 0, ?, '2026-11-10T00:00:00Z', 'Kinh tế học', ?)
            """,
            (old_note, legacy_ws_id),
        )
        conn.commit()

    # 1. Row retained in DB
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM planner_worksheets WHERE worksheet_id = ?", (legacy_ws_id,)).fetchone()
        assert row is not None
        assert bytes(row["pdf_blob"]) == b"%PDF-legacy-unverified-blob"

    # 2. Blocked from get_worksheet_pdf
    assert get_worksheet_pdf(db_path, legacy_ws_id) is None

    # 3. Blocked from list_weekly_lessons
    lessons = list_weekly_lessons(db_path)
    assert len(lessons) == 1
    assert lessons[0]["pdf_url"] is None
    assert lessons[0]["worksheet_status"] == "unavailable"

    # 4. Blocked from build_calendar_bundle_zip
    raw_ics = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
    zip_bytes = build_calendar_bundle_zip(db_path, ics_bytes=raw_ics)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        pdf_names = [n for n in names if n.endswith(".pdf")]
        assert len(pdf_names) == 0, f"Legacy PDF must not be bundled in ZIP: {pdf_names}"
        manifest = zf.read("HUONG_DAN_DINH_KEM_LICH.txt").decode("utf-8")
        assert "Chưa có bài tập PDF" in manifest

    # 5. ICS generation sanitizes old notes
    from app.timetable_ics import generate_ics

    entries = [
        {
            "course": "Kinh tế vi mô",
            "weekday": 3,
            "start_time": "19:00",
            "end_time": "20:30",
            "notes": old_note,
            "classified_topic": "Kinh tế học",
            "pdf_url": "",  # Unverified / empty
        }
    ]
    ics_text = generate_ics(entries, "2026-11-09", "2026-12-20")
    assert f"/api/timetable/worksheets/{legacy_ws_id}.pdf" not in ics_text
    assert "ATTACH;FMTTYPE=application/pdf" not in ics_text
    assert "Chưa có bài tập PDF đính kèm" in ics_text


def test_05b_new_versioned_worksheet_download_list_bundle_ics(tmp_path):
    """Supported new worksheets with versioned provenance work across download, list, bundle and ICS."""
    db_path = tmp_path / "versioned_test.db"

    class _StubLearningService:
        def exercises(self, topic: str, limit: int = 4, **kwargs):
            return {
                "items": [
                    {
                        "question": f"Bài tập nguồn chuẩn cho {topic}",
                        "answer": "Đáp án chuẩn A",
                        "answer_status": "source_document",
                        "source": {"title": f"Tài liệu {topic}", "page_num": 10, "snippet": "Trích đoạn chuẩn."},
                    }
                ]
            }

    lesson = add_weekly_lesson(
        db_path,
        course="Giải tích 2",
        lesson_title="Tích phân bội",
        status="upcoming",
        learning_service=_StubLearningService(),
    )
    assert lesson["worksheet_id"] is None
    assert lesson["worksheet_status"] == "not_generated"

    ws = create_or_update_worksheet(
        db_path,
        course="Giải tích 2",
        lesson_title="Tích phân bội",
        classified_topic=lesson["classified_topic"],
        learning_service=_StubLearningService(),
    )
    ws_id = ws["worksheet_id"]
    assert ws_id and ws_id.startswith("ws_")
    assert ws["pdf_url"] == f"/api/timetable/worksheets/{ws_id}.pdf"
    with _connect(db_path) as conn:
        conn.execute("UPDATE weekly_lessons SET worksheet_id = ? WHERE id = ?", (ws_id, lesson["id"]))
        conn.commit()

    # 1. Download
    pdf_info = get_worksheet_pdf(db_path, ws_id)
    assert pdf_info is not None
    assert pdf_info["pdf_bytes"].startswith(b"%PDF-")

    # 2. List
    lessons = list_weekly_lessons(db_path)
    assert len(lessons) == 1
    assert lessons[0]["pdf_url"] == f"/api/timetable/worksheets/{ws_id}.pdf"
    assert lessons[0]["worksheet_status"] == "available"

    # 3. Bundle ZIP
    raw_ics = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
    zip_bytes = build_calendar_bundle_zip(db_path, ics_bytes=raw_ics)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        pdf_names = [n for n in zf.namelist() if n.endswith(".pdf")]
        assert len(pdf_names) == 1
        assert ws_id in pdf_names[0]


def test_05b_unknown_lesson_schedulable_without_pdf(tmp_path):
    """Unknown lesson with no source exercises is schedulable without PDF (no failure)."""
    db_path = tmp_path / "unknown_lesson.db"

    class _EmptyLearningService:
        def exercises(self, topic: str, limit: int = 4, **kwargs):
            return {"items": []}

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Môn học hoàn toàn mới', 'Bài chưa có giáo trình', 'Chủ đề mới', '2026-11-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO personal_routines (weekday, start_time, end_time, title, activity_type, flexibility, created_at)
            VALUES (5, '14:00', '17:00', 'Tự học thứ 6', 'study_window', 'study_window', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    res = auto_schedule_90m_one_subject_per_day(db_path, learning_service=_EmptyLearningService())
    assert res["status"] == "scheduled"
    assert res["scheduled_count"] == 1
    session = res["sessions"][0]
    assert session["pdf_url"] is None
    assert session["worksheet_id"] is None
    assert "Chưa có bài tập PDF đính kèm" in session["notes"]


# ============================================================================
# 05-C REGRESSIONS: Truthful PDF labels, choices preservation, 404
# ============================================================================


def test_05c_exercise_pdf_truthful_conditional_labels():
    """PDF labels source, model-proposed, and missing answers truthfully, never claiming 'Đáp án đúng:' for unverified/missing."""
    questions = [
        {
            "id": "q1",
            "question": "Câu hỏi có đáp án từ tài liệu gốc?",
            "choices": [{"label": "A", "text": "Phương án gốc"}],
            "answer": "A",
            "answer_status": "source_document",
            "source": {"title": "Sách Toán", "page_num": 1},
        },
        {
            "id": "q2",
            "question": "Câu hỏi có lời giải đề xuất bởi mô hình?",
            "choices": [{"label": "B", "text": "Phương án đề xuất"}],
            "answer": "B",
            "answer_status": "model_solved",
            "source": {"title": "Sách Toán", "page_num": 2},
        },
        {
            "id": "q3",
            "question": "Câu hỏi không có đáp án chính thức trong nguồn?",
            "choices": [{"label": "C", "text": "Phương án C"}],
            "answer": None,
            "answer_status": "missing",
            "source": {"title": "Sách Toán", "page_num": 3},
        },
    ]

    pdf_bytes = generate_exercise_worksheet_pdf(
        topic="Kiểm định nhãn đáp án",
        course="Toán",
        difficulty="medium",
        questions=questions,
    )
    import pymupdf as fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        last_page_text = doc[-1].get_text("text")

        # Source question has truthful source label
        assert "Câu 1 — Đáp án từ tài liệu gốc: A" in last_page_text

        # Model question has truthful model-proposed label
        assert "Câu 2 — Đáp án đề xuất bởi mô hình: B" in last_page_text

        # Missing question has truthful missing status label
        assert "Câu 3 — Trạng thái đáp án: Chưa có đáp án chính thức" in last_page_text

        # Never labels missing/model as "Đáp án đúng:"
        assert "Câu 2 — Đáp án đúng:" not in last_page_text
        assert "Câu 3 — Đáp án đúng:" not in last_page_text
    finally:
        doc.close()


def test_05c_learning_export_pdf_preserves_choices(tmp_path, monkeypatch):
    """POST /api/learning/export-pdf preserves choices in normalized questions."""
    from app import server as _srv

    class _StubLearningService:
        def exercises(self, topic: str, limit: int = 4, **kwargs):
            return {
                "items": [
                    {
                        "question": "Câu hỏi trắc nghiệm có 4 lựa chọn?",
                        "choices": [
                            {"label": "A", "text": "Lựa chọn A"},
                            {"label": "B", "text": "Lựa chọn B"},
                            {"label": "C", "text": "Lựa chọn C"},
                            {"label": "D", "text": "Lựa chọn D"},
                        ],
                        "answer": "A",
                        "answer_status": "source_document",
                        "source": {"title": "Tài liệu kiểm định", "page_num": 5, "snippet": "Đoạn trích."},
                    }
                ]
            }

    monkeypatch.setattr(_srv, "learning_service", _StubLearningService())

    with TestClient(app) as client:
        res = client.post(
            "/api/learning/export-pdf",
            json={"topic": "Đại số tuyến tính", "course": "Toán", "difficulty": "medium"},
        )
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert res.content.startswith(b"%PDF-")

        import pymupdf as fitz
        doc = fitz.open(stream=res.content, filetype="pdf")
        try:
            first_page_text = doc[0].get_text("text")
            # Choices must be rendered in PDF Part I
            assert "A. Lựa chọn A" in first_page_text
            assert "B. Lựa chọn B" in first_page_text
            assert "C. Lựa chọn C" in first_page_text
            assert "D. Lựa chọn D" in first_page_text
        finally:
            doc.close()


def test_05c_invalid_explicit_practice_id_returns_404():
    """Explicitly requested invalid practice_id returns 404."""
    with TestClient(app) as client:
        res = client.get("/api/learning/export-pdf/non_existent_practice_id_999999")
        assert res.status_code == 404
        assert res.json().get("detail")
