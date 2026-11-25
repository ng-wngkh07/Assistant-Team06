"""Timetable & Vision Schedule API Routes (Single Responsibility Module)."""

from datetime import date, timedelta
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from app import study_planner
from app.gpu_lock import GPUBusyError
from app.response_projection import sanitize_error_detail

logger = logging.getLogger("psychology_agent")

router = APIRouter()

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class TimetableExtractRequest(BaseModel):
    filename: str = "schedule.png"
    image_base64: str


class TimetableConfirmEntry(BaseModel):
    course: str
    weekday: int
    start_time: str
    end_time: str
    room: Optional[str] = None
    period: Optional[str] = None

    @field_validator("course")
    @classmethod
    def validate_course(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Tên môn học không được để trống")
        return s

    @field_validator("weekday")
    @classmethod
    def validate_weekday(cls, v: int) -> int:
        if v < 1 or v > 7:
            raise ValueError("Thứ trong tuần phải từ 1 đến 7")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        s = v.strip()
        if not TIME_PATTERN.match(s):
            raise ValueError(f"Giờ không hợp lệ ({v}). Định dạng phải là HH:MM (00:00 - 23:59)")
        return s

    @model_validator(mode="after")
    def validate_range(self):
        if self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                raise ValueError("Giờ kết thúc phải lớn hơn giờ bắt đầu")
        return self


class TimetableConfirmRequest(BaseModel):
    draft_id: str
    entries: List[TimetableConfirmEntry]
    replace_existing: bool = False
    clear_existing: Optional[bool] = None

    @model_validator(mode="after")
    def resolve_replace(self):
        if self.clear_existing is not None:
            self.replace_existing = bool(self.clear_existing or self.replace_existing)
        return self


class TimetableManualEntryRequest(BaseModel):
    course: str
    weekday: int
    start_time: str
    end_time: str
    room: Optional[str] = None

    @field_validator("course")
    @classmethod
    def validate_course(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Tên môn học không được để trống")
        return s

    @field_validator("weekday")
    @classmethod
    def validate_weekday(cls, v: int) -> int:
        if v < 1 or v > 7:
            raise ValueError("Thứ trong tuần phải từ 1 đến 7")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        s = v.strip()
        if not TIME_PATTERN.match(s):
            raise ValueError(f"Giờ không hợp lệ ({v}). Định dạng phải là HH:MM (00:00 - 23:59)")
        return s

    @model_validator(mode="after")
    def validate_range(self):
        if self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                raise ValueError("Giờ kết thúc phải lớn hơn giờ bắt đầu")
        return self


@router.get("/api/timetable")
async def get_timetable():
    from app import server as _srv
    entries = await run_in_threadpool(_srv.timetable_store.get_confirmed_entries)
    return {"entries": entries}


@router.post("/api/timetable/extract")
async def extract_timetable(payload: TimetableExtractRequest):
    from app import server as _srv
    try:
        image_bytes, image_hash = await run_in_threadpool(
            _srv.timetable_vision.validate_image_payload,
            payload.filename,
            payload.image_base64,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=sanitize_error_detail(str(e)))

    try:
        result = await run_in_threadpool(
            _srv.timetable_vision.call_vlm_extract,
            image_bytes,
            image_hash,
            payload.filename,
        )
        return result
    except GPUBusyError as e:
        raise HTTPException(status_code=503, detail=sanitize_error_detail(str(e)))
    except _srv.timetable_vision.VLMUnavailableError as e:
        raise HTTPException(status_code=503, detail=sanitize_error_detail(str(e)))
    except _srv.timetable_vision.VLMOutputInvalidError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error extracting timetable from image")
        raise HTTPException(status_code=500, detail="Lỗi trích xuất thời khóa biểu từ hình ảnh.")


@router.post("/api/timetable/confirm")
async def confirm_timetable(payload: TimetableConfirmRequest):
    from app import server as _srv
    if not payload.entries:
        raise HTTPException(status_code=422, detail="Danh sách môn học xác nhận không được để trống")
    entries_data = [e.model_dump() for e in payload.entries]
    try:
        saved_count = await run_in_threadpool(
            _srv.timetable_store.confirm_draft,
            payload.draft_id,
            entries_data,
            clear_existing=payload.replace_existing,
        )
        return {"status": "ok", "saved_entries": saved_count, "entries": entries_data}
    except ValueError as e:
        msg = sanitize_error_detail(str(e))
        if "Draft not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        if "Xung đột" in msg or "Trùng lịch" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=422, detail=msg)


@router.get("/api/timetable/drafts/{draft_id}")
async def get_timetable_draft(draft_id: str):
    from app import server as _srv
    draft = await run_in_threadpool(_srv.timetable_store.get_draft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.get("/api/timetable/images/{image_hash}")
async def get_timetable_image(image_hash: str):
    from app import server as _srv
    if not re.match(r"^[a-f0-9]{64}$", image_hash):
        raise HTTPException(status_code=400, detail="Invalid image hash")
    image_path = await run_in_threadpool(_srv.timetable_store.get_image_path, image_hash)
    if not image_path or not image_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    data = await run_in_threadpool(image_path.read_bytes)
    media_type = (
        "image/png"
        if data.startswith(b"\x89PNG")
        else ("image/jpeg" if data.startswith(b"\xff\xd8") else "image/webp")
    )
    return Response(content=data, media_type=media_type)


@router.post("/api/timetable/manual")
async def add_timetable_manual_entry(payload: TimetableManualEntryRequest):
    from app import server as _srv
    try:
        entry_id = await run_in_threadpool(
            _srv.timetable_store.add_manual_entry,
            course=payload.course,
            weekday=payload.weekday,
            start_time=payload.start_time,
            end_time=payload.end_time,
            room=payload.room,
        )
        return {"status": "ok", "id": entry_id}
    except ValueError as e:
        msg = sanitize_error_detail(str(e))
        if "Xung đột" in msg or "Trùng lịch" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=422, detail=msg)


@router.put("/api/timetable/entries/{entry_id}")
async def update_timetable_entry(entry_id: int, payload: TimetableManualEntryRequest):
    from app import server as _srv
    try:
        updated = await run_in_threadpool(
            _srv.timetable_store.update_entry,
            entry_id=entry_id,
            course=payload.course,
            weekday=payload.weekday,
            start_time=payload.start_time,
            end_time=payload.end_time,
            room=payload.room,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Entry not found")
        return {"status": "ok", "id": entry_id}
    except ValueError as e:
        msg = sanitize_error_detail(str(e))
        if "Xung đột" in msg or "Trùng lịch" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=422, detail=msg)


@router.delete("/api/timetable/entries/{entry_id}")
async def delete_timetable_entry(entry_id: int):
    from app import server as _srv
    success = await run_in_threadpool(_srv.timetable_store.delete_entry, entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "ok"}


@router.delete("/api/timetable")
async def clear_timetable():
    from app import server as _srv
    await run_in_threadpool(_srv.timetable_store.clear_all_entries)
    return {"status": "ok"}


def _routine_ics_entries(routines: List[Dict[str, Any]], week_start) -> List[Dict[str, Any]]:
    """Convert non-study-window routines into single-day ICS entries (splitting overnight spans)."""
    out: List[Dict[str, Any]] = []
    for r in routines:
        if r.get("flexibility") == "study_window":
            continue
        w = int(r["weekday"])
        st = str(r["start_time"])
        et = str(r["end_time"])
        is_rec = bool(r.get("is_recurring", True))
        fixed_date = None if is_rec else (str(r.get("routine_date") or "").strip() or (week_start + timedelta(days=w - 1)).isoformat())
        base = {
            "course": f"[Cá nhân] {r['title']}",
            "room": r.get("activity_type_label") or "",
            "notes": r.get("flexibility_label") or "",
        }
        if st < et:
            out.append({
                **base,
                "uid": f"routine:{r['id']}",
                "event_date": fixed_date,
                "weekday": w,
                "start_time": st,
                "end_time": et,
            })
        else:
            out.append({
                **base,
                "uid": f"routine:{r['id']}:pm",
                "event_date": fixed_date,
                "weekday": w,
                "start_time": st,
                "end_time": "23:59",
            })
            if et > "00:00":
                next_w = (w % 7) + 1
                next_fixed_date = None
                if not is_rec:
                    try:
                        next_fixed_date = (date.fromisoformat(fixed_date) + timedelta(days=1)).isoformat()
                    except ValueError:
                        next_fixed_date = (week_start + timedelta(days=next_w - 1)).isoformat()
                out.append({
                    **base,
                    "uid": f"routine:{r['id']}:am",
                    "event_date": next_fixed_date,
                    "weekday": next_w,
                    "start_time": "00:00",
                    "end_time": et,
                })
    return out


def _resolve_public_base_url(request: Optional[Request] = None) -> str:
    env_url = os.getenv("STUDY_PUBLIC_URL", "").strip()
    if env_url.startswith(("http://", "https://")):
        return env_url.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return ""


@router.get("/api/timetable/export.ics")
async def export_timetable_ics(
    start_date: str = Query(..., description="Start date of semester in YYYY-MM-DD"),
    end_date: str = Query(..., description="End date of semester in YYYY-MM-DD"),
    include_planner: bool = Query(False, description="Include personal routines, confirmed study sessions and dated deadlines"),
    reminder_minutes: int = Query(60, ge=0, le=10080),
    request: Request = None,
):
    from app import server as _srv
    try:
        entries = list(await run_in_threadpool(_srv.timetable_store.get_confirmed_entries))
        for entry in entries:
            if entry.get("id") is not None:
                entry["uid"] = f"class:{entry['id']}"
        deadlines = []
        if include_planner:
            preferences = await run_in_threadpool(study_planner.get_preferences)
            from app.accounts import account_id
            tasks = await run_in_threadpool(study_planner.get_tasks)
            week_start = study_planner._infer_week_monday(preferences, tasks)
            if week_start is None:
                sd = date.fromisoformat(start_date)
                week_start = sd - timedelta(days=sd.weekday())
            routines = await run_in_threadpool(study_planner.get_routines)
            entries.extend(_routine_ics_entries(routines, week_start))
            sessions = await run_in_threadpool(study_planner.get_study_sessions)
            for s in sessions:
                entries.append({
                    "uid": f"study:{s['id']}",
                    "event_date": s.get("session_date") or (week_start + timedelta(days=s["weekday"]-1)).isoformat(),
                    "course": f"[Tự học - {s['course']}] {s['title']}",
                    "weekday": s["weekday"],
                    "start_time": s["start_time"],
                    "end_time": s["end_time"],
                    "room": s.get("source_label") or "",
                    "notes": s.get("notes") or "",
                    "classified_topic": s.get("classified_topic") or "",
                    "pdf_url": s.get("pdf_url") or "",
                })
            deadlines = [t for t in tasks if t.get("deadline_date") and not t.get("completed")]
        if not entries and not deadlines:
            raise HTTPException(
                status_code=400,
                detail="Thời khóa biểu hiện chưa có môn học nào để xuất lịch. Vui lòng thêm môn học trước.",
            )
        from app.accounts import account_id
        base_url = _resolve_public_base_url(request)
        ics_content = _srv.timetable_ics.generate_ics(
            entries,
            start_date,
            end_date,
            deadlines=deadlines,
            reminder_minutes=reminder_minutes,
            namespace=account_id.get() or "local",
            base_url=base_url or None,
        )
        return Response(
            content=ics_content,
            media_type="text/calendar; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="timetable_{start_date}_{end_date}.ics"'
            },
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))
    except Exception:
        logger.exception("Lỗi xuất lịch sang ICS")
        raise HTTPException(status_code=500, detail="Lỗi xuất tệp lịch .ics.")


