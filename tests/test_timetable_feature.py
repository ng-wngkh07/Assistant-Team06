"""Comprehensive tests for Timetable Feature (S01-S03)."""

import base64
import os
import pymupdf
import pytest
from datetime import datetime
from fastapi.testclient import TestClient

from app import server, timetable_store, timetable_vision, timetable_ics


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test_timetable.db"
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(db_path))
    return TestClient(server.app)


def make_test_png_bytes(width=100, height=100) -> bytes:
    """Create a minimal valid PNG image in memory using PyMuPDF."""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(pymupdf.Rect(10, 10, 90, 90), color=(0, 0, 1), fill=(0.9, 0.9, 0.9))
    page.insert_text((15, 30), "THỜI KHÓA BIỂU", fontsize=10)
    page.insert_text((15, 50), "Toán rời rạc - Thứ 2 - 08:00 - 10:00", fontsize=8)
    pix = page.get_pixmap()
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def test_image_validation_bounds():
    # 1. Invalid base64
    with pytest.raises(ValueError):
        timetable_vision.validate_image_payload("test.png", "not-base-64!!!")

    # 2. Valid image
    png_bytes = make_test_png_bytes()
    b64 = base64.b64encode(png_bytes).decode()
    raw_bytes, sha = timetable_vision.validate_image_payload("test.png", b64)
    assert len(raw_bytes) == len(png_bytes)
    assert len(sha) == 64


def test_timetable_crud_and_isolation(isolated_client):
    # 1. Initially empty
    res = isolated_client.get("/api/timetable")
    assert res.status_code == 200
    assert res.json()["entries"] == []

    # 2. Add manual entry
    res = isolated_client.post("/api/timetable/manual", json={
        "course": "Đại số tuyến tính",
        "weekday": 2,
        "start_time": "07:30",
        "end_time": "09:30",
        "room": "B1-204"
    })
    assert res.status_code == 200
    entry_id = res.json()["id"]

    # 3. Verify retrieved
    res = isolated_client.get("/api/timetable")
    entries = res.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["course"] == "Đại số tuyến tính"
    assert entries[0]["weekday"] == 2
    assert entries[0]["room"] == "B1-204"

    # 4. Delete entry
    res = isolated_client.delete(f"/api/timetable/entries/{entry_id}")
    assert res.status_code == 200
    assert isolated_client.get("/api/timetable").json()["entries"] == []


def test_draft_confirmation_workflow(isolated_client):
    draft_id = "draft_test_123"
    entries = [
        {"course": "Lập trình C++", "weekday": 1, "start_time": "08:00", "end_time": "11:15", "room": "A201"},
        {"course": "Tâm lý học đại cương", "weekday": 4, "start_time": "13:30", "end_time": "16:00", "room": "C105"}
    ]
    timetable_store.save_draft(
        draft_id=draft_id,
        image_hash="hash123",
        filename="tkb.png",
        entries=entries,
        uncertainties=["Kiểm tra lại phòng học"]
    )

    # Fetch draft
    draft_res = isolated_client.get(f"/api/timetable/drafts/{draft_id}")
    assert draft_res.status_code == 200
    assert len(draft_res.json()["entries"]) == 2

    # Confirm draft
    confirm_res = isolated_client.post("/api/timetable/confirm", json={
        "draft_id": draft_id,
        "entries": entries
    })
    assert confirm_res.status_code == 200
    assert confirm_res.json()["saved_entries"] == 2

    # Confirmed timetable has entries
    tt_res = isolated_client.get("/api/timetable")
    assert len(tt_res.json()["entries"]) == 2


def test_conflict_detection():
    entries = [
        {"course": "Vật lý đại cương", "weekday": 2, "start_time": "08:00", "end_time": "10:00"},
        {"course": "Giải tích 1", "weekday": 2, "start_time": "09:30", "end_time": "11:30"},
        {"course": "Triết học", "weekday": 3, "start_time": "08:00", "end_time": "10:00"},
    ]
    conflicts = timetable_vision.detect_conflicts(entries)
    assert len(conflicts) == 1
    assert "Vật lý đại cương" in conflicts[0] and "Giải tích 1" in conflicts[0]


def test_ics_export_generation(isolated_client):
    entries = [
        {"course": "Toán rời rạc", "weekday": 1, "start_time": "08:00", "end_time": "10:00", "room": "A301"},
        {"course": "Cơ sở dữ liệu", "weekday": 5, "start_time": "13:30", "end_time": "15:30", "room": "B202"}
    ]
    ics_text = timetable_ics.generate_ics(entries, "2026-10-05", "2026-12-31")
    assert "BEGIN:VCALENDAR" in ics_text
    assert "END:VCALENDAR" in ics_text
    assert "TZID:Asia/Ho_Chi_Minh" in ics_text
    assert "SUMMARY:Toán rời rạc" in ics_text
    assert "SUMMARY:Cơ sở dữ liệu" in ics_text
    assert "BYDAY=MO" in ics_text
    assert "BYDAY=FR" in ics_text

    # Via HTTP
    for e in entries:
        isolated_client.post("/api/timetable/manual", json=e)

    res = isolated_client.get("/api/timetable/export.ics?start_date=2026-10-05&end_date=2026-12-31")
    assert res.status_code == 200
    assert "text/calendar" in res.headers["content-type"]
    assert "ATTACHMENT" in res.headers["content-disposition"].upper()
    assert "SUMMARY:Toán rời rạc" in res.text


def test_confirmed_entry_editing_and_unconfirmed_draft_isolation(isolated_client):
    """Criterion 1: Only confirmed classes are used as fixed classes; drafts can be edited before confirm, and confirmed entries can be edited/deleted."""
    draft_id = "draft_ocr_only"
    timetable_store.save_draft(
        draft_id=draft_id,
        image_hash="hash_unconfirmed",
        filename="unconfirmed.png",
        entries=[{"course": "Môn chưa xác nhận", "weekday": 1, "start_time": "08:00", "end_time": "10:00", "room": "P101"}],
        uncertainties=["OCR nghi ngờ giờ học"]
    )

    # Before confirmation, planner schedule must NOT include unconfirmed draft classes
    sched_res = isolated_client.get("/api/timetable/planner/schedule")
    assert sched_res.status_code == 200
    assert sched_res.json()["summary"]["class_count"] == 0

    # Confirm with edited entry
    confirm_res = isolated_client.post("/api/timetable/confirm", json={
        "draft_id": draft_id,
        "entries": [
            {"course": "Cấu trúc dữ liệu & Giải thuật", "weekday": 1, "start_time": "08:00", "end_time": "10:00", "room": "A101"}
        ]
    })
    assert confirm_res.status_code == 200

    # Now confirmed class appears in planner schedule
    sched_res = isolated_client.get("/api/timetable/planner/schedule")
    assert sched_res.json()["summary"]["class_count"] == 1
    cls_item = sched_res.json()["items"][0]
    assert cls_item["title"] == "Cấu trúc dữ liệu & Giải thuật"
    assert cls_item["source_badge"] == "OCR / Đã xác nhận"

    # Update confirmed class via PUT
    entry_id = cls_item["item_id"]
    upd_res = isolated_client.put(f"/api/timetable/entries/{entry_id}", json={
        "course": "CTDL & Giải thuật (Đã sửa)",
        "weekday": 1,
        "start_time": "08:30",
        "end_time": "10:30",
        "room": "A102"
    })
    assert upd_res.status_code == 200
    assert upd_res.json()["id"] == entry_id
    updated_entries = isolated_client.get("/api/timetable").json()["entries"]
    assert updated_entries[0]["course"] == "CTDL & Giải thuật (Đã sửa)"
    assert updated_entries[0]["start_time"] == "08:30"


