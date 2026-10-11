import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import (
    BASE_DIR, DATA_DIR, DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL, TRAINED_MODEL_NAME,
    DEFAULT_TOP_K, SERVER_HOST, SERVER_PORT
)
from app.indexer import KnowledgeIndexer
from app.searcher import HybridSearcher
from app.rag_agent import PsychologyAgent
from app.ollama_client import OllamaClient
from app.trained_client import TrainedModelClient
from app.history import HistoryStore
from app.topic_summarizer import TopicSummarizer
from app.gpu_lock import gpu_coordinator, GPUBusyError
from app.document_lookup import DocumentLookup
from app.dialogue import dialogue_gate, DialogueBusyError, BUSY_TEXT
from app import timetable_store, timetable_vision, timetable_ics
from app.practice import PracticeGenerationError, PracticeService, PracticeSessionNotFound

logger = logging.getLogger("psychology_agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="Local Multi-domain Study Agent", version="1.0.0")

# Mount static files
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

indexer = KnowledgeIndexer()
searcher = HybridSearcher()
document_lookup = DocumentLookup()
agent = PsychologyAgent(searcher=searcher)
practice_service = PracticeService(searcher=searcher, document_lookup=document_lookup)
import os

_history_store: Optional[HistoryStore] = None

def get_history_store() -> HistoryStore:
    global _history_store
    if _history_store is None:
        db_path = os.environ.get("HISTORY_DB_PATH")
        _history_store = HistoryStore(Path(db_path) if db_path else DATA_DIR / "history.db")
    return _history_store

def set_history_store(store: HistoryStore) -> None:
    global _history_store
    _history_store = store
    summarizer.history_store = store

class _LazyHistoryStore:
    def __getattr__(self, name):
        return getattr(get_history_store(), name)

history_store = _LazyHistoryStore()
ollama = OllamaClient()
summarizer = TopicSummarizer(history_store=history_store, ollama=ollama)

class ChatRequest(BaseModel):
    query: str = Field(max_length=4000)
    chat_history: Optional[List[Dict[str, str]]] = Field(default=None, max_length=32)
    answer_mode: Literal["quick", "steps", "compare", "summary", "step_by_step", "comparison"] = "quick"
    explanation_mode: Optional[Literal["quick", "steps", "compare", "summary", "step_by_step", "comparison"]] = None
    isolate_context: Optional[bool] = False
    temperature: Optional[float] = Field(default=0.2, ge=0, le=1)
    save_history: Optional[bool] = True
    session_id: Optional[str] = None
    request_id: Optional[str] = Field(default=None, max_length=128)
    source_document_id: Optional[int] = Field(default=None, ge=1)
    source_page_num: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode='after')
    def paired_source(self):
        if self.source_page_num is not None and self.source_document_id is None:
            raise ValueError('Chọn tài liệu cho trang nguồn.')
        if self.explanation_mode:
            self.answer_mode = self.explanation_mode
        self.answer_mode = {
            "summary": "quick",
            "step_by_step": "steps",
            "comparison": "compare",
        }.get(self.answer_mode, self.answer_mode)
        return self


class PracticeGenerateRequest(BaseModel):
    source_document_id: int = Field(ge=1)
    source_page_num: int = Field(ge=1)
    source_chunk_id: Optional[int] = Field(default=None, ge=1)
    count: int = Field(default=3, ge=1, le=3)


class PracticeAnswerRequest(BaseModel):
    practice_id: str = Field(min_length=16, max_length=64)
    question_id: str = Field(min_length=2, max_length=16)
    answer: Optional[Literal["A", "B", "C", "D"]] = None


@app.post("/api/practice/generate")
async def generate_practice(req: PracticeGenerateRequest):
    document = await run_in_threadpool(document_lookup.document, req.source_document_id)
    if not document or req.source_page_num > document["total_pages"]:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang nguồn đã chọn.")

    def run_generation():
        with dialogue_gate.acquire(), gpu_coordinator.acquire_for_inference():
            return practice_service.generate(
                doc_id=req.source_document_id,
                page_num=req.source_page_num,
                count=req.count,
                model=DEFAULT_CHAT_MODEL,
                source_chunk_id=req.source_chunk_id,
            )

    try:
        return await run_in_threadpool(run_generation)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    except GPUBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"})
    except PracticeGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Không tạo được bộ câu hỏi luyện tập")
        raise HTTPException(status_code=502, detail="Không tạo được câu hỏi lúc này. Hãy thử lại sau.") from exc