# ==========================================
# Personal Study Schedule Assistant Endpoints
# ==========================================


class RoutineItemPayload(BaseModel):
    weekday: int
    start_time: str
    end_time: str
    title: str
    activity_type: str = "other"
    flexibility: Optional[str] = None
    is_recurring: bool = True
    note: Optional[str] = ""


class RoutineBulkPayload(BaseModel):
    routines: List[RoutineItemPayload]


class StudyPreferencesPayload(BaseModel):
    earliest_study_time: Optional[str] = None
    latest_study_time: Optional[str] = None
    max_daily_study_minutes: Optional[int] = None
    preferred_session_minutes: Optional[int] = None
    min_session_minutes: Optional[int] = None
    buffer_before_deadline_hours: Optional[int] = None
    ai_notes: Optional[str] = None
    week_start_date: Optional[str] = None


class StudyTaskCreatePayload(BaseModel):
    title: str
    course: str
    deadline: Optional[str] = None
    deadline_weekday: Optional[int] = None
    deadline_time: Optional[str] = "23:59"
    deadline_date: Optional[str] = None
    estimated_minutes: Optional[int] = None
    priority: str = "medium"
    completed: bool = False
    splittable: bool = True
    notes: Optional[str] = ""


class StudyTaskUpdatePayload(BaseModel):
    title: Optional[str] = None
    course: Optional[str] = None
    deadline: Optional[str] = None
    deadline_weekday: Optional[int] = None
    deadline_time: Optional[str] = None
    deadline_date: Optional[str] = None
    estimated_minutes: Optional[int] = None
    priority: Optional[str] = None
    completed: Optional[bool] = None
    splittable: Optional[bool] = None
    notes: Optional[str] = None