def test_personal_routines_and_preferences_crud(isolated_client):
    """Criterion 2: Weekly personal routine table (fixed, protected, flexible, study_window) and personal limits/AI notes."""
    from app import study_planner
    bulk_routines = [
        {"title": "Tập gym chiều", "activity_type": "gym", "flexibility": "fixed", "weekday": 1, "start_time": "17:30", "end_time": "19:00"},
        {"title": "Nghỉ trưa", "activity_type": "rest", "flexibility": "protected", "weekday": 1, "start_time": "12:00", "end_time": "13:30"},
        {"title": "Đi cà phê với bạn", "activity_type": "personal", "flexibility": "flexible", "weekday": 2, "start_time": "19:00", "end_time": "21:00"},
        {"title": "Khung giờ tự học tối Thứ 2", "activity_type": "study_window", "flexibility": "study_window", "weekday": 1, "start_time": "19:30", "end_time": "22:00"},
    ]
    res = isolated_client.put("/api/timetable/planner/routines/bulk", json={"routines": bulk_routines})
    assert res.status_code == 200
    assert len(res.json()["routines"]) == 4

    # Save preferences with natural language AI note
    pref_res = isolated_client.put("/api/timetable/planner/preferences", json={
        "earliest_study_time": "07:00",
        "latest_study_time": "22:30",
        "max_daily_study_minutes": 180,
        "preferred_session_minutes": 60,
        "min_session_minutes": 30,
        "buffer_before_deadline_hours": 12,
        "ai_notes": "Tối đa 2 giờ mỗi ngày, mỗi phiên 50 phút, không học sau 21:30"
    })
    assert pref_res.status_code == 200
    prefs = pref_res.json()["preferences"]
    assert prefs["ai_notes"] == "Tối đa 2 giờ mỗi ngày, mỗi phiên 50 phút, không học sau 21:30"
    parsed = study_planner.parse_ai_preference_notes(prefs["ai_notes"], prefs)
    assert parsed["max_daily_min"] == 120
    assert parsed["pref_session_min"] == 50
    assert parsed["latest_min"] == 21 * 60 + 30


def test_study_tasks_missing_estimate_and_scheduling_constraints(isolated_client):
    """Criteria 3 & 4: Tasks with deadlines, missing estimate detection, splitting long tasks, and respecting fixed/protected/flexible/study_window constraints."""
    # 1. Fixed class on Monday 08:00-10:00
    isolated_client.post("/api/timetable/manual", json={
        "course": "Hệ điều hành",
        "weekday": 1,
        "start_time": "08:00",
        "end_time": "10:00",
        "room": "B201"
    })

    # 2. Routines on Monday & Tuesday:
    # - Monday 08:00-12:00 declared as study_window (overlaps class 08:00-10:00 -> planner must only use 10:00-12:00!)
    # - Monday 12:00-13:30 protected rest
    # - Monday 14:00-16:00 study_window, with a flexible activity 14:00-15:00 (must NOT silently overwrite flexible activity!)
    # - Tuesday 19:00-21:00 study_window
    isolated_client.put("/api/timetable/planner/routines/bulk", json={
        "routines": [
            {"title": "Khung sáng Thứ 2", "activity_type": "study_window", "flexibility": "study_window", "weekday": 1, "start_time": "08:00", "end_time": "12:00"},
            {"title": "Nghỉ trưa", "activity_type": "rest", "flexibility": "protected", "weekday": 1, "start_time": "12:00", "end_time": "13:30"},
            {"title": "Khung chiều Thứ 2", "activity_type": "study_window", "flexibility": "study_window", "weekday": 1, "start_time": "14:00", "end_time": "16:00"},
            {"title": "Chơi cầu lông (có thể đổi)", "activity_type": "sport", "flexibility": "flexible", "weekday": 1, "start_time": "14:00", "end_time": "15:00"},
            {"title": "Khung tối Thứ 3", "activity_type": "study_window", "flexibility": "study_window", "weekday": 2, "start_time": "19:00", "end_time": "21:00"},
        ]
    })

    isolated_client.put("/api/timetable/planner/preferences", json={
        "earliest_study_time": "07:00",
        "latest_study_time": "22:30",
        "max_daily_study_minutes": 180,
        "preferred_session_minutes": 60,
        "min_session_minutes": 30,
        "buffer_before_deadline_hours": 2,
        "ai_notes": ""
    })

    # 3. Add a task WITH estimate (150 mins -> should split into multiple sessions) and a task WITHOUT estimate (0 mins)
    t1 = isolated_client.post("/api/timetable/planner/tasks", json={
        "title": "Bài tập lớn Hệ điều hành",
        "course": "Hệ điều hành",
        "deadline_weekday": 3,
        "deadline_time": "18:00",
        "estimated_minutes": 150,
        "priority": "high",
        "splittable": True
    }).json()["task"]

    t2 = isolated_client.post("/api/timetable/planner/tasks", json={
        "title": "Đọc chương 3 Mạng máy tính",
        "course": "Mạng máy tính",
        "deadline_weekday": 4,
        "deadline_time": "20:00",
        "estimated_minutes": 0,
        "priority": "medium",
        "splittable": True
    }).json()["task"]
    assert t2["missing_estimate"] is True

    # 4. Generate plan
    gen_res = isolated_client.post("/api/timetable/planner/generate", json={})
    assert gen_res.status_code == 200
    draft = gen_res.json()["draft"]

    # Check missing estimate task is flagged clearly
    assert len(draft["missing_estimate_tasks"]) == 1
    assert draft["missing_estimate_tasks"][0]["task_id"] == t2["id"]
    assert any("Đọc chương 3 Mạng máy tính" in w for w in draft["warnings"])

    # Check t1 (150 mins) was split and scheduled without violating constraints
    sessions = [s for s in draft["sessions"] if s["task_id"] == t1["id"]]
    total_scheduled = sum(s["duration_minutes"] for s in sessions)
    assert total_scheduled == 150
    assert len(sessions) >= 2  # Split into multiple sessions
    assert draft["constraint_violations"] == []

    # Ensure no session overlaps Monday 08:00-10:00 (class), 12:00-13:30 (protected), or 14:00-15:00 (flexible without consent)
    for s in sessions:
        if s["weekday"] == 1:
            assert not (s["start_time"] < "10:00" and "08:00" < s["end_time"]), "Overlapped fixed class!"
            assert not (s["start_time"] < "13:30" and "12:00" < s["end_time"]), "Overlapped protected rest!"
            assert not (s["start_time"] < "15:00" and "14:00" < s["end_time"]), "Silently overlapped flexible routine!"


