"""Behavioral regression test suite for UI-AUDIT-20261111-11.
Covers:
11A: Stale preference rejection under transaction (awake bounds, max_daily_study_minutes,
     completed task, deadline) returning 409 Conflict without modifying database rows.
11B: Calendar and weekly lesson exercise on-demand generation, topic retention from course/lesson,
     truthful unavailable state without fake filler questions, and no client-visible answers.
11C: Durable account-scoped practice persistence in SQLite (practice.db) across service restart
     and cache eviction; answer persistence; cross-account isolation (fail-closed).
11D: Single primary proposal action, preferred_session_minutes configuration honored,
     and concise UI naming review.
11E: Calendar bundle zip and ICS integration packaging persisted practice worksheet PDFs
     without stale fallbacks.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.db.schemas import practice_migrations, timetable_migrations
from app.exercise_pdf import generate_exercise_worksheet_pdf
from app.planner_topic_bridge import (
    _connect,
    add_weekly_lesson,
    auto_schedule_90m_one_subject_per_day,
    build_calendar_bundle_zip,
)
from app.practice import (
    PracticeGenerationError,
    PracticeService,
    PracticeSessionNotFound,
)
from app.server import app
from app.study_planner import (
    confirm_study_plan,
    generate_study_plan,
    get_plan_draft,
    get_session,
    get_study_sessions,
    update_preferences,
    update_session_practice,
    verify_schedule_constraints,
)


def _init_timetable_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        timetable_migrations.apply(conn)


def _init_practice_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        practice_migrations.apply(conn)


# ==============================================================================
# 11A: STALE PREFERENCE REJECTION UNDER TRANSACTION
# ==============================================================================


def test_11a_stale_awake_bounds_rejected_with_409_without_modifying_db(tmp_path, monkeypatch):
    """When preferences change (e.g. earliest study time shifts after proposal)
    confirm_study_plan detects the awake-bounds violation under BEGIN IMMEDIATE,
    rejects with HTTP 409 Conflict, and rolls back cleanly without saving sessions.
    """
    db_path = tmp_path / "stale_awake.db"
    _init_timetable_db(db_path)

    # 1. Initial preferences: 08:00 - 12:00
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (
                id, earliest_study_time, latest_study_time, max_daily_study_minutes,
                preferred_session_minutes, updated_at
            ) VALUES (1, '08:00', '12:00', 240, 60, '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    # 2. Create a draft with a session at 08:00 - 09:30 on Monday (weekday=1)
    draft_id = "draft_awake_test_1"
    sessions_json = json.dumps([
        {
            "weekday": 1,
            "start_time": "08:00",
            "end_time": "09:30",
            "title": "Tự học Toán",
            "course": "Toán cao cấp",
            "priority": "high",
            "locked": 0,
        }
    ])
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_plan_drafts (
                plan_id, created_at, status, sessions_json, unscheduled_json,
                warnings_json, suggestions_json, explanation
            ) VALUES (?, '2026-11-11T00:00:00Z', 'draft', ?, '[]', '[]', '[]', 'Đề xuất 1 phiên')
            """,
            (draft_id, sessions_json),
        )
        conn.commit()

    # 3. User updates preferences before confirming: earliest study time shifted to 10:00
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE study_preferences SET earliest_study_time = '10:00', latest_study_time = '12:00' WHERE id = 1"
        )
        conn.commit()

    # 4. Attempting to confirm draft directly raises ValueError
    with pytest.raises(ValueError) as excinfo:
        confirm_study_plan(draft_id, db_path=db_path)
    assert "khung giờ học sớm nhất" in str(excinfo.value) or "khung giờ" in str(excinfo.value)

    # 5. Database has 0 confirmed study sessions; draft status is still 'draft'
    with _connect(db_path) as conn:
        sessions = conn.execute("SELECT * FROM study_sessions").fetchall()
        assert len(sessions) == 0
        draft_row = conn.execute("SELECT status FROM study_plan_drafts WHERE plan_id = ?", (draft_id,)).fetchone()
        assert draft_row["status"] == "draft"

    # 6. Test via HTTP route: returns 409 Conflict
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(db_path))
    client = TestClient(app)
    resp = client.post("/api/timetable/planner/confirm", json={"plan_id": draft_id})
    assert resp.status_code == 409
    data = resp.json()
    assert "khung giờ" in data["detail"] or "sớm nhất" in data["detail"]


