"""OCR Review, Page Preview & Vision OCR API Routes (Single Responsibility Module)."""

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.gpu_lock import GPUBusyError

logger = logging.getLogger("psychology_agent")

router = APIRouter()


class OCRReviewRequest(BaseModel):
    cache_key: str
    corrected_text: str
    reviewer_name: str
    notes: Optional[str] = ""


class OCRRunPageRequest(BaseModel):
    filename: str
    page_num: int = 1
    backend: Optional[str] = "ollama_vision"


@router.get("/api/ocr/reviews")
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


@router.post("/api/ocr/review")
async def submit_ocr_review(req: OCRReviewRequest):
    from app import server as _srv
    from app.vision_ocr import VisionOCRManager
    try:
        updated = VisionOCRManager.review_and_verify_transcript(
            cache_key=req.cache_key,
            corrected_text=req.corrected_text,
            reviewer_name=req.reviewer_name,
            notes=req.notes or "",
        )
        index_update = {"status": "not_indexed", "error": "Bản ghi thiếu vị trí nguồn"}
        try:
            filename = updated.get("source_relpath")
            page_num = updated.get("page_num")
            text = updated.get("text") or req.corrected_text
            if filename and page_num is not None:
                index_update = _srv.indexer.update_page_chunks(
                    filename=filename,
                    page_num=int(page_num),
                    text=text,
                )
        except Exception as e:
            logger.warning(f"Lỗi cập nhật page chunks sau khi review OCR: {e}")
            index_update = {"status": "failed", "error": str(e)}

        return {
            "status": "success" if index_update.get("status") == "success" else "verified_not_indexed",
            "record": updated,
            "index_update": index_update,
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Error reviewing OCR transcript")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/ocr/page-image")
async def get_ocr_page_image(filename: str = Query(...), page_num: int = Query(1)):
    """Render original page image as PNG for human OCR review preview."""
    import pymupdf
    from app import server as _srv

    file_path = _srv.document_lookup.file_path(filename)
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
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi trích xuất ảnh trang: {e}")
    elif ext in (".png", ".jpg", ".jpeg", ".webp"):
        media_type = "image/png" if ext == ".png" else ("image/webp" if ext == ".webp" else "image/jpeg")
        return Response(content=file_path.read_bytes(), media_type=media_type)
    elif ext in (".pptx", ".docx"):
        from app.slide_preview import render_office_page_png
        png_bytes = render_office_page_png(file_path, page_num=page_num)
        if not png_bytes:
            raise HTTPException(status_code=400, detail=f"Trang/slide {page_num} không hợp lệ cho tệp {filename}")
        return Response(content=png_bytes, media_type="image/png")
    else:
        raise HTTPException(status_code=400, detail=f"Không hỗ trợ xem ảnh cho định dạng {ext}")


@router.post("/api/ocr/run-page")
async def run_ocr_page(req: OCRRunPageRequest):
    import pymupdf
    from app import server as _srv
    from app.pdf_extractor import PDFExtractor
    from app.vision_ocr import BACKEND_OLLAMA_VISION, VisionOCRManager

    file_path = _srv.document_lookup.file_path(req.filename)
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


@router.get("/api/ocr/locator")
async def get_ocr_locator(filename: str = Query(...), page_num: int = Query(1)):
    from app.vision_ocr import get_source_locator
    return get_source_locator(source_relpath=filename, page_or_unit_num=page_num)