def test_insufficient_capacity_warning_and_suggestion_application(isolated_client):
    """Criterion 5: Clear warning when insufficient time for deadlines, exact missing time, and actionable suggestions."""
    # Only 60 mins of study_window on Monday 20:00-21:00, plus a 60-min flexible routine on Monday 19:00-20:00
    isolated_client.put("/api/timetable/planner/routines/bulk", json={
        "routines": [
            {"title": "Tự học tối T2", "activity_type": "study_window", "flexibility": "study_window", "weekday": 1, "start_time": "20:00", "end_time": "21:00"},
            {"title": "Xem phim (có thể đổi)", "activity_type": "personal", "flexibility": "flexible", "weekday": 1, "start_time": "19:00", "end_time": "20:00"},
        ]
    })

    # Task needing 120 mins due Monday 23:00
    isolated_client.post("/api/timetable/planner/tasks", json={
        "title": "Báo cáo Đồ án",
        "course": "Công nghệ phần mềm",
        "deadline_weekday": 1,
        "deadline_time": "23:00",
        "estimated_minutes": 120,
        "priority": "high",
        "splittable": True
    })

    gen_res = isolated_client.post("/api/timetable/planner/generate", json={})
    draft = gen_res.json()["draft"]

    # Only 60 mins scheduled, 60 mins unscheduled
    assert len(draft["unscheduled_tasks"]) == 1
    unsched = draft["unscheduled_tasks"][0]
    assert unsched["scheduled_minutes"] == 60
    assert unsched["missing_minutes"] == 60
    assert len(draft["suggestions"]) > 0

    # Apply the suggestion to convert the flexible routine into a study window with user consent
    flex_sugg = next((s for s in draft["suggestions"] if s["type"] == "convert_flexible_to_study_window"), None)
    assert flex_sugg is not None

    apply_res = isolated_client.post("/api/timetable/planner/apply-suggestion", json={
        "type": "convert_flexible_to_study_window",
        "routine_id": flex_sugg["routine_id"]
    })
    assert apply_res.status_code == 200

    # Regenerate plan after applying suggestion
    new_draft = isolated_client.post("/api/timetable/planner/generate", json={}).json()["draft"]
    assert len(new_draft["unscheduled_tasks"]) == 0


def test_confirm_plan_complete_lock_and_reschedule_incomplete(isolated_client):
    """Criteria 6 & 7: Review/edit draft before confirming, mark session completed/locked, and reschedule remaining incomplete work without moving fixed/locked/completed items."""
    isolated_client.put("/api/timetable/planner/routines/bulk", json={
        "routines": [
            {"title": "Khung học Thứ 2", "activity_type": "study_window", "flexibility": "study_window", "weekday": 1, "start_time": "08:00", "end_time": "11:00"},
            {"title": "Khung học Thứ 3", "activity_type": "study_window", "flexibility": "study_window", "weekday": 2, "start_time": "08:00", "end_time": "11:00"},
        ]
    })
    isolated_client.put("/api/timetable/planner/preferences", json={
        "earliest_study_time": "07:00",
        "latest_study_time": "22:00",
        "max_daily_study_minutes": 180,
        "preferred_session_minutes": 60,
        "min_session_minutes": 30,
        "buffer_before_deadline_hours": 2,
        "ai_notes": ""
    })

    isolated_client.post("/api/timetable/planner/tasks", json={
        "title": "Ôn thi Giải tích",
        "course": "Giải tích",
        "deadline_weekday": 5,
        "deadline_time": "18:00",
        "estimated_minutes": 120,
        "priority": "high",
        "splittable": True
    })

    gen_res = isolated_client.post("/api/timetable/planner/generate", json={})
    draft = gen_res.json()["draft"]
    plan_id = draft["plan_id"]
    assert len(draft["sessions"]) == 2

    # Confirm draft
    conf_res = isolated_client.post("/api/timetable/planner/confirm", json={
        "plan_id": plan_id,
        "sessions": draft["sessions"]
    })
    assert conf_res.status_code == 200
    saved_sessions = conf_res.json()["sessions"]
    assert len(saved_sessions) == 2

    # Mark first session as completed (60 mins done, 60 mins remaining)
    s1_id = saved_sessions[0]["id"]
    upd_s1 = isolated_client.put(f"/api/timetable/planner/sessions/{s1_id}", json={
        "completed": True
    })
    assert upd_s1.status_code == 200
    assert upd_s1.json()["session"]["completed"] is True

    # Suppose Monday 08:00-11:00 now gets a fixed personal routine at 09:00-11:00 and Tuesday 08:00-11:00 is open
    isolated_client.post("/api/timetable/planner/routines", json={
        "title": "Họp CLB cố định",
        "activity_type": "personal",
        "flexibility": "fixed",
        "weekday": 1,
        "start_time": "09:00",
        "end_time": "11:00"
    })

    # Re-generate schedule for incomplete work
    regen_res = isolated_client.post("/api/timetable/planner/generate", json={"reschedule_only": True})
    assert regen_res.status_code == 200
    regen_draft = regen_res.json()["draft"]

    # Preserved sessions has 1 completed session (60 mins), and proposed sessions has 1 remaining session (60 mins)
    assert len(regen_draft["preserved_sessions"]) == 1
    assert sum(s["duration_minutes"] for s in regen_draft["sessions"]) == 60

    # Confirm the regenerated plan and verify completed session s1 is still preserved
    isolated_client.post("/api/timetable/planner/confirm", json={
        "plan_id": regen_draft["plan_id"],
        "sessions": regen_draft["sessions"]
    })
    final_sched = isolated_client.get("/api/timetable/planner/schedule").json()
    assert final_sched["summary"]["completed_study_minutes"] == 60
    assert final_sched["summary"]["study_session_count"] == 2


