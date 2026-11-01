import base64
import json
import logging
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Literal

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Response, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import (
    BASE_DIR, DATA_DIR, SRC_DIR, DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL, TRAINED_MODEL_NAME,
    DEFAULT_TOP_K, SERVER_HOST, SERVER_PORT, OLLAMA_AUX_CHAT_MODEL
)
from app.indexer import KnowledgeIndexer
from app.searcher import HybridSearcher
from app.rag_agent import PsychologyAgent
from app.ollama_client import OllamaClient
from app.trained_client import TrainedModelClient
from app.history import HistoryStore

from app.runtime_health import probe_mlx_runtime, runtime_failure_message
from app.topic_summarizer import TopicSummarizer
from app.gpu_lock import gpu_coordinator, GPUBusyError
from app.document_lookup import DocumentLookup
from app.dialogue import dialogue_gate, DialogueBusyError, BUSY_TEXT
from app import timetable_store, timetable_vision, timetable_ics
from app.practice import PracticeGenerationError, PracticeService, PracticeSessionNotFound
from app.learning import LearningService
from app.study_materials import study_material_manager, StudyMaterialError, StudyMaterialIndexError
from app.input_policy import (
    validate_query_text,
    validate_chat_history,
    validate_identifier,
    validate_topic_input,
)

logger = logging.getLogger("psychology_agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="Local Multi-domain Study Agent", version="1.0.0")

@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(request: Request, exc: RequestValidationError):
    """Sanitize error messages so invalid Unicode surrogates or raw malicious input never fail JSON encoding."""
    def _sanitize(val: Any) -> Any:
        if isinstance(val, str):
            return val.encode("utf-8", "replace").decode("utf-8", "replace")
        elif isinstance(val, (list, tuple)):
            return [_sanitize(x) for x in val]
        elif isinstance(val, dict):
            return {k: _sanitize(v) for k, v in val.items()}
        return val

    safe_errors = []
    for err in exc.errors():
        safe_errors.append({
            "type": _sanitize(err.get("type", "value_error")),
            "loc": _sanitize(err.get("loc", ())),
            "msg": _sanitize(str(err.get("msg", "Dữ liệu đầu vào không hợp lệ."))),
        })
    return JSONResponse(status_code=422, content={"detail": safe_errors})

MAX_POST_BODY_BYTES = 128 * 1024  # 128 KiB limit for chat and learning endpoints

@app.middleware("http")
async def limit_json_body_middleware(request, call_next):
    path = request.url.path
    if request.method == "POST" and (path.startswith("/api/chat") or path.startswith("/api/learning")):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_POST_BODY_BYTES:
                    return JSONResponse(status_code=413, content={"detail": "Dung lượng yêu cầu vượt quá giới hạn 128 KiB."})
            except ValueError:
                pass
        receive_ = request._receive
        total_received = 0
        chunks = []
        while True:
            msg = await receive_()
            if msg["type"] == "http.request":
                chunk = msg.get("body", b"")
                total_received += len(chunk)
                if total_received > MAX_POST_BODY_BYTES:
                    return JSONResponse(status_code=413, content={"detail": "Dung lượng yêu cầu vượt quá giới hạn 128 KiB."})
                chunks.append(chunk)
                if not msg.get("more_body", False):
                    break
            elif msg["type"] == "http.disconnect":
                break

        full_body = b"".join(chunks)
        delivered = False
        async def replayed_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            return await receive_()
        request._receive = replayed_receive
        return await call_next(request)
    return await call_next(request)

# Mount static files
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

indexer = KnowledgeIndexer()
searcher = HybridSearcher()
document_lookup = DocumentLookup()
agent = PsychologyAgent(searcher=searcher)
practice_service = PracticeService(searcher=searcher, document_lookup=document_lookup)
learning_service = LearningService(document_lookup=document_lookup, practice_service=practice_service, searcher=searcher)
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
summarizer = TopicSummarizer(history_store=history_store)

class ChatRequest(BaseModel):
    query: str
    chat_history: Optional[List[Dict[str, Any]]] = None
    processing_level: Literal["auto", "standard", "careful"] = "standard"
    answer_mode: Literal["quick", "steps", "compare", "summary", "step_by_step", "comparison", "normal"] = "quick"
    explanation_mode: Optional[Literal["quick", "steps", "compare", "summary", "step_by_step", "comparison", "normal"]] = None
    isolate_context: Optional[bool] = False
    temperature: Optional[float] = Field(default=0.2, ge=0, le=1)
    save_history: Optional[bool] = True
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    source_document_id: Optional[int] = Field(default=None, ge=1)
    source_page_num: Optional[int] = Field(default=None, ge=1)
    attachment_filename: Optional[str] = None

    @field_validator("query")
    @classmethod
    def check_query(cls, v: str) -> str:
        return validate_query_text(v)

    @field_validator("chat_history")
    @classmethod
    def check_chat_history(cls, v: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, str]]]:
        return validate_chat_history(v)

    @field_validator("session_id")
    @classmethod
    def check_session_id(cls, v: Optional[str]) -> Optional[str]:
        return validate_identifier(v, "session_id")

    @field_validator("request_id")
    @classmethod
    def check_request_id(cls, v: Optional[str]) -> Optional[str]:
        return validate_identifier(v, "request_id")

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