class StudyPlanGeneratePayload(BaseModel):
    reschedule_only: bool = False
    ai_notes: Optional[str] = None
    require_estimates: bool = False
    include_courses: Optional[bool] = None


class StudyPlanConfirmPayload(BaseModel):
    plan_id: str
    sessions: Optional[List[Dict[str, Any]]] = None


class StudySessionPayload(BaseModel):
    task_id: Optional[int] = None
    title: str
    course: str
    weekday: int
    start_time: str
    end_time: str
    priority: Optional[str] = "medium"
    completed: Optional[bool] = False
    locked: Optional[bool] = True
    notes: Optional[str] = ""


class StudySessionUpdatePayload(BaseModel):
    title: Optional[str] = None
    course: Optional[str] = None
    weekday: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    completed: Optional[bool] = None
    locked: Optional[bool] = None
    notes: Optional[str] = None


@router.get("/api/timetable/planner/state")
async def get_planner_state():
    return await run_in_threadpool(study_planner.get_full_planner_state)


@router.get("/api/timetable/planner/schedule")
async def get_planner_combined_schedule():
    return await run_in_threadpool(study_planner.get_combined_weekly_schedule)


@router.get("/api/timetable/planner/routines")
async def list_planner_routines():
    routines = await run_in_threadpool(study_planner.get_routines)
    return {"routines": routines}


