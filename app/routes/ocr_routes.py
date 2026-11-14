"""OCR Review, Page Preview & Vision OCR API Routes (Single Responsibility Module)."""

import asyncio
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from app.gpu_lock import GPUBusyError, IndexingCancelledError
from app.response_projection import project_public_response, sanitize_error_detail

logger = logging.getLogger("psychology_agent")

router = APIRouter()


class OCRReviewRequest(BaseModel):
    cache_key: str
    corrected_text: str
    reviewer_name: str
    notes: Optional[str] = ""


class OCRRegionReviewRequest(BaseModel):
    cache_key: str
    regions: List[Dict[str, Any]]
    reviewer_name: str


class OCRRunPageRequest(BaseModel):
    filename: str
    page_num: int = 1
    backend: Optional[str] = "ollama_vision"


def _candidate_cache_dirs(server_mod: Any) -> List[Path]:
    from app.vision_ocr import CACHE_DIR
    dirs: List[Path] = []
    seen: set[str] = set()
    candidates = [CACHE_DIR]
    indexer_obj = getattr(server_mod, "indexer", None)
    idx_cache = getattr(indexer_obj, "ocr_cache_dir", None) if indexer_obj is not None else None
    if idx_cache is not None:
        candidates.append(idx_cache)
    for item in candidates:
        if not isinstance(item, Path) or not item.exists():
            continue
        key = str(item.resolve())
        if key not in seen:
            seen.add(key)
            dirs.append(item)
    return dirs


def _resolve_cache_dir_for_key(server_mod: Any, cache_key: str) -> Optional[Path]:
    from app.vision_ocr import CACHE_DIR
    if isinstance(CACHE_DIR, Path) and (CACHE_DIR / f"{cache_key}.json").is_file():
        return CACHE_DIR
    indexer_obj = getattr(server_mod, "indexer", None)
    idx_cache = getattr(indexer_obj, "ocr_cache_dir", None) if indexer_obj is not None else None
    if isinstance(idx_cache, Path) and (idx_cache / f"{cache_key}.json").is_file():
        return idx_cache
    return CACHE_DIR if isinstance(CACHE_DIR, Path) else idx_cache