QueryRequest = ChatRequest


def requested_source_scope(req):
    source_doc_id = req.source_document_id
    if source_doc_id is None and getattr(req, "attachment_filename", None):
        found = document_lookup.document_by_filename(req.attachment_filename)
        if not found:
            raise HTTPException(status_code=404, detail='Không tìm thấy tài liệu đính kèm đã chọn. Hãy chọn lại từ thư viện.')
        source_doc_id = found["id"]
    if source_doc_id is None and getattr(req, "session_id", None):
        sess = history_store.get_session(req.session_id)
        if sess and sess.get("turns"):
            for turn in reversed(sess["turns"]):
                scope = turn.get("search_scope") or {}
                if scope.get("document_id") or scope.get("attachment_filename"):
                    source_doc_id = scope.get("document_id")
                    if not getattr(req, "attachment_filename", None):
                        req.attachment_filename = scope.get("attachment_filename")
                    break
    if source_doc_id is None:
        return None, None
    doc = document_lookup.document(source_doc_id)
    if not doc or (req.source_page_num is not None and req.source_page_num > doc['total_pages']):
        raise HTTPException(status_code=404, detail='Không tìm thấy tài liệu hoặc trang nguồn đã chọn.')
    if getattr(req, "attachment_filename", None) and req.attachment_filename != doc.get("filename"):
        raise HTTPException(status_code=400, detail='Tệp đính kèm và tài liệu nguồn không khớp. Hãy chọn lại tài liệu.')
    if not getattr(req, "attachment_filename", None) and doc.get("filename"):
        req.attachment_filename = doc["filename"]
    page = (source_doc_id, req.source_page_num) if req.source_page_num is not None else None
    return page, source_doc_id


def requested_source_page(req):
    """Backward-compatible helper for callers that need only a page selection."""
    return requested_source_scope(req)[0]


class IndexRequest(BaseModel):
    force: bool = False
    ocr: bool = True


async def locked_history_mutation(method, *args):
    def mutate():
        with dialogue_gate.acquire():
            return method(*args)
    try:
        return await run_in_threadpool(mutate)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})