def _freeze_planner_now(monkeypatch, frozen_dt: datetime) -> None:
    """Freeze app.study_planner.datetime.now() deterministically without mutating real datetime."""
    from datetime import datetime as _real_datetime
    from app import study_planner

    class _FrozenDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return frozen_dt.astimezone(tz)
            return frozen_dt.replace(tzinfo=None)

    monkeypatch.setattr(study_planner, "datetime", _FrozenDatetime)


def _setup_cutoff_preferences(db_path, week_start_date: str, **overrides) -> None:
    from app import study_planner

    payload = {
        "earliest_study_time": "07:00",
        "latest_study_time": "22:30",
        "max_daily_study_minutes": 240,
        "preferred_session_minutes": 60,
        "min_session_minutes": 30,
        "buffer_before_deadline_hours": 2,
        "ai_notes": "",
        "week_start_date": week_start_date,
    }
    payload.update(overrides)
    study_planner.update_preferences(payload, db_path=db_path)


def test_current_week_cutoff_and_future_week_scheduling(tmp_path, monkeypatch):
    """FIX-PLANNER-DATE-01: Current week schedules only at >= ceil(VN now); future week starts Monday 07:00."""
    from datetime import timezone as _tz
    from zoneinfo import ZoneInfo
    from app import study_planner

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    db_path = tmp_path / "cutoff_current_future.db"
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 13, 14, 11, 30, tzinfo=vn_tz))

    _setup_cutoff_preferences(db_path, week_start_date="2026-11-13")
    task = study_planner.add_task(
        {
            "title": "Bài tập tuần hiện tại",
            "course": "Giải tích 2",
            "deadline_date": "2026-11-15",
            "deadline_time": "23:59",
            "estimated_minutes": 60,
            "priority": "high",
            "splittable": True,
        },
        db_path=db_path,
    )

    draft = study_planner.generate_study_plan(db_path=db_path)
    assert draft["constraint_violations"] == []
    assert len(draft["sessions"]) == 1
    for s in draft["sessions"]:
        assert int(s["weekday"]) >= 5
        if int(s["weekday"]) == 5:
            assert s["start_time"] >= "14:12"
    assert draft["sessions"][0]["weekday"] == 5
    assert draft["sessions"][0]["start_time"] == "14:12"

    # Future week schedules from Monday 07:00
    study_planner.update_preferences({"week_start_date": "2026-11-16"}, db_path=db_path)
    study_planner.update_task(
        task["id"],
        {"deadline_date": "2026-11-22", "deadline_weekday": 7, "deadline_time": "23:59"},
        db_path=db_path,
    )
    future_draft = study_planner.generate_study_plan(db_path=db_path)
    assert future_draft["constraint_violations"] == []
    assert len(future_draft["sessions"]) == 1
    assert future_draft["sessions"][0]["weekday"] == 1
    assert future_draft["sessions"][0]["start_time"] == "07:00"
    assert _tz.utc is not None


def test_expired_historical_week_and_exhausted_current_week(tmp_path, monkeypatch):
    """Expired historical weeks and exhausted current weeks return unscheduled + warnings truthfully."""
    from zoneinfo import ZoneInfo
    from app import study_planner

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")

    # 1. Historical week (2026-11-02..2026-11-08) when now is 2026-11-13
    hist_db = tmp_path / "historical_week.db"
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 13, 14, 11, 30, tzinfo=vn_tz))
    _setup_cutoff_preferences(hist_db, week_start_date="2026-11-02")
    study_planner.add_task(
        {
            "title": "Bài tập tuần đã qua",
            "course": "Vật lý",
            "deadline_date": "2026-11-08",
            "deadline_time": "23:59",
            "estimated_minutes": 60,
            "priority": "high",
            "splittable": True,
        },
        db_path=hist_db,
    )
    hist_draft = study_planner.generate_study_plan(db_path=hist_db)
    assert hist_draft["sessions"] == []
    assert len(hist_draft["unscheduled_tasks"]) == 1
    assert len(hist_draft["warnings"]) >= 1
    assert "đã qua" in " ".join(hist_draft["warnings"])
    assert "Toàn bộ nhiệm vụ học tập đã được phân bổ đầy đủ" not in hist_draft["explanation"]
    assert "đã qua" in hist_draft["explanation"]

    # 2. Current week with exhausted future window (Sunday 22:20:30 -> ceil 22:21, latest 22:30, 30m task)
    exhausted_db = tmp_path / "exhausted_week.db"
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 15, 22, 20, 30, tzinfo=vn_tz))
    _setup_cutoff_preferences(exhausted_db, week_start_date="2026-11-09", latest_study_time="22:30")
    study_planner.add_routine(
        {
            "title": "Việc linh hoạt Thứ 6 (đã qua)",
            "activity_type": "personal",
            "flexibility": "flexible",
            "weekday": 5,
            "start_time": "19:00",
            "end_time": "20:00",
        },
        db_path=exhausted_db,
    )
    study_planner.add_task(
        {
            "title": "Bài tập sát giờ cuối tuần",
            "course": "Cơ sở dữ liệu",
            "deadline_date": "2026-11-15",
            "deadline_time": "23:59",
            "estimated_minutes": 30,
            "priority": "high",
            "splittable": False,
        },
        db_path=exhausted_db,
    )
    exhausted_draft = study_planner.generate_study_plan(db_path=exhausted_db)
    assert exhausted_draft["sessions"] == []
    assert len(exhausted_draft["unscheduled_tasks"]) == 1
    assert len(exhausted_draft["warnings"]) >= 1
    assert "Toàn bộ nhiệm vụ học tập đã được phân bổ đầy đủ" not in exhausted_draft["explanation"]
    for sugg in exhausted_draft.get("suggestions", []):
        assert int(sugg.get("weekday", 7)) >= 7


