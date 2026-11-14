"""Document Lookup, Source Preview & Original Page Image API Routes (Single Responsibility Module)."""

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.dialogue import DialogueBusyError, acquire_inference
from app.response_projection import project_public_response, sanitize_error_detail

router = APIRouter()

DOCUMENT_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _require_fresh_document(
    lookup,
    *,
    doc_id: Optional[int] = None,
    filename: Optional[str] = None,
    default_404: str,
    conflict_on_missing: bool = False,
) -> dict:
    inspect_fn = getattr(lookup, "inspect_document_status", None)
    if callable(inspect_fn):
        info = inspect_fn(doc_id=doc_id, filename=filename)
        if not info.get("fresh"):
            status = info.get("status")
            if status in {"needs_update", "error"} or (conflict_on_missing and status == "missing_source"):
                raise HTTPException(
                    status_code=409,
                    detail=sanitize_error_detail(
                        info.get("message"),
                        fallback="Tài liệu nguồn đã thay đổi hoặc cần cập nhật chỉ mục.",
                    ),
                )
            detail = (
                sanitize_error_detail(info.get("message"), fallback=default_404)
                if status == "missing_source"
                else default_404
            )
            raise HTTPException(status_code=404, detail=detail or default_404)
        return info["doc"]
    doc = lookup.document(doc_id) if doc_id is not None else lookup.document_by_filename(filename)
    if not doc:
        raise HTTPException(status_code=404, detail=default_404)
    return doc


@router.get("/api/documents")
async def list_documents():
    from app import server as _srv
    return project_public_response({"documents": await run_in_threadpool(_srv.document_lookup.documents)})


@router.get("/api/documents/search")
async def lookup_documents(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(10, ge=1, le=30),
    document_id: Optional[int] = Query(None, ge=1),
    method: Literal["fts", "hybrid"] = "fts",
):
    from app import server as _srv
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Nhập từ khóa cần tìm.")
    if document_id is not None:
        await run_in_threadpool(
            _require_fresh_document,
            _srv.document_lookup,
            doc_id=document_id,
            default_404="Không tìm thấy tài liệu.",
            conflict_on_missing=True,
        )

    def run_lookup():
        if method == "fts":
            return _srv.document_lookup.search(query, limit, document_id, method)
        with acquire_inference(_srv.dialogue_gate):
            return _srv.document_lookup.search(query, limit, document_id, method)

    try:
        result = await run_in_threadpool(run_lookup)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=sanitize_error_detail(str(exc)), headers={"Retry-After": "2"})
    return project_public_response({"query": query, **result})


@router.get("/api/documents/source-preview")
async def document_source_preview(
    filename: str = Query(..., max_length=1000),
    page_num: int = Query(..., ge=1),
):
    from app import server as _srv
    await run_in_threadpool(
        _require_fresh_document,
        _srv.document_lookup,
        filename=filename,
        default_404="Không tìm thấy trang nguồn.",
    )
    result = await run_in_threadpool(_srv.document_lookup.source_preview, filename, page_num)
    if not result:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang nguồn.")
    return project_public_response(result)


@router.get("/api/documents/{document_id}/original")
async def download_document_original(document_id: int):
    from app import server as _srv
    doc = await run_in_threadpool(
        _require_fresh_document,
        _srv.document_lookup,
        doc_id=document_id,
        default_404="Không tìm thấy tài liệu trong thư viện.",
    )
    path = _srv.document_lookup.supported_path(doc["filename"])
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp nguồn của tài liệu.")
    ext = path.suffix.lower()
    media_type = DOCUMENT_MEDIA_TYPES.get(ext)
    if not media_type:
        raise HTTPException(status_code=404, detail="Định dạng tệp không được hỗ trợ tải về.")
    return FileResponse(
        path,
        media_type=media_type,
        filename=Path(doc["filename"]).name,
        content_disposition_type="attachment",
    )


@router.get("/api/documents/{document_id}/pdf")
async def open_document_pdf(document_id: int):
    from app import server as _srv
    doc = await run_in_threadpool(
        _require_fresh_document,
        _srv.document_lookup,
        doc_id=document_id,
        default_404="Không tìm thấy PDF trong thư viện.",
    )
    path = _srv.document_lookup.pdf_path(doc["filename"])
    if not path:
        raise HTTPException(status_code=404, detail="Không tìm thấy PDF trong thư viện.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=doc["filename"],
        content_disposition_type="inline",
    )


@router.get("/api/documents/{document_id}/pages/{page_num}/image")
async def open_document_page_image(document_id: int, page_num: int):
    from app import server as _srv
    await run_in_threadpool(
        _require_fresh_document,
        _srv.document_lookup,
        doc_id=document_id,
        default_404="Không tìm thấy trang gốc của tài liệu.",
    )
    data = await run_in_threadpool(_srv.document_lookup.page_image, document_id, page_num)
    if data is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang gốc của tài liệu.")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-cache"})
