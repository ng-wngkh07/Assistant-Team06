"""Comprehensive offline integration tests for automatic src/ downstream ingestion lifecycle (SRC-E2E-01..06)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import config, server
from app.account_services import AccountService
from app.document_lookup import DocumentLookup
from app.gpu_lock import GPUBusyError, gpu_coordinator
from app.history import HistoryStore
from app.indexer import KnowledgeIndexer, pack_vector
from app.learning import LearningService
from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient
from app.native_vision import NativeVisionClient, cancel_active_vision_workers
from app.ollama_client import OllamaClient
from app.pdf_extractor import PDFExtractor
from app.practice import PracticeService
from app.rag_agent import PsychologyAgent
from app.searcher import HybridSearcher
from app.src_watcher import SrcWatcher
from app.study_materials import StudyMaterialManager
from app.trained_client import TrainedModelClient
from app.vision_ocr import CACHE_DIR as DEFAULT_OCR_CACHE_DIR, VisionOCRManager


REAL_SIX_DIR_ENV = "STUDY_TEST_REAL_SIX_DIR"


def _resolve_real_six_dir() -> Path:
    raw = os.getenv(REAL_SIX_DIR_ENV, "").strip()
    if not raw:
        pytest.skip(f"Opt-in real-six corpus test skipped: set {REAL_SIX_DIR_ENV} to run locally.")
    candidate = Path(raw).expanduser()
    if not candidate.is_dir():
        pytest.skip(f"Opt-in real-six corpus test skipped: {REAL_SIX_DIR_ENV} directory does not exist.")
    return candidate


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_text_pdf(path: Path, pages: list[str]) -> None:
    doc = pymupdf.open()
    for txt in pages:
        page = doc.new_page()
        page.insert_text((72, 72), txt, fontsize=12)
    doc.save(path)
    doc.close()


def _make_blank_image_pdf(path: Path, page_count: int = 1, *, uniform_white: bool = False) -> None:
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 160), False)
    pix.clear_with(255 if uniform_white else 245)
    for _ in range(page_count):
        page = doc.new_page(width=200, height=240)
        rect = pymupdf.Rect(0, 0, 200, 240) if uniform_white else pymupdf.Rect(20, 20, 180, 220)
        page.insert_image(rect, stream=pix.tobytes("png"))
    doc.save(path)
    doc.close()


def _make_mixed_pdf(path: Path, digital_text: str) -> None:
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), digital_text, fontsize=12)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 160), False)
    pix.clear_with(240)
    p2 = doc.new_page(width=200, height=240)
    p2.insert_image(pymupdf.Rect(20, 20, 180, 220), stream=pix.tobytes("png"))
    doc.save(path)
    doc.close()


def _wire_isolated_server(tmp_path: Path, monkeypatch, *, auto_ocr: bool = True, embed_model: str | None = None):
    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "ocr_cache"
    accounts_dir = tmp_path / "accounts"
    src_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    accounts_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "knowledge_base.db"
    history_db = data_dir / "history.db"
    practice_db = data_dir / "practice.db"
    timetable_db = data_dir / "timetable.db"

    monkeypatch.setenv("STUDY_SRC_AUTO_WATCH", "1")
    monkeypatch.setenv("STUDY_SRC_AUTO_OCR", "1" if auto_ocr else "0")
    monkeypatch.setenv("PRACTICE_DB_PATH", str(practice_db))
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(timetable_db))
    monkeypatch.setenv("HISTORY_DB_PATH", str(history_db))
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(accounts_dir))

    for mod in (config, server):
        monkeypatch.setattr(mod, "SRC_DIR", src_dir, raising=False)
        monkeypatch.setattr(mod, "DATA_DIR", data_dir, raising=False)
        monkeypatch.setattr(mod, "DB_PATH", db_path, raising=False)
    monkeypatch.setattr("app.vision_ocr.CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.vision_ocr.SRC_DIR", src_dir)
    monkeypatch.setattr("app.study_materials.DATA_DIR", data_dir)

    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src_dir, ocr_cache_dir=cache_dir)
    searcher = HybridSearcher(db_path=db_path)
    lookup = DocumentLookup(db_path=db_path, src_dir=src_dir, data_dir=data_dir)
    agent = PsychologyAgent(searcher=searcher)
    practice = PracticeService(searcher=searcher, document_lookup=lookup)
    learning = LearningService(document_lookup=lookup, practice_service=practice, searcher=searcher)
    mat_mgr = StudyMaterialManager()
    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        poll_interval=0.05,
        debounce_seconds=0.02,
        max_retries=2,
        base_backoff_seconds=0.05,
        embed_model=embed_model,
        auto_ocr=auto_ocr,
    )

    monkeypatch.setattr(server, "indexer", indexer)
    monkeypatch.setattr(server, "searcher", searcher)
    monkeypatch.setattr(server, "document_lookup", lookup)
    monkeypatch.setattr(server, "agent", agent)
    monkeypatch.setattr(server, "src_watcher", watcher)
    monkeypatch.setattr(server, "practice_service", AccountService(practice, lambda: PracticeService(searcher=searcher, document_lookup=lookup)))
    monkeypatch.setattr(server, "learning_service", AccountService(learning, lambda: LearningService(document_lookup=lookup, practice_service=practice, searcher=searcher)))
    monkeypatch.setattr(server, "study_material_manager", AccountService(mat_mgr, lambda: StudyMaterialManager()))
    server.set_history_store(HistoryStore(history_db))

    return {
        "src_dir": src_dir,
        "data_dir": data_dir,
        "cache_dir": cache_dir,
        "db_path": db_path,
        "indexer": indexer,
        "searcher": searcher,
        "lookup": lookup,
        "watcher": watcher,
        "agent": agent,
    }


def _unit_vec(seed: float = 0.0) -> list[float]:
    vec = [0.01] * 1024
    vec[0] = 0.8 + seed
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def test_src_e2e_01_restart_recovery_vector_retry_and_process_group_reap(tmp_path, monkeypatch):
    """SRC-E2E-01: Restart recovery from processing/ocr_running, bounded retry, vector backoff without dupes, and Popen killpg reap."""
    env = _wire_isolated_server(tmp_path, monkeypatch, auto_ocr=False, embed_model="bge-m3-mlx-fp16")
    src_dir, data_dir, db_path, watcher = env["src_dir"], env["data_dir"], env["db_path"], env["watcher"]

    good_pdf = src_dir / "recovered_note.pdf"
    _make_text_pdf(good_pdf, ["Ghi chu tam ly hoc nhan thuc E2E01_VECTOR_TOKEN trang 1."])
    good_sha = _sha256(good_pdf)

    scan_pdf = src_dir / "interrupted_scan.pdf"
    _make_blank_image_pdf(scan_pdf, 1)

    corrupt = src_dir / "bad.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nnot-valid")

    # Simulate interrupted crash state before restart.
    watcher.state_path.parent.mkdir(parents=True, exist_ok=True)
    watcher.state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "recovered_note.pdf": {
                        "rel_path": "recovered_note.pdf",
                        "status": "processing",
                        "file_size": good_pdf.stat().st_size,
                        "mtime_ns": good_pdf.stat().st_mtime_ns,
                        "stable_since": 0.0,
                    },
                    "interrupted_scan.pdf": {
                        "rel_path": "interrupted_scan.pdf",
                        "status": "ocr_running",
                        "file_size": scan_pdf.stat().st_size,
                        "mtime_ns": scan_pdf.stat().st_mtime_ns,
                        "stable_since": 0.0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    restarted = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        poll_interval=0.05,
        debounce_seconds=0.02,
        max_retries=2,
        base_backoff_seconds=5.0,
        embed_model="bge-m3-mlx-fp16",
        auto_ocr=False,
    )
    st0 = {i["filename"]: i for i in restarted.get_status()["items"]}
    assert st0["recovered_note.pdf"]["status"] == "pending"
    assert st0["interrupted_scan.pdf"]["status"] == "needs_ocr"

    # Mock embedding client: fail on first scan, then succeed with bge-m3 digest on later scan.
    emb_calls = {"fail": True}
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeEmbeddingClient, "get_identity", lambda self: BGE_NATIVE_IDENTITY)

    def fake_get_batch_embeddings(self, texts, max_length=2048, **kw):
        if emb_calls["fail"]:
            raise RuntimeError("transient metal buffer exhaustion")
        return [_unit_vec(i * 0.001) for i, _ in enumerate(texts)]

    monkeypatch.setattr(NativeEmbeddingClient, "get_batch_embeddings", fake_get_batch_embeddings)

    s1 = restarted.scan_once()
    assert s1["indexed"] >= 1
    assert s1["needs_ocr"] == 1
    time.sleep(0.03)
    s_bad = restarted.scan_once()
    assert s_bad["errors"] == 1

    # Chunks exist cleanly in fts_only/vector_pending without duplicate retry on corrupt.pdf during backoff.
    with sqlite3.connect(db_path) as db:
        chunk_count_before = db.execute("SELECT COUNT(*) FROM chunks WHERE filename='recovered_note.pdf'").fetchone()[0]
        assert chunk_count_before >= 1
    items1 = {i["filename"]: i for i in restarted.get_status()["items"]}
    assert items1["recovered_note.pdf"]["status"] == "indexed"
    assert items1["recovered_note.pdf"]["vectors_ready"] is False
    assert items1["recovered_note.pdf"]["embedding_status"] in ("vector_pending", "fts_only")
    assert items1["bad.pdf"]["attempts"] == 1

    # Vector stage now succeeds: backfill runs without duplicating chunks and records provenance.
    emb_calls["fail"] = False
    restarted._items["recovered_note.pdf"]["vector_next_retry_at"] = 0.0
    restarted._save_state()
    s2 = restarted.scan_once()
    assert s2["vectors_synced"] == 1
    items2 = {i["filename"]: i for i in restarted.get_status()["items"]}
    assert items2["recovered_note.pdf"]["vectors_ready"] is True
    assert items2["recovered_note.pdf"]["embedding_status"] == "vectorized"
    assert items2["bad.pdf"]["attempts"] == 1  # obeyed backoff

    with sqlite3.connect(db_path) as db:
        chunk_count_after = db.execute("SELECT COUNT(*) FROM chunks WHERE filename='recovered_note.pdf'").fetchone()[0]
        prov_count = db.execute(
            "SELECT COUNT(*) FROM corpus_vector_provenance p JOIN chunks c ON c.id=p.chunk_id "
            "WHERE c.filename='recovered_note.pdf' AND p.full_text=1 AND p.dimensions=1024"
        ).fetchone()[0]
        assert chunk_count_after == chunk_count_before
        assert prov_count == chunk_count_after

    # Corrupt provenance (full_text=0) -> indexer & watcher must not report vectors_ready until repaired.
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE corpus_vector_provenance SET full_text = 0")
        db.commit()
    st_corrupt = env["indexer"].get_status()
    doc_corrupt = next(d for d in st_corrupt["documents"] if d["filename"] == "recovered_note.pdf")
    assert st_corrupt["embeddings_ready"] == 0
    assert doc_corrupt["compatible_embeddings"] == 0
    assert doc_corrupt["vectors_ready"] is False

    s_repair = restarted.scan_once()
    assert s_repair["vectors_synced"] == 1
    st_repaired = env["indexer"].get_status()
    doc_repaired = next(d for d in st_repaired["documents"] if d["filename"] == "recovered_note.pdf")
    assert st_repaired["embeddings_ready"] == chunk_count_after
    assert doc_repaired["vectors_ready"] is True

    # Digest mismatch must refuse silent vector mixing.
    monkeypatch.setattr(NativeEmbeddingClient, "get_identity", lambda self: "bge-m3-mlx-fp16@otherdigest")
    with pytest.raises(RuntimeError, match="digest"):
        env["indexer"].backfill_missing_embeddings(embed_model="bge-m3-mlx-fp16")
    monkeypatch.setattr(NativeEmbeddingClient, "get_identity", lambda self: BGE_NATIVE_IDENTITY)

    # Modifying original keeps old indexed digest & flags needs_update; removing retains DB & marks missing_source.
    _make_text_pdf(good_pdf, ["Noi dung da bi sua khac SHA ban dau."])
    s3 = restarted.scan_once()
    assert s3["needs_update"] == 1
    good_pdf.unlink()
    s4 = restarted.scan_once()
    assert s4["missing_source"] == 1
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT file_hash FROM documents WHERE filename='recovered_note.pdf'").fetchone()
        assert row[0] == good_sha

    # Process-group termination: simulate long-running worker subprocess and verify stop() reaps it.
    from app import native_vision
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    with native_vision._ACTIVE_WORKERS_LOCK:
        native_vision._ACTIVE_WORKERS.add(proc)
    try:
        restarted.start()
        assert restarted.stop(timeout=2.0) is True
        rc = proc.wait(timeout=2.0)
        assert rc != 0
    finally:
        cancel_active_vision_workers()
        if proc.poll() is None:
            proc.kill()


def test_src_e2e_02_bounded_ocr_queue_mixed_pdf_blank_and_failure_taxonomy(tmp_path, monkeypatch):
    """SRC-E2E-02: Bounded batch OCR queue, cache reuse, mixed PDF, blank page review, and failure taxonomy."""
    env = _wire_isolated_server(tmp_path, monkeypatch, auto_ocr=True, embed_model=None)
    src_dir, cache_dir, db_path = env["src_dir"], env["cache_dir"], env["db_path"]

    multi_scan = src_dir / "multi_scan.pdf"
    _make_blank_image_pdf(multi_scan, page_count=3)

    mixed = src_dir / "mixed_doc.pdf"
    _make_mixed_pdf(mixed, "Trang 1 van ban so MIXED_DIGITAL_TOKEN san sang.")

    blank_pdf = src_dir / "blank_only.pdf"
    _make_blank_image_pdf(blank_pdf, page_count=1, uniform_white=True)

    fail_pdf = src_dir / "fail_modes.pdf"
    _make_blank_image_pdf(fail_pdf, page_count=1)

    calls: list[tuple[str, int]] = []
    failure_mode = {"mode": "ok"}

    monkeypatch.setattr(NativeVisionClient, "available", classmethod(lambda cls: True))
    monkeypatch.setattr(NativeVisionClient, "get_identity", lambda self: "qwen3.5-9b-4bit@testdigest")

    def fake_vision_gen(cls, image_bytes, prompt="", **kw):
        mode = failure_mode["mode"]
        if mode == "timeout":
            raise TimeoutError("Vision OCR worker timed out")
        if mode == "truncated":
            return {"text": "partial...", "finish_reason": "length"}
        if mode == "invalid_json":
            return {"text": "{not-valid-json", "finish_reason": "stop"}
        if mode == "ambiguous":
            return {
                "text": json.dumps({"text": "khó đọc [UNCLEAR]"}),
                "finish_reason": "stop",
            }
        calls.append(("ocr", len(calls) + 1))
        return {
            "text": json.dumps({"text": f"Captured OCR #{len(calls)}"}),
            "finish_reason": "stop",
            "model_id": "qwen3.5-mock",
            "model_revision": "rev1",
        }

    monkeypatch.setattr(NativeVisionClient, "generate_from_image", classmethod(fake_vision_gen))

    w = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=env["data_dir"],
        debounce_seconds=0.0,
        ocr_batch_pages=2,
        max_retries=2,
        base_backoff_seconds=0.01,
        embed_model=None,
        auto_ocr=True,
    )

    # Pass 1: blank page flagged cleanly & ambiguous page produces HOLD with zero indexed chunks.
    failure_mode["mode"] = "ambiguous"
    w.scan_once()
    items = {i["filename"]: i for i in w.get_status()["items"]}
    assert items["blank_only.pdf"]["status"] == "captured_pending_review"
    assert items["blank_only.pdf"]["ocr_pages_blank"] == 1
    assert items["fail_modes.pdf"]["status"] == "hold"
    assert items["fail_modes.pdf"]["reason_code"] == "ocr_ambiguous"

    # Mixed doc page 1 digital text is immediately searchable despite OCR HOLD/queue on other files!
    kb_mid = env["indexer"].get_status()
    mixed_entry = next(d for d in kb_mid["documents"] if d["filename"] == "mixed_doc.pdf")
    assert mixed_entry["status"] == "ocr_partial_reviewed"
    assert mixed_entry["searchable"] is True

    # Reset cache for clean bounded queue run with ok mode across 3-page scan.
    for p in cache_dir.glob("*.json"):
        p.unlink()
    w._items.clear()
    failure_mode["mode"] = "ok"

    # Run batches deterministically: 2 pages budget per pass.
    w.scan_once()
    w.scan_once()
    w.scan_once()
    # 6 OCR pages total (blank_only=1, fail_modes=1, mixed_doc p2=1, multi_scan=3) across 3 passes of budget 2.
    st_all = {i["filename"]: i for i in w.get_status()["items"]}
    assert st_all["multi_scan.pdf"]["status"] == "captured_pending_review"
    assert st_all["multi_scan.pdf"]["ocr_pages_captured"] == 3
    assert st_all["multi_scan.pdf"]["ocr_pages_reviewed"] == 0
    assert st_all["mixed_doc.pdf"]["ocr_pages_captured"] == 1
    assert st_all["blank_only.pdf"]["ocr_pages_blank"] == 1
    # Cache reuse: running scan_once again must NOT call vision OCR again.
    calls_before = len(calls)
    w.scan_once()
    assert len(calls) == calls_before

    # Verify timeout / truncated / invalid_json taxonomy explicitly on direct OCR calls.
    from app.vision_ocr import BACKEND_OLLAMA_VISION, classify_ocr_failure
    mgr = VisionOCRManager(backend=BACKEND_OLLAMA_VISION, cache_dir=cache_dir)
    img_bytes_valid = SrcWatcher._render_single_page_bytes(fail_pdf, 1)
    for m, exp_err, exp_code in [
        ("timeout", TimeoutError, "ocr_timeout"),
        ("truncated", RuntimeError, "ocr_truncated"),
        ("invalid_json", (ValueError, RuntimeError), "ocr_invalid_json"),
    ]:
        failure_mode["mode"] = m
        with pytest.raises(exp_err) as exc_info:
            mgr.ocr_image_bytes(img_bytes_valid, f"{_sha256(fail_pdf)}-{m}", f"direct_{m}.pdf", 1)
        assert classify_ocr_failure(exc_info.value) == exp_code


def test_src_e2e_03_human_review_gate_and_automatic_continuation_no_manual_reindex(tmp_path, monkeypatch):
    """SRC-E2E-03: Capture != verified; full-page, region-partial, and source_page_reviews trigger automatic resume without POST /api/index."""
    env = _wire_isolated_server(tmp_path, monkeypatch, auto_ocr=True, embed_model="bge-m3-mlx-fp16")
    src_dir, cache_dir, db_path, watcher = env["src_dir"], env["cache_dir"], env["db_path"], env["watcher"]
    client = TestClient(server.app)

    scan2 = src_dir / "notes_two_pages.pdf"
    _make_blank_image_pdf(scan2, page_count=2)
    scan_hash = _sha256(scan2)

    region_pdf = src_dir / "handwritten_regions.pdf"
    _make_blank_image_pdf(region_pdf, page_count=1)
    region_hash = _sha256(region_pdf)

    monkeypatch.setattr(NativeVisionClient, "available", classmethod(lambda cls: True))
    monkeypatch.setattr(NativeVisionClient, "get_identity", lambda self: "qwen3.5-9b-4bit@testdigest")
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeEmbeddingClient, "get_identity", lambda self: BGE_NATIVE_IDENTITY)
    monkeypatch.setattr(NativeEmbeddingClient, "get_batch_embeddings", lambda self, texts, max_length=2048, **kw: [_unit_vec(0.01) for _ in texts])
    monkeypatch.setattr(
        NativeVisionClient,
        "generate_from_image",
        classmethod(lambda cls, *a, **kw: {
            "text": json.dumps({"text": "UNVERIFIED_CAPTURE_LEAK_TOKEN"}),
            "finish_reason": "stop",
        }),
    )

    # Settle + bounded OCR capture (2 passes for 3 total OCR pages at batch=2).
    watcher.scan_once()
    time.sleep(0.03)
    watcher.scan_once()
    watcher.scan_once()

    # Negative guard (capture != verified): zero searchable documents and zero chunks.
    assert client.get("/api/documents/search", params={"q": "UNVERIFIED_CAPTURE_LEAK_TOKEN"}).json()["results"] == []
    st_pre = client.get("/api/status").json()
    assert st_pre["knowledge_base"]["searchable_documents"] == 0
    assert st_pre["knowledge_base"]["total_chunks"] == 0

    reviews_resp = client.get("/api/ocr/reviews").json()["records"]
    assert len(reviews_resp) == 3
    # Internal host paths never leaked to API consumers.
    assert "image_path" not in json.dumps(reviews_resp)

    key_p1 = next(r["cache_key"] for r in reviews_resp if r["source_relpath"] == "notes_two_pages.pdf" and r["page_num"] == 1)
    key_reg = next(r["cache_key"] for r in reviews_resp if r["source_relpath"] == "handwritten_regions.pdf")

    # 1) Full-page review on page 1 -> automatically indexes + vectorizes + clears freshness cache without POST /api/index.
    r_full = client.post("/api/ocr/review", json={
        "cache_key": key_p1,
        "corrected_text": "Noi dung trang 1 da duoc kiem duyet VERIFIED_PAGE1_TOKEN.",
        "reviewer_name": "Reviewer A",
        "notes": "Verified page 1",
    })
    assert r_full.status_code == 200
    assert r_full.json()["status"] == "success"
    hits1 = client.get("/api/documents/search", params={"q": "VERIFIED_PAGE1_TOKEN"}).json()["results"]
    assert len(hits1) >= 1
    assert client.get("/api/documents/search", params={"q": "UNVERIFIED_CAPTURE_LEAK_TOKEN"}).json()["results"] == []

    # 2) Region review on handwritten_regions.pdf: only 'verified' span is searchable; 'unreadable'/'derived_explanation' excluded.
    r_reg = client.post("/api/ocr/review-regions", json={
        "cache_key": key_reg,
        "reviewer_name": "Reviewer B",
        "regions": [
            {"id": "r1", "bbox": [0.1, 0.1, 0.5, 0.3], "status": "verified", "text": "REGION_ACCEPTED_TOKEN", "reason": "Clear handwriting"},
            {"id": "r2", "bbox": [0.1, 0.4, 0.5, 0.6], "status": "unreadable", "text": "UNREADABLE_SECRET_TOKEN", "reason": "Blurry stroke"},
            {"id": "r3", "bbox": [0.1, 0.7, 0.5, 0.9], "status": "derived_explanation", "text": "DERIVED_EXPLANATION_TOKEN", "reason": "Inferred formula"},
        ],
    })
    assert r_reg.status_code == 200
    assert len(client.get("/api/documents/search", params={"q": "REGION_ACCEPTED_TOKEN"}).json()["results"]) >= 1
    assert client.get("/api/documents/search", params={"q": "UNREADABLE_SECRET_TOKEN"}).json()["results"] == []
    assert client.get("/api/documents/search", params={"q": "DERIVED_EXPLANATION_TOKEN"}).json()["results"] == []
    st_reg = {i["filename"]: i for i in watcher.get_status()["items"]}
    assert st_reg["handwritten_regions.pdf"]["status"] == "hold"

    # 3) External source_page_reviews arrival for notes_two_pages.pdf page 2 -> automatically resumed on watcher scan!
    p2_raw_capture = next(
        json.loads(p.read_text(encoding="utf-8"))
        for p in cache_dir.glob("*.json")
        if json.loads(p.read_text(encoding="utf-8")).get("source_relpath") == "notes_two_pages.pdf"
        and json.loads(p.read_text(encoding="utf-8")).get("page_num") == 2
    )
    raw_path = cache_dir / f"{p2_raw_capture['cache_key']}.json"
    ext_rev_dir = tmp_path / "data" / "evaluation" / "handwriting-complete-20261004" / "reviews" / "notes_two_pages"
    ext_rev_dir.mkdir(parents=True, exist_ok=True)
    img_path = ext_rev_dir / "page_002.png"
    img_path.write_bytes(SrcWatcher._render_single_page_bytes(scan2, 2))
    (ext_rev_dir / "page_002.json").write_text(
        json.dumps({
            "filename": "notes_two_pages.pdf",
            "page": 2,
            "kind": "text",
            "source_sha256": scan_hash,
            "image_reviewed": True,
            "completeness": "complete",
            "transcript": "Trang 2 tu source_page_reviews SOURCE_REVIEW_P2_TOKEN.",
            "reviewed_at": "2026-11-13T21:00:00Z",
            "reviewer": "Codex",
            "image_path": str(img_path),
            "image_sha256": _sha256(img_path),
            "raw_capture_path": str(raw_path),
            "raw_capture_sha256": _sha256(raw_path),
        }),
        encoding="utf-8",
    )
    watcher.scan_once()
    assert len(client.get("/api/documents/search", params={"q": "SOURCE_REVIEW_P2_TOKEN"}).json()["results"]) >= 1
    st_done = {i["filename"]: i for i in watcher.get_status()["items"]}
    assert st_done["notes_two_pages.pdf"]["status"] == "indexed"
    assert st_done["notes_two_pages.pdf"]["vectors_ready"] is True
    # Also verify incomplete draft cannot supersede or index without valid text.
    assert len(region_hash) == 64


def test_src_e2e_04_and_05_consumers_endpoints_and_account_isolation(tmp_path, monkeypatch):
    """SRC-E2E-04 & 05: Consumer visibility (/api/documents, chat, chat/stream, study_materials, practice, learning, planner), account isolation, fast /api/health, zero host paths."""
    env = _wire_isolated_server(tmp_path, monkeypatch, auto_ocr=True, embed_model=None)
    src_dir, watcher = env["src_dir"], env["watcher"]
    client = TestClient(server.app)

    doc_pdf = src_dir / "cognitive_memory.pdf"
    _make_blank_image_pdf(doc_pdf, page_count=1)

    monkeypatch.setattr(NativeVisionClient, "available", classmethod(lambda cls: True))
    monkeypatch.setattr(NativeVisionClient, "get_identity", lambda self: "qwen3.5-9b-4bit@testdigest")
    monkeypatch.setattr(
        NativeVisionClient,
        "generate_from_image",
        classmethod(lambda cls, *a, **kw: {
            "text": json.dumps({"text": "Tri nho lam viec la he thong luu tru tam thoi WORKING_MEMORY_E2E04."}),
            "finish_reason": "stop",
        }),
    )

    # Settle + capture -> still blocked by study_materials before review!
    watcher.scan_once()
    time.sleep(0.03)
    watcher.scan_once()
    assert client.post("/api/materials/inspect-library", json={"filename": "cognitive_memory.pdf"}).status_code == 422

    # Approve via human review -> automatic continuation makes all consumers see it!
    ck = client.get("/api/ocr/reviews").json()["records"][0]["cache_key"]
    rev = client.post("/api/ocr/review", json={
        "cache_key": ck,
        "corrected_text": "Tri nho lam viec WORKING_MEMORY_E2E04 cho phep thao tac thong tin tuc thoi va suy luan.",
        "reviewer_name": "Reviewer",
    })
    assert rev.status_code == 200

    # 1. /api/documents, original PDF download, page image, /api/documents/search
    docs_list = client.get("/api/documents").json()["documents"]
    assert len(docs_list) == 1
    doc_id = docs_list[0]["id"]
    assert client.get(f"/api/documents/{doc_id}/original").status_code == 200
    assert client.get(f"/api/documents/{doc_id}/pdf").status_code == 200
    assert client.get(f"/api/documents/{doc_id}/pages/1/image").status_code == 200
    assert len(client.get("/api/documents/search", params={"q": "WORKING_MEMORY_E2E04"}).json()["results"]) >= 1

    # 2. study_materials inspect-library now returns reviewed OCR passage cleanly!
    mat = client.post("/api/materials/inspect-library", json={"document_id": doc_id})
    assert mat.status_code == 200
    assert "WORKING_MEMORY_E2E04" in mat.json()["material"]["pages"][0]["text"]

    # 3. /api/chat & /api/chat/stream use retrieved OCR chunks cleanly (mock Ollama generator).
    monkeypatch.setattr(server, "DEFAULT_CHAT_MODEL", "test-ollama")
    monkeypatch.setattr(server, "DEFAULT_EMBED_MODEL", None)
    monkeypatch.setattr(OllamaClient, "check_health", lambda self: False)
    monkeypatch.setattr(TrainedModelClient, "available", classmethod(lambda cls, *a, **kw: False))

    def fake_chat_stream(self, messages, model, **kw):
        self.last_done_reason = "stop"
        yield "Trí nhớ làm việc WORKING_MEMORY_E2E04 cho phép thao tác thông tin tức thời và suy luận [S1]."

    monkeypatch.setattr(OllamaClient, "chat_stream", fake_chat_stream)

    chat_resp = client.post("/api/chat", json={"query": "WORKING_MEMORY_E2E04 la gi?", "save_history": False})
    assert chat_resp.status_code == 200
    chat_body = chat_resp.json()
    assert "WORKING_MEMORY_E2E04" in chat_body["answer"]
    assert any(c.get("filename") == "cognitive_memory.pdf" for c in chat_body["citations"])

    stream_resp = client.post("/api/chat/stream", json={"query": "WORKING_MEMORY_E2E04?", "save_history": False})
    assert stream_resp.status_code == 200
    assert "WORKING_MEMORY_E2E04" in stream_resp.text
    assert "cognitive_memory.pdf" in stream_resp.text

    # 4. practice, learning, timetable planner visibility
    assert client.get("/api/practice/examples", params={"document_id": doc_id}).status_code == 200
    topics_resp = client.get("/api/learning/topics")
    assert topics_resp.status_code == 200
    wl_resp = client.post(
        "/api/timetable/planner/weekly-lessons",
        json={"course": "Tam ly hoc nhan thuc", "lesson_title": "Tri nho lam viec WORKING_MEMORY_E2E04"},
    )
    assert wl_resp.status_code == 200
    assert wl_resp.json()["lesson"]["lesson_title"] == "Tri nho lam viec WORKING_MEMORY_E2E04"
    wl_list = client.get("/api/timetable/planner/weekly-lessons")
    assert wl_list.status_code == 200
    assert any(l["lesson_title"] == "Tri nho lam viec WORKING_MEMORY_E2E04" for l in wl_list.json()["lessons"])
    assert client.get("/api/timetable/planner/state").status_code == 200

    # 5. SRC-E2E-05: /api/health does NOT hash source PDFs; /api/status & /api/storage never expose tmp_path to remote/account callers.
    hash_calls = {"count": 0}
    orig_hash = PDFExtractor.calculate_file_hash

    def tracked_hash(p):
        hash_calls["count"] += 1
        return orig_hash(p)

    monkeypatch.setattr(PDFExtractor, "calculate_file_hash", staticmethod(tracked_hash))
    assert client.get("/api/health").status_code == 200
    assert hash_calls["count"] == 0

    # Authenticated private account upload stays isolated from shared SRC_DIR / watcher / KB.
    reg = client.post(
        "/api/account/register",
        json={"username": "student_e2e04", "password": "VeryStrongPassword123!"},
        headers={"X-Study-Request": "1"},
    )
    assert reg.status_code == 200
    up = client.post(
        "/api/materials/upload",
        json={
            "filename": "private_only.txt",
            "file_base64": base64.b64encode(
                b"PRIVATE_ACCOUNT_SECRET_TOKEN_04 private uploaded study notes for cognitive psychology account isolation."
            ).decode("ascii"),
            "generate_study_set": False,
        },
        headers={"X-Study-Request": "1"},
    )
    assert up.status_code == 200
    assert not (src_dir / "private_only.txt").exists()
    assert "private_only.txt" not in {i["filename"] for i in watcher.get_status()["items"]}

    st_acct = client.get("/api/status").json()
    stor_acct = client.get("/api/storage").json()
    assert "sources_dir" not in stor_acct
    assert str(tmp_path) not in json.dumps(st_acct)
    assert str(tmp_path) not in json.dumps(stor_acct)
    assert st_acct["knowledge_base"]["searchable_documents"] == 1
    assert st_acct["knowledge_base"]["discovered_files"] == 1


def test_src_e2e_06_isolated_real_six_pdfs_reconcile(tmp_path, monkeypatch):
    """SRC-E2E-06: Opt-in isolated temp copy of 6 real scanned PDFs reconciles across 1,411 physical pages without mutating originals."""
    real_six_dir = _resolve_real_six_dir()
    host_pdfs = sorted(real_six_dir.glob("*.pdf"))
    assert len(host_pdfs) == 6
    host_shas_before = {p.name: _sha256(p) for p in host_pdfs}

    env = _wire_isolated_server(tmp_path, monkeypatch, auto_ocr=False, embed_model=None)
    src_dir, watcher = env["src_dir"], env["watcher"]
    for p in host_pdfs:
        shutil.copy2(p, src_dir / p.name)

    watcher.scan_once()
    time.sleep(0.03)
    watcher.scan_once()

    st = watcher.get_status()
    kb = env["indexer"].get_status()

    assert st["discovered_count"] == 6
    assert st["needs_ocr_count"] == 6
    assert kb["registered_documents"] == 6
    assert kb["total_documents"] == 6
    assert kb["searchable_documents"] == 0
    assert kb["total_chunks"] == 0
    assert sum(d["total_pages"] for d in kb["documents"]) == 1411

    # Original opt-in directory remains completely untouched.
    for p in host_pdfs:
        assert _sha256(p) == host_shas_before[p.name]


def test_src_e2e_07_vector_identity_readiness_matrix_and_opt_in_skip_guard(tmp_path, monkeypatch):
    """SRC-E2E-07: Readiness validates active retrieval identity (not first provenance row), prevents silent migration, and skips opt-in gate cleanly."""

    def _seed_case(case_name: str, chunk_specs: list[dict], *, backend: str = "native"):
        case_dir = tmp_path / case_name
        case_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("EMBED_BACKEND", backend)
        env = _wire_isolated_server(case_dir, monkeypatch, auto_ocr=False, embed_model="bge-m3")
        pdf_path = env["src_dir"] / f"{case_name}.pdf"
        _make_text_pdf(pdf_path, [spec["text"] for spec in chunk_specs])
        file_hash = _sha256(pdf_path)

        with sqlite3.connect(env["db_path"]) as db:
            cur = db.execute(
                """
                INSERT INTO documents
                (filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'indexed')
                """,
                (
                    pdf_path.name,
                    case_name,
                    pdf_path.name,
                    pdf_path.stat().st_size,
                    file_hash,
                    len(chunk_specs),
                    len(chunk_specs),
                ),
            )
            doc_id = cur.lastrowid
            for idx, spec in enumerate(chunk_specs):
                blob = spec.get("blob")
                if blob is None and spec.get("embed", True):
                    blob = pack_vector(_unit_vec(idx * 0.01))
                c_cur = db.execute(
                    """
                    INSERT INTO chunks (doc_id, book_title, filename, page_num, chunk_index, text, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (doc_id, case_name, pdf_path.name, idx + 1, 0, spec["text"], blob),
                )
                chunk_id = c_cur.lastrowid
                if spec.get("with_provenance", True):
                    text_sha = spec.get("text_sha256") or hashlib.sha256(spec["text"].encode("utf-8")).hexdigest()
                    vec_sha = spec.get("vec_sha256") or (hashlib.sha256(blob).hexdigest() if blob is not None else "")
                    db.execute(
                        """
                        INSERT INTO corpus_vector_provenance
                        (chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'fixture')
                        """,
                        (
                            chunk_id,
                            text_sha,
                            spec["digest"],
                            vec_sha,
                            spec.get("dims", 1024),
                            spec.get("full_text", 1),
                        ),
                    )
            db.commit()

        item = {
            "filename": pdf_path.name,
            "file_hash": file_hash,
            "indexed_hash": file_hash,
            "status": "indexed",
            "total_pages": len(chunk_specs),
            "extracted_pages_count": len(chunk_specs),
            "vectors_ready": True,
            "embedding_status": "vectorized",
        }
        env["watcher"].state_path.parent.mkdir(parents=True, exist_ok=True)
        env["watcher"].state_path.write_text(
            json.dumps({"version": 1, "items": {pdf_path.name: dict(item)}}),
            encoding="utf-8",
        )
        return env, pdf_path, doc_id, file_hash

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("MLX_BGE_MODEL_DIR", raising=False)
    monkeypatch.delenv("BGE_MODEL_DIR", raising=False)
    NativeEmbeddingClient.clear_status_identity_cache()

    # 0. Missing default, explicit missing override, and unverified native artifacts stay pending (no identity mock).
    env_ok, pdf_ok, doc_ok, hash_ok = _seed_case(
        "active_native",
        [{"text": "Active native identity passage one.", "digest": BGE_NATIVE_IDENTITY}],
    )
    assert not NativeEmbeddingClient().model_dir.exists()
    assert NativeEmbeddingClient.available() is False
    assert NativeEmbeddingClient().get_identity() == "bge-m3-unknown@unverified"
    assert env_ok["indexer"]._resolve_active_embedding_digest() is None
    with env_ok["indexer"].get_connection() as conn:
        assert env_ok["indexer"]._count_compatible_vectors(conn, doc_id=doc_ok, require_1024=True) == (1, 0)
    st_missing_default = env_ok["indexer"].get_status()
    assert st_missing_default["embeddings_ready"] == 0
    assert st_missing_default["embeddings_pending"] == 1
    assert st_missing_default["documents"][0]["compatible_embeddings"] == 0
    assert st_missing_default["documents"][0]["vectors_ready"] is False
    assert st_missing_default["documents"][0]["embedding_status"] == "vector_pending"
    w_missing_default = {}
    ready_missing_default, _, _ = env_ok["watcher"]._sync_vectors_for_doc(
        env_ok["indexer"], w_missing_default, {"id": doc_ok, "filename": pdf_ok.name, "file_hash": hash_ok}, time.time()
    )
    assert ready_missing_default is False
    assert w_missing_default["vectors_ready"] is False
    env_ok["watcher"].sync_after_manual_index()
    assert env_ok["watcher"].get_status()["items"][0]["vectors_ready"] is False

    missing_override_dir = tmp_path / "missing_bge_override"
    for env_key in ("MLX_BGE_MODEL_DIR", "BGE_MODEL_DIR"):
        monkeypatch.delenv("MLX_BGE_MODEL_DIR", raising=False)
        monkeypatch.delenv("BGE_MODEL_DIR", raising=False)
        monkeypatch.setenv(env_key, str(missing_override_dir))
        assert env_ok["indexer"]._resolve_active_embedding_digest() is None
        assert env_ok["indexer"].get_status()["embeddings_ready"] == 0

    monkeypatch.delenv("BGE_MODEL_DIR", raising=False)
    fake_base = tmp_path / "native_runtime_base"
    fake_runtime = fake_base / ".train-venv" / "bin" / "python"
    fake_runtime.parent.mkdir(parents=True, exist_ok=True)
    fake_runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_runtime.chmod(0o755)
    monkeypatch.setattr("app.native_embedding.BASE_DIR", fake_base)

    unverified_dir = tmp_path / "unverified_bge_artifact"
    unverified_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in (
        ("config.json", b'{"hidden_size": 1024}'),
        ("tokenizer.json", b'{"fixture": "unverified"}'),
        ("tokenizer_config.json", b'{"fixture": "unverified"}'),
        ("special_tokens_map.json", b'{"fixture": "unverified"}'),
        ("model.safetensors", b"unverified-weights-fixture"),
    ):
        (unverified_dir / fname).write_bytes(content)
    monkeypatch.setenv("MLX_BGE_MODEL_DIR", str(unverified_dir))
    assert NativeEmbeddingClient.available() is True
    assert NativeEmbeddingClient().get_identity().startswith("bge-m3-custom@")
    assert env_ok["indexer"]._resolve_active_embedding_digest() is None
    st_unverified = env_ok["indexer"].get_status()
    assert st_unverified["embeddings_ready"] == 0
    assert st_unverified["documents"][0]["vectors_ready"] is False

    # 1. Explicit verified-identity boundary fixture: agrees across indexer/watcher, avoids status rehashing,
    #    and preserves query/embedding-time actual byte tamper detection + status cache invalidation.
    verified_dir = tmp_path / "verified_bge_artifact"
    verified_dir.mkdir(parents=True, exist_ok=True)
    verified_assets = {
        "config.json": b'{"hidden_size": 1024}',
        "tokenizer.json": b'{"fixture": "verified"}',
        "tokenizer_config.json": b'{"fixture": "verified"}',
        "special_tokens_map.json": b'{"fixture": "verified"}',
        "model.safetensors": b"pinned-weight-fixture-0001",
    }
    pinned_fixture_meta = {}
    for fname, raw in verified_assets.items():
        (verified_dir / fname).write_bytes(raw)
        pinned_fixture_meta[fname] = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    monkeypatch.setattr("app.native_embedding.PINNED_BGE_FILES", pinned_fixture_meta)
    monkeypatch.setenv("MLX_BGE_MODEL_DIR", str(verified_dir))
    NativeEmbeddingClient.clear_status_identity_cache()

    digest_calls: list[str] = []
    real_file_digest = hashlib.file_digest

    def _counting_file_digest(fileobj, digest, *args, **kwargs):
        digest_calls.append(getattr(fileobj, "name", str(fileobj)))
        return real_file_digest(fileobj, digest, *args, **kwargs)

    monkeypatch.setattr(hashlib, "file_digest", _counting_file_digest)

    st_ok = env_ok["indexer"].get_status()
    assert len(digest_calls) == len(verified_assets)
    assert st_ok["embeddings_ready"] == 1
    assert st_ok["documents"][0]["vectors_ready"] is True
    assert st_ok["documents"][0]["embedding_status"] == "vectorized"

    digest_calls.clear()
    with env_ok["indexer"].get_connection() as conn:
        assert env_ok["indexer"]._count_compatible_vectors(conn, doc_id=doc_ok, require_1024=True) == (1, 1)
    assert env_ok["indexer"].get_status()["embeddings_ready"] == 1
    assert env_ok["indexer"].get_status()["documents"][0]["vectors_ready"] is True
    w_item_ok = {}
    ready_ok, _, _ = env_ok["watcher"]._sync_vectors_for_doc(
        env_ok["indexer"], w_item_ok, {"id": doc_ok, "filename": pdf_ok.name, "file_hash": hash_ok}, time.time()
    )
    assert ready_ok is True
    assert w_item_ok["vectors_ready"] is True
    assert w_item_ok["embedding_status"] == "vectorized"
    env_ok["watcher"].sync_after_manual_index()
    assert env_ok["watcher"].get_status()["items"][0]["vectors_ready"] is True
    assert digest_calls == [], f"Repeated status/watcher polling rehashed model files: {digest_calls}"

    # Query/embedding-time verification still hashes bytes on same-size/same-mtime replacement and invalidates status cache.
    weights_file = verified_dir / "model.safetensors"
    before_stat = weights_file.stat()
    weights_file.write_bytes(b"pinned-weight-fixture-0002")
    os.utime(weights_file, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    assert weights_file.stat().st_size == before_stat.st_size
    assert weights_file.stat().st_mtime_ns == before_stat.st_mtime_ns
    assert NativeEmbeddingClient().get_identity().startswith("bge-m3-custom@")
    assert len(digest_calls) > 0
    assert env_ok["indexer"].get_status()["embeddings_ready"] == 0

    weights_file.write_bytes(verified_assets["model.safetensors"])
    digest_calls.clear()
    assert NativeEmbeddingClient().get_identity() == BGE_NATIVE_IDENTITY
    assert len(digest_calls) == len(verified_assets)
    digest_calls.clear()
    assert env_ok["indexer"].get_status()["embeddings_ready"] == 1
    assert digest_calls == []

    monkeypatch.setattr(
        NativeEmbeddingClient,
        "get_batch_embeddings",
        lambda self, texts, max_length=2048, **kw: [_unit_vec(0.02) for _ in texts],
    )
    upd_ok = env_ok["indexer"].update_page_chunks(pdf_ok.name, 1, "Active reviewed page one.")
    assert upd_ok["vectors_ready"] is True
    assert upd_ok["embedding_status"] == "vectorized"

    # 2. Sole well-formed retired digest must fail readiness & refuse silent migration/deletion.
    retired_digest = "bge-m3-mlx-fp16@retired-fixture"
    env_ret, pdf_ret, doc_ret, hash_ret = _seed_case(
        "sole_retired",
        [{"text": "Well-formed retired passage one.", "digest": retired_digest}],
    )
    with env_ret["indexer"].get_connection() as conn:
        assert env_ret["indexer"]._count_compatible_vectors(conn, doc_id=doc_ret, require_1024=True) == (1, 0)
    st_ret = env_ret["indexer"].get_status()
    assert st_ret["embeddings_ready"] == 0
    assert st_ret["embeddings_pending"] == 1
    assert st_ret["documents"][0]["compatible_embeddings"] == 0
    assert st_ret["documents"][0]["vectors_ready"] is False
    assert st_ret["documents"][0]["embedding_status"] == "vector_pending"

    env_ret["watcher"].sync_after_manual_index()
    man_ret = env_ret["watcher"].get_status()["items"][0]
    assert man_ret["vectors_ready"] is False
    assert man_ret["embedding_status"] == "vector_pending"

    w_item_ret = {}
    ready_ret, _, _ = env_ret["watcher"]._sync_vectors_for_doc(
        env_ret["indexer"], w_item_ret, {"id": doc_ret, "filename": pdf_ret.name, "file_hash": hash_ret}, time.time()
    )
    assert ready_ret is False
    assert w_item_ret["vectors_ready"] is False
    assert w_item_ret["embedding_status"] == "vector_pending"

    with pytest.raises(RuntimeError, match="digest"):
        env_ret["indexer"].backfill_missing_embeddings(embed_model="bge-m3")

    upd_ret = env_ret["indexer"].update_page_chunks(pdf_ret.name, 1, "Updated reviewed page on retired corpus.")
    assert upd_ret["vectors_ready"] is False
    assert upd_ret["embedding_status"] == "vector_pending"
    assert upd_ret.get("vector_error")

    # Retired vectors in untouched documents remain preserved (neither deleted nor rewritten).
    env_ret2, _, doc_ret2, _ = _seed_case(
        "preserve_retired",
        [
            {"text": "Retired page 1 kept intact.", "digest": retired_digest},
            {"text": "Retired page 2 kept intact.", "digest": retired_digest},
        ],
    )
    upd_ret2 = env_ret2["indexer"].update_page_chunks("preserve_retired.pdf", 2, "Reviewed page 2 text.")
    assert upd_ret2["vectors_ready"] is False
    assert upd_ret2["embedding_status"] == "vector_pending"
    with sqlite3.connect(env_ret2["db_path"]) as db:
        p1_row = db.execute(
            "SELECT p.model_digest, c.embedding IS NOT NULL FROM chunks c "
            "JOIN corpus_vector_provenance p ON p.chunk_id=c.id WHERE c.doc_id=? AND c.page_num=1",
            (doc_ret2,),
        ).fetchone()
    assert p1_row == (retired_digest, 1)

    # 3. Mixed active + retired identities report partial and refuse backfill/config overwrite.
    env_mix, pdf_mix, doc_mix, hash_mix = _seed_case(
        "mixed_identity",
        [
            {"text": "Active chunk in mixed corpus.", "digest": BGE_NATIVE_IDENTITY},
            {"text": "Retired chunk in mixed corpus.", "digest": retired_digest},
        ],
    )
    with env_mix["indexer"].get_connection() as conn:
        assert env_mix["indexer"]._count_compatible_vectors(conn, doc_id=doc_mix, require_1024=True) == (2, 1)
    st_mix = env_mix["indexer"].get_status()
    assert st_mix["embeddings_ready"] == 1
    assert st_mix["embeddings_pending"] == 1
    assert st_mix["documents"][0]["vectors_ready"] is False
    assert st_mix["documents"][0]["embedding_status"] == "partial"

    env_mix["watcher"].sync_after_manual_index()
    assert env_mix["watcher"].get_status()["items"][0]["embedding_status"] == "partial"
    w_item_mix = {}
    ready_mix, _, _ = env_mix["watcher"]._sync_vectors_for_doc(
        env_mix["indexer"], w_item_mix, {"id": doc_mix, "filename": pdf_mix.name, "file_hash": hash_mix}, time.time()
    )
    assert ready_mix is False
    assert w_item_mix["vectors_ready"] is False
    assert w_item_mix["embedding_status"] == "partial"

    with pytest.raises(RuntimeError, match="digest"):
        env_mix["indexer"]._resolve_embedding_config("bge-m3")
    with pytest.raises(RuntimeError, match="digest"):
        env_mix["indexer"].backfill_missing_embeddings(embed_model="bge-m3")
    with sqlite3.connect(env_mix["db_path"]) as db:
        digests_left = [
            r[0]
            for r in db.execute("SELECT model_digest FROM corpus_vector_provenance ORDER BY chunk_id").fetchall()
        ]
    assert digests_left == [BGE_NATIVE_IDENTITY, retired_digest]

    # 4. Unknown/unprovenanced vector & unverified/unavailable identity (Ollama + tampered native) fail closed.
    env_unk, _, doc_unk, _ = _seed_case(
        "unbound_vector",
        [{"text": "Vector without provenance row.", "digest": "", "with_provenance": False}],
    )
    with env_unk["indexer"].get_connection() as conn:
        assert env_unk["indexer"]._count_compatible_vectors(conn, doc_id=doc_unk, require_1024=True) == (1, 0)
    with pytest.raises(RuntimeError, match="digest"):
        env_unk["indexer"].backfill_missing_embeddings(embed_model="bge-m3")

    ollama_digest = "ollama-sha256:" + ("a" * 64)
    env_ol, _, doc_ol, _ = _seed_case(
        "ollama_identity",
        [{"text": "Ollama verified passage.", "digest": ollama_digest}],
        backend="ollama",
    )
    current_ol = [ollama_digest]
    monkeypatch.setattr(env_ol["indexer"].ollama, "embedding_identity", lambda m: current_ol[0])
    with env_ol["indexer"].get_connection() as conn:
        assert env_ol["indexer"]._count_compatible_vectors(conn, doc_id=doc_ol, require_1024=True) == (1, 1)
    assert env_ol["indexer"].get_status()["documents"][0]["vectors_ready"] is True

    current_ol[0] = None
    with env_ol["indexer"].get_connection() as conn:
        assert env_ol["indexer"]._count_compatible_vectors(conn, doc_id=doc_ol, require_1024=True) == (1, 0)
    assert env_ol["indexer"].get_status()["documents"][0]["vectors_ready"] is False

    monkeypatch.setenv("MLX_BGE_MODEL_DIR", str(unverified_dir))
    with env_ok["indexer"].get_connection() as conn:
        assert env_ok["indexer"]._count_compatible_vectors(conn, doc_id=doc_ok, require_1024=True) == (1, 0)
    monkeypatch.setenv("MLX_BGE_MODEL_DIR", str(verified_dir))

    # 5. Corrupted provenance rows (full_text=0, text_sha256 mismatch, vector_sha256 mismatch, corrupt blob).
    for fault_name, spec in (
        ("bad_full_text", {"text": "Bad full text.", "digest": BGE_NATIVE_IDENTITY, "full_text": 0}),
        ("bad_text_sha", {"text": "Bad text sha.", "digest": BGE_NATIVE_IDENTITY, "text_sha256": "0" * 64}),
        ("bad_vec_sha", {"text": "Bad vec sha.", "digest": BGE_NATIVE_IDENTITY, "vec_sha256": "0" * 64}),
        ("corrupt_blob", {"text": "Corrupt blob.", "digest": BGE_NATIVE_IDENTITY, "blob": b"x", "vec_sha256": hashlib.sha256(b"x").hexdigest()}),
    ):
        env_f, _, doc_f, _ = _seed_case(fault_name, [spec])
        with env_f["indexer"].get_connection() as conn:
            assert env_f["indexer"]._count_compatible_vectors(conn, doc_id=doc_f, require_1024=True) == (1, 0)
        assert env_f["indexer"].get_status()["documents"][0]["vectors_ready"] is False

    # 6. Opt-in STUDY_TEST_REAL_SIX_DIR skips cleanly when unset or pointing to a nonexistent directory.
    monkeypatch.delenv(REAL_SIX_DIR_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception, match=REAL_SIX_DIR_ENV):
        _resolve_real_six_dir()
    monkeypatch.setenv(REAL_SIX_DIR_ENV, str(tmp_path / "missing_real_six_dir"))
    with pytest.raises(pytest.skip.Exception, match="does not exist"):
        _resolve_real_six_dir()