def test_11a_stale_max_daily_budget_reduction_rejected_with_409(tmp_path, monkeypatch):
    """When user reduces max_daily_study_minutes between proposal and confirmation,
    confirm_study_plan detects the budget violation and returns 409 without saving.
    """
    db_path = tmp_path / "stale_budget.db"
    _init_timetable_db(db_path)

    # Initial preferences: 240 mins/day
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (
                id, earliest_study_time, latest_study_time, max_daily_study_minutes,
                preferred_session_minutes, updated_at
            ) VALUES (1, '08:00', '21:00', 240, 60, '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    # Proposed draft: 2 sessions on Monday totaling 120 minutes
    draft_id = "draft_budget_test_1"
    sessions_json = json.dumps([
        {
            "weekday": 1,
            "start_time": "08:00",
            "end_time": "09:00",
            "title": "Phiên 1",
            "course": "Toán",
            "priority": "medium",
            "locked": 0,
        },
        {
            "weekday": 1,
            "start_time": "10:00",
            "end_time": "11:00",
            "title": "Phiên 2",
            "course": "Văn",
            "priority": "medium",
            "locked": 0,
        },
    ])
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_plan_drafts (
                plan_id, created_at, status, sessions_json, unscheduled_json,
                warnings_json, suggestions_json, explanation
            ) VALUES (?, '2026-11-11T00:00:00Z', 'draft', ?, '[]', '[]', '[]', 'Đề xuất 2 phiên')
            """,
            (draft_id, sessions_json),
        )
        conn.commit()

    # User reduces max_daily_study_minutes to 30 minutes
    with _connect(db_path) as conn:
        conn.execute("UPDATE study_preferences SET max_daily_study_minutes = 30 WHERE id = 1")
        conn.commit()

    monkeypatch.setenv("TIMETABLE_DB_PATH", str(db_path))
    client = TestClient(app)
    resp = client.post("/api/timetable/planner/confirm", json={"plan_id": draft_id})
    assert resp.status_code == 409
    assert "vượt quá giới hạn tối đa" in resp.json()["detail"]

    # Verify zero sessions committed
    with _connect(db_path) as conn:
        count = conn.execute("SELECT count(*) as c FROM study_sessions").fetchone()["c"]
        assert count == 0


def test_11a_completed_task_rejection_protects_historical_confirmed(tmp_path):
    """If a task allocated in a draft was completed before confirmation,
    confirm_study_plan rejects the proposal truthfully, but leaves existing confirmed sessions intact.
    """
    db_path = tmp_path / "completed_task.db"
    _init_timetable_db(db_path)

    with _connect(db_path) as conn:
        # Existing locked historical session
        conn.execute(
            """
            INSERT INTO study_sessions (
                id, plan_id, title, course, weekday, start_time, end_time, duration_minutes,
                priority, source, completed, locked, created_at
            ) VALUES (1, 'hist_plan', 'Phiên cũ đã khóa', 'Toán', 2, '09:00', '10:00', 60,
                      'medium', 'ai_assistant', 0, 1, '2026-11-11T00:00:00Z')
            """
        )
        # Task that gets completed
        conn.execute(
            """
            INSERT INTO study_tasks (
                id, title, course, deadline_weekday, deadline_time, estimated_minutes, priority, completed, created_at
            ) VALUES (10, 'Bài tập lớn', 'Toán', 4, '18:00', 60, 'high', 1, '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    draft_id = "draft_completed_task"
    sessions_json = json.dumps([
        {
            "weekday": 3,
            "start_time": "14:00",
            "end_time": "15:00",
            "title": "Học bài tập lớn",
            "course": "Toán",
            "task_id": 10,
            "priority": "high",
            "locked": 0,
        }
    ])
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_plan_drafts (
                plan_id, created_at, status, sessions_json, unscheduled_json,
                warnings_json, suggestions_json, explanation
            ) VALUES (?, '2026-11-11T00:00:00Z', 'draft', ?, '[]', '[]', '[]', 'Đề xuất')
            """,
            (draft_id, sessions_json),
        )
        conn.commit()

    with pytest.raises(ValueError) as excinfo:
        confirm_study_plan(draft_id, db_path=db_path)
    assert "hoàn thành" in str(excinfo.value)

    # Existing historical session remains intact!
    with _connect(db_path) as conn:
        hist = conn.execute("SELECT * FROM study_sessions WHERE id = 1").fetchone()
        assert hist is not None
        assert hist["title"] == "Phiên cũ đã khóa"


def test_11a_deleted_task_rejected_with_409_without_modifying_db(tmp_path, monkeypatch):
    """When a task allocated in a draft was deleted before confirmation,
    confirm_study_plan detects the missing task under BEGIN IMMEDIATE,
    rejects with HTTP 409 Conflict, and rolls back cleanly with 0 changed sessions.
    """
    db_path = tmp_path / "deleted_task.db"
    _init_timetable_db(db_path)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (
                id, earliest_study_time, latest_study_time, max_daily_study_minutes,
                preferred_session_minutes, updated_at
            ) VALUES (1, '08:00', '22:00', 240, 60, '2026-11-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO study_tasks (
                id, title, course, deadline_weekday, deadline_time, estimated_minutes, priority, completed, created_at
            ) VALUES (99, 'Task to be deleted', 'Toán', 5, '18:00', 60, 'high', 0, '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    draft_id = "draft_deleted_task_test"
    sessions_json = json.dumps([
        {
            "weekday": 2,
            "start_time": "09:00",
            "end_time": "10:00",
            "title": "Học theo task đã xóa",
            "course": "Toán",
            "task_id": 99,
            "priority": "high",
            "locked": 0,
        }
    ])
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_plan_drafts (
                plan_id, created_at, status, sessions_json, unscheduled_json,
                warnings_json, suggestions_json, explanation
            ) VALUES (?, '2026-11-11T00:00:00Z', 'draft', ?, '[]', '[]', '[]', 'Đề xuất')
            """,
            (draft_id, sessions_json),
        )
        # Now delete the task!
        conn.execute("DELETE FROM study_tasks WHERE id = 99")
        conn.commit()

    monkeypatch.setenv("TIMETABLE_DB_PATH", str(db_path))
    client = TestClient(app)
    resp = client.post("/api/timetable/planner/confirm", json={"plan_id": draft_id})
    assert resp.status_code == 409
    assert "không còn tồn tại hoặc đã bị xóa" in resp.json()["detail"] or "đã bị xóa" in resp.json()["detail"]

    # Verify zero sessions committed
    with _connect(db_path) as conn:
        count = conn.execute("SELECT count(*) as c FROM study_sessions").fetchone()["c"]
        assert count == 0


@pytest.mark.parametrize("flag_override", [
    {"locked": 1},
    {"completed": 1},
    {"source": "manual"},
    {"locked": True, "completed": True, "source": "manual"},
])
def test_11a_reviewed_flags_bypass_rejected_with_409(tmp_path, monkeypatch, flag_override):
    """When a client submits reviewed draft sessions outside awake bounds (e.g. 07:00-08:00 with prefs 08:00-12:00),
    setting locked=True, completed=True, or source='manual' cannot evade awake/night constraints.
    Rejects with 409 Conflict and saves zero rows.
    """
    db_path = tmp_path / f"flags_{abs(hash(str(flag_override)))}.db"
    _init_timetable_db(db_path)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (
                id, earliest_study_time, latest_study_time, max_daily_study_minutes,
                preferred_session_minutes, updated_at
            ) VALUES (1, '08:00', '12:00', 240, 60, '2026-11-11T00:00:00Z')
            """
        )
        # Historical session that was legitimately confirmed
        conn.execute(
            """
            INSERT INTO study_sessions (
                id, plan_id, title, course, weekday, start_time, end_time, duration_minutes,
                priority, source, completed, locked, created_at
            ) VALUES (1, 'hist_plan', 'Phiên cũ lịch sử', 'Toán', 3, '09:00', '10:00', 60,
                      'medium', 'user_manual', 0, 1, '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    draft_id = f"draft_flag_{abs(hash(str(flag_override)))}"
    base_session = {
        "weekday": 1,
        "start_time": "07:00",
        "end_time": "08:00",
        "title": "Phiên vi phạm giờ sớm",
        "course": "Triết học",
        "priority": "medium",
    }
    base_session.update(flag_override)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_plan_drafts (
                plan_id, created_at, status, sessions_json, unscheduled_json,
                warnings_json, suggestions_json, explanation
            ) VALUES (?, '2026-11-11T00:00:00Z', 'draft', '[]', '[]', '[]', '[]', 'Đề xuất')
            """,
            (draft_id,),
        )
        conn.commit()

    monkeypatch.setenv("TIMETABLE_DB_PATH", str(db_path))
    client = TestClient(app)
    resp = client.post(
        "/api/timetable/planner/confirm",
        json={"plan_id": draft_id, "sessions": [base_session]},
    )
    assert resp.status_code == 409
    assert "bắt đầu trước khung giờ học sớm nhất" in resp.json()["detail"]

    # Verify historical session unchanged, and zero new sessions saved
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM study_sessions").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == 1
        assert rows[0]["title"] == "Phiên cũ lịch sử"


# ==============================================================================
# 11B: CALENDAR SESSION & WEEKLY LESSON EXERCISE GENERATION CONTRACT
# ==============================================================================


def test_11b_session_exercise_generation_and_client_safe_retrieval(tmp_path, monkeypatch):
    """Calendar session exercise generation produces an account-scoped practice session,
    binds practice_id to the timetable session, and GET /api/practice/session/{id} returns
    client-safe questions without server-side answers.
    """
    tt_db_path = tmp_path / "tt_exercise.db"
    pr_db_path = tmp_path / "pr_exercise.db"
    _init_timetable_db(tt_db_path)
    _init_practice_db(pr_db_path)

    monkeypatch.setenv("TIMETABLE_DB_PATH", str(tt_db_path))
    monkeypatch.setenv("PRACTICE_DB_PATH", str(pr_db_path))

    session_id = 1
    with _connect(tt_db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_sessions (
                id, plan_id, title, course, weekday, start_time, end_time, duration_minutes,
                priority, source, completed, locked, created_at
            ) VALUES (?, 'plan_1', 'Ôn tập Giới hạn hàm số', 'Giải tích 1', 1, '08:00', '09:00', 60,
                      'medium', 'ai_assistant', 0, 0, '2026-11-11T00:00:00Z')
            """,
            (session_id,),
        )
        conn.commit()

    # Create practice session in pr_db_path directly
    p_service = PracticeService(searcher=None, document_lookup=None, db_path=pr_db_path)
    practice_id = "prac_11b_test_01"
    questions_full = [
        {
            "id": "q1",
            "question": "Giới hạn của (sin x)/x khi x dần tới 0 là?",
            "choices": [
                {"label": "A", "text": "0"},
                {"label": "B", "text": "1"},
                {"label": "C", "text": "-1"},
                {"label": "D", "text": "Vô cùng"},
            ],
            "answer": "B",
            "explanation": "Theo định lý giới hạn cơ bản, lim_{x->0} (sin x)/x = 1.",
            "source": {
                "doc_id": 1,
                "chunk_id": 42,
                "title": "Giáo trình Giải tích 1",
                "page_num": 42,
                "snippet": "lim (sin x)/x = 1 khi x tiến về 0",
                "pdf_url": None,
            },
        }
    ]
    with sqlite3.connect(str(pr_db_path)) as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES (?, ?, 1, 1, 'medium', 'practice', 'Giải tích 1 - Giới hạn hàm số',
                      NULL, 'careful', ?, '{}', '{}')
            """,
            (practice_id, 1000.0, json.dumps(questions_full, ensure_ascii=False)),
        )
        conn.commit()

    # Mock learning_service.generate to return the prepared question set
    mock_learning = MagicMock()
    mock_learning.generate.return_value = {
        "status": "ready",
        "practice_id": practice_id,
        "count": 1,
        "questions": [
            {
                "id": "q1",
                "question": "Giới hạn của (sin x)/x khi x dần tới 0 là?",
                "choices": questions_full[0]["choices"],
                "source": questions_full[0]["source"],
            }
        ],
        "topic": "Giải tích 1 - Giới hạn hàm số",
    }
    monkeypatch.setattr("app.server.learning_service", mock_learning)
    monkeypatch.setattr("app.server.practice_service", p_service)

    client = TestClient(app)
    resp = client.post(f"/api/timetable/planner/sessions/{session_id}/exercise/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["practice"]["practice_id"] == practice_id
    questions = data["practice"]["questions"]
    assert len(questions) == 1

    # Check client-safe question format: NO answer key leaked!
    q = questions[0]
    assert "question" in q
    assert "choices" in q
    assert "answer" not in q

    # Verify timetable session has practice_id bound in DB
    with _connect(tt_db_path) as conn:
        sess_row = conn.execute("SELECT practice_id, exercise_status FROM study_sessions WHERE id = ?", (session_id,)).fetchone()
        assert sess_row["practice_id"] == practice_id
        assert sess_row["exercise_status"] == "available"

    # Test GET /api/practice/session/{practice_id} endpoint
    get_resp = client.get(f"/api/practice/session/{practice_id}")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["practice_id"] == practice_id
    assert get_data["topic"] == "Giải tích 1 - Giới hạn hàm số"
    assert len(get_data["questions"]) == 1
    assert "answer" not in get_data["questions"][0]


def test_11b_generation_failure_returns_truthful_unavailable(tmp_path, monkeypatch):
    """When practice generation fails (no sources, model error), returns truthful 422
    with error message without fabricating filler questions, and marks status unavailable.
    """
    tt_db_path = tmp_path / "tt_fail.db"
    _init_timetable_db(tt_db_path)
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(tt_db_path))

    session_id = 2
    with _connect(tt_db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_sessions (
                id, plan_id, title, course, weekday, start_time, end_time, duration_minutes,
                priority, source, completed, locked, created_at
            ) VALUES (?, 'plan_1', 'Chủ đề chưa có tài liệu', 'Khoa học máy tính', 2, '10:00', '11:00', 60,
                      'medium', 'ai_assistant', 0, 0, '2026-11-11T00:00:00Z')
            """,
            (session_id,),
        )
        conn.commit()

    mock_learning = MagicMock()
    mock_learning.generate.side_effect = PracticeGenerationError("Không tìm thấy đoạn trích phù hợp")
    monkeypatch.setattr("app.server.learning_service", mock_learning)

    client = TestClient(app)
    resp = client.post(f"/api/timetable/planner/sessions/{session_id}/exercise/generate")
    assert resp.status_code == 422
    assert "Không tìm thấy đoạn trích phù hợp" in resp.json()["detail"]

    # Verify exercise_status updated to unavailable in timetable DB
    with _connect(tt_db_path) as conn:
        sess_row = conn.execute("SELECT exercise_status FROM study_sessions WHERE id = ?", (session_id,)).fetchone()
        assert sess_row["exercise_status"] == "unavailable"


