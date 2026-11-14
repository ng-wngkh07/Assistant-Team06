"""Comprehensive behavioral regressions for DATA01/DATA02, CONTENT01, and PLANNER01.

Verifies:
1. DATA01/DATA02: dev.py launcher defaults to local profile for serve/check, demo profile
   for demo command, preserves explicit root src/data when resources descriptor exists,
   and does not install a pack by default on check/serve.
2. CONTENT01:
   - Topic exercise synthesis rejects fabricated fallback questions and preserves exact
     source stem, answer, and provenance.
   - Missing answers are explicit ("Chưa có đáp án chính thức / Unavailable").
   - Unsupported weekly lessons save without PDF worksheets (readable unavailable status)
     and auto-schedule/calendar export cleanly omit nonexistent PDF URLs.
   - /api/learning/export-pdf returns 404 for invalid explicit practice_id, never falls back,
     and does not trust raw client-supplied question/source dictionaries.
3. PLANNER01:
   - auto_schedule_90m honors declared study windows only (no sessions on undeclared days).
   - Avoids all retained existing sessions (locked, completed, manual, normal plans).
   - Never deletes non-ai_auto_90m or locked/completed work; replaces only own eligible entries.
   - Enforces 1 subject per day including retained sessions.
   - Explains unplaced courses with an explicit insufficient-space warning.
   - Repeated calls are idempotent and preserve existing manual/locked work.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.exercise_pdf import _normalize_item, generate_exercise_worksheet_pdf
from app.planner_topic_bridge import (
    _connect,
    _synthesize_grounded_questions_for_topic,
    add_weekly_lesson,
    auto_schedule_90m_one_subject_per_day,
    build_calendar_bundle_zip,
    create_or_update_worksheet,
    list_weekly_lessons,
)
from app.server import app


# ============================================================================
# DATA01 / DATA02 REGRESSIONS
# ============================================================================


def test_dev_launcher_defaults_profile_and_preserves_root_paths(tmp_path, monkeypatch):
    """Launcher defaults serve/check to local, preserves explicit root src/data."""
    from scripts import dev

    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    fake_src = fake_root / "src"
    fake_src.mkdir()
    fake_data = fake_root / "data"
    fake_data.mkdir()
    resources = fake_root / "resources"
    resources.mkdir()
    (resources / "knowledge-packs.json").write_text(
        json.dumps({
            "format": 1,
            "packs": {
                "native": {"path": "resources/demo.zip", "sha256": "a" * 64},
                "ollama": {"path": "resources/demo.zip", "sha256": "a" * 64},
            },
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(dev, "ROOT", fake_root)
    monkeypatch.delenv("AGENT_SRC_DIR", raising=False)
    monkeypatch.delenv("AGENT_DATA_DIR", raising=False)
    monkeypatch.delenv("KNOWLEDGE_PACK_PATH", raising=False)
    monkeypatch.delenv("KNOWLEDGE_PACK_SHA256", raising=False)

    # In local mode, explicit root src/data is preserved even when resources descriptor exists
    dev.configure("local")
    import os
    # Should NOT be pointed to user-library
    assert os.environ.get("AGENT_DATA_DIR") != str(fake_root / "data/user-library")
    assert dev._CONFIGURED_BUNDLED_PACK is None


def test_dev_main_command_line_parsing(monkeypatch):
    """Check/serve default to local; demo defaults to demo, calling dev.main()."""
    from scripts import dev
    import sys

    configured_profiles = []
    monkeypatch.setattr(dev, "configure", lambda p: configured_profiles.append(p))
    monkeypatch.setattr(dev, "check_environment", lambda **kwargs: True)

    # 1. 'check' without --profile defaults to 'local'
    monkeypatch.setattr(sys, "argv", ["dev.py", "check"])
    rc = dev.main()
    assert rc == 0
    assert configured_profiles[-1] == "local"

    # 2. 'demo' without --profile defaults to 'demo'
    monkeypatch.setattr(dev, "prepare_demo", lambda: "demo ok")
    monkeypatch.setattr(sys, "argv", ["dev.py", "demo"])
    rc_demo = dev.main()
    assert rc_demo == 0
    assert configured_profiles[-1] == "demo"


# ============================================================================
# CONTENT01 REGRESSIONS
# ============================================================================


class StubExerciseLibrary:
    def __init__(self, items=None):
        self._items = items or []

    def exercises(self, **kwargs):
        return {"items": self._items}


def test_no_fabricated_fallback_for_unknown_topic():
    """Unsupported topics return empty questions, never fabricated always-A filler."""
    res = _synthesize_grounded_questions_for_topic(
        topic="TOPIC_NOT_IN_INDEX_20261110",
        course="Unknown Course",
        lesson_title="Lesson Without Source",
        count=3,
        learning_service=StubExerciseLibrary(items=[]),
    )
    assert res == []


def test_preserve_exact_source_exercise_and_answer_provenance():
    """Source-backed exercises preserve exact question, answer, status, and source citation."""
    source_item = {
        "question": "Tính tích phân I = int_0^1 x dx.",
        "answer": "I = 1/2.",
        "answer_status": "source_document",
        "answer_source": {"title": "Giải tích I", "page_num": 105},
        "source": {"title": "Giải tích I", "page_num": 104, "snippet": "Bài tập tích phân xác định."},
    }
    lib = StubExerciseLibrary(items=[source_item])
    questions = _synthesize_grounded_questions_for_topic(
        topic="Tích phân",
        course="Toán",
        lesson_title="Tích phân xác định",
        count=1,
        learning_service=lib,
    )
    assert len(questions) == 1
    q = questions[0]
    assert q["question"] == "Tính tích phân I = int_0^1 x dx."
    assert q["answer"] == "I = 1/2."
    assert q["answer_status"] == "source_document"
    assert q["answer_source"]["title"] == "Giải tích I"
    assert q["source"]["title"] == "Giải tích I"


def test_missing_answer_is_explicit():
    """Missing exercise answer renders explicit unavailable label."""
    norm = _normalize_item(
        {
            "question": "Chứng minh định lý Bolzano-Weierstrass.",
            "source": {"title": "Giải tích cổ điển", "page_num": 50, "snippet": "Định lý dãy bị chặn."},
        },
        1,
    )
    ans = norm["answer"].lower()
    assert any(term in ans for term in ["chưa", "không có", "unavailable"])
    assert norm["answer_status"] == "missing"


def test_unsupported_weekly_lesson_saves_without_pdf(tmp_path):
    """Unsupported lesson saves on-demand without worksheet (not_generated), and explicit worksheet generation fails closed to unavailable."""
    db_path = tmp_path / "test_unsupported.db"
    lesson = add_weekly_lesson(
        db_path,
        course="Kinh tế lượng",
        lesson_title="Bài 9: Hồi quy chuỗi thời gian phi dừng",
        learning_service=StubExerciseLibrary(items=[]),
    )
    assert lesson["id"] is not None
    assert lesson["worksheet_id"] is None
    assert lesson["pdf_url"] is None
    assert lesson["worksheet_status"] == "not_generated"

    ws = create_or_update_worksheet(
        db_path,
        course="Kinh tế lượng",
        lesson_title="Bài 9: Hồi quy chuỗi thời gian phi dừng",
        classified_topic=lesson["classified_topic"],
        learning_service=StubExerciseLibrary(items=[]),
    )
    assert ws["worksheet_id"] is None
    assert ws["pdf_url"] is None
    assert ws["status"] == "unavailable"

    lessons = list_weekly_lessons(db_path)
    assert len(lessons) == 1
    assert lessons[0]["worksheet_id"] is None
    assert lessons[0]["pdf_url"] is None
    assert lessons[0]["worksheet_status"] == "not_generated"


def test_calendar_bundle_handles_lessons_without_worksheets(tmp_path):
    """Bundle zip contains no fake worksheet URLs or missing zip entries."""
    db_path = tmp_path / "test_bundle_no_ws.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_sessions (
                plan_id, title, course, weekday, start_time, end_time,
                duration_minutes, priority, source, completed, locked,
                session_index, total_sessions, notes, created_at,
                classified_topic, worksheet_id
            ) VALUES ('p1', 'Tự học Lịch sử', 'Lịch sử', 2, '14:00', '15:30', 90, 'high', 'ai_auto_90m', 0, 0, 1, 1, 'note', '2026-11-10', 'Lịch sử', '')
            """
        )
        conn.commit()

    bundle_bytes = build_calendar_bundle_zip(db_path, ics_bytes=b"BEGIN:VCALENDAR\nEND:VCALENDAR")
    import zipfile, io
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
        txt = zf.read("HUONG_DAN_DINH_KEM_LICH.txt").decode("utf-8")
        assert "Chưa có bài tập PDF" in txt
        assert "/api/timetable/worksheets/.pdf" not in txt
        assert "None.pdf" not in txt
        # No dummy PDF in zip
        pdf_members = [m for m in zf.namelist() if m.endswith(".pdf")]
        assert len(pdf_members) == 0


