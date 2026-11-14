"""Tests for managed operator src/ filesystem watcher and incremental indexing (WATCH-01..06)."""

import hashlib
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.indexer import KnowledgeIndexer, corpus_writer_lock
from app.pdf_extractor import PDFExtractor
from app.retrieval_tree import build_tree
from app.searcher import HybridSearcher
import app.server as server


def _make_text_pdf(path: Path, text: str) -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _make_blank_image_pdf(path: Path) -> None:
    """Create a valid PDF whose page renders as an image-only scan with zero digital text."""
    import pymupdf

    src_doc = pymupdf.open()
    src_doc.new_page(width=200, height=200)
    pix = src_doc[0].get_pixmap(alpha=False)
    img_bytes = pix.tobytes("png")
    src_doc.close()

    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.insert_image(page.rect, stream=img_bytes)
    doc.save(str(path))
    doc.close()


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _create_seed_corpus(src_dir: Path, db_path: Path):
    seed_file = src_dir / "seed_notes.txt"
    seed_file.write_text(
        "Tai lieu goc da kiem dinh cho hoc phan Tam ly hoc dai cuong.\n"
        "Day la noi dung nen tang phai duoc bao toan nguyen ven.",
        encoding="utf-8",
    )
    seed_indexer = KnowledgeIndexer(db_path=db_path, src_dir=src_dir)
    seed_indexer.index_all(embed_model=None)
    with seed_indexer.get_connection() as conn:
        doc_row = dict(
            conn.execute("SELECT * FROM documents WHERE filename = ?", ("seed_notes.txt",)).fetchone()
        )
        chunk_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id, doc_id, filename, page_num, chunk_index, text, embedding "
                "FROM chunks WHERE doc_id = ? ORDER BY id",
                (doc_row["id"],),
            ).fetchall()
        ]
    return seed_indexer, seed_file, doc_row, chunk_rows


def test_import_safety_and_default_disabled_auto_watch(tmp_path):
    """WATCH-01/WATCH-05: auto_watch defaults to False and importing app.server does not mutate DB."""
    client = TestClient(server.app)
    storage = client.get("/api/storage")
    assert storage.status_code == 200
    payload = storage.json()
    assert payload["auto_watch"] is False
    assert payload["watcher"]["enabled"] is False
    assert payload["watcher"]["running"] is False

    status_res = client.get("/api/status")
    assert status_res.status_code == 200
    assert status_res.json()["storage"]["auto_watch"] is False
    assert status_res.json()["storage"]["watcher"]["running"] is False


def test_watch01_lifespan_auto_indexes_dropped_files_and_exposes_lookup(tmp_path, monkeypatch):
    """WATCH-01: Dropping text/PDF files while enabled indexes without POST /api/index."""
    from app.document_lookup import DocumentLookup
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    preexisting = src_dir / "startup_lesson.txt"
    preexisting.write_text(
        "Tai lieu co san truoc khi may chu khoi dong voi tu khoa WATCH01_STARTUP_TOKEN.",
        encoding="utf-8",
    )

    test_indexer = KnowledgeIndexer(db_path=db_path, src_dir=src_dir)
    test_searcher = HybridSearcher(db_path=db_path)
    test_lookup = DocumentLookup(db_path=db_path, src_dir=src_dir)
    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        poll_interval=0.08,
        debounce_seconds=0.1,
        embed_model=None,
    )

    monkeypatch.setenv("STUDY_SRC_AUTO_WATCH", "1")
    monkeypatch.setattr(server, "SRC_DIR", src_dir)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "indexer", test_indexer)
    monkeypatch.setattr(server, "searcher", test_searcher)
    monkeypatch.setattr(server, "document_lookup", test_lookup)
    monkeypatch.setattr(server, "src_watcher", watcher)

    with TestClient(server.app) as client:
        assert watcher.get_status()["running"] is True

        assert _wait_until(
            lambda: "startup_lesson.txt" in test_indexer.get_indexed_files_map(),
            timeout=5.0,
        )

        # Drop a nested PDF after backend is running
        nested_dir = src_dir / "week_02"
        nested_dir.mkdir()
        dropped_pdf = nested_dir / "dropped_chapter.pdf"
        _make_text_pdf(dropped_pdf, "Noi dung PDF moi tha vao thu muc nguon WATCH01_PDF_TOKEN.")

        assert _wait_until(
            lambda: "week_02/dropped_chapter.pdf" in test_indexer.get_indexed_files_map(),
            timeout=5.0,
        )

        docs_res = client.get("/api/documents")
        assert docs_res.status_code == 200
        doc_names = {d["filename"]: d for d in docs_res.json()["documents"]}
        assert "startup_lesson.txt" in doc_names
        assert "week_02/dropped_chapter.pdf" in doc_names

        pdf_id = doc_names["week_02/dropped_chapter.pdf"]["id"]
        orig_res = client.get(f"/api/documents/{pdf_id}/original")
        assert orig_res.status_code == 200
        assert len(orig_res.content) > 100

        storage_res = client.get("/api/storage").json()
        assert storage_res["auto_watch"] is True
        assert storage_res["watcher"]["running"] is True
        assert storage_res["watcher"]["indexed_count"] >= 2
        assert storage_res["watcher"]["last_scan_at"] is not None

        status_res = client.get("/api/status").json()
        assert status_res["storage"]["auto_watch"] is True
        assert status_res["storage"]["watcher"]["indexed_count"] >= 2

    # Shutdown cleanly stopped the watcher
    assert watcher.get_status()["running"] is False