def test_11b_weekly_lesson_on_demand_generation_shares_practice_id_with_session(tmp_path, monkeypatch):
    """Saving a weekly lesson does NOT create a legacy worksheet or PDF.
    Exercises are generated ON DEMAND through LearningService/PracticeService,
    preserve lesson title ('Ngăn xếp' not broad DSA), honor requested question count,
    share the same practice_id with linked study sessions, and reuse on repeated request.
    """
    tt_db = tmp_path / "tt_lesson.db"
    pr_db = tmp_path / "pr_lesson.db"
    _init_timetable_db(tt_db)
    _init_practice_db(pr_db)

    monkeypatch.setenv("TIMETABLE_DB_PATH", str(tt_db))
    monkeypatch.setenv("PRACTICE_DB_PATH", str(pr_db))

    # 1. Add weekly lesson with specific title and requested question count = 3
    added = add_weekly_lesson(
        tt_db,
        course="Cấu trúc dữ liệu & Giải thuật",
        lesson_title="Ngăn xếp & Hàng đợi",
        question_count=3,
    )
    lesson_id = added["id"]
    assert added["worksheet_id"] is None
    assert added["practice_id"] is None
    assert added["exercise_status"] == "not_generated"

    # Verify no legacy worksheet in DB
    with _connect(tt_db) as conn:
        ws_count = conn.execute("SELECT count(*) as c FROM planner_worksheets").fetchone()["c"]
        assert ws_count == 0

    # 2. Add a planned session linked to this weekly lesson
    with _connect(tt_db) as conn:
        conn.execute(
            """
            INSERT INTO study_sessions (
                id, plan_id, weekly_lesson_id, title, course, weekday, start_time, end_time,
                duration_minutes, priority, source, completed, locked, created_at
            ) VALUES (1, 'plan_w', ?, 'Tự học: Ngăn xếp & Hàng đợi', 'Cấu trúc dữ liệu & Giải thuật',
                      2, '14:00', '15:00', 60, 'high', 'ai_assistant', 0, 0, '2026-11-11T00:00:00Z')
            """,
            (lesson_id,),
        )
        conn.commit()

    # 3. Mock practice service & learning service
    p_service = PracticeService(searcher=None, document_lookup=None, db_path=pr_db)
    monkeypatch.setattr("app.server.practice_service", p_service)

    mock_learning = MagicMock()
    generated_pid = "prac_stack_queue_1"
    questions_data = [
        {
            "id": f"q{i}",
            "question": f"Câu hỏi {i} về Ngăn xếp?",
            "choices": [{"label": "A", "text": "Đúng"}, {"label": "B", "text": "Sai"}, {"label": "C", "text": "Có"}, {"label": "D", "text": "Không"}],
            "answer": "A",
            "explanation": f"Giải thích câu {i}",
            "source": {"doc_id": 1, "chunk_id": i, "title": "Cấu trúc dữ liệu", "page_num": 10, "snippet": "Ngăn xếp LIFO", "pdf_url": None},
        }
        for i in range(1, 4)
    ]
    with sqlite3.connect(str(pr_db)) as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES (?, ?, 3, 3, 'medium', 'practice', 'Ngăn xếp & Hàng đợi',
                      NULL, 'careful', ?, '{}', '{}')
            """,
            (generated_pid, 1000.0, json.dumps(questions_data, ensure_ascii=False)),
        )
        conn.commit()

    mock_learning.generate.return_value = {
        "status": "ready",
        "practice_id": generated_pid,
        "count": 3,
        "requested_count": 3,
        "questions": [{"id": q["id"], "question": q["question"], "choices": q["choices"], "source": q["source"]} for q in questions_data],
        "topic": "Ngăn xếp & Hàng đợi",
    }
    monkeypatch.setattr("app.server.learning_service", mock_learning)

    # 4. Generate exercise on demand via weekly lesson endpoint
    client = TestClient(app)
    resp = client.post(f"/api/timetable/planner/weekly-lessons/{lesson_id}/exercise/generate")
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "ok"
    assert res_data["practice_id"] == generated_pid

    # Check mock called with specific lesson title and requested count
    mock_learning.generate.assert_called_once()
    call_kwargs = mock_learning.generate.call_args[1]
    assert "Ngăn xếp" in call_kwargs["topic"]
    assert call_kwargs["count"] == 3

    # Check that weekly_lesson and linked study_session both share the SAME practice_id
    with _connect(tt_db) as conn:
        l_row = conn.execute("SELECT practice_id, exercise_status, question_count FROM weekly_lessons WHERE id = ?", (lesson_id,)).fetchone()
        assert l_row["practice_id"] == generated_pid
        assert l_row["exercise_status"] == "available"
        assert l_row["question_count"] == 3

        s_row = conn.execute("SELECT practice_id, exercise_status FROM study_sessions WHERE id = 1").fetchone()
        assert s_row["practice_id"] == generated_pid
        assert s_row["exercise_status"] == "available"

    # 5. Repeated request reuses existing practice session without calling generate again!
    mock_learning.generate.reset_mock()
    repeat_resp = client.post(f"/api/timetable/planner/weekly-lessons/{lesson_id}/exercise/generate")
    assert repeat_resp.status_code == 200
    assert repeat_resp.json()["practice_id"] == generated_pid
    mock_learning.generate.assert_not_called()

    # 6. Session exercise endpoint also reuses the same practice_id
    sess_resp = client.post("/api/timetable/planner/sessions/1/exercise/generate")
    assert sess_resp.status_code == 200
    assert sess_resp.json()["practice"]["practice_id"] == generated_pid
    mock_learning.generate.assert_not_called()


# ==============================================================================
# 11C: DURABLE PRACTICE PERSISTENCE IN SQLITE & ACCOUNT ISOLATION
# ==============================================================================


def test_11c_durable_practice_persistence_across_service_instances(tmp_path):
    """Generated practice questions, answer keys, citations, and metadata persist in practice.db.
    A fresh PracticeService instance (restart/cache eviction) reloads the identical session,
    evaluates answers against the server-side keys, and persists answers into answers_json.
    """
    db_path = tmp_path / "practice_durability.db"
    _init_practice_db(db_path)

    sample_questions = [
        {
            "id": "q1",
            "question": "Phân loại nào thuộc học có giám sát?",
            "choices": [
                {"label": "A", "text": "K-means"},
                {"label": "B", "text": "Hồi quy tuyến tính"},
                {"label": "C", "text": "PCA"},
                {"label": "D", "text": "Autoencoder"},
            ],
            "answer": "B",
            "explanation": "Hồi quy tuyến tính học từ dữ liệu có nhãn.",
            "source": {
                "doc_id": 1,
                "chunk_id": 10,
                "title": "Machine Learning",
                "page_num": 15,
                "snippet": "Hồi quy tuyến tính là thuật toán có giám sát",
                "pdf_url": None,
            },
        }
    ]
    practice_id = "prac_durability_1"

    # 1. Insert directly into practice_sessions table
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES (?, ?, 1, 1, 'medium', 'practice', 'Machine Learning cơ bản',
                      NULL, 'careful', ?, '{}', '{}')
            """,
            (practice_id, 1000.0, json.dumps(sample_questions, ensure_ascii=False)),
        )
        conn.commit()

    # 2. Fresh service instance 1: reload session questions with answer keys
    service1 = PracticeService(searcher=None, document_lookup=None, db_path=db_path)
    reloaded_qset = service1.get_session_questions_with_answers(practice_id)
    assert len(reloaded_qset) == 1
    assert reloaded_qset[0]["answer"] == "B"
    assert reloaded_qset[0]["question"] == "Phân loại nào thuộc học có giám sát?"

    # Client-safe get_session has NO answers
    client_safe = service1.get_session(practice_id)
    assert client_safe["practice_id"] == practice_id
    assert client_safe["topic"] == "Machine Learning cơ bản"
    assert "answer" not in client_safe["questions"][0]

    # 3. Answer question q1 with correct choice 'B'
    ans_result = service1.answer(practice_id, question_id="q1", answer="B")
    assert ans_result["is_correct"] is True
    assert ans_result["correct_answer"] == "B"

    # Verify answers_json was persisted to practice.db
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        updated_row = conn.execute("SELECT answers_json FROM practice_sessions WHERE practice_id = ?", (practice_id,)).fetchone()
        answers = json.loads(updated_row["answers_json"])
        assert answers["q1"]["submitted"] == "B"
        assert answers["q1"]["is_correct"] is True

    # 4. Fresh service instance 2 (simulating restart after submission)
    service2 = PracticeService(searcher=None, document_lookup=None, db_path=db_path)
    reloaded_again = service2.get_session_questions_with_answers(practice_id)
    assert len(reloaded_again) == 1
    assert reloaded_again[0]["id"] == "q1"


