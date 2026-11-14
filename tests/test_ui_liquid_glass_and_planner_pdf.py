"""Tests for the Pure Black + Apple Liquid Glass UI redesign, ARYM branding,
one-time button spotlight onboarding tour, topic exercise PDF export with
answer key at the end, and 1h30p (1 subject/day) timetable auto-scheduler with PDF attachments.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
from fastapi.testclient import TestClient

from app.db.schemas import practice_migrations
from app.exercise_pdf import generate_exercise_worksheet_pdf
from app.planner_topic_bridge import (
    _connect,
    add_weekly_lesson,
    auto_schedule_90m_one_subject_per_day,
    classify_weekly_lesson,
    create_or_update_worksheet,
    get_worksheet_pdf,
    list_weekly_lessons,
)
from app.practice import PracticeService
from app.server import app

ROOT = Path(__file__).resolve().parents[1]


def test_branding_arym_and_liquid_glass_structure():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    liquid_css = (ROOT / "static" / "liquid-glass.css").read_text(encoding="utf-8")
    spatial_css = (ROOT / "static" / "spatial-layout.css").read_text(encoding="utf-8")
    accounts_js = (ROOT / "static" / "accounts.js").read_text(encoding="utf-8")

    # 1. Branding: ARYM without "AI", with "Adaptive Revision Yields Mastery" subtitle
    assert "<h2>ARYM</h2>" in html
    assert "Adaptive Revision Yields Mastery" in html
    assert "<h2>ARYM AI</h2>" not in html

    # 2. Pure black + Apple Liquid Glass optical filter, stylesheet & Fixed Layout 3 (Bottom Dock + Bento Spatial Hub)
    assert '/static/liquid-glass.css' in html
    assert '/static/spatial-layout.css' in html
    assert '/static/spatial-layout.js' in html
    assert 'data-spatial-layout="bottom-dock"' in html
    assert 'id="btn-spatial-layout-cycle"' not in html
    assert 'id="apple-liquid-glass-lens"' in html
    assert "<feTurbulence" in html
    assert "<feDisplacementMap" in html
    assert "<feSpecularLighting" in html
    assert "#000000" in liquid_css
    assert "backdrop-filter:" in liquid_css
    assert "--lg-surface:" in liquid_css
    assert "arym-bento-grid" in html
    assert "arym-bento-card" in html
    assert "bottom: 16px !important;" in spatial_css

    # 3. No unrequested clutter text ("Chế độ cục bộ · đăng nhập để lưu theo tài khoản")
    assert "Chế độ cục bộ" not in html
    assert "Chế độ cục bộ" not in accounts_js
    assert "đăng nhập để lưu theo tài khoản" not in html
    assert "đăng nhập để lưu theo tài khoản" not in accounts_js


def test_button_spotlight_onboarding_tour_structure_and_one_time_account_rule(tmp_path, monkeypatch):
    tour_js = (ROOT / "static" / "onboarding-tour.js").read_text(encoding="utf-8")
    tour_css = (ROOT / "static" / "onboarding-tour.css").read_text(encoding="utf-8")

    # Small banner wrapping around button + directional arrow + separate callout frame
    assert "arym-tour-button-banner" in tour_js
    assert "arym-tour-arrow-svg" in tour_js
    assert "arym-tour-arrow-path" in tour_js
    assert "arym-tour-callout-frame" in tour_js
    assert ".arym-tour-button-banner" in tour_css
    assert ".arym-tour-callout-frame" in tour_css

    # Verify server-side per-account one-time rule: shows on 1st use, never on 2nd use
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(tmp_path / "accounts"))

    with TestClient(app, headers={"X-Study-Request": "1"}) as client:
        reg = client.post(
            "/api/account/register",
            json={"username": "new_student_tour", "password": "private-example-123", "display_name": "Học viên Mới"},
        )
        assert reg.status_code == 200

        # First entry on newly created account -> first_use is True
        first = client.get("/api/account/features/qa")
        assert first.status_code == 200
        assert first.json()["first_use"] is True

        # Onboarding tour marks guided -> subsequent visits must return first_use = False
        guided = client.post("/api/account/features/qa/guided")
        assert guided.status_code == 200

        second = client.get("/api/account/features/qa")
        assert second.status_code == 200
        assert second.json()["first_use"] is False


def test_exercise_pdf_export_places_answer_key_separately_at_end():
    sample_questions = [
        {
            "id": "q1",
            "question": "Ánh xạ tuyến tính bảo toàn phép toán nào trên không gian vector?",
            "choices": [
                {"label": "A", "text": "Phép cộng vector và phép nhân vô hướng"},
                {"label": "B", "text": "Chỉ phép nhân hai vector tự do"},
                {"label": "C", "text": "Phép chia vô hướng khác không"},
                {"label": "D", "text": "Không bảo toàn phép toán nào"},
            ],
            "answer": "A",
            "answer_status": "source_document",
            "explanation": "Theo định nghĩa, ánh xạ tuyến tính bảo toàn tổ hợp tuyến tính (phép cộng và phép nhân vô hướng).",
            "source": {
                "title": "Giáo trình Đại số tuyến tính",
                "page_num": 42,
                "snippet": "Ánh xạ f: V -> W được gọi là tuyến tính nếu bảo toàn phép cộng và phép nhân với vô hướng.",
            },
        },
        {
            "id": "q2",
            "question": "Hạng của ma trận A là gì?",
            "choices": [
                {"label": "A", "text": "Tổng các phần tử trên đường chéo"},
                {"label": "B", "text": "Số chiều của không gian cột sinh bởi A"},
                {"label": "C", "text": "Số hàng bất kỳ của ma trận"},
                {"label": "D", "text": "Định thức của ma trận vuông"},
            ],
            "answer": "B",
            "answer_status": "source_document",
            "explanation": "Hạng của ma trận bằng số chiều của không gian vector sinh bởi các cột (hoặc hàng) của nó.",
            "source": {
                "title": "Giáo trình Đại số tuyến tính",
                "page_num": 58,
                "snippet": "Hạng của ma trận A, ký hiệu rank(A), là số chiều của không gian cột.",
            },
        },
    ]

    pdf_bytes = generate_exercise_worksheet_pdf(
        topic="Đại số tuyến tính",
        course="Toán cao cấp A2",
        difficulty="medium",
        questions=sample_questions,
    )
    assert pdf_bytes.startswith(b"%PDF-")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert len(doc) >= 2
        first_page_text = doc[0].get_text("text")
        last_page_text = doc[-1].get_text("text")

        # Part I (Questions) is on the first page without revealing "Đáp án đúng:"
        assert "ARYM" in first_page_text
        assert "Adaptive Revision Yields Mastery" in first_page_text
        assert "PHẦN I" in first_page_text
        assert "Ánh xạ tuyến tính bảo toàn phép toán nào" in first_page_text
        assert "Đáp án đúng:" not in first_page_text

        # Part II (Answer Key & Explanations) is on the final page with truthful source labels
        assert "PHẦN II" in last_page_text
        assert "BẢNG ĐÁP ÁN" in last_page_text
        assert "Câu 1 — Đáp án từ tài liệu gốc: A" in last_page_text
        assert "Câu 2 — Đáp án từ tài liệu gốc: B" in last_page_text
    finally:
        doc.close()


def test_weekly_lesson_topic_classification_and_90m_one_subject_per_day_scheduler(tmp_path):
    db_path = tmp_path / "timetable_test.db"

    # 1. Verify topic classification
    classified = classify_weekly_lesson(
        "Đại số tuyến tính",
        "Chương 3: Không gian vector và ánh xạ tuyến tính",
    )
    assert classified["classified_topic"] == "Đại số tuyến tính"
    assert "Toán" in classified["domain_label"]

    # 2. Seed confirmed timetable classes & weekly lessons for 2 subjects
    from app.planner_topic_bridge import _connect

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO timetable_confirmed (draft_id, course, weekday, start_time, end_time, room, created_at)
            VALUES
                ('d1', 'Đại số tuyến tính', 1, '07:30', '09:30', 'A101', '2026-11-10T00:00:00Z'),
                ('d1', 'Tâm lý học đại cương', 2, '13:30', '15:30', 'B202', '2026-11-10T00:00:00Z')
            """
        )
        for d in range(1, 8):
            conn.execute(
                """
                INSERT INTO personal_routines (weekday, start_time, end_time, title, activity_type, flexibility, created_at)
                VALUES (?, '19:00', '22:00', 'Tự học', 'study_window', 'study_window', '2026-11-10T00:00:00Z')
                """,
                (d,),
            )
        conn.commit()

    class _StubVerifiedLearningService:
        def exercises(self, topic: str, limit: int = 4, **kwargs):
            return {
                "items": [
                    {
                        "question": f"Bài tập nguồn cho chủ đề {topic}: Ánh xạ tuyến tính f(x+y)=f(x)+f(y).",
                        "answer": "Bảo toàn phép cộng vector và phép nhân vô hướng theo định nghĩa.",
                        "answer_status": "source_document",
                        "answer_source": {
                            "title": f"Giáo trình {topic}",
                            "page_num": 15,
                        },
                        "source": {
                            "title": f"Giáo trình {topic}",
                            "page_num": 15,
                            "snippet": f"Định nghĩa ánh xạ tuyến tính trong giáo trình {topic}.",
                        },
                    }
                ]
            }

    stub_library = _StubVerifiedLearningService()

    lesson_math = add_weekly_lesson(
        db_path,
        course="Đại số tuyến tính",
        lesson_title="Chương 3: Không gian vector và ánh xạ tuyến tính",
        status="upcoming",
        question_count=3,
        learning_service=stub_library,
    )
    lesson_psych = add_weekly_lesson(
        db_path,
        course="Tâm lý học đại cương",
        lesson_title="Bài 4: Trí nhớ ngắn hạn và trí nhớ dài hạn",
        status="studied",
        question_count=3,
        learning_service=stub_library,
    )

    assert lesson_math["classified_topic"] == "Đại số tuyến tính"
    assert lesson_math["worksheet_status"] == "not_generated"
    assert lesson_math["pdf_url"] is None
    assert "Trí nhớ" in lesson_psych["classified_topic"]
    assert lesson_psych["worksheet_status"] == "not_generated"

    ws_math = create_or_update_worksheet(
        db_path,
        course="Đại số tuyến tính",
        lesson_title="Chương 3: Không gian vector và ánh xạ tuyến tính",
        classified_topic=lesson_math["classified_topic"],
        question_count=3,
        learning_service=stub_library,
    )
    ws_psych = create_or_update_worksheet(
        db_path,
        course="Tâm lý học đại cương",
        lesson_title="Bài 4: Trí nhớ ngắn hạn và trí nhớ dài hạn",
        classified_topic=lesson_psych["classified_topic"],
        question_count=3,
        learning_service=stub_library,
    )
    assert ws_math["pdf_url"].endswith(".pdf")
    assert ws_psych["pdf_url"].endswith(".pdf")
    with _connect(db_path) as conn:
        conn.execute("UPDATE weekly_lessons SET worksheet_id = ? WHERE id = ?", (ws_math["worksheet_id"], lesson_math["id"]))
        conn.execute("UPDATE weekly_lessons SET worksheet_id = ? WHERE id = ?", (ws_psych["worksheet_id"], lesson_psych["id"]))
        conn.commit()

    lessons = list_weekly_lessons(db_path)
    assert len(lessons) == 2

    ws_pdf = get_worksheet_pdf(db_path, ws_math["worksheet_id"])
    assert ws_pdf is not None
    assert ws_pdf["pdf_bytes"].startswith(b"%PDF-")

    # 3. Run the 1h30p (90 minutes) / 1 subject per day auto-scheduler
    schedule_res = auto_schedule_90m_one_subject_per_day(db_path, session_minutes=90, learning_service=stub_library)
    assert schedule_res["status"] == "scheduled"
    assert schedule_res["session_duration_minutes"] == 90
    assert schedule_res["max_subjects_per_day"] == 1
    sessions = schedule_res["sessions"]
    assert len(sessions) == 7

    # Verify every session is 90 minutes, at most 1 subject per day, does not overlap classes, and has a PDF attached
    seen_days = set()
    for sess in sessions:
        day = sess["weekday"]
        assert day not in seen_days, "Each day must have at most 1 self-study subject session"
        seen_days.add(day)
        assert sess["duration_minutes"] == 90
        assert sess["worksheet_id"].startswith("ws_")
        assert sess["pdf_url"].startswith("/api/timetable/worksheets/ws_")
        if day == 1:
            # Monday class is 07:30-09:30 (+15m buffer -> 09:45), so session cannot start at 08:30
            assert sess["start_time"] >= "09:45"