def test_deadline_buffer_two_pass_and_timezone_boundaries(tmp_path, monkeypatch):
    """Buffer two-pass stays >= ceil_now; Saturday and UTC-Sunday/VN-Monday boundaries obey VN time."""
    from datetime import timezone as _tz
    from zoneinfo import ZoneInfo
    from app import study_planner

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")

    # 1. Buffer pass 1 (< cutoff) vs pass 2 (exact deadline inside future window)
    buf_db = tmp_path / "buffer_twopass.db"
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 13, 14, 11, 30, tzinfo=vn_tz))
    _setup_cutoff_preferences(
        buf_db,
        week_start_date="2026-11-13",
        buffer_before_deadline_hours=12,
        latest_study_time="22:30",
    )
    # Deadline Sat 2026-11-14 02:00 -> minus 12h buffer is Fri 14:00 (< 14:12 cutoff).
    # Pass 1 finds 0 future minutes; Pass 2 allocates Fri 14:12-15:12 (never before 14:12).
    study_planner.add_task(
        {
            "title": "Nộp bài rạng sáng T7",
            "course": "Mạng máy tính",
            "deadline_date": "2026-11-14",
            "deadline_time": "02:00",
            "estimated_minutes": 60,
            "priority": "high",
            "splittable": True,
        },
        db_path=buf_db,
    )
    buf_draft = study_planner.generate_study_plan(db_path=buf_db)
    assert buf_draft["constraint_violations"] == []
    assert len(buf_draft["sessions"]) == 1
    assert buf_draft["sessions"][0]["weekday"] == 5
    assert buf_draft["sessions"][0]["start_time"] == "14:12"

    # 2. Saturday 2026-11-14T12:00:30+07:00 -> Sat 12:01
    sat_db = tmp_path / "saturday.db"
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 14, 12, 0, 30, tzinfo=vn_tz))
    _setup_cutoff_preferences(sat_db, week_start_date="2026-11-09")
    study_planner.add_task(
        {
            "title": "Bài tập cuối tuần",
            "course": "Thuật toán",
            "deadline_date": "2026-11-15",
            "deadline_time": "23:59",
            "estimated_minutes": 60,
            "priority": "high",
            "splittable": True,
        },
        db_path=sat_db,
    )
    sat_draft = study_planner.generate_study_plan(db_path=sat_db)
    assert len(sat_draft["sessions"]) == 1
    assert sat_draft["sessions"][0]["weekday"] == 6
    assert sat_draft["sessions"][0]["start_time"] == "12:01"

    # 3. UTC Sunday 18:00:30+00:00 == VN Monday 2026-11-16 01:00:30+07:00
    utc_db = tmp_path / "utc_boundary.db"
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 15, 18, 0, 30, tzinfo=_tz.utc))
    _setup_cutoff_preferences(utc_db, week_start_date="2026-11-09")
    t_utc = study_planner.add_task(
        {
            "title": "Kiểm tra ranh giới múi giờ",
            "course": "Hệ điều hành",
            "deadline_date": "2026-11-15",
            "deadline_time": "23:59",
            "estimated_minutes": 60,
            "priority": "high",
            "splittable": True,
        },
        db_path=utc_db,
    )
    expired_draft = study_planner.generate_study_plan(db_path=utc_db)
    assert expired_draft["sessions"] == []
    assert len(expired_draft["unscheduled_tasks"]) == 1
    assert len(expired_draft["warnings"]) >= 1

    study_planner.update_preferences({"week_start_date": "2026-11-16"}, db_path=utc_db)
    study_planner.update_task(
        t_utc["id"],
        {"deadline_date": "2026-11-22", "deadline_weekday": 7, "deadline_time": "23:59"},
        db_path=utc_db,
    )
    next_week_draft = study_planner.generate_study_plan(db_path=utc_db)
    assert len(next_week_draft["sessions"]) == 1
    assert next_week_draft["sessions"][0]["weekday"] == 1
    assert next_week_draft["sessions"][0]["start_time"] == "07:00"


def test_preserved_history_and_confirm_past_rejection_via_http(isolated_client, monkeypatch):
    """Preserved historical sessions stay intact; new/edited/forged past draft sessions return HTTP 409."""
    from zoneinfo import ZoneInfo

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")

    # Seed Monday historical completed + manual sessions when clock was Monday 2026-11-09
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 9, 7, 30, 0, tzinfo=vn_tz))
    isolated_client.put("/api/timetable/planner/preferences", json={
        "earliest_study_time": "07:00",
        "latest_study_time": "22:30",
        "max_daily_study_minutes": 240,
        "preferred_session_minutes": 60,
        "min_session_minutes": 30,
        "buffer_before_deadline_hours": 2,
        "ai_notes": "",
        "week_start_date": "2026-11-09",
    })
    s_done = isolated_client.post("/api/timetable/planner/sessions", json={
        "title": "Phiên đã hoàn thành T2",
        "course": "Toán rời rạc",
        "weekday": 1,
        "start_time": "08:00",
        "end_time": "09:00",
        "completed": True,
        "locked": True,
    })
    assert s_done.status_code == 200
    s_manual = isolated_client.post("/api/timetable/planner/sessions", json={
        "title": "Phiên thủ công T3",
        "course": "Cấu trúc dữ liệu",
        "weekday": 2,
        "start_time": "10:00",
        "end_time": "11:00",
        "completed": False,
        "locked": True,
    })
    assert s_manual.status_code == 200

    # Advance clock to Friday 2026-11-13 14:11:30+07:00
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 13, 14, 11, 30, tzinfo=vn_tz))
    isolated_client.post("/api/timetable/planner/tasks", json={
        "title": "Bài tập cuối tuần mới",
        "course": "Trí tuệ nhân tạo",
        "deadline_date": "2026-11-15",
        "deadline_time": "23:59",
        "estimated_minutes": 60,
        "priority": "high",
        "splittable": True,
    })

    gen_res = isolated_client.post("/api/timetable/planner/generate", json={})
    assert gen_res.status_code == 200
    draft = gen_res.json()["draft"]
    assert len(draft["preserved_sessions"]) == 2
    assert len(draft["sessions"]) == 1
    assert draft["sessions"][0]["weekday"] == 5
    assert draft["sessions"][0]["start_time"] == "14:12"

    # 1. Edited past session must fail confirmation with 409 Conflict
    edited_past = dict(draft["sessions"][0])
    edited_past["weekday"] = 5
    edited_past["start_time"] = "10:00"
    edited_past["end_time"] = "11:00"
    reject_res = isolated_client.post("/api/timetable/planner/confirm", json={
        "plan_id": draft["plan_id"],
        "sessions": [edited_past],
    })
    assert reject_res.status_code == 409

    # 2. Forged preserved/manual/completed/locked flags on a newly proposed past session must STILL fail with 409
    forged_past = dict(edited_past)
    forged_past["_is_preserved"] = True
    forged_past["locked"] = True
    forged_past["completed"] = 1
    forged_past["source"] = "manual"
    forged_res = isolated_client.post("/api/timetable/planner/confirm", json={
        "plan_id": draft["plan_id"],
        "sessions": [forged_past],
    })
    assert forged_res.status_code == 409

    # 3. Confirm valid draft -> preserved history remains intact alongside Friday 14:12 session
    ok_res = isolated_client.post("/api/timetable/planner/confirm", json={
        "plan_id": draft["plan_id"],
        "sessions": draft["sessions"],
    })
    assert ok_res.status_code == 200
    all_sessions = isolated_client.get("/api/timetable/planner/state").json()["study_sessions"]
    assert len(all_sessions) == 3
    titles_by_day = {(s["weekday"], s["start_time"]): s["title"] for s in all_sessions}
    assert titles_by_day[(1, "08:00")] == "Phiên đã hoàn thành T2"
    assert titles_by_day[(2, "10:00")] == "Phiên thủ công T3"
    assert (5, "14:12") in titles_by_day