from app.routes.learning_routes import (
    router as _learning_router,
    PracticeGenerateRequest,
    PracticeAnswerRequest,
    LearningGenerateRequest,
    LearningExercisesRequest,
    generate_practice,
    list_practice_examples,
    answer_practice,
    get_learning_topics,
    generate_learning,
    list_learning_exercises,
)
from app.routes.material_routes import (
    router as _material_router,
    MaterialUploadRequest,
    MaterialLibraryGenerateRequest,
    MaterialLibraryInspectRequest,
    upload_and_generate_materials,
    inspect_library_document_endpoint,
    generate_materials_from_library,
    get_study_material_documents,
    download_study_material_file,
    get_study_material,
)
from app.routes.document_routes import (
    router as _document_router,
    list_documents,
    lookup_documents,
    document_source_preview,
    open_document_pdf,
    open_document_page_image,
)
from app.routes.ocr_routes import (
    router as _ocr_router,
    OCRReviewRequest,
    OCRRunPageRequest,
    list_ocr_reviews,
    submit_ocr_review,
    get_ocr_page_image,
    run_ocr_page,
    get_ocr_locator,
)
from app.routes.timetable_routes import (
    router as _timetable_router,
    TIME_PATTERN,
    TimetableExtractRequest,
    TimetableConfirmEntry,
    TimetableConfirmRequest,
    TimetableManualEntryRequest,
    get_timetable,
    extract_timetable,
    confirm_timetable,
    get_timetable_draft,
    get_timetable_image,
    add_timetable_manual_entry,
    delete_timetable_entry,
    clear_timetable,
    export_timetable_ics,
)
from app.routes.session_routes import (
    router as _session_router,
    SessionCreateRequest,
    SessionRenameRequest,
    SessionSummarizeRequest,
    list_history,
    get_history,
    delete_history,
    clear_history,
    list_sessions,
    create_session,
    search_sessions,
    get_session,
    rename_session,
    delete_session,
    clear_all_sessions,
    summarize_session,
)

for _subrouter in (
    _learning_router,
    _material_router,
    _document_router,
    _ocr_router,
    _timetable_router,
    _session_router,
):
    app.router.routes.extend(_subrouter.routes)
if hasattr(app.router, "_mark_routes_changed"):
    app.router._mark_routes_changed()