@app.get("/api/practice/examples")
async def list_practice_examples(
    document_id: int = Query(..., ge=1),
    page_from: int = Query(1, ge=1),
    page_to: Optional[int] = Query(None, ge=1),
    topic: str = Query("", max_length=120),
    limit: int = Query(20, ge=1, le=50),
):
    document = await run_in_threadpool(document_lookup.document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    final_page = page_to or document["total_pages"]
    if page_from > final_page or final_page > document["total_pages"]:
        raise HTTPException(status_code=422, detail="Khoảng trang không hợp lệ với tài liệu đã chọn.")
    try:
        items = await run_in_threadpool(
            practice_service.list_example_exercises,
            document_id, page_from, final_page, topic.strip(), limit,
        )
    except PracticeGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"items": items, "origin": "source_document", "document": document, "page_from": page_from, "page_to": final_page}


@app.post("/api/practice/answer")
async def answer_practice(req: PracticeAnswerRequest):
    try:
        return practice_service.answer(req.practice_id, req.question_id, req.answer)
    except PracticeSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def requested_source_scope(req):
    if req.source_document_id is None:
        return None, None
    doc = document_lookup.document(req.source_document_id)
    if not doc or (req.source_page_num is not None and req.source_page_num > doc['total_pages']):
        raise HTTPException(status_code=404, detail='Không tìm thấy tài liệu hoặc trang nguồn đã chọn.')
    page = (req.source_document_id, req.source_page_num) if req.source_page_num is not None else None
    return page, req.source_document_id


def requested_source_page(req):
    """Backward-compatible helper for callers that need only a page selection."""
    return requested_source_scope(req)[0]

class SessionCreateRequest(BaseModel):
    title: Optional[str] = None

class SessionRenameRequest(BaseModel):
    title: str

class SessionSummarizeRequest(BaseModel):
    pass

class IndexRequest(BaseModel):
    force: bool = False
    ocr: bool = True

class OCRReviewRequest(BaseModel):
    cache_key: str
    corrected_text: str
    reviewer_name: str
    notes: Optional[str] = ""

class OCRRunPageRequest(BaseModel):
    filename: str
    page_num: int = 1
    backend: Optional[str] = "ollama_vision"



async def locked_history_mutation(method, *args):
    def mutate():
        with dialogue_gate.acquire():
            return method(*args)
    try:
        return await run_in_threadpool(mutate)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})

@app.get("/api/documents")
async def list_documents():
    return {"documents": await run_in_threadpool(document_lookup.documents)}

@app.get("/api/documents/search")
async def lookup_documents(q: str = Query(..., min_length=1, max_length=500),
                           limit: int = Query(10, ge=1, le=30),
                           document_id: Optional[int] = Query(None, ge=1),
                           method: Literal["fts", "hybrid"] = "fts"):
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Nhập từ khóa cần tìm.")
    if document_id is not None and not await run_in_threadpool(document_lookup.document, document_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    def run_lookup():
        if method == "fts":
            return document_lookup.search(query, limit, document_id, method)
        with dialogue_gate.acquire():
            return document_lookup.search(query, limit, document_id, method)
    try:
        result = await run_in_threadpool(run_lookup)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    return {"query": query, **result}


@app.get('/api/documents/source-preview')
async def document_source_preview(filename: str = Query(..., max_length=1000), page_num: int = Query(..., ge=1)):
    result = await run_in_threadpool(document_lookup.source_preview, filename, page_num)
    if not result:
        raise HTTPException(status_code=404, detail='Không tìm thấy trang nguồn.')
    return result

@app.get("/api/documents/{document_id}/pdf")
async def open_document_pdf(document_id: int):
    doc = await run_in_threadpool(document_lookup.document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy PDF trong thư viện.")
    path = document_lookup.pdf_path(doc["filename"])
    if not path:
        raise HTTPException(status_code=404, detail="Không tìm thấy PDF trong thư viện.")
    return FileResponse(path, media_type="application/pdf", filename=doc["filename"],
                        content_disposition_type="inline")


@app.get("/api/documents/{document_id}/pages/{page_num}/image")
async def open_document_page_image(document_id: int, page_num: int):
    data = await run_in_threadpool(document_lookup.page_image, document_id, page_num)
    if data is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang gốc của tài liệu.")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-cache"})