def test_watch02_debounce_filtering_symlinks_and_idempotency(tmp_path):
    """WATCH-02: Debounce settles partial writes, filters temps/hidden/symlinks, avoids duplicates."""
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    outside_dir = tmp_path / "outside"
    src_dir.mkdir()
    data_dir.mkdir()
    outside_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    outside_secret = outside_dir / "secret.txt"
    outside_secret.write_text("SECRET_OUTSIDE_CORPUS", encoding="utf-8")

    # Ignored files & symlink escape
    (src_dir / ".DS_Store").write_text("junk", encoding="utf-8")
    (src_dir / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (src_dir / "notes.tmp").write_text("temp", encoding="utf-8")
    (src_dir / "download.part").write_text("partial", encoding="utf-8")
    (src_dir / "archive.crdownload").write_text("cr", encoding="utf-8")
    (src_dir / "~$lock.docx").write_text("office lock", encoding="utf-8")
    (src_dir / "script.py").write_text("print('unsupported')", encoding="utf-8")
    escape_link = src_dir / "escape.txt"
    escape_link.symlink_to(outside_secret)

    # Partial growing file: first write waits through debounce window
    growing = src_dir / "growing.txt"
    growing.write_text("Phan 1 dang chep...", encoding="utf-8")

    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        debounce_seconds=0.25,
        embed_model=None,
    )

    first = watcher.scan_once()
    assert "growing.txt" not in _indexed_map_or_empty(db_path)
    assert watcher.get_status()["pending_count"] == 1
    assert first["indexed"] == 0

    # Grow file before debounce expires -> resets settle timer
    time.sleep(0.08)
    growing.write_text("Phan 1 dang chep... Phan 2 da day du noi dung.", encoding="utf-8")
    second = watcher.scan_once()
    assert "growing.txt" not in _indexed_map_or_empty(db_path)
    assert second["indexed"] == 0

    # After settling beyond debounce window, file is indexed with complete content
    time.sleep(0.3)
    third = watcher.scan_once()
    assert third["indexed"] == 1
    indexed = _indexed_map_or_empty(db_path)
    assert set(indexed.keys()) == {"growing.txt"}

    with KnowledgeIndexer(db_path=db_path, src_dir=src_dir).get_connection() as conn:
        chunk_texts = [r[0] for r in conn.execute("SELECT text FROM chunks").fetchall()]
    assert any("Phan 2 da day du noi dung" in t for t in chunk_texts)

    # Repeated scans & fresh watcher restart must NOT duplicate docs or chunks
    chunk_count_before = len(chunk_texts)
    watcher.scan_once()
    watcher_restarted = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        debounce_seconds=0.25,
        embed_model=None,
    )
    watcher_restarted.scan_once()

    with KnowledgeIndexer(db_path=db_path, src_dir=src_dir).get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == chunk_count_before


def _indexed_map_or_empty(db_path: Path):
    if not db_path.exists():
        return {}
    return KnowledgeIndexer(db_path=db_path).get_indexed_files_map()


