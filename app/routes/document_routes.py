"""Document Lookup, Source Preview & Original Page Image API Routes (Single Responsibility Module)."""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.dialogue import DialogueBusyError

router = APIRouter()


@router.get("/api/documents")
async def list_documents():
    from app import server as _srv
    return {"documents": await run_in_threadpool(_srv.document_lookup.documents)}


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
    if document_id is not None and not await run_in_threadpool(_srv.document_lookup.document, document_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")

    def run_lookup():
        if method == "fts":
            return _srv.document_lookup.search(query, limit, document_id, method)
        with _srv.dialogue_gate.acquire():
            return _srv.document_lookup.search(query, limit, document_id, method)

    try:
        result = await run_in_threadpool(run_lookup)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    return {"query": query, **result}


@router.get("/api/documents/source-preview")
async def document_source_preview(
    filename: str = Query(..., max_length=1000),
    page_num: int = Query(..., ge=1),
):
    from app import server as _srv
    result = await run_in_threadpool(_srv.document_lookup.source_preview, filename, page_num)
    if not result:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang nguồn.")
    return result


@router.get("/api/documents/{document_id}/pdf")
async def open_document_pdf(document_id: int):
    from app import server as _srv
    doc = await run_in_threadpool(_srv.document_lookup.document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy PDF trong thư viện.")
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
    data = await run_in_threadpool(_srv.document_lookup.page_image, document_id, page_num)
    if data is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang gốc của tài liệu.")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-cache"})