@router.post("/api/timetable/planner/routines")
async def create_planner_routine(payload: RoutineItemPayload):
    try:
        res = await run_in_threadpool(study_planner.add_routine, payload.model_dump())
        return {"status": "ok", **res}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))


@router.put("/api/timetable/planner/routines/bulk")
async def replace_planner_routines_bulk(payload: RoutineBulkPayload):
    try:
        items = [r.model_dump() for r in payload.routines]
        res = await run_in_threadpool(study_planner.replace_routines_bulk, items)
        return {"status": "ok", **res}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))


@router.put("/api/timetable/planner/routines/{routine_id}")
async def update_planner_routine(routine_id: int, payload: RoutineItemPayload):
    try:
        res = await run_in_threadpool(study_planner.update_routine, routine_id, payload.model_dump())
        return {"status": "ok", **res}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=sanitize_error_detail(str(e)))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))


@router.delete("/api/timetable/planner/routines/{routine_id}")
async def delete_planner_routine(routine_id: int):
    ok = await run_in_threadpool(study_planner.delete_routine, routine_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy mục lịch sinh hoạt")
    return {"status": "ok"}


@router.get("/api/timetable/planner/preferences")
async def get_planner_preferences():
    prefs = await run_in_threadpool(study_planner.get_preferences)
    return {"preferences": prefs}


@router.put("/api/timetable/planner/preferences")
async def save_planner_preferences(payload: StudyPreferencesPayload):
    try:
        clean = {k: v for k, v in payload.model_dump().items() if v is not None}
        prefs = await run_in_threadpool(study_planner.update_preferences, clean)
        return {"status": "ok", "preferences": prefs}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))


@router.get("/api/timetable/planner/tasks")
async def list_planner_tasks():
    tasks = await run_in_threadpool(study_planner.get_tasks)
    return {"tasks": tasks}


@router.post("/api/timetable/planner/tasks")
async def create_planner_task(payload: StudyTaskCreatePayload):
    try:
        task = await run_in_threadpool(study_planner.add_task, payload.model_dump())
        return {"status": "ok", "task": task}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))


@router.put("/api/timetable/planner/tasks/{task_id}")
async def update_planner_task(task_id: int, payload: StudyTaskUpdatePayload):
    try:
        clean = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
        task = await run_in_threadpool(study_planner.update_task, task_id, clean)
        return {"status": "ok", "task": task}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=sanitize_error_detail(str(e)))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))


@router.delete("/api/timetable/planner/tasks/{task_id}")
async def delete_planner_task(task_id: int):
    ok = await run_in_threadpool(study_planner.delete_task, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài tập")
    return {"status": "ok"}


@router.post("/api/timetable/planner/generate")
async def generate_planner_schedule(payload: Optional[StudyPlanGeneratePayload] = None):
    req = payload or StudyPlanGeneratePayload()
    if req.require_estimates:
        tasks = await run_in_threadpool(study_planner.get_tasks)
        missing = [t for t in tasks if not t.get("completed") and t.get("missing_estimate")]
        if missing:
            names = ", ".join(f"'{t['title']}' ({t['course']})" for t in missing)
            raise HTTPException(
                status_code=422,
                detail=f"Vui lòng nhập thời gian ước tính hoàn thành (phút) cho: {names} trước khi tạo kế hoạch.",
            )
    try:
        draft = await run_in_threadpool(
            study_planner.generate_study_plan,
            reschedule_only=req.reschedule_only,
            ai_notes_override=req.ai_notes,
            include_courses=req.include_courses,
        )
        return {"status": "ok", "draft": draft}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))


@router.get("/api/timetable/planner/drafts/{plan_id}")
async def get_planner_draft_endpoint(plan_id: str):
    draft = await run_in_threadpool(study_planner.get_plan_draft, plan_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản nháp kế hoạch")
    return draft


@router.delete("/api/timetable/planner/drafts/{plan_id}")
async def discard_planner_draft_endpoint(plan_id: str):
    ok = await run_in_threadpool(study_planner.discard_plan_draft, plan_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản nháp kế hoạch")
    return {"status": "ok"}


@router.post("/api/timetable/planner/confirm")
async def confirm_planner_draft_endpoint(payload: StudyPlanConfirmPayload):
    try:
        res = await run_in_threadpool(
            study_planner.confirm_study_plan,
            payload.plan_id,
            payload.sessions,
        )
        return res
    except KeyError as e:
        raise HTTPException(status_code=404, detail=sanitize_error_detail(str(e)))
    except ValueError as e:
        msg = sanitize_error_detail(str(e))
        if any(kw in msg for kw in (
            "trùng giờ", "xâm phạm", "lỗi thời", "xung đột", "không còn hợp lệ",
            "giới hạn", "vượt quá", "ngoài khung giờ", "hạn nộp", "bắt đầu trước",
            "kết thúc sau", "khung giờ đêm", "thiết lập", "hoàn thành",
            "không tồn tại", "đã bị xóa", "không còn tồn tại",
        )):
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=422, detail=msg)


@router.post("/api/timetable/planner/apply-suggestion")
async def apply_planner_suggestion_endpoint(payload: Dict[str, Any]):
    try:
        res = await run_in_threadpool(study_planner.apply_planner_suggestion, payload)
        return res
    except KeyError as e:
        raise HTTPException(status_code=404, detail=sanitize_error_detail(str(e)))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(e)))