def test_watch03_corrupt_scan_backoff_and_recovery_on_content_change(tmp_path):
    """WATCH-03: Corrupt PDF backs off safely without spinning; repaired bytes clear error; scan stays needs_ocr."""
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    corrupt = src_dir / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nnot-a-valid-pdf-stream-at-all")

    scan_pdf = src_dir / "scan_only.pdf"
    _make_blank_image_pdf(scan_pdf)

    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        debounce_seconds=0.05,
        max_retries=2,
        base_backoff_seconds=5.0,
        embed_model=None,
        auto_ocr=False,
    )

    watcher.scan_once()
    time.sleep(0.08)
    watcher.scan_once()

    st = watcher.get_status()
    assert st["needs_ocr_count"] == 1
    assert st["error_count"] == 1
    # Next immediate scan must obey backoff and NOT increment attempt counter or spin
    watcher.scan_once()
    items = {item["filename"]: item for item in watcher.get_status()["items"]}
    assert items["scan_only.pdf"]["status"] == "needs_ocr"
    assert items["corrupt.pdf"]["status"] == "error"
    assert items["corrupt.pdf"]["attempts"] == 1

    # Sanitized public error message must never leak filesystem paths or tracebacks
    err_msg = items["corrupt.pdf"]["message"]
    assert str(tmp_path) not in err_msg
    assert "Traceback" not in err_msg

    # Replacing corrupt file with valid PDF bytes clears old error state immediately
    _make_text_pdf(corrupt, "Da sua xong PDF hop le WATCH03_RECOVERED.")
    watcher.scan_once()
    time.sleep(0.08)
    watcher.scan_once()

    items_after = {item["filename"]: item for item in watcher.get_status()["items"]}
    assert items_after["corrupt.pdf"]["status"] == "indexed"
    assert watcher.get_status()["error_count"] == 0

    indexed_map = KnowledgeIndexer(db_path=db_path, src_dir=src_dir).get_indexed_files_map()
    assert indexed_map["scan_only.pdf"]["status"] == "scanned_unocred"
    assert indexed_map["corrupt.pdf"]["status"] == "indexed"


def test_watch04_preserves_seed_corpus_flags_changed_sources_and_recovers_crash(tmp_path):
    """WATCH-04: Watcher preserves seed docs/chunks/reviews, flags changed sources, avoids overwrite, recovers processing state."""
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    seed_indexer, seed_file, seed_doc_before, seed_chunks_before = _create_seed_corpus(src_dir, db_path)

    # Also drop a brand-new file while a synthetic stale "processing" marker is in state file (crash recovery)
    new_doc = src_dir / "brand_new.txt"
    new_doc.write_text("Tai lieu moi hoan toan WATCH04_NEW.", encoding="utf-8")

    state_file = data_dir / "runtime" / "src_watcher_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "brand_new.txt": {
                        "rel_path": "brand_new.txt",
                        "status": "processing",
                        "file_size": new_doc.stat().st_size,
                        "mtime_ns": new_doc.stat().st_mtime_ns,
                        "first_seen_at": time.time() - 10.0,
                        "stable_since": time.time() - 10.0,
                        "last_seen_at": time.time() - 10.0,
                        "attempts": 0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        debounce_seconds=0.05,
        embed_model=None,
    )
    watcher.scan_once()
    time.sleep(0.08)
    watcher.scan_once()

    # Brand new doc indexed, seed doc & chunks untouched byte-for-byte
    assert "brand_new.txt" in seed_indexer.get_indexed_files_map()
    with seed_indexer.get_connection() as conn:
        seed_doc_after = dict(
            conn.execute("SELECT * FROM documents WHERE filename = ?", ("seed_notes.txt",)).fetchone()
        )
        seed_chunks_after = [
            dict(r)
            for r in conn.execute(
                "SELECT id, doc_id, filename, page_num, chunk_index, text, embedding "
                "FROM chunks WHERE doc_id = ? ORDER BY id",
                (seed_doc_after["id"],),
            ).fetchall()
        ]
    assert seed_doc_after["file_hash"] == seed_doc_before["file_hash"]
    assert seed_chunks_after == seed_chunks_before

    # Modify already-indexed seed source on disk -> must flag needs_update without silent overwrite!
    seed_file.write_text("Noi dung seed da bi thay doi tren dia!", encoding="utf-8")
    watcher.scan_once()
    time.sleep(0.08)
    watcher.scan_once()

    w_status = watcher.get_status()
    assert w_status["needs_update_count"] == 1
    w_items = {it["filename"]: it for it in w_status["items"]}
    assert w_items["seed_notes.txt"]["status"] == "needs_update"
    assert w_items["seed_notes.txt"]["stale"] is True

    # Seed chunks in DB remain un-overwritten, and indexer status does not advertise stale doc as current
    with seed_indexer.get_connection() as conn:
        unchanged_chunks = [
            dict(r)
            for r in conn.execute(
                "SELECT id, doc_id, filename, page_num, chunk_index, text, embedding "
                "FROM chunks WHERE doc_id = ? ORDER BY id",
                (seed_doc_before["id"],),
            ).fetchall()
        ]
    assert unchanged_chunks == seed_chunks_before

    kb_status = KnowledgeIndexer(db_path=db_path, src_dir=src_dir).get_status()
    seed_entry = next(d for d in kb_status["documents"] if d["filename"] == "seed_notes.txt")
    assert seed_entry["passages_current"] is False
    assert seed_entry["sync_status"] == "needs_update"