def test_export_bundle_zip_and_pdf_endpoint(tmp_path, monkeypatch):
    test_db = tmp_path / "api_timetable.db"
    pr_db = tmp_path / "api_practice.db"
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(test_db))
    monkeypatch.setenv("PRACTICE_DB_PATH", str(pr_db))
    with sqlite3.connect(str(pr_db)) as conn:
        practice_migrations.apply(conn)

    sample_questions = [
        {
            "id": "q1",
            "question": "Định thức ma trận A tính như thế nào theo khai triển Laplace?",
            "choices": [
                {"label": "A", "text": "Khai triển theo một hàng hoặc một cột bất kỳ"},
                {"label": "B", "text": "Luôn bằng 0 với mọi ma trận vuông"},
                {"label": "C", "text": "Tổng các phần tử trên đường chéo chính"},
                {"label": "D", "text": "Không xác định với ma trận cấp lớn hơn 2"},
            ],
            "answer": "A",
            "explanation": "det(A) tính qua khai triển Laplace theo hàng hoặc cột.",
            "source": {
                "doc_id": 1,
                "chunk_id": 25,
                "title": "Giáo trình Đại số tuyến tính",
                "page_num": 25,
                "snippet": "Định thức và khai triển Laplace.",
                "pdf_url": None,
            },
        }
    ]

    class _StubVerifiedLearningService:
        def exercises(self, topic: str, limit: int = 4, **kwargs):
            return {
                "items": [
                    {
                        "question": f"Bài tập nguồn: Định thức ma trận A trong chủ đề {topic}.",
                        "answer": "det(A) tính qua khai triển Laplace.",
                        "answer_status": "source_document",
                        "source": {
                            "title": f"Giáo trình {topic}",
                            "page_num": 25,
                            "snippet": "Định thức và khai triển Laplace.",
                        },
                    }
                ]
            }

        def generate(self, *, topic: str, count: int = 3, difficulty: str = "medium", purpose: str = "practice", **kwargs):
            pid = "prac_bundle_det_1"
            with sqlite3.connect(str(pr_db)) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO practice_sessions (
                        practice_id, created_at, count, requested_count, difficulty,
                        purpose, topic, warning, processing_level, questions_json,
                        answers_json, metadata_json
                    ) VALUES (?, 1000.0, 1, ?, ?, ?, ?, NULL, 'careful', ?, '{}', '{}')
                    """,
                    (pid, int(count), difficulty, purpose, topic, json.dumps(sample_questions, ensure_ascii=False)),
                )
                conn.commit()
            return {
                "status": "ready",
                "practice_id": pid,
                "count": 1,
                "requested_count": int(count),
                "questions": [{k: v for k, v in q.items() if k not in ("answer", "explanation")} for q in sample_questions],
                "topic": topic,
            }

    from app import server as _srv
    monkeypatch.setattr(_srv, "learning_service", _StubVerifiedLearningService())
    monkeypatch.setattr(_srv, "practice_service", PracticeService(searcher=None, document_lookup=None, db_path=pr_db))

    with TestClient(app) as client:
        # 1. Add a manual class entry and declared study window
        r_cls = client.post(
            "/api/timetable/manual",
            json={
                "course": "Đại số tuyến tính",
                "weekday": 1,
                "start_time": "07:30",
                "end_time": "09:30",
                "room": "C301",
            },
        )
        assert r_cls.status_code == 200

        r_rt = client.post(
            "/api/timetable/planner/routines",
            json={
                "title": "Khung tự học",
                "weekday": 1,
                "start_time": "19:00",
                "end_time": "21:30",
                "activity_type": "study_window",
                "flexibility": "study_window",
            },
        )
        assert r_rt.status_code == 200

        # 2. Add a weekly lesson (not_generated on save) then generate exercise PDF on-demand
        r_les = client.post(
            "/api/timetable/planner/weekly-lessons",
            json={
                "course": "Đại số tuyến tính",
                "lesson_title": "Chương 2: Ma trận và định thức",
                "status": "upcoming",
                "question_count": 3,
            },
        )
        assert r_les.status_code == 200
        lesson_data = r_les.json()["lesson"]
        assert lesson_data["classified_topic"] == "Đại số tuyến tính"
        assert lesson_data["pdf_url"] is None
        assert lesson_data["exercise_status"] == "not_generated"

        r_gen = client.post(f"/api/timetable/planner/weekly-lessons/{lesson_data['id']}/exercise/generate")
        assert r_gen.status_code == 200
        practice_id = r_gen.json()["practice_id"]
        assert practice_id
        pdf_url = f"/api/learning/export-pdf/{practice_id}"

        # 3. Download the attached exercise PDF
        r_pdf = client.get(pdf_url)
        assert r_pdf.status_code == 200
        assert r_pdf.headers["content-type"].startswith("application/pdf")
        assert r_pdf.content.startswith(b"%PDF-")

        # 4. Run 1h30p (1 subject/day) auto-scheduler via API
        r_sched = client.post(
            "/api/timetable/planner/auto-schedule-courses",
            json={"session_minutes": 90},
        )
        assert r_sched.status_code == 200
        sched_data = r_sched.json()
        assert sched_data["session_duration_minutes"] == 90
        assert sched_data["max_subjects_per_day"] == 1
        assert sched_data["scheduled_count"] >= 1

        # 5. Export the .zip bundle containing .ics + all PDF worksheets
        r_zip = client.get(
            "/api/timetable/export-bundle.zip?start_date=2026-11-09&end_date=2026-12-20&reminder_minutes=60"
        )
        assert r_zip.status_code == 200
        assert r_zip.headers["content-type"].startswith("application/zip")

        with zipfile.ZipFile(io.BytesIO(r_zip.content), "r") as zf:
            names = zf.namelist()
            assert "thoi-khoa-bieu-arym.ics" in names
            assert "HUONG_DAN_DINH_KEM_LICH.txt" in names
            pdf_files = [n for n in names if n.startswith("bai-tap-pdf/") and n.endswith(".pdf")]
            assert len(pdf_files) >= 1
            ics_text = zf.read("thoi-khoa-bieu-arym.ics").decode("utf-8")
            unfolded_ics = ics_text.replace("\r\n ", "").replace("\n ", "")
            assert "ATTACH;FMTTYPE=application/pdf:" in unfolded_ics
            assert "/api/learning/export-pdf/" in unfolded_ics