@router.get("/api/timetable/planner/sessions")
async def list_planner_sessions():
    sessions = await run_in_threadpool(study_planner.get_study_sessions)
    return {"sessions": sessions}


@router.post("/api/timetable/planner/sessions")
async def add_planner_session(payload: StudySessionPayload):
    try:
        session = await run_in_threadpool(study_planner.add_manual_study_session, payload.model_dump())
        warnings = await run_in_threadpool(study_planner.collect_manual_session_warnings, session)
        return {"status": "ok", "session": session, "warnings": warnings}
    except ValueError as e:
        msg = sanitize_error_detail(str(e))
        if any(kw in msg for kw in (
            "trùng giờ", "xâm phạm", "lỗi thời", "xung đột", "không còn hợp lệ",
            "giới hạn", "vượt quá", "ngoài khung giờ", "hạn nộp", "bắt đầu trước",
            "kết thúc sau", "khung giờ đêm", "thiết lập", "hoàn thành",
            "không tồn tại", "đã bị xóa", "không còn tồn tại",
        )):
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=422, detail=msg)


@router.put("/api/timetable/planner/sessions/{session_id}")
async def update_planner_session(session_id: int, payload: StudySessionUpdatePayload):
    try:
        clean = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
        session = await run_in_threadpool(study_planner.update_study_session, session_id, clean)
        warnings = await run_in_threadpool(study_planner.collect_manual_session_warnings, session)
        return {"status": "ok", "session": session, "warnings": warnings}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=sanitize_error_detail(str(e)))
    except ValueError as e:
        msg = sanitize_error_detail(str(e))
        if any(kw in msg for kw in (
            "trùng giờ", "xâm phạm", "lỗi thời", "xung đột", "không còn hợp lệ",
            "giới hạn", "vượt quá", "ngoài khung giờ", "hạn nộp", "bắt đầu trước",
            "kết thúc sau", "khung giờ đêm", "thiết lập", "hoàn thành",
            "không tồn tại", "đã bị xóa", "không còn tồn tại",
        )):
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=422, detail=msg)


@router.delete("/api/timetable/planner/sessions/{session_id}")
async def delete_planner_session(session_id: int):
    ok = await run_in_threadpool(study_planner.delete_study_session, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên học")
    return {"status": "ok"}


# ==============================================================================
# Weekly Lessons Topic Classifier, 1h30p (1 Subject/Day) Auto-Scheduler & PDF Bundle
# ==============================================================================


class WeeklyLessonCreatePayload(BaseModel):
    course: str = Field(..., min_length=1, max_length=160)
    lesson_title: str = Field(..., min_length=1, max_length=240)
    status: str = Field(default="upcoming", max_length=24)
    question_count: int = Field(default=4, ge=1, le=10)


class AutoSchedule90mPayload(BaseModel):
    session_minutes: int = Field(default=90, ge=15, le=240)
    propose_only: bool = Field(default=False)


@router.get("/api/timetable/planner/weekly-lessons")
async def get_weekly_lessons_endpoint():
    from app.planner_topic_bridge import list_weekly_lessons

    lessons = await run_in_threadpool(list_weekly_lessons, None)
    return {"lessons": lessons}


@router.post("/api/timetable/planner/weekly-lessons")
async def create_weekly_lesson_endpoint(payload: WeeklyLessonCreatePayload):
    from app import server as _srv
    from app.planner_topic_bridge import add_weekly_lesson

    try:
        lesson = await run_in_threadpool(
            add_weekly_lesson,
            None,
            course=payload.course,
            lesson_title=payload.lesson_title,
            status=payload.status,
            question_count=payload.question_count,
            learning_service=getattr(_srv, "learning_service", None),
            document_lookup=getattr(_srv, "document_lookup", None),
        )
        return {"status": "ok", "lesson": lesson}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(exc)))