def test_watch05_gpu_busy_defers_truthfully_and_health_stays_nonblocking(tmp_path, monkeypatch):
    """WATCH-05: Busy GPU/training defers vector indexing truthfully, emits real vectors when idle, and never blocks /api/health."""
    from app.gpu_lock import GPUBusyError, gpu_coordinator
    from app.native_embedding import NativeEmbeddingClient
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    doc_file = src_dir / "vector_doc.txt"
    doc_file.write_text("Tai lieu can vector embedding that qua gpu_coordinator.", encoding="utf-8")

    # Simulate macOS native embedding configured & available, but GPU training busy first
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **k: True))
    monkeypatch.setattr(NativeEmbeddingClient, "get_identity", lambda self: "bge-m3-mlx-fp16@testdigest")

    gpu_busy = {"active": True}
    monkeypatch.setattr(
        gpu_coordinator,
        "check_inference_allowed",
        lambda: (False, "GPU dang ban huan luyen") if gpu_busy["active"] else (True, "San sang"),
    )

    def fake_batch(self, texts, max_length=512):
        if gpu_busy["active"]:
            raise GPUBusyError("GPU dang ban huan luyen")
        return [[0.25, 0.5, 0.75, 1.0] for _ in texts]

    monkeypatch.setattr(NativeEmbeddingClient, "get_batch_embeddings", fake_batch)

    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        poll_interval=0.05,
        debounce_seconds=0.05,
        embed_model="bge-m3",
    )

    monkeypatch.setenv("STUDY_SRC_AUTO_WATCH", "1")
    monkeypatch.setattr(server, "SRC_DIR", src_dir)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "indexer", KnowledgeIndexer(db_path=db_path, src_dir=src_dir))
    monkeypatch.setattr(server, "src_watcher", watcher)

    with TestClient(server.app) as client:
        # While GPU busy, health stays fast 200 and watcher defers cleanly
        assert client.get("/api/health").status_code == 200
        assert _wait_until(
            lambda: watcher.get_status()["waiting_gpu_count"] == 1,
            timeout=3.0,
        )
        assert "vector_doc.txt" not in _indexed_map_or_empty(db_path)

        # Free GPU -> real vectors written with provenance
        gpu_busy["active"] = False
        assert _wait_until(
            lambda: "vector_doc.txt" in _indexed_map_or_empty(db_path),
            timeout=3.0,
        )

    with KnowledgeIndexer(db_path=db_path, src_dir=src_dir).get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(embedding) FROM chunks WHERE filename = ?",
            ("vector_doc.txt",),
        ).fetchone()
        prov = conn.execute(
            "SELECT model_digest FROM corpus_vector_provenance LIMIT 1"
        ).fetchone()
    assert row[0] >= 1 and row[1] == row[0]
    assert prov is not None and prov[0] == "bge-m3-mlx-fp16@testdigest"


