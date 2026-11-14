"""Study Material Upload, In-Memory Flashcard/Quiz & Library Generation Routes (Single Responsibility Module)."""

import base64
import logging
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.dialogue import DialogueBusyError
from app.gpu_lock import GPUBusyError
from app.response_projection import (
    is_degraded_runtime_error,
    project_public_response,
    sanitize_error_detail,
)
from app.study_materials import StudyMaterialError, StudyMaterialIndexError

logger = logging.getLogger("psychology_agent")

router = APIRouter()


class MaterialUploadRequest(BaseModel):
    filename: str
    file_base64: str
    quiz_count: int = Field(default=3, ge=1, le=10)
    flashcard_count: int = Field(default=5, ge=1, le=15)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    generate_study_set: bool = True
    persist_to_src: Optional[bool] = Field(
        default=False,
        description=(
            "Whether to persist document to src/ and index into knowledge base. "
            "Defaults to False (instant use; omitted or null is treated as false). "
            "Pass True explicitly to persist and index."
        ),
    )


class MaterialLibraryGenerateRequest(BaseModel):
    document_id: Optional[int] = Field(default=None, ge=1)
    filename: Optional[str] = None
    page_from: int = Field(default=1, ge=1)
    page_to: Optional[int] = Field(default=None, ge=1)
    quiz_count: int = Field(default=3, ge=1, le=10)
    flashcard_count: int = Field(default=5, ge=1, le=15)
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class MaterialLibraryInspectRequest(BaseModel):
    document_id: Optional[int] = Field(default=None, ge=1)
    filename: Optional[str] = None
    persist_to_src: Optional[bool] = Field(
        default=False,
        description=(
            "Whether to persist inspected document to src/ and index. "
            "Defaults to False (instant use; omitted or null is treated as false). "
            "Pass True explicitly to persist and index."
        ),
    )


def _retain_valid_upload_extraction(manager, filename: str, file_bytes: bytes, extracted, ingestion_info) -> None:
    """Persist valid extracted material and raw file when generation fails on degraded/busy runtime."""
    try:
        saved_record = manager.store_extracted_document(
            extracted, ingestion_info=ingestion_info
        )
        manager.cache_raw_file(
            saved_record.get("filename") or getattr(extracted, "filename", None) or filename,
            file_bytes,
            material_id=saved_record.get("id"),
        )
    except Exception:
        logger.warning("Không thể lưu tạm học liệu đã trích xuất cho %s", filename, exc_info=True)


def _raise_material_http_error(exc: Exception, *, fallback_detail: str, context_label: str) -> None:
    """Map material route errors to bounded HTTP statuses without path or traceback leaks."""
    from app.dialogue import ContextLimitError

    if isinstance(exc, StudyMaterialIndexError):
        raise HTTPException(
            status_code=503,
            detail="Không thể lưu hoặc cập nhật chỉ mục tài liệu vào cơ sở tri thức. Vui lòng thử lại sau.",
        ) from exc
    if isinstance(exc, GPUBusyError):
        raise HTTPException(
            status_code=503,
            detail=sanitize_error_detail(str(exc)),
            headers={"Retry-After": "2"},
        ) from exc
    if isinstance(exc, DialogueBusyError):
        raise HTTPException(
            status_code=409,
            detail=sanitize_error_detail(str(exc)),
            headers={"Retry-After": "2"},
        ) from exc
    if is_degraded_runtime_error(exc):
        logger.warning("%s: runtime unavailable (%s)", context_label, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail=sanitize_error_detail(str(exc)),
        ) from exc
    if isinstance(exc, (StudyMaterialError, ContextLimitError)):
        raise HTTPException(status_code=422, detail=sanitize_error_detail(str(exc))) from exc
    logger.exception(context_label)
    raise HTTPException(status_code=500, detail=fallback_detail) from exc