def test_11c_cross_account_practice_isolation(tmp_path):
    """Account B cannot fetch, view, or answer Account A's practice session (fail-closed 404)."""
    db_a = tmp_path / "account_a" / "practice.db"
    db_b = tmp_path / "account_b" / "practice.db"
    db_a.parent.mkdir(parents=True)
    db_b.parent.mkdir(parents=True)
    _init_practice_db(db_a)
    _init_practice_db(db_b)

    service_a = PracticeService(searcher=None, document_lookup=None, db_path=db_a)
    service_b = PracticeService(searcher=None, document_lookup=None, db_path=db_b)

    practice_id = "prac_private_a"
    sample_q = [
        {
            "id": "q1",
            "question": "Dữ liệu riêng tư của tài khoản A?",
            "choices": [{"label": "A", "text": "Đúng"}, {"label": "B", "text": "Sai"}],
            "answer": "A",
            "explanation": "Tài liệu riêng tư.",
            "source": {"doc_id": 1, "chunk_id": 1, "title": "Riêng tư A", "page_num": 1, "snippet": "Riêng", "pdf_url": None},
        }
    ]

    with sqlite3.connect(str(db_a)) as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES (?, ?, 1, 1, 'medium', 'practice', 'Tài liệu A',
                      NULL, 'careful', ?, '{}', '{}')
            """,
            (practice_id, 1000.0, json.dumps(sample_q, ensure_ascii=False)),
        )
        conn.commit()

    # Account A can access
    assert service_a.get_session(practice_id)["practice_id"] == practice_id

    # Account B attempting to access raises PracticeSessionNotFound
    with pytest.raises(PracticeSessionNotFound):
        service_b.get_session(practice_id)

    with pytest.raises(PracticeSessionNotFound):
        service_b.answer(practice_id, "q1", "A")

    with pytest.raises(PracticeSessionNotFound):
        service_b.get_session_questions_with_answers(practice_id)


def test_11c_injected_db_error_on_generation_raises_truthful_error_and_no_ram_session(tmp_path):
    """When database write fails (e.g. SQLite OperationalError), generate_from_passages
    raises PracticeGenerationError, does NOT store the session in RAM cache, and fresh service
    fails closed with PracticeSessionNotFound.
    """
    db_path = tmp_path / "pr_db_fail.db"
    _init_practice_db(db_path)

    service = PracticeService(searcher=None, document_lookup=None, db_path=db_path)
    passages = [
        {
            "doc_id": 1,
            "chunk_id": 1,
            "filename": "test.pdf",
            "page_num": 1,
            "clean_title": "Toán",
            "text": "Định lý Pytago trong tam giác vuông: bình phương cạnh huyền bằng tổng bình phương hai cạnh góc vuông.",
        }
    ]

    mock_doc = MagicMock()
    mock_doc.document.return_value = {"clean_title": "Toán"}
    mock_doc.pdf_path.return_value = None
    service.document_lookup = mock_doc

    generated_raw = json.dumps({
        "questions": [
            {
                "question": "Trong tam giác vuông, định lý Pytago phát biểu thế nào?",
                "choices": {
                    "A": "Bình phương cạnh huyền bằng tổng bình phương hai cạnh góc vuông",
                    "B": "Cạnh huyền bằng tổng hai cạnh góc vuông",
                    "C": "Bình phương cạnh huyền bằng hiệu hai cạnh góc vuông",
                    "D": "Tất cả đều sai",
                },
                "answer": "A",
                "explanation": "Đúng theo định lý Pytago.",
                "source_chunk_id": 1,
                "evidence_quote": "bình phương cạnh huyền bằng tổng bình phương hai cạnh góc vuông",
            }
        ]
    })
    service._generate_text = MagicMock(return_value=generated_raw)
    service._verify_questions = MagicMock(side_effect=lambda candidates, *args, **kwargs: candidates)

    # Injected database connection failure on write
    failing_conn = MagicMock()
    failing_conn.execute.side_effect = sqlite3.OperationalError("disk I/O error or database is locked")
    failing_conn.cursor.side_effect = sqlite3.OperationalError("disk I/O error or database is locked")

    with patch.object(service, "_db_connection", return_value=failing_conn):
        with pytest.raises(PracticeGenerationError) as excinfo:
            service.generate_from_passages(passages, count=1)
        assert "Lỗi lưu trữ dữ liệu" in str(excinfo.value) or "cơ sở dữ liệu" in str(excinfo.value)

    # Memory cache must NOT contain any session!
    assert len(service._sessions) == 0


def test_11c_injected_db_error_on_answer_raises_runtime_error(tmp_path):
    """When saving answer progress fails in SQLite, answer() raises RuntimeError
    instead of silently swallowing the error.
    """
    db_path = tmp_path / "pr_ans_fail.db"
    _init_practice_db(db_path)

    sample_questions = [
        {
            "id": "q1",
            "question": "Câu hỏi test?",
            "choices": [{"label": "A", "text": "Đúng"}, {"label": "B", "text": "Sai"}],
            "answer": "A",
            "explanation": "Giải thích.",
            "source": {"doc_id": 1, "chunk_id": 1, "title": "Doc", "page_num": 1, "snippet": "Test", "pdf_url": None},
        }
    ]
    practice_id = "prac_ans_fail_1"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES (?, ?, 1, 1, 'medium', 'practice', 'Test', NULL, 'careful', ?, '{}', '{}')
            """,
            (practice_id, 1000.0, json.dumps(sample_questions)),
        )
        conn.commit()

    service = PracticeService(searcher=None, document_lookup=None, db_path=db_path)

    class BadConnProxy:
        def __init__(self, target):
            self._target = target
        def execute(self, sql, *args, **kwargs):
            if "UPDATE practice_sessions" in sql:
                raise sqlite3.OperationalError("database disk image is malformed")
            return self._target.execute(sql, *args, **kwargs)
        def commit(self):
            return self._target.commit()
        def rollback(self):
            return self._target.rollback()
        def close(self):
            return self._target.close()
        def __getattr__(self, name):
            return getattr(self._target, name)

    def failing_conn_factory():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return BadConnProxy(conn)

    with patch.object(service, "_db_connection", side_effect=failing_conn_factory):
        with pytest.raises(RuntimeError) as excinfo:
            service.answer(practice_id, "q1", "A")
        assert "Lỗi lưu trữ tiến độ" in str(excinfo.value)