def test_export_pdf_invalid_practice_id_returns_404():
    """Explicit invalid practice ID returns 404, never a fallback."""
    with TestClient(app) as client:
        resp = client.post(
            "/api/learning/export-pdf",
            json={"practice_id": "nonexistent_practice_id_12345", "topic": "Đại số"},
        )
        assert resp.status_code == 404
        assert "không tồn tại" in resp.json().get("detail", "").lower()


def test_export_pdf_does_not_trust_client_questions(monkeypatch):
    """Raw client questions dictionary is ignored in favor of server learning_service."""
    fake_client_q = [
        {
            "question": "Client fabricated question?",
            "answer": "A",
            "source": {"title": "Fictional book", "page_num": 999, "snippet": "Fictional"},
        }
    ]
    from app import server as _srv

    # Configure server learning service to return 0 items for this unknown topic
    monkeypatch.setattr(_srv, "learning_service", StubExerciseLibrary(items=[]))

    with TestClient(app) as client:
        resp = client.post(
            "/api/learning/export-pdf",
            json={
                "topic": "Chủ đề chưa có trong thư viện",
                "questions": fake_client_q,
            },
        )
        # Must refuse with 422 because server has no exercises, rather than exporting the client's fake questions
        assert resp.status_code == 422
        assert "Chưa tìm thấy câu hỏi hoặc bài tập" in resp.json().get("detail", "")