@router.post("/api/materials/upload")
async def upload_and_generate_materials(req: MaterialUploadRequest):
    from app import server as _srv
    try:
        raw_b64 = req.file_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        file_bytes = base64.b64decode(raw_b64)
    except Exception:
        raise HTTPException(status_code=400, detail="Dữ liệu tệp tải lên (Base64) không hợp lệ.")

    retained_extraction = False

    def run_extraction_and_generation():
        nonlocal retained_extraction
        quota_check = getattr(_srv.study_material_manager, "check_upload_quota", None)
        if callable(quota_check):
            quota_check(file_bytes)
        extracted = _srv.study_material_manager.extract_document(
            req.filename,
            file_bytes,
            allow_image_without_text=not req.generate_study_set,
        )
        should_persist = bool(req.persist_to_src)
        ingestion_info = None
        if should_persist:
            ingestion_info = _srv.study_material_manager.ingest_to_src_and_index(
                req.filename, file_bytes, extracted
            )
        if not req.generate_study_set:
            record = _srv.study_material_manager.store_extracted_document(
                extracted, ingestion_info=ingestion_info
            )
        else:
            try:
                record = _srv.study_material_manager.generate_materials_from_pages(
                    extracted,
                    quiz_count=req.quiz_count,
                    flashcard_count=req.flashcard_count,
                    difficulty=req.difficulty,
                    model=_srv.DEFAULT_CHAT_MODEL,
                )
            except Exception as exc:
                if isinstance(exc, (GPUBusyError, DialogueBusyError)) or is_degraded_runtime_error(exc):
                    _retain_valid_upload_extraction(
                        _srv.study_material_manager,
                        req.filename,
                        file_bytes,
                        extracted,
                        ingestion_info,
                    )
                    retained_extraction = True
                raise
            if ingestion_info:
                record.update(ingestion_info)
        _srv.study_material_manager.cache_raw_file(
            record.get("filename") or getattr(extracted, "filename", None) or req.filename,
            file_bytes,
            material_id=record.get("id"),
        )
        return record

    try:
        record = await run_in_threadpool(run_extraction_and_generation)
        return project_public_response({
            "status": "success",
            "material": record,
            "material_id": record["id"],
            "id": record["id"],
        })
    except Exception as exc:
        if not retained_extraction:
            _srv.study_material_manager.evict_raw_file(req.filename)
        _raise_material_http_error(
            exc,
            fallback_detail="Lỗi xử lý tài liệu tải lên. Vui lòng thử lại sau.",
            context_label="Lỗi xử lý tài liệu tải lên",
        )


@router.post("/api/materials/inspect-library")
async def inspect_library_document_endpoint(req: MaterialLibraryInspectRequest):
    from app import server as _srv

    def run_lib_inspect():
        should_persist = bool(req.persist_to_src)
        return _srv.study_material_manager.inspect_library_document(
            doc_id=req.document_id,
            filename=req.filename,
            persist_to_src=should_persist,
        )

    try:
        record = await run_in_threadpool(run_lib_inspect)
        return project_public_response({
            "status": "success",
            "material": record,
            "material_id": record["id"],
            "id": record["id"],
        })
    except Exception as exc:
        _raise_material_http_error(
            exc,
            fallback_detail="Lỗi đọc nội dung tài liệu thư viện. Vui lòng thử lại sau.",
            context_label="Lỗi đọc nội dung tài liệu thư viện",
        )


@router.post("/api/materials/generate-from-library")
async def generate_materials_from_library(req: MaterialLibraryGenerateRequest):
    from app import server as _srv

    def run_lib_generation():
        return _srv.study_material_manager.generate_from_library_document(
            doc_id=req.document_id,
            filename=req.filename,
            page_from=req.page_from,
            page_to=req.page_to,
            quiz_count=req.quiz_count,
            flashcard_count=req.flashcard_count,
            difficulty=req.difficulty,
            model=_srv.DEFAULT_CHAT_MODEL,
        )

    try:
        record = await run_in_threadpool(run_lib_generation)
        return project_public_response({
            "status": "success",
            "material": record,
            "material_id": record["id"],
            "id": record["id"],
        })
    except Exception as exc:
        _raise_material_http_error(
            exc,
            fallback_detail="Lỗi tạo câu hỏi từ thư viện. Vui lòng thử lại sau.",
            context_label="Lỗi tạo câu hỏi từ thư viện",
        )


@router.get("/api/materials/documents")
async def get_study_material_documents():
    from app import server as _srv

    def load_all_docs():
        indexed = _srv.document_lookup.documents()
        return _srv.study_material_manager.list_available_documents(indexed)

    docs = await run_in_threadpool(load_all_docs)
    return project_public_response({"documents": docs})


@router.get("/api/materials/download")
async def download_study_material_file(
    filename: Optional[str] = Query(None, max_length=1000),
    material_id: Optional[str] = Query(None, max_length=128),
    document_id: Optional[int] = Query(None, ge=1),
):
    from app import server as _srv

    resolved = await run_in_threadpool(
        _srv.study_material_manager.resolve_raw_file,
        filename=filename,
        material_id=material_id,
        doc_id=document_id,
    )
    if not resolved:
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp gốc để tải về.")

    safe_name, raw_bytes = resolved
    ext = Path(safe_name).suffix.lower()
    mime_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".markdown": "text/markdown; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    media_type = mime_map.get(ext, "application/octet-stream")
    ascii_name = safe_name.encode("ascii", "ignore").decode("ascii") or f"document{ext}"
    encoded_name = quote(safe_name)
    return Response(
        content=raw_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
        },
    )


@router.get("/api/materials/{material_id}")
async def get_study_material(material_id: str):
    from app import server as _srv
    mat = _srv.study_material_manager.get_material(material_id)
    if not mat:
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ tài liệu ôn tập đã tạo.")
    return project_public_response(mat)
