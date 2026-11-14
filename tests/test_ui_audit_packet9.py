"""Behavioral regression test suite for UI-AUDIT-20261111-09.
Covers:
09-A: Automatic availability derived from daytime bounds without manual study windows;
      cross-midnight routine collision handling; fully busy week truthful reporting without overwrite.
09-B: Unified proposal/conflict-validation/confirmation workflow; transaction serialization;
      concurrent conflicting DB edit rejection without mutation.
09-C: Calendar session exercise on-demand generation, practice_id binding, source verification,
      truthful not_generated/unavailable states without fake filler questions.
09-D: Legacy API and route backward compatibility, draft preview, and account isolation.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

import pytest
from starlette.testclient import TestClient

from app.db.schemas import timetable_migrations
from app.planner_topic_bridge import (
    _connect,
    add_weekly_lesson,
    auto_schedule_90m_one_subject_per_day,
)
from app.study_planner import (
    confirm_study_plan,
    generate_study_plan,
    get_plan_draft,
    get_session,
    get_study_sessions,
    update_preferences,
    update_session_practice,
)
from app.server import app


# ==============================================================================
# 09-A: AUTOMATIC AVAILABILITY & CONFLICT HANDLING WITHOUT MANUAL STUDY WINDOWS
# ==============================================================================


def test_09a_automatic_availability_derived_from_daytime_bounds(tmp_path):
    """When zero study_window routines are declared:
    1. Availability is derived automatically from configured daytime bounds.
    2. Sessions are placed within daytime hours (never during night 23:00 - 06:00).
    3. Explanation transparently documents daytime availability assumption.
    """
    db_path = tmp_path / "auto_avail.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (
                id, earliest_study_time, latest_study_time, max_daily_study_minutes,
                preferred_session_minutes, min_session_minutes, updated_at
            ) VALUES (1, '08:00', '21:00', 240, 90, 45, '2026-11-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES
                ('Toán cao cấp', 'Chương 1: Ma trận và định thức', 'Đại số tuyến tính', '2026-11-11T00:00:00Z'),
                ('Triết học Mác - Lênin', 'Bài 2: Phép biện chứng duy vật', 'Triết học', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    res = auto_schedule_90m_one_subject_per_day(db_path, session_minutes=90)
    assert res["status"] == "scheduled"
    assert res["scheduled_count"] >= 2
    assert len(res["sessions"]) >= 2

    # Check each session is strictly within daytime bounds [08:00, 21:00]
    for sess in res["sessions"]:
        assert sess["start_time"] >= "08:00"
        assert sess["end_time"] <= "21:00"
        assert sess["duration_minutes"] == 90
        # No night scheduling
        assert sess["start_time"] >= "06:00"
        assert sess["end_time"] <= "23:00"

    assert "tự động phân bổ" in res["explanation"].lower() or "khung giờ" in res["explanation"].lower()


def test_09a_cross_midnight_sleep_routine_blocks_slots(tmp_path):
    """A sleep routine from 23:00 to 07:00 (crossing midnight) blocks the late night
    on day w and early morning on day w+1.
    """
    db_path = tmp_path / "cross_midnight.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (
                id, earliest_study_time, latest_study_time, max_daily_study_minutes,
                preferred_session_minutes, updated_at
            ) VALUES (1, '06:30', '23:30', 240, 90, '2026-11-11T00:00:00Z')
            """
        )
        # Sleep on day 1 crossing into day 2 (23:00 to 07:00)
        conn.execute(
            """
            INSERT INTO personal_routines (weekday, start_time, end_time, title, activity_type, flexibility, created_at)
            VALUES (1, '23:00', '07:00', 'Ngủ đêm', 'sleep', 'protected', '2026-11-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Vật lý đại cương', 'Quang học sóng', 'Vật lý', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    draft = generate_study_plan(include_courses=True, session_duration=90, db_path=db_path)
    sessions = draft["sessions"]
    assert len(sessions) >= 1

    # On day 2, no session may start before 07:00 (blocked by sleep ending at 07:00)
    for s in sessions:
        if s["weekday"] == 2:
            assert s["start_time"] >= "07:00"
        if s["weekday"] == 1:
            assert s["end_time"] <= "23:00"


def test_09a_fully_busy_week_reports_truthful_unscheduled_reasons(tmp_path):
    """When the entire permitted daytime schedule across all 7 days is occupied:
    1. System truthfully reports unplaced courses.
    2. Zero sessions are scheduled.
    3. Existing locked/manual work is NOT overwritten to manufacture slots.
    """
    db_path = tmp_path / "busy_week.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (id, earliest_study_time, latest_study_time, updated_at)
            VALUES (1, '08:00', '18:00', '2026-11-11T00:00:00Z')
            """
        )
        # Block 08:00 - 18:00 on every day with classes
        for d in range(1, 8):
            conn.execute(
                """
                INSERT INTO timetable_confirmed (draft_id, course, weekday, start_time, end_time, room, created_at)
                VALUES ('d1', 'Lớp học cả ngày', ?, '08:00', '18:00', 'H1', '2026-11-11T00:00:00Z')
                """,
                (d,),
            )
        # Pre-seed a locked manual session
        conn.execute(
            """
            INSERT INTO study_sessions (
                plan_id, task_id, title, course, weekday, start_time, end_time,
                duration_minutes, priority, source, completed, locked,
                session_index, total_sessions, notes, created_at, classified_topic, worksheet_id
            ) VALUES
                ('man_1', NULL, 'Phiên học đã khóa của tôi', 'Kinh tế lượng', 1, '19:00', '20:30', 90, 'high', 'manual', 0, 1, 1, 1, '', '2026-11-11T00:00:00Z', '', '')
            """
        )
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Hóa học đại cương', 'Bài 1: Cấu tạo nguyên tử', 'Hóa học', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    draft = generate_study_plan(include_courses=True, session_duration=90, db_path=db_path)
    assert len(draft["sessions"]) == 0
    assert "Hóa học đại cương" in draft["unplaced_courses"]
    assert len(draft["days_without_free_slot"]) >= 7

    # Attempting to confirm does not mutate or overwrite existing locked work
    confirmed = confirm_study_plan(draft["plan_id"], db_path=db_path)
    sessions = confirmed["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["title"] == "Phiên học đã khóa của tôi"
    assert sessions[0]["locked"] == 1


# ==============================================================================
# 09-B: UNIFIED PROPOSAL/CONFIRMATION WORKFLOW & CONFLICT REVALIDATION
# ==============================================================================


def test_09b_propose_preview_and_confirm_workflow(tmp_path):
    """Unified flow: propose first, preview draft sessions, then explicitly confirm."""
    db_path = tmp_path / "propose_confirm.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Lập trình mạng', 'Bài 3: Socket TCP và UDP', 'Mạng máy tính', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    # 1. Propose only
    draft_res = auto_schedule_90m_one_subject_per_day(db_path, session_minutes=90, propose_only=True)
    assert draft_res["status"] == "draft"
    plan_id = draft_res["plan_id"]
    assert draft_res["scheduled_count"] >= 1
    assert len(draft_res["sessions"]) >= 1

    # Verify nothing has been written to study_sessions yet
    with _connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM study_sessions").fetchone()[0]
    assert count == 0

    # 2. Inspect draft from store
    stored_draft = get_plan_draft(plan_id, db_path=db_path)
    assert stored_draft is not None
    assert stored_draft["status"] == "draft"

    # 3. Explicit confirm
    confirm_res = confirm_study_plan(plan_id, db_path=db_path)
    assert confirm_res["status"] == "ok"
    assert confirm_res["saved_count"] >= 1

    # Verify sessions are now written in study_sessions
    with _connect(db_path) as conn:
        saved_rows = conn.execute("SELECT * FROM study_sessions WHERE plan_id = ?", (plan_id,)).fetchall()
    assert len(saved_rows) >= 1

    # 4. Draft cannot be re-confirmed (status is now 'confirmed')
    with pytest.raises(ValueError, match="không còn hợp lệ"):
        confirm_study_plan(plan_id, db_path=db_path)


def test_09b_stale_concurrent_edit_rejected_without_mutation(tmp_path):
    """When a concurrent edit introduces a conflict after a draft was generated,
    confirm_study_plan detects the conflict during atomic transaction validation,
    rejects the stale draft, and prevents any corrupted or overlapping writes.
    """
    db_path = tmp_path / "stale_conflict.db"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (id, earliest_study_time, latest_study_time, updated_at)
            VALUES (1, '08:00', '18:00', '2026-11-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Cơ sở dữ liệu', 'Chương 2: Mô hình thực thể ER', 'Cơ sở dữ liệu', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    # Step 1: Create a draft plan
    draft = generate_study_plan(include_courses=True, session_duration=90, db_path=db_path)
    plan_id = draft["plan_id"]
    proposed_slot = draft["sessions"][0]
    conflicting_day = proposed_slot["weekday"]
    conflicting_start = proposed_slot["start_time"]
    conflicting_end = proposed_slot["end_time"]

    # Step 2: Simulate concurrent user action adding a fixed class at that exact slot
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO timetable_confirmed (draft_id, course, weekday, start_time, end_time, room, created_at)
            VALUES ('concurrent_d', 'Môn học mới thêm đột xuất', ?, ?, ?, 'B101', '2026-11-11T00:00:00Z')
            """,
            (conflicting_day, conflicting_start, conflicting_end),
        )
        conn.commit()

    # Step 3: Attempt to confirm the now-stale draft
    with pytest.raises(ValueError, match="trùng giờ|lỗi thời|xung đột"):
        confirm_study_plan(plan_id, db_path=db_path)

    # Step 4: Verify zero sessions from the stale draft were written into study_sessions
    with _connect(db_path) as conn:
        stale_rows = conn.execute("SELECT * FROM study_sessions WHERE plan_id = ?", (plan_id,)).fetchall()
    assert len(stale_rows) == 0


# ==============================================================================
# 09-C: SESSION EXERCISE ON-DEMAND BINDING & TRUTHFUL PROVENANCE
# ==============================================================================


def test_09c_session_exercise_on_demand_binding(tmp_path, monkeypatch):
    """Calendar session exercises use existing practice/learning generation service,
    bind practice_id to the session, and provide accurate exercise_status without
    fabricating fallback questions.
    """
    test_db = tmp_path / "session_exercise.db"
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(test_db))

    with _connect(test_db) as conn:
        cur = conn.execute(
            """
            INSERT INTO study_sessions (
                plan_id, task_id, title, course, weekday, start_time, end_time,
                duration_minutes, priority, source, completed, locked,
                session_index, total_sessions, notes, created_at,
                classified_topic, worksheet_id, practice_id, exercise_status
            ) VALUES (
                'p1', NULL, 'Tự học Kiến trúc máy tính', 'Kiến trúc máy tính',
                2, '09:00', '10:30', 90, 'high', 'ai_auto_90m', 0, 0, 1, 1,
                'Ghi chú', '2026-11-11T00:00:00Z', 'Vi xử lý', '', '', 'not_generated'
            )
            """
        )
        session_id = cur.lastrowid
        conn.commit()

    # Verify initial exercise state is not_generated
    sess = get_session(session_id, db_path=test_db)
    assert sess is not None
    assert sess["exercise_status"] == "not_generated"
    assert sess["practice_id"] is None

    # Mock learning_service on app.server
    from app import server as _srv

    class _StubLearningService:
        def generate(self, topic: str, count: int = 3, **kwargs):
            return {
                "practice_id": "prac_test_12345",
                "topic": topic,
                "count": count,
                "questions": [
                    {
                        "id": "q1",
                        "question": f"Câu hỏi kiểm định về {topic}?",
                        "choices": ["A", "B", "C", "D"],
                        "answer": "A",
                    }
                ],
            }

    monkeypatch.setattr(_srv, "learning_service", _StubLearningService(), raising=False)

    client = TestClient(app)

    # 1. Query exercise status endpoint
    get_res = client.get(f"/api/timetable/planner/sessions/{session_id}/exercise")
    assert get_res.status_code == 200
    ex_info = get_res.json()
    assert ex_info["exercise_status"] == "not_generated"
    assert ex_info["classified_topic"] == "Vi xử lý"

    # 2. Trigger on-demand exercise generation
    post_res = client.post(f"/api/timetable/planner/sessions/{session_id}/exercise/generate")
    assert post_res.status_code == 200
    gen_data = post_res.json()
    assert gen_data["status"] == "ok"
    assert gen_data["practice"]["practice_id"] == "prac_test_12345"

    # 3. Verify session in DB has practice_id bound and status available
    updated_sess = get_session(session_id, db_path=test_db)
    assert updated_sess["practice_id"] == "prac_test_12345"
    assert updated_sess["exercise_status"] == "available"
    assert updated_sess["pdf_url"] == "/api/learning/export-pdf/prac_test_12345"


def test_09c_missing_documents_truthfully_reports_unavailable(tmp_path, monkeypatch):
    """When the learning service has no documents matching the topic, it raises
    PracticeGenerationError and truthfully records exercise_status='unavailable'
    without fabricating filler questions or fake choices.
    """
    test_db = tmp_path / "exercise_unavailable.db"
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(test_db))

    with _connect(test_db) as conn:
        cur = conn.execute(
            """
            INSERT INTO study_sessions (
                plan_id, task_id, title, course, weekday, start_time, end_time,
                duration_minutes, priority, source, completed, locked,
                session_index, total_sessions, notes, created_at,
                classified_topic, worksheet_id, practice_id, exercise_status
            ) VALUES (
                'p1', NULL, 'Tự học Khảo cổ học', 'Khảo cổ học',
                3, '14:00', '15:30', 90, 'high', 'ai_auto_90m', 0, 0, 1, 1,
                'Ghi chú', '2026-11-11T00:00:00Z', 'Khảo cổ học Ai Cập cổ đại', '', '', 'not_generated'
            )
            """
        )
        session_id = cur.lastrowid
        conn.commit()

    from app import server as _srv
    from app.learning import PracticeGenerationError

    class _MissingDocLearningService:
        def generate(self, topic: str, **kwargs):
            raise PracticeGenerationError(f"Không tìm thấy tài liệu phù hợp với chủ đề «{topic}».")

    monkeypatch.setattr(_srv, "learning_service", _MissingDocLearningService(), raising=False)

    client = TestClient(app)
    post_res = client.post(f"/api/timetable/planner/sessions/{session_id}/exercise/generate")
    assert post_res.status_code == 422
    assert "Không tìm thấy tài liệu" in post_res.json()["detail"]

    # Verify session is marked unavailable, no fake practice_id
    updated_sess = get_session(session_id, db_path=test_db)
    assert updated_sess["exercise_status"] == "unavailable"
    assert updated_sess["practice_id"] is None


# ==============================================================================
# 09-D: BACKWARD API COMPATIBILITY & ROUTE DELEGATION
# ==============================================================================


def test_09d_legacy_route_compatibility(tmp_path, monkeypatch):
    """Legacy endpoint /api/timetable/planner/auto-schedule-courses delegates
    to the unified proposal + confirm workflow seamlessly with duration parameters.
    """
    test_db = tmp_path / "legacy_route.db"
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(test_db))

    with _connect(test_db) as conn:
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Hệ điều hành', 'Tiến trình và luồng (Process and Thread)', 'Hệ điều hành', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    client = TestClient(app)

    # 1. Default payload {} should schedule automatically (propose + confirm)
    res = client.post("/api/timetable/planner/auto-schedule-courses", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "scheduled"
    assert data["scheduled_count"] >= 1
    assert data["session_duration_minutes"] == 90

    # 2. propose_only: true returns draft without mutating DB
    res_draft = client.post(
        "/api/timetable/planner/auto-schedule-courses",
        json={"session_minutes": 60, "propose_only": True},
    )
    assert res_draft.status_code == 200
    draft_data = res_draft.json()
    assert draft_data["status"] == "draft"
    assert draft_data["session_duration_minutes"] == 60
    assert len(draft_data["sessions"]) >= 1