def test_11c_get_session_resumes_answers_without_exposing_unanswered_keys(tmp_path):
    """get_session returns client-safe questions without answer keys,
    while returning safe_answers for questions that have been answered.
    """
    db_path = tmp_path / "pr_resume.db"
    _init_practice_db(db_path)

    sample_questions = [
        {
            "id": "q1",
            "question": "Câu hỏi 1?",
            "choices": [{"label": "A", "text": "Đáp án A"}, {"label": "B", "text": "Đáp án B"}],
            "answer": "A",
            "explanation": "Giải thích 1.",
            "source": {"doc_id": 1, "chunk_id": 1, "title": "Doc", "page_num": 1, "snippet": "Snippet 1", "pdf_url": None},
        },
        {
            "id": "q2",
            "question": "Câu hỏi 2?",
            "choices": [{"label": "A", "text": "Đáp án A2"}, {"label": "B", "text": "Đáp án B2"}],
            "answer": "B",
            "explanation": "Giải thích 2.",
            "source": {"doc_id": 1, "chunk_id": 2, "title": "Doc", "page_num": 2, "snippet": "Snippet 2", "pdf_url": None},
        },
    ]
    practice_id = "prac_resume_test"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES (?, ?, 2, 2, 'medium', 'practice', 'Test Resume', NULL, 'careful', ?, '{}', '{}')
            """,
            (practice_id, 1000.0, json.dumps(sample_questions)),
        )
        conn.commit()

    service = PracticeService(searcher=None, document_lookup=None, db_path=db_path)

    # 1. Initially, no questions answered
    sess = service.get_session(practice_id)
    assert sess["answers"] == {}
    for q in sess["questions"]:
        assert "answer" not in q
        assert "explanation" not in q

    # 2. Answer question q1
    service.answer(practice_id, "q1", "A")

    # 3. Call get_session again
    sess_after = service.get_session(practice_id)
    answers = sess_after["answers"]
    assert "q1" in answers
    assert answers["q1"]["is_correct"] is True
    assert answers["q1"]["submitted_answer"] == "A"
    assert answers["q1"]["correct_answer"] == "A"
    assert answers["q1"]["correct_choice"] == "Đáp án A"
    assert answers["q1"]["explanation"] == "Giải thích 1."

    # q2 was NOT answered, so it must NOT be in answers, and its answer key must remain concealed!
    assert "q2" not in answers
    for q in sess_after["questions"]:
        assert "answer" not in q
        assert "explanation" not in q


def test_11c_max_sessions_cache_eviction_on_reload(tmp_path):
    """When reloading sessions from SQLite, PracticeService respects MAX_SESSIONS cache bound."""
    db_path = tmp_path / "pr_evict.db"
    _init_practice_db(db_path)

    service = PracticeService(searcher=None, document_lookup=None, db_path=db_path)
    service.MAX_SESSIONS = 1

    # Insert two sessions in DB
    for pid in ("sess_1", "sess_2"):
        q = [{
            "id": "q1",
            "question": f"Q for {pid}",
            "choices": [{"label": "A", "text": "A"}],
            "answer": "A",
            "explanation": "Exp",
            "source": {"doc_id": 1, "chunk_id": 1, "title": "Doc", "page_num": 1, "snippet": "S", "pdf_url": None},
        }]
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                INSERT INTO practice_sessions (
                    practice_id, created_at, count, requested_count, difficulty,
                    purpose, topic, warning, processing_level, questions_json,
                    answers_json, metadata_json
                ) VALUES (?, 1000.0, 1, 1, 'medium', 'practice', 'T', NULL, 'careful', ?, '{}', '{}')
                """,
                (pid, json.dumps(q)),
            )
            conn.commit()

    # Load session 1
    service.get_session_questions_with_answers("sess_1")
    assert len(service._sessions) <= 1
    assert "sess_1" in service._sessions

    # Load session 2 -> sess_1 should be evicted from RAM cache
    service.get_session_questions_with_answers("sess_2")
    assert len(service._sessions) <= 1
    assert "sess_2" in service._sessions
    assert "sess_1" not in service._sessions