@app.get("/api/ocr/reviews")
async def list_ocr_reviews(needs_review: Optional[bool] = None, source_relpath: Optional[str] = None):
    from app.vision_ocr import CACHE_DIR
    records = []
    for cp in sorted(CACHE_DIR.glob("*.json")):
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            if needs_review is not None and data.get("needs_review") != needs_review:
                continue
            if source_relpath and data.get("source_relpath") != source_relpath:
                continue
            records.append(data)
        except Exception:
            continue
    return {"records": records, "total": len(records)}

@app.post("/api/ocr/review")
async def submit_ocr_review(req: OCRReviewRequest):
    from app.vision_ocr import VisionOCRManager
    try:
        updated = VisionOCRManager.review_and_verify_transcript(
            cache_key=req.cache_key,
            corrected_text=req.corrected_text,
            reviewer_name=req.reviewer_name,
            notes=req.notes or "",
        )
        # Update chunks, embeddings, and full-text search for the affected page
        index_update = {"status": "not_indexed", "error": "Bản ghi thiếu vị trí nguồn"}
        try:
            filename = updated.get("source_relpath")
            page_num = updated.get("page_num")
            text = updated.get("text") or req.corrected_text
            if filename and page_num is not None:
                index_update = indexer.update_page_chunks(filename=filename, page_num=int(page_num), text=text)
        except Exception as e:
            logger.warning(f"Lỗi cập nhật page chunks sau khi review OCR: {e}")
            index_update = {"status": "failed", "error": str(e)}

        return {"status": "success" if index_update.get("status") == "success" else "verified_not_indexed",
                "record": updated, "index_update": index_update}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Error reviewing OCR transcript")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ocr/page-image")
async def get_ocr_page_image(filename: str = Query(...), page_num: int = Query(1)):
    """Render original page image as PNG for human OCR review preview."""
    import pymupdf
    from fastapi.responses import Response

    file_path = document_lookup.file_path(filename)
    if not file_path or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tài liệu: {filename}")

    ext = file_path.suffix.lower()
    if ext == ".pdf":
        try:
            doc = pymupdf.open(str(file_path))
            if page_num < 1 or page_num > len(doc):
                doc.close()
                raise HTTPException(status_code=400, detail=f"Trang {page_num} không hợp lệ (tổng số trang: {len(doc)})")
            page = doc[page_num - 1]
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            doc.close()
            return Response(content=img_bytes, media_type="image/png")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi trích xuất ảnh trang: {e}")
    elif ext in (".png", ".jpg", ".jpeg"):
        media_type = "image/png" if ext == ".png" else "image/jpeg"
        return Response(content=file_path.read_bytes(), media_type=media_type)
    else:
        raise HTTPException(status_code=400, detail=f"Không hỗ trợ xem ảnh cho định dạng {ext}")

@app.post("/api/ocr/run-page")
async def run_ocr_page(req: OCRRunPageRequest):
    import asyncio
    from app.vision_ocr import VisionOCRManager, BACKEND_OLLAMA_VISION
    from app.pdf_extractor import PDFExtractor
    import pymupdf

    file_path = document_lookup.file_path(req.filename)
    if not file_path or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tài liệu nguồn: {req.filename}")

    try:
        source_hash = PDFExtractor.calculate_file_hash(file_path)
        ext = file_path.suffix.lower()
        if ext == ".pdf":
            doc = pymupdf.open(str(file_path))
            total_pages = len(doc)
            if req.page_num < 1 or req.page_num > total_pages:
                doc.close()
                raise HTTPException(status_code=400, detail=f"Trang {req.page_num} không hợp lệ (tổng số trang: {total_pages})")
            page = doc[req.page_num - 1]
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            doc.close()
        elif ext in (".png", ".jpg", ".jpeg"):
            img_bytes = file_path.read_bytes()
        else:
            raise HTTPException(status_code=400, detail=f"Định dạng {ext} không hỗ trợ chạy OCR theo trang")

        backend = req.backend or BACKEND_OLLAMA_VISION
        mgr = VisionOCRManager(backend=backend)
        res = await asyncio.to_thread(
            mgr.ocr_image_bytes,
            image_bytes=img_bytes,
            source_hash=source_hash,
            source_relpath=req.filename,
            page_num=req.page_num,
        )
        return {"status": "success", "record": res}
    except HTTPException:
        raise
    except GPUBusyError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Error running OCR on page")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ocr/locator")
async def get_ocr_locator(filename: str = Query(...), page_num: int = Query(1)):
    from app.vision_ocr import get_source_locator
    return get_source_locator(source_relpath=filename, page_or_unit_num=page_num)