def test_weekly_lessons_reschedule_and_explicit_db_isolation(tmp_path, monkeypatch):
    """Weekly lessons and reschedule_only obey current-week cutoff; explicit db_path isolates state."""
    from zoneinfo import ZoneInfo
    from app import study_planner

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    default_db = tmp_path / "default_should_stay_untouched.db"
    account_b_db = tmp_path / "account_b.db"
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(default_db))

    _freeze_planner_now(monkeypatch, datetime(2026, 11, 13, 14, 11, 30, tzinfo=vn_tz))
    _setup_cutoff_preferences(account_b_db, week_start_date="2026-11-13")

    # 1. include_courses=True respects Friday 14:12 cutoff (only Fri, Sat, Sun open -> 3 sessions, none Mon..Thu)
    course_draft = study_planner.generate_study_plan(
        include_courses=True,
        course_items=[{"course": "Học máy", "lesson_title": "Hồi quy tuyến tính"}],
        session_duration=90,
        ai_notes_override="ưu tiên tự học chiều tối",
        db_path=account_b_db,
    )
    assert course_draft["constraint_violations"] == []
    assert len(course_draft["sessions"]) == 3
    for cs in course_draft["sessions"]:
        assert int(cs["weekday"]) >= 5
        if int(cs["weekday"]) == 5:
            assert cs["start_time"] >= "14:12"

    # 2. Explicit db_path isolation: default_db was NOT mutated by update_preferences or ai_notes_override
    assert not default_db.exists()
    assert study_planner.get_preferences(db_path=account_b_db)["ai_notes"] == "ưu tiên tự học chiều tối"
    assert study_planner.get_preferences(db_path=default_db)["ai_notes"] == ""

    # 3. reschedule_only=True respects current-week cutoff as well
    study_planner.add_task(
        {
            "title": "Bù tiến độ môn Học máy",
            "course": "Học máy",
            "deadline_date": "2026-11-15",
            "deadline_time": "23:59",
            "estimated_minutes": 60,
            "priority": "high",
            "splittable": True,
        },
        db_path=account_b_db,
    )
    resched_draft = study_planner.generate_study_plan(reschedule_only=True, db_path=account_b_db)
    assert len(resched_draft["sessions"]) == 1
    assert int(resched_draft["sessions"][0]["weekday"]) >= 5
    if int(resched_draft["sessions"][0]["weekday"]) == 5:
        assert resched_draft["sessions"][0]["start_time"] >= "14:12"

    # 4. Expired week with include_courses=True produces unplaced_courses + warnings
    study_planner.update_preferences({"week_start_date": "2026-11-02"}, db_path=account_b_db)
    expired_courses = study_planner.generate_study_plan(
        include_courses=True,
        course_items=[{"course": "Học máy", "lesson_title": "Hồi quy tuyến tính"}],
        session_duration=90,
        db_path=account_b_db,
    )
    assert expired_courses["sessions"] == []
    assert expired_courses["unplaced_courses"] == ["Học máy"]
    assert len(expired_courses["warnings"]) >= 1


def test_http_planner_generate_confirm_state_and_ics_export_agreement(isolated_client, monkeypatch):
    """Generated, confirmed, state readback, and .ics export dates match on selected Fri 2026-11-13."""
    from zoneinfo import ZoneInfo

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 13, 14, 11, 30, tzinfo=vn_tz))

    pref_res = isolated_client.put("/api/timetable/planner/preferences", json={
        "earliest_study_time": "07:00",
        "latest_study_time": "22:30",
        "max_daily_study_minutes": 180,
        "preferred_session_minutes": 60,
        "min_session_minutes": 30,
        "buffer_before_deadline_hours": 2,
        "ai_notes": "",
        "week_start_date": "2026-11-13",
    })
    assert pref_res.status_code == 200

    task_res = isolated_client.post("/api/timetable/planner/tasks", json={
        "title": "Ôn tập giữa kỳ",
        "course": "Xác suất thống kê",
        "deadline_date": "2026-11-15",
        "deadline_time": "23:59",
        "estimated_minutes": 60,
        "priority": "high",
        "splittable": True,
    })
    assert task_res.status_code == 200

    gen_res = isolated_client.post("/api/timetable/planner/generate", json={})
    assert gen_res.status_code == 200
    draft = gen_res.json()["draft"]
    assert len(draft["sessions"]) == 1
    s = draft["sessions"][0]
    assert s["weekday"] == 5
    assert s["start_time"] == "14:12"
    assert s["end_time"] == "15:12"

    conf_res = isolated_client.post("/api/timetable/planner/confirm", json={
        "plan_id": draft["plan_id"],
        "sessions": draft["sessions"],
    })
    assert conf_res.status_code == 200
    assert conf_res.json()["saved_count"] == 1

    state_res = isolated_client.get("/api/timetable/planner/state")
    assert state_res.status_code == 200
    state_sessions = state_res.json()["study_sessions"]
    assert len(state_sessions) == 1
    assert state_sessions[0]["weekday"] == 5
    assert state_sessions[0]["start_time"] == "14:12"
    assert state_sessions[0]["end_time"] == "15:12"

    ics_res = isolated_client.get("/api/timetable/export.ics?start_date=2026-11-13&end_date=2027-03-05&include_planner=true")
    assert ics_res.status_code == 200
    assert "Ôn tập giữa kỳ" in ics_res.text
    assert "DTSTART;TZID=Asia/Ho_Chi_Minh:20261113T141200" in ics_res.text
    assert "DTEND;TZID=Asia/Ho_Chi_Minh:20261113T151200" in ics_res.text


