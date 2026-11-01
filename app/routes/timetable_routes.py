"""Timetable & Vision Schedule API Routes (Single Responsibility Module)."""

import logging
import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from app.gpu_lock import GPUBusyError

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
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = await run_in_threadpool(
            _srv.timetable_vision.call_vlm_extract,
            image_bytes,
            image_hash,
            payload.filename,
        )
        return result
    except GPUBusyError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except _srv.timetable_vision.VLMUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except _srv.timetable_vision.VLMOutputInvalidError as e:
        raise HTTPException(status_code=422, detail=str(e))
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
        msg = str(e)
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
        msg = str(e)
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


@router.get("/api/timetable/export.ics")
async def export_timetable_ics(
    start_date: str = Query(..., description="Start date of semester in YYYY-MM-DD"),
    end_date: str = Query(..., description="End date of semester in YYYY-MM-DD"),
):
    from app import server as _srv
    try:
        entries = await run_in_threadpool(_srv.timetable_store.get_confirmed_entries)
        if not entries:
            raise HTTPException(
                status_code=400,
                detail="Thời khóa biểu hiện chưa có môn học nào để xuất lịch. Vui lòng thêm môn học trước.",
            )
        ics_content = _srv.timetable_ics.generate_ics(entries, start_date, end_date)
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
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("Lỗi xuất lịch sang ICS")
        raise HTTPException(status_code=500, detail="Lỗi xuất tệp lịch .ics.")