def test_watch06_stale_retrieval_tree_and_writer_lock_serialization(tmp_path):
    """WATCH-06: New watched doc is searchable despite stale retrieval_tree.db; writer lock serializes concurrent indexing."""
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    seed_indexer, _, _, _ = _create_seed_corpus(src_dir, db_path)
    sidecar_db = data_dir / "retrieval_tree.db"
    build_tree(source_db=db_path, shadow_db=sidecar_db)
    assert sidecar_db.is_file()

    # Watch & index a brand new document after retrieval_tree.db was built
    new_doc = src_dir / "fresh_topic.txt"
    new_doc.write_text("Chu de moi WATCH06_UNIQUE_NEEDLE trong tai lieu vua them.", encoding="utf-8")

    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        debounce_seconds=0.05,
        embed_model=None,
    )
    watcher.scan_once()
    time.sleep(0.08)
    watcher.scan_once()

    # Sidecar retrieval_tree.db staleness must fall back rather than hiding the newly indexed doc
    searcher = HybridSearcher(db_path=db_path)
    hits = searcher.search_hybrid("WATCH06_UNIQUE_NEEDLE", top_k=5, embed_model=None)
    assert any(h["filename"] == "fresh_topic.txt" for h in hits)
    assert searcher.last_topic_routing["fallback_reason"] == "stale_or_invalid_tree"

    # Writer lock serialization: concurrent thread holding corpus_writer_lock blocks second writer cleanly
    order = []
    entered = threading.Event()
    released = threading.Event()

    def writer_one():
        with corpus_writer_lock(db_path):
            order.append("w1_enter")
            entered.set()
            released.wait(timeout=2.0)
            order.append("w1_exit")

    def writer_two():
        assert entered.wait(timeout=2.0)
        with corpus_writer_lock(db_path):
            order.append("w2_enter")

    t1 = threading.Thread(target=writer_one)
    t2 = threading.Thread(target=writer_two)
    t1.start()
    t2.start()
    assert entered.wait(timeout=2.0)
    time.sleep(0.05)
    released.set()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)
    assert order == ["w1_enter", "w1_exit", "w2_enter"]


def test_account_user_does_not_receive_host_paths_from_storage_or_watcher(tmp_path, monkeypatch):
    """Public /api/storage and /api/status must omit host filesystem paths for authenticated/non-local users."""
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(tmp_path / "accounts"))

    with TestClient(server.app) as client:
        reg = client.post(
            "/api/account/register",
            json={"username": "student_watch", "password": "VeryLongPassword123!"},
            headers={"X-Study-Request": "1"},
        )
        assert reg.status_code == 200

        storage = client.get("/api/storage")
        assert storage.status_code == 200
        s_data = storage.json()
        assert "sources_dir" not in s_data or s_data["sources_dir"] is None
        assert "data_dir" not in s_data or s_data["data_dir"] is None
        assert s_data["source_scope"] == "operator_managed_shared_src"
        assert s_data["uploads_scope"] == "account_private_isolated"
        assert "watcher" in s_data


def test_watch04_missing_source_flagged_without_deleting_existing_index(tmp_path):
    """WATCH-04: Removing a previously indexed source flags missing_source without silently deleting DB rows."""
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    seed_indexer, seed_file, seed_doc_before, seed_chunks_before = _create_seed_corpus(src_dir, db_path)
    seed_file.unlink()

    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        debounce_seconds=0.05,
        embed_model=None,
    )
    watcher.scan_once()

    w_status = watcher.get_status()
    assert w_status["missing_source_count"] == 1
    item = next(it for it in w_status["items"] if it["filename"] == "seed_notes.txt")
    assert item["status"] == "missing_source"
    assert item["stale"] is True

    with seed_indexer.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE filename = ?", ("seed_notes.txt",)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (seed_doc_before["id"],)).fetchone()[0] == len(seed_chunks_before)


def test_watch02_identity_recheck_aborts_commit_if_file_changes_mid_extraction(tmp_path, monkeypatch):
    """WATCH-02: Changing a file during extraction raises FileModifiedDuringIndexingError and leaves DB untouched."""
    from app.indexer import FileModifiedDuringIndexingError

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    target = src_dir / "racy.txt"
    target.write_text("Noi dung ban dau truoc khi bi ghi de.", encoding="utf-8")

    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src_dir)
    real_extract = PDFExtractor.extract_file

    def mutating_extract(filepath, *args, **kwargs):
        res = real_extract(filepath, *args, **kwargs)
        Path(filepath).write_text("Noi dung da thay doi giua chung!", encoding="utf-8")
        return res

    monkeypatch.setattr(PDFExtractor, "extract_file", classmethod(lambda cls, *a, **kw: mutating_extract(*a, **kw)))

    try:
        indexer.index_file(target, embed_model=None, require_new=True)
        raised = False
    except FileModifiedDuringIndexingError:
        raised = True

    assert raised is True
    assert indexer.get_indexed_files_map() == {}