def test_future_week_unscheduled_diagnostics_dated_and_weekday_only(tmp_path, monkeypatch):
    """Future selected week deadlines report cap/window reasons rather than false past-deadline errors."""
    from zoneinfo import ZoneInfo
    from app import study_planner

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    frozen_sat = datetime(2026, 11, 14, 12, 0, 0, tzinfo=vn_tz)
    _freeze_planner_now(monkeypatch, frozen_sat)

    # 1. Dated task due Monday 2026-11-16 23:59 with daily cap 30m in selected future week 2026-11-16
    future_db = tmp_path / "future_dated_cap.db"
    _setup_cutoff_preferences(
        future_db,
        week_start_date="2026-11-16",
        max_daily_study_minutes=30,
        preferred_session_minutes=30,
        min_session_minutes=30,
        buffer_before_deadline_hours=0,
    )
    study_planner.add_task(
        {
            "title": "Future Monday task",
            "course": "Fixture",
            "deadline_weekday": 1,
            "deadline_time": "23:59",
            "deadline_date": "2026-11-16",
            "estimated_minutes": 180,
            "priority": "medium",
            "splittable": True,
        },
        db_path=future_db,
    )
    future_draft = study_planner.generate_study_plan(db_path=future_db)
    assert len(future_draft["sessions"]) == 1
    assert future_draft["sessions"][0]["weekday"] == 1
    assert future_draft["sessions"][0]["duration_minutes"] == 30
    assert len(future_draft["unscheduled_tasks"]) == 1
    unsched = future_draft["unscheduled_tasks"][0]
    assert unsched["scheduled_minutes"] == 30
    assert unsched["missing_minutes"] == 150
    assert "đã qua hạn nộp" not in unsched["reason"]
    assert "chưa khai báo khung giờ" not in unsched["reason"]
    assert "chạm giới hạn học tối đa mỗi ngày" in unsched["reason"]
    sugg_types = [s["type"] for s in future_draft["suggestions"]]
    assert "increase_daily_limit" in sugg_types
    assert "update_deadline" not in sugg_types
    assert "add_study_window" not in sugg_types

    inc_sugg = next(s for s in future_draft["suggestions"] if s["type"] == "increase_daily_limit")
    assert inc_sugg["new_max_daily_minutes"] == 180
    study_planner.apply_planner_suggestion(inc_sugg, db_path=future_db)
    resolved_draft = study_planner.generate_study_plan(db_path=future_db)
    assert resolved_draft["unscheduled_tasks"] == []
    assert sum(s["duration_minutes"] for s in resolved_draft["sessions"]) == 180

    # 2. Weekday-only task (no deadline_date) in future selected week 2026-11-16
    weekday_only_future_db = tmp_path / "future_weekday_only.db"
    _setup_cutoff_preferences(
        weekday_only_future_db,
        week_start_date="2026-11-16",
        max_daily_study_minutes=30,
        preferred_session_minutes=30,
        min_session_minutes=30,
        buffer_before_deadline_hours=0,
    )
    study_planner.add_task(
        {
            "title": "Weekday-only future task",
            "course": "Fixture",
            "deadline_weekday": 1,
            "deadline_time": "23:59",
            "deadline_date": None,
            "estimated_minutes": 180,
            "priority": "medium",
            "splittable": True,
        },
        db_path=weekday_only_future_db,
    )
    wo_draft = study_planner.generate_study_plan(db_path=weekday_only_future_db)
    assert len(wo_draft["sessions"]) == 1
    assert len(wo_draft["unscheduled_tasks"]) == 1
    assert "đã qua hạn nộp" not in wo_draft["unscheduled_tasks"][0]["reason"]
    assert "chạm giới hạn học tối đa mỗi ngày" in wo_draft["unscheduled_tasks"][0]["reason"]
    assert any(s["type"] == "increase_daily_limit" for s in wo_draft["suggestions"])

    # 3. Same-week already-expired deadlines (both dated and weekday-only) still report expired
    for suffix, d_date in (("dated", "2026-11-09"), ("weekday_only", None)):
        expired_db = tmp_path / f"same_week_expired_{suffix}.db"
        _setup_cutoff_preferences(expired_db, week_start_date="2026-11-09", max_daily_study_minutes=180)
        study_planner.add_task(
            {
                "title": f"Past Monday {suffix}",
                "course": "Fixture",
                "deadline_weekday": 1,
                "deadline_time": "23:59",
                "deadline_date": d_date,
                "estimated_minutes": 60,
                "priority": "high",
                "splittable": True,
            },
            db_path=expired_db,
        )
        exp_draft = study_planner.generate_study_plan(db_path=expired_db)
        assert exp_draft["sessions"] == []
        assert len(exp_draft["unscheduled_tasks"]) == 1
        assert "đã qua hạn nộp" in exp_draft["unscheduled_tasks"][0]["reason"]
        assert any(s["type"] == "update_deadline" for s in exp_draft["suggestions"])

    # 4. Same-week future Saturday evening deadline (2026-11-14 23:59 when now is 12:00) stays future
    sat_cap_db = tmp_path / "same_week_future_sat.db"
    _setup_cutoff_preferences(
        sat_cap_db,
        week_start_date="2026-11-09",
        max_daily_study_minutes=30,
        preferred_session_minutes=30,
        min_session_minutes=30,
        buffer_before_deadline_hours=0,
    )
    study_planner.add_task(
        {
            "title": "Saturday evening task",
            "course": "Fixture",
            "deadline_weekday": 6,
            "deadline_time": "23:59",
            "deadline_date": "2026-11-14",
            "estimated_minutes": 120,
            "priority": "high",
            "splittable": True,
        },
        db_path=sat_cap_db,
    )
    sat_draft = study_planner.generate_study_plan(db_path=sat_cap_db)
    assert len(sat_draft["sessions"]) == 1
    assert sat_draft["sessions"][0]["start_time"] == "12:00"
    assert len(sat_draft["unscheduled_tasks"]) == 1
    assert "đã qua hạn nộp" not in sat_draft["unscheduled_tasks"][0]["reason"]
    assert "chạm giới hạn học tối đa mỗi ngày" in sat_draft["unscheduled_tasks"][0]["reason"]