@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_html = STATIC_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(content=index_html.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Study QA Agent Backend Running</h1><p>index.html not found in static/</p>")

def _requires_mlx_model():
    from app.model_resolver import is_mlx_model
    return is_mlx_model(DEFAULT_CHAT_MODEL)


def _inference_model_available(mlx_available, models):
    from app.model_resolver import reject_retired_model
    try:
        reject_retired_model(DEFAULT_CHAT_MODEL)
    except ValueError:
        return False
    if _requires_mlx_model():
        return mlx_available
    return any(model.get("name") in {DEFAULT_CHAT_MODEL, DEFAULT_CHAT_MODEL + ":latest"}
               for model in models)


def _configured_index_coverage():
    """Check actual embedding bytes off the event loop, independently of QA."""
    import sqlite3
    from app.config import DB_PATH, DEFAULT_EMBED_MODEL
    from app.vector_integrity import index_coverage
    with sqlite3.connect(DB_PATH.resolve().as_uri() + '?mode=ro', uri=True) as db:
        actual_identity = None
        try:
            installed_digest = None
            try:
                row = db.execute("SELECT model_digest FROM corpus_vector_provenance WHERE full_text=1 LIMIT 1").fetchone()
                if row:
                    installed_digest = row[0]
            except sqlite3.Error:
                pass

            if installed_digest and installed_digest.startswith("ollama-sha256:"):
                actual_identity = ollama.embedding_identity(DEFAULT_EMBED_MODEL)
                expected_identity = actual_identity
            elif installed_digest and installed_digest.startswith("bge-m3-mlx-fp16@"):
                if sys.platform != 'darwin':
                    actual_identity = None
                    expected_identity = None
                else:
                    from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient
                    actual_identity = NativeEmbeddingClient().get_identity()
                    expected_identity = actual_identity if actual_identity == BGE_NATIVE_IDENTITY else None
            elif os.getenv("EMBED_BACKEND") == "ollama" or sys.platform != 'darwin':
                actual_identity = ollama.embedding_identity(DEFAULT_EMBED_MODEL)
                expected_identity = actual_identity
            else:
                from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient
                actual_identity = NativeEmbeddingClient().get_identity()
                expected_identity = actual_identity if actual_identity == BGE_NATIVE_IDENTITY else None
        except Exception as exc:
            logger.debug('Embedding identity unavailable during coverage check: %s', type(exc).__name__)
            expected_identity = None
        result = index_coverage(db, expected_identity)
        result['configured_model_identity'] = actual_identity
        return result


@app.get("/api/health")
async def health_check():
    requires_mlx = _requires_mlx_model()
    installed_digest = None
    try:
        from app.config import DB_PATH
        if DB_PATH.exists():
            import sqlite3
            with sqlite3.connect(DB_PATH.resolve().as_uri() + '?mode=ro', uri=True) as db:
                row = db.execute("SELECT model_digest FROM corpus_vector_provenance WHERE full_text=1 LIMIT 1").fetchone()
                if row:
                    installed_digest = row[0]
    except Exception:
        pass

    embed_uses_ollama = (
        os.getenv("EMBED_BACKEND") == "ollama"
        or (installed_digest and installed_digest.startswith("ollama-sha256:"))
        or (sys.platform != "darwin" and os.getenv("EMBED_BACKEND") not in ("native", "mlx"))
    )

    ollama_needed = (not requires_mlx) or embed_uses_ollama or (sys.platform != "darwin")
    ollama_ok = ollama.check_health() if ollama_needed else False
    gpu_status = gpu_coordinator.get_status()
    gpu_busy = bool(gpu_status.get("busy", False))
    mlx_available = TrainedModelClient.available()
    models = [] if (gpu_busy or not ollama_ok) else ollama.list_models()
    runtime = await run_in_threadpool(probe_mlx_runtime) if requires_mlx and sys.platform == "darwin" else None
    model_ready = _inference_model_available(mlx_available, models) and (runtime is None or runtime["ready"])

    names = {m.get("name") for m in models} if models else set()

    # QA readiness
    qa_ready = bool(model_ready and not gpu_busy)

    # Vision readiness
    if requires_mlx and sys.platform == "darwin":
        from app.native_vision import NativeVisionClient
        vision_ready = bool(NativeVisionClient.available() and not gpu_busy and (runtime is None or runtime["ready"]))
    else:
        vision_ready = bool(OLLAMA_AUX_CHAT_MODEL in names and not gpu_busy)

    # Embedding readiness
    if embed_uses_ollama:
        embedding_ready = bool(ollama_ok and any("bge-m3" in m for m in names) and not gpu_busy)
    elif sys.platform == "darwin":
        from app.native_embedding import NativeEmbeddingClient
        embedding_ready = bool(NativeEmbeddingClient.available() and not gpu_busy and (runtime is None or runtime["ready"]))
    else:
        embedding_ready = False

    if gpu_busy:
        service_state = "degraded"
        service_message = f"GPU đang bận huấn luyện (PID {gpu_status.get('pid')}) - Suy luận tạm thời bị chặn, web hoạt động bình thường"
        inference_ready = False
    elif not requires_mlx and not ollama_ok:
        service_state = "degraded"
        service_message = "Ollama mất kết nối"
        inference_ready = False
    elif not model_ready:
        service_state = "degraded"
        service_message = (runtime["message"] if runtime and not runtime["ready"] else f"Mô hình MLX {DEFAULT_CHAT_MODEL} chưa khả dụng" if requires_mlx
                           else f"Mô hình Ollama {DEFAULT_CHAT_MODEL} chưa khả dụng")
        inference_ready = False
    else:
        service_state = "healthy"
        service_message = "Môi trường mô hình sẵn sàng. Dùng Kiểm tra mô hình để chạy thử suy luận."
        inference_ready = True

    index_coverage = {
        "total_chunks": 0,
        "compatible_native_chunks": 0,
        "coverage_ratio": 0.0,
        "status": "keyword_only",
        "degraded_fallback": True,
    }
    try:
        from app.config import DB_PATH
        if DB_PATH.exists():
            index_coverage = await run_in_threadpool(_configured_index_coverage)
    except Exception as exc:
        logger.debug("Failed to calculate index coverage: %s", exc)

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
        "runtime": runtime,
        "qa_ready": qa_ready,
        "vision_ready": vision_ready,
        "embedding_ready": embedding_ready,
        "features": {
            "qa": qa_ready,
            "vision": vision_ready,
            "embedding": embedding_ready,
        },
        "index_coverage": index_coverage,
        "gpu": gpu_status
    }

