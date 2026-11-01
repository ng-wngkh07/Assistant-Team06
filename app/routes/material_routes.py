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
    persist_to_src: Optional[bool] = None


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
    persist_to_src: Optional[bool] = None


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

    def run_extraction_and_generation():
        extracted = _srv.study_material_manager.extract_document(
            req.filename,
            file_bytes,
            allow_image_without_text=not req.generate_study_set,
        )
        should_persist = True if req.persist_to_src is None else bool(req.persist_to_src)
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
            record = _srv.study_material_manager.generate_materials_from_pages(
                extracted,
                quiz_count=req.quiz_count,
                flashcard_count=req.flashcard_count,
                difficulty=req.difficulty,
                model=_srv.DEFAULT_CHAT_MODEL,
            )
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
        return {
            "status": "success",
            "material": record,
            "material_id": record["id"],
            "id": record["id"],
        }
    except StudyMaterialError as exc:
        _srv.study_material_manager.evict_raw_file(req.filename)
        raise HTTPException(status_code=422, detail=str(exc))
    except StudyMaterialIndexError:
        _srv.study_material_manager.evict_raw_file(req.filename)
        raise HTTPException(
            status_code=503,
            detail="Không thể lưu hoặc cập nhật chỉ mục tài liệu vào cơ sở tri thức. Vui lòng thử lại sau.",
        )
    except GPUBusyError as exc:
        _srv.study_material_manager.evict_raw_file(req.filename)
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"})
    except DialogueBusyError as exc:
        _srv.study_material_manager.evict_raw_file(req.filename)
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    except Exception:
        _srv.study_material_manager.evict_raw_file(req.filename)
        logger.exception("Lỗi xử lý tài liệu tải lên")
        raise HTTPException(status_code=500, detail="Lỗi xử lý tài liệu tải lên. Vui lòng thử lại sau.")


@router.post("/api/materials/inspect-library")
async def inspect_library_document_endpoint(req: MaterialLibraryInspectRequest):
    from app import server as _srv

    def run_lib_inspect():
        should_persist = True if req.persist_to_src is None else bool(req.persist_to_src)
        return _srv.study_material_manager.inspect_library_document(
            doc_id=req.document_id,
            filename=req.filename,
            persist_to_src=should_persist,
        )

    try:
        record = await run_in_threadpool(run_lib_inspect)
        return {
            "status": "success",
            "material": record,
            "material_id": record["id"],
            "id": record["id"],
        }
    except StudyMaterialError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except StudyMaterialIndexError:
        raise HTTPException(
            status_code=503,
            detail="Không thể lưu hoặc cập nhật chỉ mục tài liệu vào cơ sở tri thức. Vui lòng thử lại sau.",
        )
    except GPUBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"})
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    except Exception:
        logger.exception("Lỗi đọc nội dung tài liệu thư viện")
        raise HTTPException(status_code=500, detail="Lỗi đọc nội dung tài liệu thư viện. Vui lòng thử lại sau.")


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
        return {
            "status": "success",
            "material": record,
            "material_id": record["id"],
            "id": record["id"],
        }
    except StudyMaterialError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except GPUBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"})
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    except Exception:
        logger.exception("Lỗi tạo câu hỏi từ thư viện")
        raise HTTPException(status_code=500, detail="Lỗi tạo câu hỏi từ thư viện. Vui lòng thử lại sau.")


@router.get("/api/materials/documents")
async def get_study_material_documents():
    from app import server as _srv

    def load_all_docs():
        indexed = _srv.document_lookup.documents()
        return _srv.study_material_manager.list_available_documents(indexed)

    docs = await run_in_threadpool(load_all_docs)
    return {"documents": docs}


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
    return mat