@router.delete("/api/timetable/planner/weekly-lessons/{lesson_id}")
async def remove_weekly_lesson_endpoint(lesson_id: int):
    from app.planner_topic_bridge import delete_weekly_lesson

    ok = await run_in_threadpool(delete_weekly_lesson, None, lesson_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài học tuần.")
    return {"status": "ok"}


@router.post("/api/timetable/planner/auto-schedule-courses")
async def auto_schedule_courses_90m_endpoint(payload: Optional[AutoSchedule90mPayload] = None):
    from app import server as _srv
    from app.planner_topic_bridge import auto_schedule_90m_one_subject_per_day

    req = payload or AutoSchedule90mPayload()
    try:
        result = await run_in_threadpool(
            auto_schedule_90m_one_subject_per_day,
            None,
            learning_service=getattr(_srv, "learning_service", None),
            document_lookup=getattr(_srv, "document_lookup", None),
            session_minutes=req.session_minutes,
            propose_only=req.propose_only,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(exc)))


@router.get("/api/timetable/planner/sessions/{session_id}/exercise")
async def get_session_exercise_endpoint(session_id: int):
    session = await run_in_threadpool(study_planner.get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên học.")
    p_id = session.get("practice_id")
    ws_id = session.get("worksheet_id")
    return {
        "session_id": session_id,
        "course": session.get("course"),
        "classified_topic": session.get("classified_topic"),
        "practice_id": p_id,
        "worksheet_id": ws_id,
        "exercise_status": session.get("exercise_status") or ("available" if (p_id or ws_id) else "not_generated"),
        "pdf_url": session.get("pdf_url"),
    }


@router.post("/api/timetable/planner/sessions/{session_id}/exercise/generate")
async def generate_session_exercise_endpoint(session_id: int):
    from app import server as _srv
    from app.dialogue import DialogueBusyError, acquire_inference
    from app.gpu_lock import GPUBusyError
    from app.learning import PracticeGenerationError
    from app.practice import PracticeSessionNotFound, is_valid_practice_session
    from app.planner_topic_bridge import _connect

    session = await run_in_threadpool(study_planner.get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên học.")

    # Reuse existing practice session if already available
    existing_pid = (session.get("practice_id") or "").strip()
    if existing_pid and is_valid_practice_session(existing_pid):
        try:
            existing_practice = await run_in_threadpool(_srv.practice_service.get_session, existing_pid)
            return {
                "status": "ok",
                "session": session,
                "practice": existing_practice,
            }
        except PracticeSessionNotFound:
            pass

    # Check if linked to a weekly lesson that already has an available practice session
    w_lesson_id = session.get("weekly_lesson_id")
    if w_lesson_id:
        from app.planner_topic_bridge import list_weekly_lessons
        lessons = await run_in_threadpool(list_weekly_lessons, None)
        linked_l = next((l for l in lessons if int(l["id"]) == int(w_lesson_id)), None)
        if linked_l and linked_l.get("practice_id") and is_valid_practice_session(linked_l["practice_id"]):
            pid = linked_l["practice_id"]
            await run_in_threadpool(study_planner.update_session_practice, session_id, pid, exercise_status="available")
            updated = await run_in_threadpool(study_planner.get_session, session_id)
            practice = await run_in_threadpool(_srv.practice_service.get_session, pid)
            return {"status": "ok", "session": updated, "practice": practice}

    raw_title = str(session.get("title") or "").strip()
    course = str(session.get("course") or "").strip()
    classified_topic = str(session.get("classified_topic") or "").strip()
    cleaned_title = re.sub(r"^Tự\s*học(?:\s+[^:]+)?\s*:\s*", "", raw_title, flags=re.IGNORECASE).strip()
    cleaned_title = re.sub(r"^Tự\s*học\s+", "", cleaned_title, flags=re.IGNORECASE).strip()
    base_course = re.sub(r"\s*\([^)]*\)\s*$", "", course).strip()
    is_generic_review = bool(
        cleaned_title
        and re.match(r"^(?:ôn\s*tập|luyện\s*tập|tự\s*học|bài\s*tập)\b", cleaned_title, re.IGNORECASE)
        and (
            not base_course
            or base_course.casefold() in cleaned_title.casefold()
            or len(cleaned_title.split()) <= 3
        )
    )
    if is_generic_review:
        cleaned_title = ""
    topic = (cleaned_title or classified_topic or course).strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Phiên học chưa có chủ đề hoặc môn học để tạo bài tập.")

    learning_svc = getattr(_srv, "learning_service", None)
    if not learning_svc:
        raise HTTPException(status_code=503, detail="Dịch vụ học tập chưa sẵn sàng.")

    requested_count = 3
    if w_lesson_id:
        from app.planner_topic_bridge import list_weekly_lessons
        lessons = await run_in_threadpool(list_weekly_lessons, None)
        linked_l = next((l for l in lessons if int(l["id"]) == int(w_lesson_id)), None)
        if linked_l and linked_l.get("question_count"):
            requested_count = max(1, min(10, int(linked_l["question_count"])))

    def run_generation():
        with acquire_inference(_srv.dialogue_gate), _srv.gpu_coordinator.acquire_for_inference():
            return learning_svc.generate(
                topic=topic,
                count=requested_count,
                difficulty="medium",
                purpose="practice",
                model=getattr(_srv, "DEFAULT_CHAT_MODEL", None),
            )

    try:
        gen_result = await run_in_threadpool(run_generation)
        practice_id = gen_result.get("practice_id")
        actual_count = len(gen_result.get("questions") or [])
        if practice_id:
            await run_in_threadpool(
                study_planner.update_session_practice,
                session_id,
                practice_id,
                exercise_status="available",
            )
            if w_lesson_id:
                def _update_weekly():
                    with _connect(None) as conn:
                        cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_lessons)").fetchall()}
                        if "practice_id" in cols:
                            conn.execute(
                                "UPDATE weekly_lessons SET practice_id = ?, exercise_status = 'available', question_count = ? WHERE id = ?",
                                (practice_id, actual_count, w_lesson_id),
                            )
                            conn.commit()
                await run_in_threadpool(_update_weekly)

        updated_session = await run_in_threadpool(study_planner.get_session, session_id)
        return {
            "status": "ok",
            "session": updated_session,
            "practice": gen_result,
        }
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=sanitize_error_detail(str(exc)), headers={"Retry-After": "2"})
    except GPUBusyError as exc:
        raise HTTPException(status_code=503, detail=sanitize_error_detail(str(exc)), headers={"Retry-After": "2"})
    except PracticeGenerationError as exc:
        await run_in_threadpool(
            study_planner.update_session_practice,
            session_id,
            "",
            exercise_status="unavailable",
        )
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(exc)))