def _call_with_supported_kwargs(fn: Any, **kwargs: Any) -> Any:
    try:
        params = inspect.signature(fn).parameters
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        filtered = kwargs if has_var_kw else {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        filtered = kwargs
    return fn(**filtered)


@router.get("/api/ocr/reviews")
async def list_ocr_reviews(needs_review: Optional[bool] = None, source_relpath: Optional[str] = None):
    from app import server as _srv
    records: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    for cdir in _candidate_cache_dirs(_srv):
        for cp in sorted(cdir.glob("*.json")):
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                ck = str(data.get("cache_key") or cp.stem)
                if ck in seen_keys:
                    continue
                if needs_review is not None and data.get("needs_review") != needs_review:
                    continue
                if source_relpath and data.get("source_relpath") != source_relpath:
                    continue
                seen_keys.add(ck)
                records.append(data)
            except Exception as exc:
                logger.debug("Skipping unreadable OCR cache %s: %s", cp.name, exc)
                continue
    return project_public_response({"records": records, "total": len(records)})


@router.post("/api/ocr/review")
async def submit_ocr_review(req: OCRReviewRequest):
    from app import server as _srv
    from app.vision_ocr import VisionOCRManager
    try:
        cache_dir = _resolve_cache_dir_for_key(_srv, req.cache_key)
        updated = _call_with_supported_kwargs(
            VisionOCRManager.review_and_verify_transcript,
            cache_key=req.cache_key,
            corrected_text=req.corrected_text,
            reviewer_name=req.reviewer_name,
            notes=req.notes or "",
            cache_dir=cache_dir,
            src_dir=getattr(_srv, "SRC_DIR", None),
        )
        index_update: Dict[str, Any] = {"status": "not_indexed", "error": "Bản ghi thiếu vị trí nguồn"}
        try:
            filename = updated.get("source_relpath")
            page_num = updated.get("page_num")
            is_blank = bool(updated.get("is_blank"))
            text = "" if is_blank else (updated.get("text") or req.corrected_text)
            if filename and page_num is not None:
                upd_kwargs: Dict[str, Any] = {
                    "filename": filename,
                    "page_num": int(page_num),
                    "text": text,
                }
                if is_blank:
                    upd_kwargs["is_blank"] = True
                index_update = _call_with_supported_kwargs(_srv.indexer.update_page_chunks, **upd_kwargs)
        except Exception as e:
            logger.warning("Lỗi cập nhật page chunks sau khi review OCR: %s", e)
            err_msg = (
                "disk indexing failure"
                if "disk indexing failure" in str(e).lower()
                else sanitize_error_detail(str(e), fallback="Không thể cập nhật chỉ mục trang tài liệu.")
            )
            index_update = {"status": "failed", "error": err_msg}

        if index_update.get("status") == "success" and getattr(_srv, "src_watcher", None) is not None:
            try:
                _srv.src_watcher.sync_after_review(filename=updated.get("source_relpath"))
            except Exception as sync_exc:
                logger.warning("Watcher sync_after_review warning: %s", sync_exc)

        return project_public_response({
            "status": "success" if index_update.get("status") == "success" else "verified_not_indexed",
            "record": updated,
            "index_update": index_update,
        })
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi OCR trong bộ nhớ đệm.")
    except ValueError:
        raise HTTPException(status_code=400, detail="Dữ liệu hiệu đính OCR không hợp lệ.")
    except Exception:
        logger.exception("Error reviewing OCR transcript")
        raise HTTPException(status_code=500, detail="Lỗi xử lý hiệu đính OCR. Vui lòng thử lại sau.")


@router.post("/api/ocr/review-regions")
async def submit_ocr_region_review(req: OCRRegionReviewRequest):
    from app import server as _srv
    from app.vision_ocr import VisionOCRManager
    try:
        cache_dir = _resolve_cache_dir_for_key(_srv, req.cache_key)
        updated = _call_with_supported_kwargs(
            VisionOCRManager.review_regions,
            cache_key=req.cache_key,
            regions=req.regions,
            reviewer_name=req.reviewer_name,
            cache_dir=cache_dir,
            src_dir=getattr(_srv, "SRC_DIR", None),
        )
        filename = updated.get("source_relpath")
        page_num = updated.get("page_num")
        accepted_text = updated.get("text") or ""
        is_verified = bool(updated.get("is_verified")) and bool(accepted_text.strip())

        index_update: Dict[str, Any] = {"status": "hold", "chunks_updated": 0}
        if is_verified and filename and page_num is not None:
            try:
                index_update = _call_with_supported_kwargs(
                    _srv.indexer.update_page_chunks,
                    filename=filename,
                    page_num=int(page_num),
                    text=accepted_text,
                    is_partial=True,
                )
            except Exception as exc:
                logger.warning("Lỗi cập nhật chunk vùng OCR: %s", exc)
                index_update = {
                    "status": "failed",
                    "error": sanitize_error_detail(str(exc), fallback="Không thể cập nhật chỉ mục trang tài liệu."),
                }

        if getattr(_srv, "src_watcher", None) is not None:
            try:
                _srv.src_watcher.sync_after_review(filename=filename)
            except Exception as sync_exc:
                logger.warning("Watcher sync_after_review warning: %s", sync_exc)

        if not is_verified:
            status_val = "hold"
        elif index_update.get("status") == "success":
            status_val = "success"
        else:
            status_val = "verified_not_indexed"

        return project_public_response({
            "status": status_val,
            "record": updated,
            "index_update": index_update,
        })
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi OCR trong bộ nhớ đệm.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=sanitize_error_detail(str(exc), fallback="Dữ liệu duyệt vùng OCR không hợp lệ."))
    except Exception:
        logger.exception("Error reviewing OCR regions")
        raise HTTPException(status_code=500, detail="Lỗi xử lý duyệt vùng OCR. Vui lòng thử lại sau.")


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
        except Exception:
            logger.exception("Error rendering PDF page image")
            raise HTTPException(status_code=500, detail="Lỗi trích xuất ảnh trang PDF.")
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
        from app import config as _cfg, vision_ocr as _vocr
        use_idx_cache = (
            isinstance(_vocr.CACHE_DIR, Path)
            and _vocr.CACHE_DIR == (_cfg.DATA_DIR / "ocr_cache")
            and getattr(_srv, "DATA_DIR", _cfg.DATA_DIR) != _cfg.DATA_DIR
        )
        mgr_kwargs: Dict[str, Any] = {"backend": backend}
        if use_idx_cache and getattr(_srv, "indexer", None) is not None:
            mgr_kwargs["cache_dir"] = _srv.indexer.ocr_cache_dir
        mgr = VisionOCRManager(**mgr_kwargs)
        res = await asyncio.to_thread(
            mgr.ocr_image_bytes,
            image_bytes=img_bytes,
            source_hash=source_hash,
            source_relpath=req.filename,
            page_num=req.page_num,
        )
        if getattr(_srv, "src_watcher", None) is not None:
            try:
                _srv.src_watcher.scan_once()
            except Exception:
                pass
        return project_public_response({"status": "success", "record": res})
    except HTTPException:
        raise
    except GPUBusyError as e:
        raise HTTPException(status_code=503, detail=sanitize_error_detail(str(e)))
    except IndexingCancelledError as e:
        raise HTTPException(status_code=503, detail=sanitize_error_detail(str(e), fallback="Tác vụ OCR đã bị hủy."))
    except Exception as exc:
        from app.vision_ocr import classify_ocr_failure
        code = classify_ocr_failure(exc)
        logger.warning("OCR page failed (%s): %s", code, exc)
        if code == "ocr_timeout":
            raise HTTPException(status_code=504, detail="OCR cục bộ quá thời gian cho phép trên trang tài liệu.")
        if code == "ocr_unavailable":
            raise HTTPException(status_code=503, detail="Mô hình OCR cục bộ hiện chưa sẵn sàng.")
        if code in ("ocr_ambiguous", "ocr_truncated", "ocr_invalid_json"):
            raise HTTPException(
                status_code=422,
                detail=sanitize_error_detail(str(exc), fallback="Kết quả OCR chưa đạt chuẩn kiểm duyệt."),
            )
        raise HTTPException(status_code=500, detail="Lỗi chạy nhận diện OCR trên trang tài liệu.")


@router.get("/api/ocr/locator")
async def get_ocr_locator(filename: str = Query(...), page_num: int = Query(1)):
    from app.vision_ocr import get_source_locator
    return project_public_response(get_source_locator(source_relpath=filename, page_or_unit_num=page_num))