def test_11c_corrupt_or_missing_practice_db_fails_closed(tmp_path):
    """Corrupt or missing practice DB fails closed with is_valid_practice_session returning False."""
    from app.practice import is_valid_practice_session

    # 1. Non-existent file
    assert is_valid_practice_session("any_id", db_path=tmp_path / "does_not_exist.db") is False

    # 2. Corrupt JSON in DB
    corrupt_db = tmp_path / "corrupt.db"
    _init_practice_db(corrupt_db)
    with sqlite3.connect(str(corrupt_db)) as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES ('corrupt_sess', 1000.0, 1, 1, 'medium', 'practice', 'T', NULL, 'careful',
                      'NOT VALID JSON', '{}', '{}')
            """
        )
        conn.commit()

    assert is_valid_practice_session("corrupt_sess", db_path=corrupt_db) is False


# ==============================================================================
# 11D: UNIFIED PROPOSAL ACTION & CONCISE UI LABELS
# ==============================================================================


def test_11d_ui_html_concise_labels_and_no_verbose_step3():
    """Verify static/index.html uses concise labels:
    - 'Việc học & Deadline' instead of verbose 'Bước 3: Nhập Bài tập...'
    - 'Bài học tuần'
    - 'Đề xuất lịch'
    - 'Xác nhận lịch'
    - 'Tải lịch và bài tập'
    - 'Kiểm tra nhận diện chữ'
    - 'Chọn tài liệu'
    """
    html_path = Path("static/index.html")
    content = html_path.read_text(encoding="utf-8")

    # Positive checks
    assert "Việc học &amp; Deadline" in content or "Việc học & Deadline" in content
    assert "📚 Bài học tuần" in content
    assert "✨ Đề xuất lịch" in content
    assert "🤖 Đề xuất lịch" in content
    assert "✅ Xác nhận lịch" in content
    assert "📦 Tải lịch và bài tập" in content
    assert "📝 Kiểm tra nhận diện chữ" in content
    assert "📚 Chọn tài liệu" in content

    # Negative checks (verbose legacy labels removed)
    assert "Bước 3: Nhập Bài tập, Công việc học tập &amp; Deadline" not in content
    assert "Tạo Lịch Học Cá Nhân &amp; Duyệt Đề Xuất Của Bộ xếp lịch" not in content
    assert "Tải gói Lịch .ics + PDF Bài tập (.zip)" not in content
    assert "Thẩm định & duyệt bản nhận diện chữ cục bộ" not in content
    assert "Chọn tài liệu từ thư viện để ôn tập" not in content


def test_11d_single_visible_actions_and_concise_copy():
    """Verify single visible proposal action, single visible reschedule action,
    concise action labels, and removal of contradictory mandatory-window copy.
    """
    html_content = Path("static/index.html").read_text(encoding="utf-8")
    wizard_content = Path("static/planner-wizard.js").read_text(encoding="utf-8")
    tour_content = Path("static/onboarding-tour.js").read_text(encoding="utf-8")

    # 1. Single primary proposal action (btn-auto-schedule-90m hidden, btn-generate-study-plan visible)
    assert 'id="btn-auto-schedule-90m" class="btn btn-sm btn-primary" style="display: none;"' in html_content
    assert 'id="btn-generate-study-plan" class="btn btn-sm btn-primary"' in html_content

    # 2. Single reschedule action (btn-reschedule-from-master hidden, btn-reschedule-incomplete visible)
    assert 'id="btn-reschedule-from-master" class="btn btn-sm btn-secondary" style="display: none;"' in html_content
    assert 'id="btn-reschedule-incomplete" class="btn btn-sm btn-secondary"' in html_content

    # 3. Concise task & lesson action buttons
    assert 'id="btn-submit-task" class="btn btn-sm btn-primary">＋ Thêm việc</button>' in html_content
    assert 'id="btn-add-weekly-lesson" class="btn btn-sm btn-secondary">＋ Lưu bài học</button>' in html_content

    # 4. Short bento headings
    assert "<h4>Tra cứu tài liệu</h4>" in html_content
    assert "<h4>Tạo bài tập</h4>" in html_content
    assert "<h4>Lịch học</h4>" in html_content

    # 5. No technical DESCRIPTION / ATTACH 3-layer paragraph in index.html
    assert "Giải pháp tương thích Google Calendar &amp; Apple Calendar" not in html_content
    assert "Do tệp <code>.ics</code> khi nhập vào Google Calendar Web không tự tải tệp nhị phân" not in html_content
    assert "💡 Bạn có thể nhập lịch vào Google Calendar / Apple Calendar" in html_content

    # 6. Weekly helper prose does not claim fixed 90 minutes
    assert "thời lượng theo thiết lập học tập" in html_content
    assert "thời lượng mặc định 90 phút" not in html_content

    # 7. planner-wizard.js Step 2 removed mandatory study window copy
    assert "Thêm khung giờ có thể học để bộ xếp lịch biết khi nào bạn rảnh" not in wizard_content
    assert "ưu tiên tự học (tùy chọn)" in wizard_content

    # 8. onboarding-tour.js concise titles
    assert "title: 'Hỏi đáp tài liệu'" in tour_content
    assert "title: 'Tạo bài tập'" in tour_content
    assert "title: 'Lịch học'" in tour_content
    assert "title: 'Thư viện & Lịch sử'" in tour_content
    assert "title: 'Cuộc trò chuyện mới'" in tour_content
    assert "title: 'Đính kèm tài liệu'" in tour_content
    assert "title: 'Mức độ xử lý'" in tour_content


def test_11d_preferred_session_minutes_configuration_honored(tmp_path):
    """The auto schedule generator respects preferred_session_minutes (e.g. 60 or 45),
    instead of hardcoding 90 minutes.
    """
    db_path = tmp_path / "pref_minutes.db"
    _init_timetable_db(db_path)

    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences (
                id, earliest_study_time, latest_study_time, max_daily_study_minutes,
                preferred_session_minutes, updated_at
            ) VALUES (1, '08:00', '18:00', 240, 60, '2026-11-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO weekly_lessons (course, lesson_title, classified_topic, created_at)
            VALUES ('Hóa đại cương', 'Bài 1: Cấu tạo nguyên tử', 'Hóa học', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    # Schedule with 60 minutes
    res = auto_schedule_90m_one_subject_per_day(db_path, session_minutes=60)
    assert res["status"] == "scheduled"
    assert len(res["sessions"]) >= 1
    assert res["sessions"][0]["duration_minutes"] == 60

    # Schedule with 45 minutes
    res45 = auto_schedule_90m_one_subject_per_day(db_path, session_minutes=45)
    assert res45["status"] == "scheduled"
    assert res45["sessions"][0]["duration_minutes"] == 45


# ==============================================================================
# 11E: CALENDAR BUNDLE EXPORT INTEGRATION
# ==============================================================================


def test_11e_calendar_bundle_packages_persisted_practice_pdf(tmp_path):
    """build_calendar_bundle_zip queries study_sessions with valid practice_id,
    retrieves persisted questions from practice.db, renders PDF worksheets,
    and includes them in the .zip bundle without stale fallbacks.
    """
    tt_db = tmp_path / "bundle_tt.db"
    pr_db = tmp_path / "bundle_pr.db"
    _init_timetable_db(tt_db)
    _init_practice_db(pr_db)

    # 1. Create practice session in pr_db
    p_id = "prac_bio_bundle"
    sample_q = [
        {
            "id": "q1",
            "question": "Đơn vị cơ bản của sự sống là gì?",
            "choices": [
                {"label": "A", "text": "Tế bào"},
                {"label": "B", "text": "Virus"},
                {"label": "C", "text": "Phân tử"},
                {"label": "D", "text": "Mô"},
            ],
            "answer": "A",
            "explanation": "Mọi sinh vật sống đều được cấu tạo từ một hoặc nhiều tế bào.",
            "source": {"doc_id": 1, "chunk_id": 1, "title": "Sinh học", "page_num": 5, "snippet": "Tế bào là đơn vị", "pdf_url": None},
        }
    ]
    with sqlite3.connect(str(pr_db)) as conn:
        conn.execute(
            """
            INSERT INTO practice_sessions (
                practice_id, created_at, count, requested_count, difficulty,
                purpose, topic, warning, processing_level, questions_json,
                answers_json, metadata_json
            ) VALUES (?, ?, 1, 1, 'medium', 'practice', 'Sinh học tế bào',
                      NULL, 'careful', ?, '{}', '{}')
            """,
            (p_id, 1000.0, json.dumps(sample_q, ensure_ascii=False)),
        )
        conn.commit()

    # 2. Insert study session with practice_id in tt_db
    with _connect(tt_db) as conn:
        conn.execute(
            """
            INSERT INTO study_sessions (
                id, plan_id, title, course, weekday, start_time, end_time, duration_minutes,
                priority, source, completed, locked, practice_id, created_at
            ) VALUES (1, 'plan_b', 'Ôn tập Sinh học tế bào', 'Sinh học', 2, '08:00', '09:00', 60,
                      'medium', 'ai_assistant', 0, 0, ?, '2026-11-11T00:00:00Z')
            """,
            (p_id,),
        )
        conn.commit()

    # 3. Build bundle zip passing practice_db_path
    with patch("app.practice.get_practice_db_path", return_value=pr_db):
        zip_bytes = build_calendar_bundle_zip(
            db_path=tt_db,
            ics_bytes=b"BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n",
        )

    assert len(zip_bytes) > 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        assert "thoi-khoa-bieu-arym.ics" in namelist
        assert "HUONG_DAN_DINH_KEM_LICH.txt" in namelist
        # Must contain the generated PDF for the session
        pdf_names = [n for n in namelist if n.startswith("bai-tap-pdf/") and n.endswith(".pdf")]
        assert len(pdf_names) == 1
        assert p_id in pdf_names[0]

        # Verify PDF is non-empty valid PDF
        pdf_content = z.read(pdf_names[0])
        assert pdf_content.startswith(b"%PDF-")


def test_11e_stale_practice_id_does_not_generate_corrupt_pdf(tmp_path):
    """If a session references a deleted/stale practice_id, build_calendar_bundle_zip
    gracefully handles it without producing a corrupt PDF or crashing.
    """
    tt_db = tmp_path / "stale_pdf_tt.db"
    pr_db = tmp_path / "stale_pdf_pr.db"
    _init_timetable_db(tt_db)
    _init_practice_db(pr_db)

    with _connect(tt_db) as conn:
        conn.execute(
            """
            INSERT INTO study_sessions (
                id, plan_id, title, course, weekday, start_time, end_time, duration_minutes,
                priority, source, completed, locked, practice_id, created_at
            ) VALUES (1, 'plan_s', 'Phiên có practice_id rác', 'Vật lý', 3, '14:00', '15:00', 60,
                      'medium', 'ai_assistant', 0, 0, 'non_existent_practice_id', '2026-11-11T00:00:00Z')
            """
        )
        conn.commit()

    with patch("app.practice.get_practice_db_path", return_value=pr_db):
        zip_bytes = build_calendar_bundle_zip(
            db_path=tt_db,
            ics_bytes=b"BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n",
        )

    assert len(zip_bytes) > 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        namelist = z.namelist()
        assert "thoi-khoa-bieu-arym.ics" in namelist
        # No fake or corrupt PDF should be generated for the stale practice_id
        pdf_names = [n for n in namelist if n.startswith("bai-tap-pdf/")]
        assert len(pdf_names) == 0
