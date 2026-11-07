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