def test_watch03_exhausts_backoff_and_watch05_fts_only_truthfulness(tmp_path):
    """WATCH-03/05: Corrupt file stops after max_retries until changed; FTS-only file never claims vectors_ready."""
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    corrupt = src_dir / "bad.pdf"
    corrupt.write_bytes(b"%PDF-1.4\ncorrupt-payload")
    good_txt = src_dir / "fts_only.txt"
    good_txt.write_text("Van ban hop le nhung khong co mo hinh vector WATCH05_FTS.", encoding="utf-8")

    watcher = SrcWatcher(
        src_dir=src_dir,
        db_path=db_path,
        data_dir=data_dir,
        debounce_seconds=0.01,
        max_retries=2,
        base_backoff_seconds=0.05,
        embed_model=None,
    )

    watcher.scan_once()
    time.sleep(0.03)
    watcher.scan_once()  # attempt 1 -> error, fts_only.txt -> indexed
    time.sleep(0.08)
    watcher.scan_once()  # attempt 2 -> exhausted
    time.sleep(0.12)
    watcher.scan_once()  # must remain exhausted at attempts == 2

    items = {it["filename"]: it for it in watcher.get_status()["items"]}
    assert items["bad.pdf"]["status"] == "error"
    assert items["bad.pdf"]["attempts"] == 2
    assert items["fts_only.txt"]["status"] == "indexed"
    assert items["fts_only.txt"]["vectors_ready"] is False
    assert items["fts_only.txt"]["embedding_status"] == "fts_only"


def test_watch06_dual_watchers_and_manual_index_compatible(tmp_path, monkeypatch):
    """WATCH-06: Two watchers + manual POST /api/index serialize cleanly without duplicating chunks or keeping stale needs_update."""
    from app.src_watcher import SrcWatcher

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    src_dir.mkdir()
    data_dir.mkdir()
    db_path = data_dir / "knowledge_base.db"

    seed_indexer, seed_file, _, _ = _create_seed_corpus(src_dir, db_path)
    new_doc = src_dir / "concurrent_doc.txt"
    new_doc.write_text("Tai lieu dong thoi WATCH06_DUAL.", encoding="utf-8")

    # Modify seed file so watcher flags needs_update first
    seed_file.write_text("Ban cap nhat seed WATCH06_MANUAL_REINDEX.", encoding="utf-8")

    w1 = SrcWatcher(src_dir=src_dir, db_path=db_path, data_dir=data_dir, debounce_seconds=0.02, embed_model=None)
    w2 = SrcWatcher(src_dir=src_dir, db_path=db_path, data_dir=data_dir, debounce_seconds=0.02, embed_model=None)

    w1.scan_once()
    w2.scan_once()
    time.sleep(0.05)

    # Run concurrent scans across two watcher threads -> no duplicate docs/chunks
    t1 = threading.Thread(target=w1.scan_once)
    t2 = threading.Thread(target=w2.scan_once)
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    w1_status = w1.get_status()
    assert w1_status["needs_update_count"] == 1
    assert "concurrent_doc.txt" in _indexed_map_or_empty(db_path)

    with seed_indexer.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE filename = ?", ("concurrent_doc.txt",)).fetchone()[0] == 1

    # Manual POST /api/index explicitly updates changed source and syncs watcher state
    monkeypatch.setattr(server, "SRC_DIR", src_dir)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "indexer", seed_indexer)
    monkeypatch.setattr(server, "src_watcher", w1)
    monkeypatch.setattr(server, "DEFAULT_EMBED_MODEL", None)

    client = TestClient(server.app)
    res = client.post("/api/index", json={"force": False, "ocr": False})
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert w1.get_status()["needs_update_count"] == 0
    kb = KnowledgeIndexer(db_path=db_path, src_dir=src_dir).get_status()
    assert kb["stale_documents"] == 0


