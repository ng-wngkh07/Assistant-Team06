"""Regression checks for instant uploads and shared OCR timeout defaults:
1. VisionOCRManager timeout_seconds defaults to shared VISION_TIMEOUT_SECONDS (360),
   retaining explicit override.
2. /api/materials/upload and /api/materials/inspect-library treat persist_to_src
   omitted or null as False (instant use), preserving explicit True for library saving
   and False for instant mode.
3. Strict isolation preventing real serving storage, database, or model execution.
"""

import base64
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.server import app, study_material_manager
from app.config import VISION_TIMEOUT_SECONDS
from app.vision_ocr import VisionOCRManager
from app.pdf_extractor import PDFExtractionResult
from app.study_materials import StudyMaterialError
from app.gpu_lock import GPUBusyError


@pytest.fixture
def test_client():
    return TestClient(app)


# ============================================================================
# 1. VisionOCRManager timeout defaults and overrides
# ============================================================================

def test_vision_ocr_default_timeout_matches_shared_constant():
    """VisionOCRManager timeout_seconds must default to shared VISION_TIMEOUT_SECONDS (360)."""
    mgr = VisionOCRManager()
    assert mgr.timeout_seconds == VISION_TIMEOUT_SECONDS


def test_vision_ocr_explicit_timeout_override_retained():
    """Explicitly provided timeout_seconds must be preserved."""
    mgr = VisionOCRManager(timeout_seconds=45)
    assert mgr.timeout_seconds == 45

    mgr_float = VisionOCRManager(timeout_seconds=12.5)
    assert mgr_float.timeout_seconds == 12.5


def test_vision_ocr_none_timeout_defaults_to_shared():
    """If timeout_seconds=None is passed, it should fall back to shared VISION_TIMEOUT_SECONDS."""
    mgr = VisionOCRManager(timeout_seconds=None)
    assert mgr.timeout_seconds == VISION_TIMEOUT_SECONDS


# ============================================================================
# 2. /api/materials/upload persist_to_src defaults (omitted, null, false, true)
# ============================================================================

@pytest.fixture
def mock_upload_pipeline(monkeypatch):
    """Isolate document extraction, indexing, and generation without serving DB or model."""
    ingest_calls = []
    fake_pages = [{"page_num": 1, "text": "Sample text for cognitive psychology concept."}]
    fake_extracted = PDFExtractionResult(
        filename="test_doc.txt",
        filepath=Path("test_doc.txt"),
    )
    fake_extracted.total_pages = 1
    fake_extracted.total_chars = 45
    fake_extracted.extracted_pages = fake_pages

    monkeypatch.setattr(
        study_material_manager,
        "extract_document",
        lambda filename, file_bytes, allow_image_without_text=False: fake_extracted,
    )

    def fake_ingest(filename, file_bytes, extracted_result, **kwargs):
        ingest_calls.append({
            "filename": filename,
            "bytes_len": len(file_bytes),
            "extracted": extracted_result,
        })
        return {
            "saved_to_src": True,
            "src_path": f"src/{filename}",
            "doc_id": 999,
            "chunks_indexed": 1,
            "status": "indexed",
        }

    monkeypatch.setattr(study_material_manager, "ingest_to_src_and_index", fake_ingest)

    monkeypatch.setattr(
        study_material_manager,
        "store_extracted_document",
        lambda extracted, ingestion_info=None: {
            "id": "mat-123",
            "material_id": "mat-123",
            "filename": extracted.filename,
            "pages": extracted.extracted_pages,
            "saved_to_src": bool(ingestion_info.get("saved_to_src") if ingestion_info else False),
            "doc_id": ingestion_info.get("doc_id") if ingestion_info else None,
        },
    )

    monkeypatch.setattr(
        study_material_manager,
        "generate_materials_from_pages",
        lambda extracted, **kwargs: {
            "id": "mat-456",
            "material_id": "mat-456",
            "filename": extracted.filename,
            "quiz": [],
            "flashcards": [],
            "generation": {"quiz": "unavailable"},
        },
    )

    monkeypatch.setattr(
        study_material_manager,
        "cache_raw_file",
        lambda filename, file_bytes, material_id=None: None,
    )

    return ingest_calls


def test_upload_omitted_persist_to_src_does_not_invoke_ingestion(test_client, mock_upload_pipeline):
    """When persist_to_src is omitted, upload must NOT invoke ingest_to_src_and_index."""
    b64 = base64.b64encode(b"Content for omitted persist test").decode("ascii")
    payload = {
        "filename": "omitted_test.txt",
        "file_base64": b64,
        "generate_study_set": False,
    }
    resp = test_client.post("/api/materials/upload", json=payload)
    assert resp.status_code == 200, resp.text
    assert len(mock_upload_pipeline) == 0, "Omitted persist_to_src must not invoke ingestion/indexing"
    mat = resp.json()["material"]
    assert mat["saved_to_src"] is False
    assert mat["doc_id"] is None


def test_upload_null_persist_to_src_does_not_invoke_ingestion(test_client, mock_upload_pipeline):
    """When persist_to_src is null, upload must NOT invoke ingest_to_src_and_index."""
    b64 = base64.b64encode(b"Content for null persist test").decode("ascii")
    payload = {
        "filename": "null_test.txt",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": None,
    }
    resp = test_client.post("/api/materials/upload", json=payload)
    assert resp.status_code == 200, resp.text
    assert len(mock_upload_pipeline) == 0, "null persist_to_src must not invoke ingestion/indexing"
    mat = resp.json()["material"]
    assert mat["saved_to_src"] is False