@router.post("/api/timetable/planner/weekly-lessons/{lesson_id}/exercise/generate")
async def generate_weekly_lesson_exercise_endpoint(lesson_id: int):
    from app import server as _srv
    from app.dialogue import DialogueBusyError, acquire_inference
    from app.gpu_lock import GPUBusyError
    from app.learning import PracticeGenerationError
    from app.planner_topic_bridge import list_weekly_lessons, _connect
    from app.practice import PracticeSessionNotFound, is_valid_practice_session

    lessons = await run_in_threadpool(list_weekly_lessons, None)
    target_lesson = next((l for l in lessons if int(l["id"]) == int(lesson_id)), None)
    if not target_lesson:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài học tuần.")

    # Reuse existing practice session if already available
    existing_pid = (target_lesson.get("practice_id") or "").strip()
    if existing_pid and is_valid_practice_session(existing_pid):
        try:
            existing_practice = await run_in_threadpool(_srv.practice_service.get_session, existing_pid)
            return {
                "status": "ok",
                "lesson_id": lesson_id,
                "practice_id": existing_pid,
                "exercise_status": "available",
                "practice": existing_practice,
            }
        except PracticeSessionNotFound:
            pass

    # Preserve lesson title / intent over broad classified fallback
    topic = (target_lesson.get("lesson_title") or target_lesson.get("classified_topic") or target_lesson.get("course") or "").strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Bài học chưa có chủ đề hoặc môn học để tạo bài tập.")

    learning_svc = getattr(_srv, "learning_service", None)
    if not learning_svc:
        raise HTTPException(status_code=503, detail="Dịch vụ học tập chưa sẵn sàng.")

    requested_count = max(1, min(10, int(target_lesson.get("question_count") or 3)))

    def run_generation():
        with acquire_inference(_srv.dialogue_gate), _srv.gpu_coordinator.acquire_for_inference():
            return learning_svc.generate(
                topic=topic,
                count=requested_count,
                difficulty="medium",
                purpose="practice",
                model=getattr(_srv, "DEFAULT_CHAT_MODEL", None),
            )

    try:
        gen_result = await run_in_threadpool(run_generation)
        practice_id = gen_result.get("practice_id")
        actual_count = len(gen_result.get("questions") or [])
        if practice_id:
            def _update_lesson_and_sessions():
                with _connect(None) as conn:
                    cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_lessons)").fetchall()}
                    if "practice_id" in cols:
                        conn.execute(
                            "UPDATE weekly_lessons SET practice_id = ?, exercise_status = 'available', question_count = ? WHERE id = ?",
                            (practice_id, actual_count, lesson_id),
                        )
                    # Also link matching planned sessions in study_sessions (by lesson ID or enrolled course)
                    s_cols = {r[1] for r in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
                    if "practice_id" in s_cols:
                        course_name = (target_lesson.get("course") or "").strip()
                        if "weekly_lesson_id" in s_cols:
                            conn.execute(
                                "UPDATE study_sessions SET practice_id = ?, exercise_status = 'available' WHERE weekly_lesson_id = ?",
                                (practice_id, lesson_id),
                            )
                        if course_name:
                            conn.execute(
                                """
                                UPDATE study_sessions
                                SET practice_id = ?, exercise_status = 'available'
                                WHERE COALESCE(practice_id, '') = '' AND lower(trim(course)) = lower(trim(?))
                                """,
                                (practice_id, course_name),
                            )
                    conn.commit()
            await run_in_threadpool(_update_lesson_and_sessions)
        return {
            "status": "ok",
            "lesson_id": lesson_id,
            "practice_id": practice_id,
            "exercise_status": "available",
            "practice": gen_result,
        }
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=sanitize_error_detail(str(exc)), headers={"Retry-After": "2"})
    except GPUBusyError as exc:
        raise HTTPException(status_code=503, detail=sanitize_error_detail(str(exc)), headers={"Retry-After": "2"})
    except PracticeGenerationError as exc:
        def _mark_unavailable():
            with _connect(None) as conn:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_lessons)").fetchall()}
                if "exercise_status" in cols:
                    conn.execute(
                        "UPDATE weekly_lessons SET exercise_status = 'unavailable' WHERE id = ?",
                        (lesson_id,),
                    )
                    conn.commit()
        await run_in_threadpool(_mark_unavailable)
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(exc)))