@app.post("/api/model/check")
async def check_local_model():
    """User-operated warm-up of the configured local model; never switches it."""
    def run_check():
        with dialogue_gate.acquire(), gpu_coordinator.acquire_for_inference():
            from app.model_resolver import is_mlx_model
            if is_mlx_model(DEFAULT_CHAT_MODEL):
                runtime = probe_mlx_runtime(force=True)
                if not runtime["ready"]:
                    return {"ready": False, "model": DEFAULT_CHAT_MODEL, "message": runtime["message"], "code": runtime["code"]}
                client = TrainedModelClient(model_name=DEFAULT_CHAT_MODEL)
                answer = client.chat_complete(
                    [{"role": "user", "content": "Chỉ trả lời: OK"}], max_tokens=16, temperature=0.0,
                )
            else:
                client = ollama
                answer = client.chat_complete(
                    [{"role": "user", "content": "Chỉ trả lời: OK"}], model=DEFAULT_CHAT_MODEL,
                    num_predict=16, temperature=0.0, think=False,
                )
            done_reason = getattr(client, "last_done_reason", None)
            if done_reason != "stop":
                raise RuntimeError(f"Mô hình phản hồi chưa trọn vẹn (done_reason: {done_reason or 'unknown'})")
            clean_answer = str(answer or "")
            clean_answer = re.sub(r"(?is)<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", "", clean_answer).strip()
            if not clean_answer or clean_answer.startswith(("❌", "Lỗi từ Ollama", "Lỗi:", "Error:")):
                raise RuntimeError("Empty or failed model response")
            return {"ready": True, "model": DEFAULT_CHAT_MODEL, "code": "inference_verified",
                    "message": "Đã chạy thử mô hình cục bộ thành công. Kết quả này không chứng nhận độ chính xác học thuật."}
    try:
        return await run_in_threadpool(run_check)
    except (DialogueBusyError, GPUBusyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"}) from exc
    except Exception as exc:
        logger.exception("Local model self-check failed")
        return {"ready": False, "model": DEFAULT_CHAT_MODEL, "code": "inference_failed", "message": runtime_failure_message(exc)}


@app.get("/api/models")
async def get_models():
    from app.active_registry import active_registry
    from app.model_resolver import is_9b_model
    registry_info = active_registry.get_active_info()
    is_mlx = _requires_mlx_model()
    models = [] if is_mlx else (ollama.list_models() if ollama.check_health() else [])
    # Exclude vision auxiliary models from QA list; vision is only for OCR
    qa_models = [
        m for m in models
        if "vl" not in m.get("name", "").lower() and "vision" not in m.get("name", "").lower()
    ]
    if is_mlx:
        active_adapter = None
        model_mode = "unadapted_base"
        model_description = "Qwen3.5 9B (MLX Nền tảng cục bộ - unadapted base)"
    else:
        active_adapter = None
        model_mode = "unadapted_base"
        model_description = f"{DEFAULT_CHAT_MODEL} (Ollama - unadapted base)"
    return {
        "models": qa_models,
        "default_model": DEFAULT_CHAT_MODEL,
        "default_model_backend": "mlx" if is_mlx else "ollama",
        "availability_check": "local_artifacts",
        "default_model_available": _inference_model_available(TrainedModelClient.available(), models),
        "trained_model": DEFAULT_CHAT_MODEL if (is_mlx and TrainedModelClient.available()) else (TRAINED_MODEL_NAME if TrainedModelClient.available() else None),
        "active_adapter": active_adapter,
        "model_mode": model_mode,
        "model_description": model_description,
    }

@app.get("/api/storage")
async def get_storage_info():
    return {
        "sources_dir": str(SRC_DIR),
        "data_dir": str(DATA_DIR),
        "persistent": True,
        "local_only": True,
        "auto_watch": False,
        "policy": "Tài liệu tải lên được lưu trữ trên máy tính này, được bảo toàn sau khi khởi động lại, không tự động xuất bản hay dùng để huấn luyện mô hình."
    }

@app.get("/api/status")
async def get_index_status():
    status = indexer.get_status()
    requires_mlx = _requires_mlx_model()
    ollama_ok = False if requires_mlx else ollama.check_health()
    models = [] if (requires_mlx or not ollama_ok) else ollama.list_models()
    gpu_status = gpu_coordinator.get_status()
    gpu_busy = bool(gpu_status.get("busy", False))
    mlx_available = TrainedModelClient.available()
    runtime = await run_in_threadpool(probe_mlx_runtime) if requires_mlx and sys.platform == "darwin" else None
    model_ready = _inference_model_available(mlx_available, models) and (runtime is None or runtime["ready"])

    if gpu_busy:
        service_state = "degraded"
        service_message = f"GPU đang bận huấn luyện (PID {gpu_status.get('pid')}) - Suy luận tạm thời bị chặn, web hoạt động bình thường"
        inference_ready = False
    elif not _requires_mlx_model() and not ollama_ok:
        service_state = "degraded"
        service_message = "Ollama mất kết nối"
        inference_ready = False
    elif not model_ready:
        service_state = "degraded"
        service_message = (runtime["message"] if runtime and not runtime["ready"] else f"Mô hình MLX {DEFAULT_CHAT_MODEL} chưa khả dụng" if _requires_mlx_model()
                           else f"Mô hình Ollama {DEFAULT_CHAT_MODEL} chưa khả dụng")
        inference_ready = False
    else:
        service_state = "healthy"
        service_message = "Môi trường mô hình sẵn sàng. Dùng Kiểm tra mô hình để chạy thử suy luận."
        inference_ready = True

    storage_info = {
        "sources_dir": str(SRC_DIR),
        "data_dir": str(DATA_DIR),
        "persistent": True,
        "local_only": True,
        "auto_watch": False,
        "policy": "Tài liệu tải lên được lưu trữ trên máy tính này, được bảo toàn sau khi khởi động lại, không tự động xuất bản hay dùng để huấn luyện mô hình."
    }

    return {
        "service_state": service_state,
        "service_message": service_message,
        "inference_ready": inference_ready,
        "runtime": runtime,
        "knowledge_base": status,
        "trained_model": {"name": DEFAULT_CHAT_MODEL if _requires_mlx_model() else TRAINED_MODEL_NAME, "available": mlx_available},
        "ollama": {
            "connected": ollama_ok,
            "url": ollama.base_url,
            "models": [m.get("name") for m in models]
        },
        "gpu": gpu_status,
        "storage": storage_info
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
        # Store-authoritative durable server context bounded to last 8 turns with bounded original-turn recall
        effective_history = [] if req.isolate_context else history_store.get_session_context(target_session_id, max_turns=8, query=req.query, document_id=source_document_id)
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
                attachment_filename=req.attachment_filename,
                isolate_context=bool(req.isolate_context),
                processing_level=req.processing_level,
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
    if (
        answer
        and req.save_history
        and not is_truncated
        and not res.get("error")
        and res.get("response_kind") not in {"generation_failed", "technical_error"}
    ):
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
    res["processing_level"] = req.processing_level
    res["resolved_processing_level"] = res.get("resolved_processing_level", req.processing_level)
    res["generation_calls"] = res.get("generation_calls", 1)
    res["check_status"] = res.get("check_status", "not_checked")
    return res


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
        effective_history = [] if req.isolate_context else history_store.get_session_context(target_session_id, max_turns=8, query=req.query, document_id=source_document_id)
        create_on_completion = False
    else:
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
                    effective_history = history_store.get_session_context(target_session_id, max_turns=8, query=req.query, document_id=source_document_id)
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
                    attachment_filename=req.attachment_filename,
                    isolate_context=bool(req.isolate_context),
                    processing_level=req.processing_level,
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
                        event.setdefault("processing_level", req.processing_level)
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
                            and event.get("response_kind") not in {"generation_failed", "technical_error"}
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