def test_upload_explicit_false_persist_to_src_does_not_invoke_ingestion(test_client, mock_upload_pipeline):
    """When persist_to_src is explicitly False, upload must NOT invoke ingest_to_src_and_index."""
    b64 = base64.b64encode(b"Content for explicit false persist test").decode("ascii")
    payload = {
        "filename": "false_test.txt",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": False,
    }
    resp = test_client.post("/api/materials/upload", json=payload)
    assert resp.status_code == 200, resp.text
    assert len(mock_upload_pipeline) == 0, "False persist_to_src must not invoke ingestion/indexing"
    mat = resp.json()["material"]
    assert mat["saved_to_src"] is False


def test_upload_explicit_true_persist_to_src_retains_persistence(test_client, mock_upload_pipeline):
    """When persist_to_src is explicitly True, upload MUST invoke ingest_to_src_and_index."""
    b64 = base64.b64encode(b"Content for explicit true persist test").decode("ascii")
    payload = {
        "filename": "true_test.txt",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": True,
    }
    resp = test_client.post("/api/materials/upload", json=payload)
    assert resp.status_code == 200, resp.text
    assert len(mock_upload_pipeline) == 1, "Explicit True persist_to_src must invoke ingestion/indexing"
    assert mock_upload_pipeline[0]["filename"] == "true_test.txt"
    mat = resp.json()["material"]
    assert mat["saved_to_src"] is True
    assert mat["doc_id"] == 999


# ============================================================================
# 3. /api/materials/inspect-library persist_to_src defaults
# ============================================================================

@pytest.fixture
def mock_inspect_pipeline(monkeypatch):
    """Isolate inspect_library_document and record persist_to_src argument."""
    inspect_calls = []

    def fake_inspect(doc_id=None, filename=None, persist_to_src=False):
        inspect_calls.append({
            "doc_id": doc_id,
            "filename": filename,
            "persist_to_src": persist_to_src,
        })
        return {
            "id": "inspect-789",
            "material_id": "inspect-789",
            "filename": filename or "doc.pdf",
            "pages": [{"page_num": 1, "text": "Inspected page text"}],
            "saved_to_src": persist_to_src,
        }

    monkeypatch.setattr(study_material_manager, "inspect_library_document", fake_inspect)
    return inspect_calls


def test_inspect_library_omitted_persist_to_src_passes_false(test_client, mock_inspect_pipeline):
    """When persist_to_src is omitted, inspect-library must pass persist_to_src=False."""
    payload = {"filename": "library_doc.pdf"}
    resp = test_client.post("/api/materials/inspect-library", json=payload)
    assert resp.status_code == 200, resp.text
    assert len(mock_inspect_pipeline) == 1
    assert mock_inspect_pipeline[0]["persist_to_src"] is False


def test_inspect_library_null_persist_to_src_passes_false(test_client, mock_inspect_pipeline):
    """When persist_to_src is null, inspect-library must pass persist_to_src=False."""
    payload = {"filename": "library_doc.pdf", "persist_to_src": None}
    resp = test_client.post("/api/materials/inspect-library", json=payload)
    assert resp.status_code == 200, resp.text
    assert len(mock_inspect_pipeline) == 1
    assert mock_inspect_pipeline[0]["persist_to_src"] is False


def test_inspect_library_explicit_false_persist_to_src_passes_false(test_client, mock_inspect_pipeline):
    """When persist_to_src is explicitly False, inspect-library must pass persist_to_src=False."""
    payload = {"filename": "library_doc.pdf", "persist_to_src": False}
    resp = test_client.post("/api/materials/inspect-library", json=payload)
    assert resp.status_code == 200, resp.text
    assert len(mock_inspect_pipeline) == 1
    assert mock_inspect_pipeline[0]["persist_to_src"] is False


def test_inspect_library_explicit_true_persist_to_src_passes_true(test_client, mock_inspect_pipeline):
    """When persist_to_src is explicitly True, inspect-library must pass persist_to_src=True."""
    payload = {"filename": "library_doc.pdf", "persist_to_src": True}
    resp = test_client.post("/api/materials/inspect-library", json=payload)
    assert resp.status_code == 200, resp.text
    assert len(mock_inspect_pipeline) == 1
    assert mock_inspect_pipeline[0]["persist_to_src"] is True


# ============================================================================
# 4. Error and lock preservation
# ============================================================================

def test_upload_preserves_study_material_error_status(test_client, monkeypatch):
    """StudyMaterialError raises 422 and evicts raw file cache."""
    evictions = []
    monkeypatch.setattr(study_material_manager, "evict_raw_file", lambda f: evictions.append(f))

    def fail_extract(*args, **kwargs):
        raise StudyMaterialError("Tệp không đúng định dạng.")

    monkeypatch.setattr(study_material_manager, "extract_document", fail_extract)

    b64 = base64.b64encode(b"bad content").decode("ascii")
    resp = test_client.post("/api/materials/upload", json={
        "filename": "corrupt.txt",
        "file_base64": b64,
    })
    assert resp.status_code == 422
    assert "Tệp không đúng định dạng" in resp.json()["detail"]
    assert "corrupt.txt" in evictions


def test_upload_preserves_gpu_busy_lock_503(test_client, monkeypatch):
    """GPUBusyError raises 503 with Retry-After header."""
    evictions = []
    monkeypatch.setattr(study_material_manager, "evict_raw_file", lambda f: evictions.append(f))

    def busy_extract(*args, **kwargs):
        raise GPUBusyError("GPU đang bận.")

    monkeypatch.setattr(study_material_manager, "extract_document", busy_extract)

    b64 = base64.b64encode(b"busy content").decode("ascii")
    resp = test_client.post("/api/materials/upload", json={
        "filename": "busy.txt",
        "file_base64": b64,
    })
    assert resp.status_code == 503
    assert resp.headers.get("retry-after") == "2"
    assert "busy.txt" in evictions