# ============================================================================
# PLANNER01 REGRESSIONS
# ============================================================================


def test_auto90_honors_only_declared_study_windows_and_avoids_locked(tmp_path):
    """Auto90 only schedules on days with declared windows and avoids locked sessions."""
    db_path = tmp_path / "planner_test.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, worksheet_id, created_at)
            VALUES ('Triết học', 'Bài 1: Ý thức', 'Triết học', '', '2026-11-10')
            """
        )
        # Declare study window ONLY on Wednesday (day 3) 19:00 - 22:00
        conn.execute(
            """
            INSERT INTO personal_routines (weekday, start_time, end_time, title, activity_type, flexibility, created_at)
            VALUES (3, '19:00', '22:00', 'Tự học', 'study_window', 'study_window', '2026-11-10')
            """
        )
        # Existing locked session on Wednesday 19:00 - 20:00
        conn.execute(
            """
            INSERT INTO study_sessions (title, course, weekday, start_time, end_time, duration_minutes, source, locked, completed, created_at)
            VALUES ('Buổi đã khóa', 'Triết học', 3, '19:00', '20:00', 60, 'manual', 1, 0, '2026-11-10')
            """
        )
        conn.commit()

    res = auto_schedule_90m_one_subject_per_day(db_path)
    sessions = res["sessions"]
    # Exactly 1 session scheduled on Wednesday (20:00 to 21:30)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["weekday"] == 3
    assert s["start_time"] == "20:00"
    assert s["end_time"] == "21:30"
    assert s["duration_minutes"] == 90

    with _connect(db_path) as conn:
        all_db_sessions = [dict(r) for r in conn.execute("SELECT * FROM study_sessions").fetchall()]

    # Locked manual session remains intact
    assert any(x["title"] == "Buổi đã khóa" and x["locked"] == 1 for x in all_db_sessions)


def test_auto90_preserves_unlocked_manual_sessions(tmp_path):
    """Auto90 preserves unlocked manual sessions and does not overwrite or delete them."""
    db_path = tmp_path / "manual_test.db"
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO weekly_lessons (course, lesson_title, classified_topic, worksheet_id, created_at) VALUES ('Toán', 'Giải tích', 'Toán', '', '2026-11-10')"
        )
        conn.execute(
            "INSERT INTO personal_routines (weekday, start_time, end_time, title, activity_type, flexibility, created_at) VALUES (4, '14:00', '18:00', 'Tự học', 'study_window', 'study_window', '2026-11-10')"
        )
        conn.execute(
            "INSERT INTO study_sessions (title, course, weekday, start_time, end_time, duration_minutes, source, locked, completed, created_at) VALUES ('Luyện đề tay', 'Toán', 4, '14:00', '15:00', 60, 'manual', 0, 0, '2026-11-10')"
        )
        conn.commit()

    res = auto_schedule_90m_one_subject_per_day(db_path)
    with _connect(db_path) as conn:
        after = [dict(r) for r in conn.execute("SELECT * FROM study_sessions").fetchall()]

    assert any(x["title"] == "Luyện đề tay" and x["source"] == "manual" for x in after)
    auto_s = [x for x in after if x["source"] == "ai_auto_90m"]
    assert len(auto_s) == 1
    # Auto90 session must start at or after 15:00 (no overlap with manual session)
    assert auto_s[0]["start_time"] >= "15:00"


def test_auto90_enforces_one_subject_per_day_with_retained_session(tmp_path):
    """If a day already has a retained session for Course A, auto90 will not schedule Course B on that day."""
    db_path = tmp_path / "one_subj_test.db"
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO weekly_lessons (course, lesson_title, classified_topic, worksheet_id, created_at) VALUES ('Vật lý', 'Quang học', 'Vật lý', '', '2026-11-10')"
        )
        conn.execute(
            "INSERT INTO weekly_lessons (course, lesson_title, classified_topic, worksheet_id, created_at) VALUES ('Hóa học', 'Hóa hữu cơ', 'Hóa học', '', '2026-11-10')"
        )
        # Declare window on Friday (day 5) only
        conn.execute(
            "INSERT INTO personal_routines (weekday, start_time, end_time, title, activity_type, flexibility, created_at) VALUES (5, '13:00', '17:00', 'Tự học', 'study_window', 'study_window', '2026-11-10')"
        )
        # Retained session on Friday for 'Vật lý'
        conn.execute(
            "INSERT INTO study_sessions (title, course, weekday, start_time, end_time, duration_minutes, source, locked, completed, created_at) VALUES ('Thí nghiệm lý', 'Vật lý', 5, '13:00', '14:00', 60, 'manual', 1, 0, '2026-11-10')"
        )
        conn.commit()

    res = auto_schedule_90m_one_subject_per_day(db_path)
    sessions = res["sessions"]
    # Only 1 subject (Vật lý) can be placed on Friday; Hóa học cannot fit because Friday already has Vật lý
    assert all(s["course"] == "Vật lý" for s in sessions)
    assert "Hóa học" in res["unplaced_courses"]
    assert res["insufficient_space_warning"] is not None
    assert "Hóa học" in res["insufficient_space_warning"]


def test_auto90_is_idempotent_on_repeated_calls(tmp_path):
    """Calling auto90 multiple times produces the same clean schedule without duplicating or deleting manual sessions."""
    db_path = tmp_path / "repeat_test.db"
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO weekly_lessons (course, lesson_title, classified_topic, worksheet_id, created_at) VALUES ('Lập trình', 'Python OOP', 'Lập trình', '', '2026-11-10')"
        )
        conn.execute(
            "INSERT INTO personal_routines (weekday, start_time, end_time, title, activity_type, flexibility, created_at) VALUES (2, '19:00', '21:30', 'Tự học', 'study_window', 'study_window', '2026-11-10')"
        )
        conn.execute(
            "INSERT INTO study_sessions (title, course, weekday, start_time, end_time, duration_minutes, source, locked, completed, created_at) VALUES ('Manual Slot', 'Lập trình', 2, '21:00', '21:30', 30, 'manual', 0, 0, '2026-11-10')"
        )
        conn.commit()

    # First call
    res1 = auto_schedule_90m_one_subject_per_day(db_path)
    # Second call
    res2 = auto_schedule_90m_one_subject_per_day(db_path)

    assert len(res1["sessions"]) == len(res2["sessions"]) == 1
    assert res1["sessions"][0]["start_time"] == res2["sessions"][0]["start_time"] == "19:30"
    assert res1["sessions"][0]["end_time"] == res2["sessions"][0]["end_time"] == "21:00"

    with _connect(db_path) as conn:
        all_sessions = [dict(r) for r in conn.execute("SELECT * FROM study_sessions").fetchall()]

    assert any(x["title"] == "Manual Slot" for x in all_sessions)
    auto_sessions = [x for x in all_sessions if x["source"] == "ai_auto_90m"]
    assert len(auto_sessions) == 1