@router.get("/api/timetable/worksheets/{worksheet_id}.pdf")
async def download_planner_worksheet_pdf(worksheet_id: str):
    from app.planner_topic_bridge import get_worksheet_pdf

    if not re.match(r"^ws_[a-f0-9]{8,24}$", worksheet_id.strip()):
        raise HTTPException(status_code=400, detail="Mã bài tập PDF không hợp lệ.")
    record = await run_in_threadpool(get_worksheet_pdf, None, worksheet_id)
    if not record:
        raise HTTPException(status_code=404, detail="Không tìm thấy file PDF bài tập đính kèm.")
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (record.get("topic") or "bai-tap").lower()).strip("-") or "bai-tap"
    filename = f"arym-bai-tap-{slug[:36]}-{worksheet_id}.pdf"
    return Response(
        content=record["pdf_bytes"],
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/timetable/export-bundle.zip")
async def export_timetable_bundle_zip(
    start_date: str = Query(..., description="Start date of semester in YYYY-MM-DD"),
    end_date: str = Query(..., description="End date of semester in YYYY-MM-DD"),
    reminder_minutes: int = Query(60, ge=0, le=10080),
    request: Request = None,
):
    from datetime import date, timedelta
    from app import server as _srv
    from app.accounts import account_id
    from app.planner_topic_bridge import build_calendar_bundle_zip

    try:
        entries = list(await run_in_threadpool(_srv.timetable_store.get_confirmed_entries))
        for entry in entries:
            if entry.get("id") is not None:
                entry["uid"] = f"class:{entry['id']}"

        preferences = await run_in_threadpool(study_planner.get_preferences)
        tasks = await run_in_threadpool(study_planner.get_tasks)
        week_start = study_planner._infer_week_monday(preferences, tasks)
        if week_start is None:
            sd = date.fromisoformat(start_date)
            week_start = sd - timedelta(days=sd.weekday())
        routines = await run_in_threadpool(study_planner.get_routines)
        entries.extend(_routine_ics_entries(routines, week_start))

        sessions = await run_in_threadpool(study_planner.get_study_sessions)
        for s in sessions:
            entries.append({
                "uid": f"study:{s['id']}",
                "event_date": s.get("session_date") or (week_start + timedelta(days=s["weekday"] - 1)).isoformat(),
                "course": f"[Tự học - {s['course']}] {s['title']}",
                "weekday": s["weekday"],
                "start_time": s["start_time"],
                "end_time": s["end_time"],
                "room": s.get("source_label") or "",
                "notes": s.get("notes") or "",
                "classified_topic": s.get("classified_topic") or "",
                "pdf_url": s.get("pdf_url") or "",
            })
        deadlines = [t for t in tasks if t.get("deadline_date") and not t.get("completed")]

        if not entries and not deadlines:
            raise HTTPException(
                status_code=400,
                detail="Chưa có lịch học hoặc buổi tự học nào để xuất gói .zip.",
            )

        base_url = _resolve_public_base_url(request)
        ics_text = _srv.timetable_ics.generate_ics(
            entries,
            start_date,
            end_date,
            deadlines=deadlines,
            reminder_minutes=reminder_minutes,
            namespace=account_id.get() or "local",
            base_url=base_url or None,
        )
        zip_bytes = await run_in_threadpool(
            build_calendar_bundle_zip,
            None,
            ics_bytes=ics_text.encode("utf-8"),
            base_url=base_url or "http://127.0.0.1:8000",
        )
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="arym-lich-hoc-va-bai-tap-pdf-{start_date}.zip"'
            },
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(exc)))
    except Exception as exc:
        logger.exception("Lỗi xuất gói lịch .zip kèm PDF bài tập")
        raise HTTPException(status_code=500, detail="Không thể tạo gói lịch .zip lúc này.") from exc