# ==========================================
# Timetable & Vision Schedule Endpoints (S01-S03)
# ==========================================

class TimetableExtractRequest(BaseModel):
    filename: str = "schedule.png"
    image_base64: str

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


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


@app.get("/api/timetable")
async def get_timetable():
    entries = await run_in_threadpool(timetable_store.get_confirmed_entries)
    return {"entries": entries}


@app.post("/api/timetable/extract")
async def extract_timetable(payload: TimetableExtractRequest):
    try:
        image_bytes, image_hash = await run_in_threadpool(timetable_vision.validate_image_payload, payload.filename, payload.image_base64)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = await run_in_threadpool(
            timetable_vision.call_vlm_extract,
            image_bytes,
            image_hash,
            payload.filename,
        )
        return result
    except GPUBusyError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except timetable_vision.VLMUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except timetable_vision.VLMOutputInvalidError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error extracting timetable from image")
        raise HTTPException(status_code=500, detail=f"Lỗi trích xuất thời khóa biểu: {e}")


@app.post("/api/timetable/confirm")
async def confirm_timetable(payload: TimetableConfirmRequest):
    if not payload.entries:
        raise HTTPException(status_code=422, detail="Danh sách môn học xác nhận không được để trống")
    entries_data = [e.model_dump() for e in payload.entries]
    try:
        saved_count = await run_in_threadpool(timetable_store.confirm_draft,
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


@app.get("/api/timetable/drafts/{draft_id}")
async def get_timetable_draft(draft_id: str):
    draft = await run_in_threadpool(timetable_store.get_draft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@app.get("/api/timetable/images/{image_hash}")
async def get_timetable_image(image_hash: str):
    if not re.match(r"^[a-f0-9]{64}$", image_hash):
        raise HTTPException(status_code=400, detail="Invalid image hash")
    image_path = await run_in_threadpool(timetable_store.get_image_path, image_hash)
    if not image_path or not image_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    data = await run_in_threadpool(image_path.read_bytes)
    media_type = "image/png" if data.startswith(b"\x89PNG") else ("image/jpeg" if data.startswith(b"\xff\xd8") else "image/webp")
    return Response(content=data, media_type=media_type)


@app.post("/api/timetable/manual")
async def add_timetable_manual_entry(payload: TimetableManualEntryRequest):
    try:
        entry_id = await run_in_threadpool(timetable_store.add_manual_entry,
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

@app.delete("/api/timetable/entries/{entry_id}")
async def delete_timetable_entry(entry_id: int):
    success = await run_in_threadpool(timetable_store.delete_entry, entry_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "ok"}

@app.delete("/api/timetable")
async def clear_timetable():
    await run_in_threadpool(timetable_store.clear_all_entries)
    return {"status": "ok"}

@app.get("/api/timetable/export.ics")
async def export_timetable_ics(
    start_date: str = Query(..., description="Start date of semester in YYYY-MM-DD"),
    end_date: str = Query(..., description="End date of semester in YYYY-MM-DD"),
):
    try:
        entries = await run_in_threadpool(timetable_store.get_confirmed_entries)
        ics_content = timetable_ics.generate_ics(entries, start_date, end_date)
        return Response(
            content=ics_content,
            media_type="text/calendar",
            headers={
                "Content-Disposition": f'attachment; filename="timetable_{start_date}_{end_date}.ics"'
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_html = STATIC_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(content=index_html.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Study QA Agent Backend Running</h1><p>index.html not found in static/</p>")

def _requires_mlx_model():
    return DEFAULT_CHAT_MODEL in (TRAINED_MODEL_NAME, "qwen2.5-3b-4bit", "tam-ly-mlx", "mlx-3b", "local")


def _inference_model_available(mlx_available, models):
    if _requires_mlx_model():
        return mlx_available
    return any(model.get("name") in {DEFAULT_CHAT_MODEL, DEFAULT_CHAT_MODEL + ":latest"}
               for model in models)


@app.get("/api/health")
async def health_check():
    ollama_ok = ollama.check_health()
    gpu_status = gpu_coordinator.get_status()
    gpu_busy = bool(gpu_status.get("busy", False))
    mlx_available = TrainedModelClient.available()
    models = ollama.list_models() if ollama_ok and not gpu_busy and not _requires_mlx_model() else []
    model_ready = _inference_model_available(mlx_available, models)

    if gpu_busy:
        service_state = "degraded"
        service_message = f"GPU đang bận huấn luyện (PID {gpu_status.get('pid')}) - Suy luận tạm thời bị chặn, web hoạt động bình thường"
        inference_ready = False
    elif not ollama_ok:
        service_state = "degraded"
        service_message = "Ollama mất kết nối"
        inference_ready = False
    elif not model_ready:
        service_state = "degraded"
        service_message = ("Mô hình MLX nền tảng cục bộ chưa khả dụng" if _requires_mlx_model()
                           else f"Mô hình Ollama {DEFAULT_CHAT_MODEL} chưa khả dụng")
        inference_ready = False
    else:
        service_state = "healthy"
        service_message = "Tất cả dịch vụ hoạt động bình thường"
        inference_ready = True

    return {
        "status": "ok",
        "implementation_revision": "2026-10-05-content-images-literal-timetable",
        "dialogue_revision": "2026-10-03-single-request-context-budget",
        "service_state": service_state,
        "service_message": service_message,
        "web_healthy": True,
        "ollama_connected": ollama_ok,
        "ollama_url": ollama.base_url,
        "mlx_available": mlx_available,
        "inference_ready": inference_ready,
        "gpu": gpu_status
    }

@app.get("/api/models")
async def get_models():
    from app.active_registry import active_registry
    registry_info = active_registry.get_active_info()
    models = ollama.list_models() if ollama.check_health() else []
    # Exclude vision auxiliary models from QA list; vision is only for OCR
    qa_models = [
        m for m in models
        if "vl" not in m.get("name", "").lower() and "vision" not in m.get("name", "").lower()
    ]
    return {
        "models": qa_models,
        "default_model": DEFAULT_CHAT_MODEL,
        "default_model_backend": "mlx" if _requires_mlx_model() else "ollama",
        "default_model_available": _inference_model_available(TrainedModelClient.available(), models),
        "trained_model": TRAINED_MODEL_NAME if TrainedModelClient.available() else None,
        "active_adapter": registry_info.get("active_adapter"),
        "model_mode": registry_info.get("mode", "unadapted_base"),
        "model_description": (
            "Qwen2.5 3B (MLX Nền tảng cục bộ - unadapted base)"
            if registry_info.get("mode") == "unadapted_base"
            else f"Qwen2.5 3B (MLX LoRA - {registry_info.get('active_adapter', {}).get('id')})"
        ),
    }

@app.get("/api/status")
async def get_index_status():
    status = indexer.get_status()
    ollama_ok = ollama.check_health()
    models = ollama.list_models() if ollama_ok else []
    gpu_status = gpu_coordinator.get_status()
    gpu_busy = bool(gpu_status.get("busy", False))
    mlx_available = TrainedModelClient.available()
    model_ready = _inference_model_available(mlx_available, models)

    if gpu_busy:
        service_state = "degraded"
        service_message = f"GPU đang bận huấn luyện (PID {gpu_status.get('pid')}) - Suy luận tạm thời bị chặn, web hoạt động bình thường"
        inference_ready = False
    elif not ollama_ok:
        service_state = "degraded"
        service_message = "Ollama mất kết nối"
        inference_ready = False
    elif not model_ready:
        service_state = "degraded"
        service_message = ("Mô hình MLX nền tảng cục bộ chưa khả dụng" if _requires_mlx_model()
                           else f"Mô hình Ollama {DEFAULT_CHAT_MODEL} chưa khả dụng")
        inference_ready = False
    else:
        service_state = "healthy"
        service_message = "Tất cả dịch vụ hoạt động bình thường"
        inference_ready = True

    return {
        "service_state": service_state,
        "service_message": service_message,
        "inference_ready": inference_ready,
        "knowledge_base": status,
        "trained_model": {"name": TRAINED_MODEL_NAME, "available": mlx_available},
        "ollama": {
            "connected": ollama_ok,
            "url": ollama.base_url,
            "models": [m.get("name") for m in models]
        },
        "gpu": gpu_status
    }

@app.post("/api/index")
async def trigger_indexing(req: IndexRequest):
    allowed, busy_msg = gpu_coordinator.check_inference_allowed()
    if not allowed:
        raise HTTPException(status_code=503, detail=busy_msg)
    def run_index():
        with dialogue_gate.acquire(), gpu_coordinator.acquire_for_inference():
            return indexer.index_all(force=req.force, ocr=req.ocr, embed_model=DEFAULT_EMBED_MODEL)
    try:
        report = await run_in_threadpool(run_index)
        return {"status": "success", "report": report}
    except DialogueBusyError as e:
        raise HTTPException(status_code=409, detail=str(e), headers={"Retry-After": "2"})
    except GPUBusyError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Error during indexing")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def chat_sync(req: ChatRequest):
    try:
        return await run_in_threadpool(_chat_sync_locked, req)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})


def _chat_sync_locked(req):
    with dialogue_gate.acquire():
        return _chat_sync(req)


def _chat_sync(req):
    source_page, source_document_id = requested_source_scope(req)
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Câu hỏi không được để trống")

    # 1. Resolve session and context
    target_session_id = req.session_id
    sess = None
    if target_session_id:
        sess = history_store.get_session(target_session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên trò chuyện")
        # Store-authoritative durable server context bounded to last 4 turns
        effective_history = [] if req.isolate_context else history_store.get_session_context(target_session_id, max_turns=8)
        create_on_completion = False
    else:
        # Legacy client fallback or new chat without session ID
        effective_history = [] if req.isolate_context else (req.chat_history or [])
        create_on_completion = bool(req.save_history)

    # 2. Idempotency replay check
    if req.request_id:
        existing_turn = history_store.get_turn_by_request_id(req.request_id)
        if existing_turn:
            if target_session_id and existing_turn["session_id"] != target_session_id:
                raise HTTPException(status_code=409, detail="request_id thuộc một phiên trò chuyện khác")
            if existing_turn["question"] != req.query.strip():
                raise HTTPException(status_code=409, detail="request_id đã tồn tại cho một câu hỏi khác")
            return {
                "answer": existing_turn["answer"],
                "citations": existing_turn["citations"],
                "answer_mode": existing_turn["answer_mode"],
                "search_scope": existing_turn["search_scope"],
                "model": existing_turn["model"],
                "session_id": existing_turn["session_id"],
                "turn_id": existing_turn["id"],
                "history_id": existing_turn.get("legacy_id", existing_turn["id"]),
                "turn_index": existing_turn["turn_index"],
                "session_title": existing_turn.get("session_title", ""),
                "cached_replay": True,
            }

    # 3. Check GPU coordinator before creating ANY rows
    allowed, busy_msg = gpu_coordinator.check_inference_allowed()
    if not allowed:
        raise HTTPException(status_code=503, detail=busy_msg)

    try:
        with gpu_coordinator.acquire_for_inference():
            res = agent.process_query_sync(
                query=req.query,
                chat_history=effective_history,
                model=DEFAULT_CHAT_MODEL,
                embed_model=DEFAULT_EMBED_MODEL,
                top_k=DEFAULT_TOP_K,
                temperature=req.temperature if req.temperature is not None else 0.2,
                answer_mode=req.answer_mode,
                source_document_id=source_document_id,
                isolate_context=bool(req.isolate_context),
                **({'source_page':source_page} if source_page else {})
            )
    except GPUBusyError as e:
        raise HTTPException(status_code=503, detail=str(e))

    answer = res.get("answer", "")
    is_truncated = res.get("is_truncated", False)
    if req.session_id and sess:
        omitted = max(0, len(sess.get("turns", [])) - 8)
        res.setdefault("context", {})["store_history_turns_omitted"] = omitted

    # Do not save truncated or failed turns
    if answer and req.save_history and not is_truncated and not res.get("error"):
        if create_on_completion and not target_session_id:
            new_sess = history_store.create_session()
            target_session_id = new_sess["id"]

        turn_info = history_store.add_turn(
            session_id=target_session_id,
            question=req.query,
            answer=answer,
            citations=res.get("citations", []),
            model=DEFAULT_CHAT_MODEL,
            request_id=req.request_id,
            answer_mode=req.answer_mode,
            search_scope=res.get("search_scope", {}),
        )
        res["history_id"] = turn_info.get("legacy_id", turn_info["id"])
        res["turn_id"] = turn_info["id"]
        res["session_id"] = target_session_id
        res["turn_index"] = turn_info["turn_index"]
        res["session_title"] = turn_info.get("session_title")

    if target_session_id:
        cur_sess = history_store.get_session(target_session_id)
        if cur_sess and cur_sess.get("turns"):
            res["recent_question"] = cur_sess["turns"][-1]["question"]
            res["recent_source"] = cur_sess["turns"][-1]["citations"][0] if cur_sess["turns"][-1].get("citations") else None

    res["model"] = DEFAULT_CHAT_MODEL
    return res


@app.get("/api/history")
async def list_history():
    return {"items": history_store.list()}


@app.get("/api/history/{item_id}")
async def get_history(item_id: int):
    item = history_store.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt tra cứu")
    return item


@app.delete("/api/history/{item_id}")
async def delete_history(item_id: int):
    if not await locked_history_mutation(history_store.delete, item_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt tra cứu")
    return {"deleted": True}


@app.delete("/api/history")
async def clear_history():
    await locked_history_mutation(history_store.clear)
    return {"deleted": True}


# Session Endpoints

@app.get("/api/sessions")
async def list_sessions(limit: int = 50, offset: int = 0, include_legacy: bool = True):
    return history_store.list_sessions(limit=limit, offset=offset, include_legacy=include_legacy)


@app.post("/api/sessions")
async def create_session(req: Optional[SessionCreateRequest] = None):
    title = req.title if req else None
    return history_store.create_session(title=title)


@app.get("/api/sessions/search")
async def search_sessions(q: str = "", limit: int = 20, offset: int = 0):
    return history_store.search_sessions(query=q, limit=limit, offset=offset)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    sess = history_store.get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên trò chuyện")
    turns = sess.get("turns", [])
    sess["recent_question"] = turns[-1]["question"] if turns else None
    sess["recent_source"] = turns[-1]["citations"][0] if turns and turns[-1].get("citations") else None
    return sess


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, req: SessionRenameRequest):
    if not req.title or not req.title.strip():
        raise HTTPException(status_code=400, detail="Tiêu đề không được để trống")
    ok = history_store.rename_session(session_id, req.title.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên trò chuyện")
    return {"renamed": True, "title": req.title.strip()[:120]}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    ok = await locked_history_mutation(history_store.delete_session, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên trò chuyện")
    return {"deleted": True, "session_id": session_id}


@app.delete("/api/sessions")
async def clear_all_sessions():
    await locked_history_mutation(history_store.clear)
    return {"deleted": True}


@app.post("/api/sessions/{session_id}/summarize")
async def summarize_session(session_id: str, req: Optional[SessionSummarizeRequest] = None):
    model = DEFAULT_CHAT_MODEL
    def run_summary():
        with dialogue_gate.acquire():
            return summarizer.summarize_session_sync(session_id=session_id, model=model)
    try:
        result = await run_in_threadpool(run_summary)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message", "Lỗi tóm tắt"))
    return result


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    source_page, source_document_id = await run_in_threadpool(requested_source_scope, req)
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Câu hỏi không được để trống")

    # 1. Resolve session and context
    target_session_id = req.session_id
    if target_session_id:
        sess = history_store.get_session(target_session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên trò chuyện")
        effective_history = history_store.get_session_context(target_session_id, max_turns=8)
        create_on_completion = False
    else:
        effective_history = req.chat_history or []
        create_on_completion = bool(req.save_history)

    # 2. Idempotency replay check
    if req.request_id:
        existing_turn = history_store.get_turn_by_request_id(req.request_id)
        if existing_turn:
            if target_session_id and existing_turn["session_id"] != target_session_id:
                raise HTTPException(status_code=409, detail="request_id thuộc một phiên trò chuyện khác")
            if existing_turn["question"] != req.query.strip():
                raise HTTPException(status_code=409, detail="request_id đã tồn tại cho một câu hỏi khác")
            def replay_generator():
                event = {
                    "type": "done",
                    "answer": existing_turn["answer"],
                    "citations": existing_turn["citations"],
                    "answer_mode": existing_turn["answer_mode"],
                    "search_scope": existing_turn["search_scope"],
                    "model": existing_turn["model"],
                    "session_id": existing_turn["session_id"],
                    "turn_id": existing_turn["id"],
                    "history_id": existing_turn.get("legacy_id", existing_turn["id"]),
                    "turn_index": existing_turn["turn_index"],
                    "session_title": existing_turn.get("session_title", ""),
                    "cached_replay": True,
                }
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            return StreamingResponse(
                replay_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )

    # 3. Check GPU coordinator before creating ANY rows
    allowed, busy_msg = gpu_coordinator.check_inference_allowed()
    if not allowed:
        def busy_generator():
            err_event = {"type": "error", "content": busy_msg, "gpu_busy": True}
            yield f"data: {json.dumps(err_event, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            busy_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    def event_generator():
        nonlocal target_session_id
        answer_parts = []
        citations = []
        search_scope = {}
        failed = False
        turn_committed = False
        try:
            with dialogue_gate.acquire(), gpu_coordinator.acquire_for_inference():
                # Reload context only after exclusive admission; prior turn is committed.
                if req.isolate_context:
                    effective_history = []
                elif target_session_id:
                    session_now = history_store.get_session(target_session_id)
                    if not session_now:
                        yield f"data: {json.dumps({'type': 'error', 'content': 'Phiên đã bị xóa; câu hỏi chưa được xử lý.'}, ensure_ascii=False)}\n\n"
                        return
                    effective_history = history_store.get_session_context(target_session_id, max_turns=8)
                    omitted = max(0, len(session_now.get("turns", [])) - 8)
                    if omitted:
                        yield f"data: {json.dumps({'type': 'context', 'history_turns_omitted': omitted, 'scope': 'stored_history_window'}, ensure_ascii=False)}\n\n"
                else:
                    effective_history = req.chat_history or []
                if req.request_id:
                    prior = history_store.get_turn_by_request_id(req.request_id)
                    if prior:
                        if prior["question"] != req.query.strip() or (target_session_id and prior["session_id"] != target_session_id):
                            yield f"data: {json.dumps({'type': 'error', 'content': 'request_id đã thuộc câu hỏi hoặc phiên khác', 'code': 'request_conflict'})}\n\n"
                            return
                        yield f"data: {json.dumps({'type': 'done', 'answer': prior['answer'], 'citations': prior['citations'], 'answer_mode': prior['answer_mode'], 'search_scope': prior['search_scope'], 'session_id': prior['session_id'], 'turn_id': prior['id'], 'cached_replay': True}, ensure_ascii=False)}\n\n"
                        return
                for event in agent.process_query_stream(
                    query=req.query,
                    chat_history=effective_history,
                    model=DEFAULT_CHAT_MODEL,
                    embed_model=DEFAULT_EMBED_MODEL,
                    top_k=DEFAULT_TOP_K,
                    temperature=req.temperature if req.temperature is not None else 0.2,
                    answer_mode=req.answer_mode,
                    source_document_id=source_document_id,
                    isolate_context=bool(req.isolate_context),
                    **({'source_page':source_page} if source_page else {})
                ):
                    if event.get("type") in ("token", "crisis"):
                        answer_parts.append(event.get("content", ""))
                    elif event.get("type") == "citations":
                        citations = event.get("citations", [])
                    elif event.get("type") == "error":
                        failed = True
                    elif event.get("type") == "done":
                        search_scope = event.get("search_scope", search_scope)
                        event.setdefault("answer_mode", req.answer_mode)
                        # Fallback citations if not sent earlier
                        if not citations and event.get("citations"):
                            citations = event.get("citations", [])

                        is_truncated = event.get("is_truncated", False)
                        # Single completion commit guard: skip truncated, error, failed
                        if (
                            req.save_history
                            and answer_parts
                            and not failed
                            and not is_truncated
                            and not turn_committed
                        ):
                            turn_committed = True
                            if create_on_completion and not target_session_id:
                                new_sess = history_store.create_session()
                                target_session_id = new_sess["id"]

                            turn_info = history_store.add_turn(
                                session_id=target_session_id,
                                question=req.query,
                                answer="".join(answer_parts),
                                citations=citations,
                                model=DEFAULT_CHAT_MODEL,
                                request_id=req.request_id,
                                answer_mode=req.answer_mode,
                                search_scope=search_scope,
                            )
                            event["history_id"] = turn_info.get("legacy_id", turn_info["id"])
                            event["turn_id"] = turn_info["id"]
                            event["session_id"] = target_session_id
                            event["turn_index"] = turn_info["turn_index"]
                            event["session_title"] = turn_info.get("session_title")

                        if target_session_id:
                            cur_s = history_store.get_session(target_session_id)
                            if cur_s and cur_s.get("turns"):
                                event["recent_question"] = cur_s["turns"][-1]["question"]
                                event["recent_source"] = cur_s["turns"][-1]["citations"][0] if cur_s["turns"][-1].get("citations") else None
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except DialogueBusyError as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e), 'code': 'dialogue_busy', 'retryable': True}, ensure_ascii=False)}\n\n"
        except GPUBusyError as e:
            err_event = {"type": "error", "content": str(e), "gpu_busy": True}
            yield f"data: {json.dumps(err_event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("Stream error")
            err_event = {"type": "error", "content": str(e)}
            yield f"data: {json.dumps(err_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