def test_diagnose_unscheduled_task_boundary_cutoffs_and_timezone_normalization():
    """Direct diagnostic checks for cutoff 0/8/1440, year boundaries, timezone handling, and study_window mode."""
    from datetime import date, timezone as _tz
    from zoneinfo import ZoneInfo
    from app import study_planner

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    base_task = {
        "id": 7,
        "title": "Boundary task",
        "deadline_display": "Thứ 2 23:59",
    }

    # cutoff_w == 0 returns before-week message before any date arithmetic
    reason_zero, suggs_zero = study_planner._diagnose_unscheduled_task(
        task=base_task,
        missing_minutes=60,
        cutoff_w=0,
        cutoff_min=0,
        free_slots_by_day={},
        blocked_by_day={},
        daily_used_minutes={},
        max_daily_min=120,
        earliest_min=420,
        latest_min=1350,
        routines=[],
        week_monday=date(2026, 11, 16),
        ceil_now=datetime(2026, 11, 14, 12, 0, tzinfo=vn_tz),
    )
    assert "nằm trước tuần lập lịch hiện tại" in reason_zero
    assert any(s["type"] == "update_deadline" for s in suggs_zero)

    # cutoff_w == 8 and cutoff_min == 1440 across year boundary must not crash or trigger false expiry
    for cw, cm in ((7, 1440), (8, 0), (8, 1440)):
        reason_hi, suggs_hi = study_planner._diagnose_unscheduled_task(
            task=base_task,
            missing_minutes=120,
            cutoff_w=cw,
            cutoff_min=cm,
            free_slots_by_day={1: [(450, 1350)]},
            blocked_by_day={},
            daily_used_minutes={1: 30},
            max_daily_min=30,
            earliest_min=420,
            latest_min=1350,
            routines=[],
            week_monday=date(2026, 12, 28),
            ceil_now=datetime(2026, 12, 27, 12, 0, tzinfo=vn_tz),
        )
        assert "đã qua hạn nộp" not in reason_hi
        assert "chạm giới hạn học tối đa mỗi ngày" in reason_hi
        assert any(s["type"] == "increase_daily_limit" for s in suggs_hi)
        assert not any(s["type"] == "add_study_window" for s in suggs_hi)

    # Naive and UTC ceil_now normalize cleanly to Asia/Ho_Chi_Minh on exact boundary
    naive_ceil = datetime(2026, 11, 14, 12, 0)
    utc_ceil = datetime(2026, 11, 14, 5, 0, tzinfo=_tz.utc)
    for ceil_variant in (naive_ceil, utc_ceil):
        r_eq, _ = study_planner._diagnose_unscheduled_task(
            task=base_task,
            missing_minutes=60,
            cutoff_w=6,
            cutoff_min=720,
            free_slots_by_day={},
            blocked_by_day={},
            daily_used_minutes={},
            max_daily_min=180,
            earliest_min=420,
            latest_min=1350,
            routines=[],
            week_monday=date(2026, 11, 9),
            ceil_now=ceil_variant,
        )
        assert "đã qua hạn nộp" in r_eq

        r_next_min, suggs_next = study_planner._diagnose_unscheduled_task(
            task=base_task,
            missing_minutes=60,
            cutoff_w=6,
            cutoff_min=721,
            free_slots_by_day={},
            blocked_by_day={},
            daily_used_minutes={},
            max_daily_min=180,
            earliest_min=420,
            latest_min=1350,
            routines=[{"id": 1, "weekday": 6, "start_time": "12:00", "end_time": "12:01", "flexibility": "study_window", "activity_type": "study_window"}],
            week_monday=date(2026, 11, 9),
            ceil_now=ceil_variant,
            past_cutoff_by_day={1: 1440, 2: 1440, 3: 1440, 4: 1440, 5: 1440, 6: 720, 7: 0},
        )
        assert "đã qua hạn nộp" not in r_next_min
        assert "không còn đủ" in r_next_min
        assert suggs_next == []

    # When explicit study_windows exist and exhaust before deadline, add_study_window is suggested
    reason_sw, suggs_sw = study_planner._diagnose_unscheduled_task(
        task=base_task,
        missing_minutes=90,
        cutoff_w=1,
        cutoff_min=1439,
        free_slots_by_day={},
        blocked_by_day={},
        daily_used_minutes={1: 60},
        max_daily_min=180,
        earliest_min=420,
        latest_min=1350,
        routines=[{"id": 2, "weekday": 1, "start_time": "20:00", "end_time": "21:00", "flexibility": "study_window", "activity_type": "study_window"}],
        week_monday=date(2026, 11, 16),
        ceil_now=datetime(2026, 11, 14, 12, 0, tzinfo=vn_tz),
    )
    assert "tổng thời gian trống trong các 'Khung giờ có thể học'" in reason_sw
    assert any(s["type"] == "add_study_window" for s in suggs_sw)


def test_http_future_week_unscheduled_diagnostic_and_suggestion_apply(isolated_client, monkeypatch):
    """HTTP TestClient end-to-end check on simulated clock: future unfilled task applies daily limit suggestion cleanly."""
    from zoneinfo import ZoneInfo

    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    _freeze_planner_now(monkeypatch, datetime(2026, 11, 14, 12, 0, 0, tzinfo=vn_tz))

    pref_res = isolated_client.put("/api/timetable/planner/preferences", json={
        "earliest_study_time": "07:00",
        "latest_study_time": "22:30",
        "max_daily_study_minutes": 30,
        "preferred_session_minutes": 30,
        "min_session_minutes": 30,
        "buffer_before_deadline_hours": 0,
        "ai_notes": "",
        "week_start_date": "2026-11-16",
    })
    assert pref_res.status_code == 200

    task_res = isolated_client.post("/api/timetable/planner/tasks", json={
        "title": "Future Monday HTTP task",
        "course": "Fixture",
        "deadline_date": "2026-11-16",
        "deadline_weekday": 1,
        "deadline_time": "23:59",
        "estimated_minutes": 180,
        "priority": "medium",
        "splittable": True,
    })
    assert task_res.status_code == 200

    gen_res = isolated_client.post("/api/timetable/planner/generate", json={})
    assert gen_res.status_code == 200
    draft = gen_res.json()["draft"]
    assert len(draft["sessions"]) == 1
    assert draft["sessions"][0]["start_time"] == "07:00"
    assert draft["sessions"][0]["duration_minutes"] == 30
    assert len(draft["unscheduled_tasks"]) == 1
    assert draft["unscheduled_tasks"][0]["missing_minutes"] == 150
    assert "đã qua hạn nộp" not in draft["unscheduled_tasks"][0]["reason"]

    inc_sugg = next((s for s in draft["suggestions"] if s["type"] == "increase_daily_limit"), None)
    assert inc_sugg is not None
    assert isolated_client.post("/api/timetable/planner/apply-suggestion", json=inc_sugg).status_code == 200

    gen2 = isolated_client.post("/api/timetable/planner/generate", json={})
    assert gen2.status_code == 200
    draft2 = gen2.json()["draft"]
    assert draft2["unscheduled_tasks"] == []
    assert sum(s["duration_minutes"] for s in draft2["sessions"]) == 180

    conf_res = isolated_client.post("/api/timetable/planner/confirm", json={
        "plan_id": draft2["plan_id"],
        "sessions": draft2["sessions"],
    })
    assert conf_res.status_code == 200
    assert conf_res.json()["saved_count"] == len(draft2["sessions"])

    ics_res = isolated_client.get("/api/timetable/export.ics?start_date=2026-11-16&end_date=2026-11-22&include_planner=true")
    assert ics_res.status_code == 200
    assert "Future Monday HTTP task" in ics_res.text
    assert "DTSTART;TZID=Asia/Ho_Chi_Minh:20261116T070000" in ics_res.text


