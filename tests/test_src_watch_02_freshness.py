"""Regression tests for FIX-SRC-WATCH-02: shared freshness admission and truthful bounded lifecycle."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import server
from app.document_lookup import DocumentLookup
from app.gpu_lock import GPUBusyError, GPULockManager
from app.history import HistoryStore
from app.indexer import KnowledgeIndexer, corpus_writer_lock
from app.learning import LearningService
from app.native_embedding import NativeEmbeddingClient
from app.ollama_client import OllamaClient
from app.practice import PracticeGenerationError, PracticeService
from app.rag_agent import PsychologyAgent
from app.retrieval_tree import build_tree
from app.searcher import HybridSearcher
from app.src_watcher import SrcWatcher
from app.study_materials import StudyMaterialError, StudyMaterialManager


class _NoCallOllama:
    def check_health(self):
        return False

    def get_embedding(self, *args, **kwargs):
        return None

    def embedding_identity(self, model=None):
        return None

    def chat_stream(self, *args, **kwargs):
        raise AssertionError("Stale source must not invoke chat generation")

    def chat_complete(self, *args, **kwargs):
        raise AssertionError("Stale source must not invoke chat/practice generation")


def _setup_corpus(tmp_path: Path, monkeypatch):
    import app.config as cfg

    src = tmp_path / "src"
    data = tmp_path / "data"
    src.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    db_path = data / "knowledge_base.db"

    seed = src / "seed.txt"
    seed.write_text("OLD_NEEDLE_642 quy tac an toan co ban trong tai lieu.", encoding="utf-8")
    keep = src / "keep.txt"
    keep.write_text("FRESH_KEEP_111 noi dung nguyen ven luon kha dung.", encoding="utf-8")

    monkeypatch.setattr(cfg, "SRC_DIR", src)
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(server, "SRC_DIR", src)
    monkeypatch.setattr(server, "DATA_DIR", data)
    monkeypatch.setattr(server, "DEFAULT_EMBED_MODEL", None)
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: False))

    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src)
    indexer.index_all(ocr=False, embed_model=None)
    build_tree(source_db=db_path, shadow_db=data / "retrieval_tree.db")

    indexed = indexer.get_indexed_files_map()
    return {
        "src": src,
        "data": data,
        "db_path": db_path,
        "seed": seed,
        "keep": keep,
        "seed_id": indexed["seed.txt"]["id"],
        "keep_id": indexed["keep.txt"]["id"],
        "indexer": indexer,
    }


def _assert_no_host_paths(text: str, *paths: Path) -> None:
    for p in paths:
        assert str(p) not in text
        assert str(p.resolve()) not in text


def test_stale_modified_original_blocked_across_retrieval_and_callers(tmp_path, monkeypatch):
    """Changing an indexed file on disk (even same length) blocks old DB retrieval/citations without deleting DB rows."""
    env = _setup_corpus(tmp_path, monkeypatch)
    src, data, db_path = env["src"], env["data"], env["db_path"]
    seed, seed_id = env["seed"], env["seed_id"]

    old_text = seed.read_text(encoding="utf-8")
    # Same-byte-length overwrite so stat byte size stays identical while mtime/hash change
    new_text = ("NEW_NEEDLE_999 noi dung da thay doi sau khi lap chi muc.").ljust(len(old_text))[:len(old_text)]
    assert len(new_text.encode("utf-8")) == len(old_text.encode("utf-8"))
    seed.write_text(new_text, encoding="utf-8")

    # Watcher flags needs_update without removing DB rows
    watcher = SrcWatcher(src_dir=src, db_path=db_path, data_dir=data, debounce_seconds=0.01, embed_model=None)
    watcher.scan_once()
    time.sleep(0.02)
    watcher.scan_once()
    assert watcher.get_status()["needs_update_count"] == 1

    # Also test cross-process / restarted reader even WITHOUT watcher state JSON present
    state_file = data / "runtime" / "src_watcher_state.json"
    if state_file.exists():
        state_file.unlink()

    searcher = HybridSearcher(db_path=db_path)
    lookup = DocumentLookup(db_path=db_path, src_dir=src)

    # 1. HybridSearcher paths exclude stale doc, preserve fresh doc
    assert searcher.source_page_chunks(seed_id, 1, limit=5) == []
    assert searcher.search_hybrid("OLD_NEEDLE_642", top_k=5, embed_model=None) == []
    fresh_hits = searcher.search_hybrid("FRESH_KEEP_111", top_k=5, embed_model=None)
    assert any(h["filename"] == "keep.txt" for h in fresh_hits)
    assert searcher.expand_markdown_answers(
        [{"chunk_id": 1, "doc_id": seed_id, "filename": "seed.txt", "page_num": 1, "chunk_index": 0, "text": old_text}]
    ) == []
    assert searcher.expand_page_context(
        [{"chunk_id": 1, "doc_id": seed_id, "filename": "seed.txt", "page_num": 1, "chunk_index": 0, "text": old_text}],
        max_chunks=4,
    ) == []

    # 2. DocumentLookup paths exclude stale doc and report actionable notice
    assert [d["filename"] for d in lookup.documents()] == ["keep.txt"]
    assert lookup.document(seed_id) is None
    assert lookup.document_by_filename("seed.txt") is None
    assert not lookup.source_preview("seed.txt", 1)
    dl_global = lookup.search("OLD_NEEDLE_642", limit=5, method="fts")
    assert dl_global["results"] == []
    dl_scoped = lookup.search("OLD_NEEDLE_642", limit=5, doc_id=seed_id, method="fts")
    assert dl_scoped["results"] == []
    assert dl_scoped["notice"]
    _assert_no_host_paths(dl_scoped["notice"], src, data)

    # 3. Direct rag_agent, PracticeService, LearningService, StudyMaterialManager exclude/block stale source
    agent = PsychologyAgent(searcher=searcher, ollama=_NoCallOllama(), translator=SimpleNamespace(get_cached=lambda _: {}))
    events = list(agent.process_query_stream("OLD_NEEDLE_642 la gi?", source_page=(seed_id, 1), source_document_id=seed_id))
    done_ev = [e for e in events if e.get("type") == "done"][-1]
    assert done_ev["citations"] == []
    assert done_ev["generation_calls"] == 0
    assert done_ev["response_kind"] == "insufficient_evidence"
    assert done_ev["insufficient_reason"] in {"source_stale", "source_missing"}
    _assert_no_host_paths(done_ev.get("message", ""), src, data)

    # Overview query on attached stale doc must not read raw DB chunks
    overview_events = list(agent.process_query_stream("Tom tat noi dung tai lieu nay", source_document_id=seed_id))
    overview_done = [e for e in overview_events if e.get("type") == "done"][-1]
    assert overview_done["citations"] == []
    assert overview_done["generation_calls"] == 0
    assert overview_done["response_kind"] == "insufficient_evidence"

    practice = PracticeService(searcher=searcher, document_lookup=lookup, ollama=_NoCallOllama())
    with pytest.raises(PracticeGenerationError) as p_exc:
        practice.generate(seed_id, 1, count=1)
    _assert_no_host_paths(str(p_exc.value), src, data)
    with pytest.raises(PracticeGenerationError):
        practice.list_example_exercises(seed_id, 1, 1)

    learning = LearningService(document_lookup=lookup, practice_service=practice, searcher=searcher)
    assert all(item.get("filename") != "seed.txt" for item in learning.exercises("OLD_NEEDLE_642").get("items", []))

    sm = StudyMaterialManager()
    with pytest.raises(StudyMaterialError) as sm_exc:
        sm.inspect_library_document(doc_id=seed_id, filename="seed.txt")
    _assert_no_host_paths(str(sm_exc.value), src, data)
    with pytest.raises(StudyMaterialError):
        sm.generate_from_library_document(doc_id=seed_id, filename="seed.txt")

    # 4. HTTP endpoints fail closed with 409 Conflict (stale modified original) without leaking paths
    monkeypatch.setattr(server, "document_lookup", lookup)
    monkeypatch.setattr(server, "searcher", searcher)
    monkeypatch.setattr(server, "indexer", env["indexer"])
    monkeypatch.setattr(server, "src_watcher", watcher)
    monkeypatch.setattr(server, "agent", agent)
    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "history.db"))
    monkeypatch.setattr(server.TrainedModelClient, "available", classmethod(lambda *a, **kw: False))

    client = TestClient(server.app)
    for url, params in [
        (f"/api/documents/{seed_id}/original", None),
        (f"/api/documents/{seed_id}/pdf", None),
        (f"/api/documents/{seed_id}/pages/1/image", None),
        ("/api/documents/source-preview", {"filename": "seed.txt", "page_num": 1}),
        ("/api/documents/search", {"q": "OLD_NEEDLE_642", "document_id": seed_id}),
    ]:
        res = client.get(url, params=params)
        assert res.status_code == 409, (url, res.status_code, res.text)
        _assert_no_host_paths(res.text, src, data)

    chat_res = client.post(
        "/api/chat",
        json={"query": "Giai thich", "source_document_id": seed_id, "source_page_num": 1, "save_history": False},
    )
    assert chat_res.status_code == 409
    _assert_no_host_paths(chat_res.text, src, data)

    # DB rows for operator inspection/update remain preserved
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (seed_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (seed_id,)).fetchone()[0] >= 1


def test_deleted_original_blocked_everywhere_while_db_preserved(tmp_path, monkeypatch):
    """Deleting an indexed original from disk blocks retrieval/citations while keeping DB rows for operator action."""
    env = _setup_corpus(tmp_path, monkeypatch)
    src, data, db_path = env["src"], env["data"], env["db_path"]
    seed, seed_id = env["seed"], env["seed_id"]

    seed.unlink()

    searcher = HybridSearcher(db_path=db_path)
    lookup = DocumentLookup(db_path=db_path, src_dir=src)

    assert searcher.source_page_chunks(seed_id, 1, limit=5) == []
    assert searcher.search_hybrid("OLD_NEEDLE_642", top_k=5, embed_model=None) == []
    assert lookup.document(seed_id) is None
    assert [d["filename"] for d in lookup.documents()] == ["keep.txt"]

    agent = PsychologyAgent(searcher=searcher, ollama=_NoCallOllama(), translator=SimpleNamespace(get_cached=lambda _: {}))
    done_ev = [e for e in agent.process_query_stream("OLD_NEEDLE_642", source_page=(seed_id, 1)) if e.get("type") == "done"][-1]
    assert done_ev["citations"] == []
    assert done_ev["generation_calls"] == 0
    assert done_ev["response_kind"] == "insufficient_evidence"

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE id = ?", (seed_id,)).fetchone()[0] == 1


def test_discovery_debounce_race_and_private_material_isolation(tmp_path, monkeypatch):
    """Modified already-indexed file never drops stale=True during debounce; private material_id remains isolated."""
    env = _setup_corpus(tmp_path, monkeypatch)
    src, data, db_path = env["src"], env["data"], env["db_path"]

    # Long debounce window so first scan captures debounce discovery phase
    watcher = SrcWatcher(src_dir=src, db_path=db_path, data_dir=data, debounce_seconds=5.0, embed_model=None)
    watcher.scan_once()  # baseline
    env["seed"].write_text("Noi dung doi trong luc debounce chua het.", encoding="utf-8")
    watcher.scan_once()  # discovery during active debounce

    item = next(it for it in watcher.get_status()["items"] if it["filename"] == "seed.txt")
    assert item["stale"] is True
    assert item["status"] == "needs_update"
    assert HybridSearcher(db_path=db_path).source_page_chunks(env["seed_id"], 1) == []

    # Account-private uploaded material remains usable even while shared seed.txt is stale
    monkeypatch.setattr(OllamaClient, "check_health", lambda self: False)
    sm = StudyMaterialManager()
    extracted = sm.extract_document(
        "private_note.txt",
        "Noi dung rieng tu ACCOUNT_PRIVATE_900 giai thich dinh ly Bayes.".encode("utf-8"),
    )
    mat = sm.store_extracted_document(extracted)
    mat_id = mat["id"]
    assert mat_id

    class FakeAnswerOllama(_NoCallOllama):
        def check_health(self):
            return False

        def chat_stream(self, *args, **kwargs):
            yield "Dinh ly Bayes [S1]."

    agent = PsychologyAgent(
        searcher=HybridSearcher(db_path=db_path),
        ollama=FakeAnswerOllama(),
        translator=SimpleNamespace(get_cached=lambda _: {}),
    )
    monkeypatch.setattr(server, "study_material_manager", sm)
    evs = list(
        agent.process_query_stream(
            "ACCOUNT_PRIVATE_900",
            material_id=mat_id,
            model="test-ollama",
        )
    )
    done_ev = [e for e in evs if e.get("type") == "done"][-1]
    assert done_ev["generation_calls"] >= 1


def test_freshness_bounded_stat_cache_avoids_repeated_hashing(tmp_path, monkeypatch):
    """Unchanged corpus does not recompute SHA-256 on every search or lookup query."""
    env = _setup_corpus(tmp_path, monkeypatch)
    from app import source_freshness

    source_freshness.clear_freshness_cache()
    calls = 0
    real_hash = source_freshness._compute_file_sha256

    def counted_hash(path: Path) -> str:
        nonlocal calls
        calls += 1
        return real_hash(path)

    monkeypatch.setattr(source_freshness, "_compute_file_sha256", counted_hash)

    searcher = HybridSearcher(db_path=env["db_path"])
    lookup = DocumentLookup(db_path=env["db_path"], src_dir=env["src"])
    # Warm pass hashes once per unchanged file
    lookup.documents()
    first_calls = calls
    assert first_calls <= 2

    # Repeated queries hit bounded stat cache without re-hashing
    for _ in range(5):
        lookup.documents()
        searcher.search_hybrid("FRESH_KEEP_111", top_k=3, embed_model=None)
        searcher.source_page_chunks(env["keep_id"], 1)
    assert calls == first_calls


def test_watcher_lifecycle_truthful_while_alive_and_prevents_second_writer(tmp_path, monkeypatch):
    """Timed-out stop keeps running=True, stopping=True while alive, blocks second start(), and drains cleanly."""
    src = tmp_path / "src"
    data = tmp_path / "data"
    src.mkdir()
    data.mkdir()
    db_path = data / "knowledge_base.db"

    watcher = SrcWatcher(src_dir=src, db_path=db_path, data_dir=data, debounce_seconds=0.01, poll_interval=0.02, embed_model=None)
    entered = threading.Event()
    release = threading.Event()
    call_count = 0

    def blocking_index(self, filepath, **kwargs):
        nonlocal call_count
        call_count += 1
        entered.set()
        release.wait(timeout=3.0)
        return {"status": "skipped", "reason": "test_stub"}

    monkeypatch.setattr(KnowledgeIndexer, "index_file", blocking_index)

    (src / "slow.txt").write_text("Noi dung chan shutdown.", encoding="utf-8")
    assert watcher.start() is True
    assert entered.wait(timeout=2.0)

    stopped = watcher.stop(timeout=0.08)
    assert stopped is False
    assert watcher._thread is not None and watcher._thread.is_alive()

    status = watcher.get_status()
    assert status["running"] is True
    assert status["stopping"] is True
    assert status["mode"] == "stopping"

    # Must NOT spawn a second writer thread while first thread is still alive
    assert watcher.start() is False
    assert call_count == 1

    release.set()
    assert watcher.stop(timeout=2.0) is True
    final_status = watcher.get_status()
    assert final_status["running"] is False
    assert final_status["stopping"] is False
    assert final_status["mode"] == "off"


def test_watcher_stop_cooperatively_cancels_owned_subprocess_and_releases_locks(tmp_path, monkeypatch):
    """Stopping watcher cancels owned embedding child process cleanly without orphans, locks, or DB corruption."""
    src = tmp_path / "src"
    data = tmp_path / "data"
    src.mkdir()
    data.mkdir()
    db_path = data / "knowledge_base.db"
    gpu_dir = data / "runtime"
    gpu_dir.mkdir(parents=True, exist_ok=True)
    gpu_lock = GPULockManager(gpu_dir / "gpu.lock", gpu_dir / "gpu_state.json")

    doc = src / "embed_slow.txt"
    doc.write_text("Noi dung can nhung bi huy giua chung khi shutdown.", encoding="utf-8")

    # Fake long-running child worker + grandchild spawned via NativeEmbeddingClient._worker_command
    child_pid_file = tmp_path / "child.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"
    gc_code = (
        "import os, pathlib, time; "
        f"pathlib.Path({json.dumps(str(grandchild_pid_file))}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    fake_py = tmp_path / "fake_python.py"
    fake_py.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        f"pathlib.Path({json.dumps(str(child_pid_file))}).write_text(str(os.getpid()))\n"
        f"gc = subprocess.Popen([sys.executable, '-c', {json.dumps(gc_code)}])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    from app.native_embedding import BGE_NATIVE_IDENTITY
    monkeypatch.setattr("app.indexer.gpu_coordinator", gpu_lock)
    monkeypatch.setattr("app.native_embedding.gpu_coordinator", gpu_lock)
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeEmbeddingClient, "verify_identity", classmethod(lambda cls, *a, **kw: BGE_NATIVE_IDENTITY))
    monkeypatch.setattr(NativeEmbeddingClient, "_worker_command", classmethod(lambda cls: [sys.executable, str(fake_py)]))

    watcher = SrcWatcher(
        src_dir=src,
        db_path=db_path,
        data_dir=data,
        debounce_seconds=0.01,
        poll_interval=0.02,
        embed_model="bge-m3-mlx-fp16",
    )
    assert watcher.start() is True

    # Wait until child & grandchild processes actually started and wrote PIDs
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and (not child_pid_file.exists() or not grandchild_pid_file.exists()):
        time.sleep(0.02)
    assert child_pid_file.exists() and grandchild_pid_file.exists()
    child_pid = int(child_pid_file.read_text().strip())
    grandchild_pid = int(grandchild_pid_file.read_text().strip())

    # Bounded stop must terminate owned child & session process group cleanly in under 2s
    t0 = time.monotonic()
    stopped = watcher.stop(timeout=1.5)
    elapsed = time.monotonic() - t0
    assert stopped is True
    assert elapsed < 1.5

    # Child & grandchild must both be dead/reaped (no PID-1 orphan)
    for pid in (child_pid, grandchild_pid):
        alive = True
        for _ in range(30):
            try:
                os.kill(pid, 0)
                time.sleep(0.02)
            except ProcessLookupError:
                alive = False
                break
        assert alive is False, f"Orphan worker PID {pid} survived shutdown"

    # Writer lock and GPU lock must both be free now
    with corpus_writer_lock(db_path, timeout=0.5):
        pass
    with gpu_lock.acquire_for_inference(timeout=0.5):
        pass

    # No partial DB commit and item was NOT marked failed/error from clean cancellation
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    item = next(it for it in watcher.get_status()["items"] if it["filename"] == "embed_slow.txt")
    assert item["status"] != "error"
    assert item["attempts"] == 0


def test_watcher_cancels_while_waiting_on_writer_lock_and_gpu_lock(tmp_path, monkeypatch):
    """Stop cancels cleanly when watcher is blocked on corpus_writer_lock or gpu_lock without error."""
    src = tmp_path / "src"
    data = tmp_path / "data"
    src.mkdir()
    data.mkdir()
    db_path = data / "knowledge_base.db"
    gpu_dir = data / "runtime"
    gpu_dir.mkdir(parents=True, exist_ok=True)
    gpu_lock = GPULockManager(gpu_dir / "gpu.lock", gpu_dir / "gpu_state.json")

    (src / "locked_doc.txt").write_text("Noi dung cho lock writer.", encoding="utf-8")

    # Case A: blocked waiting on corpus_writer_lock
    with corpus_writer_lock(db_path, timeout=1.0):
        watcher = SrcWatcher(src_dir=src, db_path=db_path, data_dir=data, debounce_seconds=0.01, poll_interval=0.02, embed_model=None)
        assert watcher.start() is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not watcher.get_status()["items"]:
            time.sleep(0.02)
        time.sleep(0.08)
        assert watcher.stop(timeout=1.5) is True
        assert watcher.get_status()["running"] is False

    # Case B: blocked waiting on gpu_coordinator.acquire_for_inference
    monkeypatch.setattr("app.indexer.gpu_coordinator", gpu_lock)
    monkeypatch.setattr("app.native_embedding.gpu_coordinator", gpu_lock)
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: True))
    with gpu_lock.acquire_for_inference(timeout=1.0):
        watcher2 = SrcWatcher(
            src_dir=src,
            db_path=db_path,
            data_dir=data,
            debounce_seconds=0.01,
            poll_interval=0.02,
            embed_model="bge-m3-mlx-fp16",
        )
        assert watcher2.start() is True
        time.sleep(0.12)
        assert watcher2.stop(timeout=1.5) is True
        st2 = watcher2.get_status()
        assert st2["running"] is False
        assert st2["items"][0]["status"] != "error"


def test_symlink_escape_and_identity_mismatch_fail_closed(tmp_path):
    """Outside symlinks and traversal document identifiers fail freshness validation closed."""
    src = tmp_path / "src"
    data = tmp_path / "data"
    src.mkdir()
    data.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("SECRET_OUTSIDE_DATA", encoding="utf-8")
    link = src / "escape.txt"
    link.symlink_to(outside)

    db_path = data / "knowledge_base.db"
    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src)
    with indexer.get_connection() as conn:
        doc_escape = conn.execute(
            "INSERT INTO documents (filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("escape.txt", "Escape", str(link), outside.stat().st_size, "0" * 64, 1, 1, 0, "indexed"),
        ).lastrowid
        doc_trav = conn.execute(
            "INSERT INTO documents (filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("../secret.txt", "Trav", str(outside), outside.stat().st_size, "0" * 64, 1, 1, 0, "indexed"),
        ).lastrowid

    lookup = DocumentLookup(db_path=db_path, src_dir=src)
    assert lookup.documents() == []
    assert lookup.document(doc_escape) is None
    assert lookup.document(doc_trav) is None
    assert lookup.inspect_document_status(doc_id=doc_escape)["status"] == "unsafe_source_path"
    assert lookup.inspect_document_status(doc_id=doc_trav)["status"] == "unsafe_source_path"


def test_manual_reindex_clears_freshness_cache_and_recovers_stale_doc(tmp_path, monkeypatch):
    """Manual POST /api/index re-indexes changed original, clears stale cache, and restores retrieval/serving."""
    env = _setup_corpus(tmp_path, monkeypatch)
    src, data, db_path = env["src"], env["data"], env["db_path"]
    seed, seed_id = env["seed"], env["seed_id"]

    watcher = SrcWatcher(src_dir=src, db_path=db_path, data_dir=data, debounce_seconds=5.0, embed_model=None)
    lookup = DocumentLookup(db_path=db_path, src_dir=src)
    searcher = HybridSearcher(db_path=db_path)

    # Modify on disk -> stale 409
    seed.write_text("UPDATED_CORPUS_VALUE_777 noi dung moi da cap nhat.", encoding="utf-8")
    assert lookup.document(seed_id) is None

    monkeypatch.setattr(server, "document_lookup", lookup)
    monkeypatch.setattr(server, "searcher", searcher)
    monkeypatch.setattr(server, "indexer", env["indexer"])
    monkeypatch.setattr(server, "src_watcher", watcher)
    client = TestClient(server.app)

    assert client.get(f"/api/documents/{seed_id}/original").status_code == 409

    # Manual re-index clears freshness cache & restores fresh status
    idx_res = client.post("/api/index", json={"force": False, "ocr": False})
    assert idx_res.status_code == 200
    orig_res = client.get(f"/api/documents/{seed_id}/original")
    assert orig_res.status_code == 200
    assert "UPDATED_CORPUS_VALUE_777" in orig_res.text
    hits = searcher.search_hybrid("UPDATED_CORPUS_VALUE_777", top_k=5, embed_model=None)
    assert any(h["filename"] == "seed.txt" for h in hits)


def test_lifespan_logs_error_when_watcher_stop_times_out(caplog, monkeypatch):
    """Server lifespan logs explicit error when watcher.stop(timeout=5.0) returns False."""
    import asyncio
    import logging

    fake_watcher = SimpleNamespace(
        is_enabled=lambda: True,
        start=lambda: True,
        stop=lambda timeout=5.0: False,
    )
    monkeypatch.setattr(server, "src_watcher", fake_watcher)

    async def _run_lifespan():
        async with server.lifespan(server.app):
            pass

    with caplog.at_level(logging.ERROR, logger="psychology_agent"):
        asyncio.run(_run_lifespan())
    assert any("did not finish stopping within bounded 5.0s shutdown window" in r.message for r in caplog.records)


def test_src_watcher_and_freshness_import_safe(tmp_path):
    """Importing app.src_watcher or app.source_freshness alone creates no runtime files, locks, or threads."""
    runtime_probe = tmp_path / "isolated_probe"
    code = (
        "import os; "
        f"os.environ['APP_DATA_DIR'] = {str(runtime_probe)!r}; "
        "import app.source_freshness, app.src_watcher; "
        "import threading; "
        "assert len(threading.enumerate()) <= 2"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (runtime_probe / "runtime").exists()


def test_accepted_scanned_library_document_inspectable_without_ocr_cache_and_refuses_stale_or_hold(
    tmp_path, monkeypatch
):
    """Accepted indexed scanned PDFs remain inspectable via DB chunks when OCR cache is absent, while legacy/stale/missing/HOLD semantics stay enforced."""
    import hashlib
    import io
    import pymupdf
    from PIL import Image
    import app.config as cfg
    from app import source_freshness, study_materials, vision_ocr

    source_freshness.clear_freshness_cache()

    src = tmp_path / "src"
    data = tmp_path / "data"
    ocr_cache = data / "ocr_cache"
    src.mkdir(parents=True, exist_ok=True)
    ocr_cache.mkdir(parents=True, exist_ok=True)
    db_path = data / "knowledge_base.db"

    monkeypatch.setattr(cfg, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cfg, "SRC_DIR", src)
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(study_materials, "DATA_DIR", data)
    monkeypatch.setattr(vision_ocr, "CACHE_DIR", ocr_cache)
    monkeypatch.setenv("OCR_CACHE_DIR", str(ocr_cache))
    monkeypatch.setattr(server, "SRC_DIR", src)
    monkeypatch.setattr(server, "DATA_DIR", data)
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: False))

    ordinary_file = src / "ordinary-guide.txt"
    ordinary_file.write_text(
        "ORDINARY_TEXT_GUIDE_301 cau truc du lieu ngan xep va hang doi co ban.",
        encoding="utf-8",
    )

    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src)
    indexer.index_all(ocr=False, embed_model=None)
    ordinary_id = indexer.get_indexed_files_map()["ordinary-guide.txt"]["id"]

    # Create a real 1-page image-only PDF with 0 digital text characters.
    img_buf = io.BytesIO()
    Image.new("RGB", (240, 120), color=(245, 245, 245)).save(img_buf, format="PNG")
    scan_pdf = src / "queue-cobalt-scan.pdf"
    pdf_doc = pymupdf.open()
    page = pdf_doc.new_page(width=240, height=120)
    page.insert_image(page.rect, stream=img_buf.getvalue())
    pdf_doc.save(str(scan_pdf))
    pdf_doc.close()

    with pymupdf.open(str(scan_pdf)) as check_pdf:
        assert (check_pdf[0].get_text() or "").strip() == ""

    scan_hash = hashlib.sha256(scan_pdf.read_bytes()).hexdigest()
    expected_scan_text = (
        "QUEUE COBALT\n"
        "A FIFO queue removes the earliest\n"
        "enqueued element.\n"
        "The front item leaves first."
    )

    with indexer.get_connection() as conn:
        scan_id = conn.execute(
            "INSERT INTO documents "
            "(filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "queue-cobalt-scan.pdf",
                "Queue Cobalt Scan",
                str(scan_pdf),
                scan_pdf.stat().st_size,
                scan_hash,
                1,
                1,
                1,
                "indexed",
            ),
        ).lastrowid
        conn.execute(
            "INSERT INTO chunks (doc_id, book_title, filename, page_num, chunk_index, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scan_id, "Queue Cobalt Scan", "queue-cobalt-scan.pdf", 1, 0, expected_scan_text),
        )
        conn.commit()

    assert not list(ocr_cache.glob("*.json"))

    # 1. Full-schema is_scanned flag survives fetch_document_row lookup by doc_id and filename.
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as ro_conn:
        ro_conn.row_factory = sqlite3.Row
        row_by_id = source_freshness.fetch_document_row(ro_conn, doc_id=scan_id)
        row_by_name = source_freshness.fetch_document_row(ro_conn, filename="queue-cobalt-scan.pdf")
        ordinary_row = source_freshness.fetch_document_row(ro_conn, doc_id=ordinary_id)
    assert row_by_id is not None and row_by_id.get("is_scanned") == 1
    assert row_by_name is not None and row_by_name.get("is_scanned") == 1
    assert ordinary_row is not None and ordinary_row.get("is_scanned") == 0

    sm = StudyMaterialManager()
    monkeypatch.setattr(server, "study_material_manager", sm)
    client = TestClient(server.app)

    # 2. Accepted indexed scan remains inspectable via doc_id, filename, and /api/materials/inspect-library with OCR cache absent.
    mat_by_id = sm.inspect_library_document(doc_id=scan_id, persist_to_src=True)
    assert mat_by_id["doc_id"] == scan_id
    assert mat_by_id["filename"] == "queue-cobalt-scan.pdf"
    assert mat_by_id["pages"] == [{"page_num": 1, "text": expected_scan_text}]

    mat_by_name = sm.inspect_library_document(filename="queue-cobalt-scan.pdf", persist_to_src=True)
    assert mat_by_name["doc_id"] == scan_id
    assert mat_by_name["pages"] == [{"page_num": 1, "text": expected_scan_text}]

    resp_by_name = client.post(
        "/api/materials/inspect-library",
        json={"filename": "queue-cobalt-scan.pdf", "persist_to_src": True},
    )
    assert resp_by_name.status_code == 200, resp_by_name.text
    payload_by_name = resp_by_name.json()
    assert payload_by_name["status"] == "success"
    assert payload_by_name["material"]["doc_id"] == scan_id
    assert payload_by_name["material"]["pages"] == [{"page_num": 1, "text": expected_scan_text}]

    resp_by_id = client.post(
        "/api/materials/inspect-library",
        json={"document_id": scan_id, "persist_to_src": False},
    )
    assert resp_by_id.status_code == 200, resp_by_id.text
    payload_by_id = resp_by_id.json()
    assert payload_by_id["status"] == "success"
    assert payload_by_id["material"]["doc_id"] == scan_id
    assert payload_by_id["material"]["pages"] == [{"page_num": 1, "text": expected_scan_text}]

    assert not list(ocr_cache.glob("*.json"))

    # 3. Ordinary text document behavior (is_scanned=0) stays intact.
    ord_by_id = sm.inspect_library_document(doc_id=ordinary_id)
    ord_by_name = sm.inspect_library_document(filename="ordinary-guide.txt")
    assert "ORDINARY_TEXT_GUIDE_301" in ord_by_id["pages"][0]["text"]
    assert "ORDINARY_TEXT_GUIDE_301" in ord_by_name["pages"][0]["text"]
    ord_resp = client.post("/api/materials/inspect-library", json={"filename": "ordinary-guide.txt"})
    assert ord_resp.status_code == 200, ord_resp.text
    assert "ORDINARY_TEXT_GUIDE_301" in ord_resp.json()["material"]["pages"][0]["text"]

    # 4. Legacy schema compatibility when is_scanned column is absent.
    legacy_db = tmp_path / "legacy.db"
    ordinary_hash = hashlib.sha256(ordinary_file.read_bytes()).hexdigest()
    with sqlite3.connect(legacy_db) as leg_conn:
        leg_conn.execute(
            "CREATE TABLE documents ("
            "id INTEGER PRIMARY KEY, filename TEXT, clean_title TEXT, filepath TEXT, "
            "file_size INTEGER, file_hash TEXT, total_pages INTEGER, status TEXT)"
        )
        leg_conn.execute(
            "CREATE TABLE chunks ("
            "id INTEGER PRIMARY KEY, doc_id INTEGER, page_num INTEGER, chunk_index INTEGER, text TEXT)"
        )
        leg_conn.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "ordinary-guide.txt", "Ordinary Guide", str(ordinary_file), ordinary_file.stat().st_size, ordinary_hash, 1, "indexed"),
        )
        leg_conn.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
            (1, 1, 1, 0, ordinary_file.read_text(encoding="utf-8")),
        )
        leg_conn.commit()
        leg_conn.row_factory = sqlite3.Row
        leg_by_id = source_freshness.fetch_document_row(leg_conn, doc_id=1)
        leg_by_name = source_freshness.fetch_document_row(leg_conn, filename="ordinary-guide.txt")
        assert leg_by_id is not None and "is_scanned" not in leg_by_id
        assert leg_by_name is not None and "is_scanned" not in leg_by_name

    monkeypatch.setattr(cfg, "DB_PATH", legacy_db)
    source_freshness.clear_freshness_cache()
    try:
        leg_mat_id = sm.inspect_library_document(doc_id=1)
        leg_mat_name = sm.inspect_library_document(filename="ordinary-guide.txt")
        assert "ORDINARY_TEXT_GUIDE_301" in leg_mat_id["pages"][0]["text"]
        assert "ORDINARY_TEXT_GUIDE_301" in leg_mat_name["pages"][0]["text"]
    finally:
        monkeypatch.setattr(cfg, "DB_PATH", db_path)
        source_freshness.clear_freshness_cache()

    # 5. HOLD / unreviewed status and visual-review refusals remain enforced.
    unocred_pdf = src / "unocred-scan.pdf"
    unocred_pdf.write_bytes(scan_pdf.read_bytes())
    hold_pdf = src / "hold-scan.pdf"
    hold_pdf.write_bytes(scan_pdf.read_bytes())
    with indexer.get_connection() as conn:
        unocred_id = conn.execute(
            "INSERT INTO documents "
            "(filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("unocred-scan.pdf", "Unocred Scan", str(unocred_pdf), unocred_pdf.stat().st_size, scan_hash, 1, 1, 1, "scanned_unocred"),
        ).lastrowid
        hold_id = conn.execute(
            "INSERT INTO documents "
            "(filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("hold-scan.pdf", "Hold Scan", str(hold_pdf), hold_pdf.stat().st_size, scan_hash, 1, 1, 1, "hold"),
        ).lastrowid
        conn.commit()
        conn.row_factory = sqlite3.Row
        assert source_freshness.fetch_document_row(conn, doc_id=unocred_id)["is_scanned"] == 1
        assert source_freshness.fetch_document_row(conn, doc_id=hold_id)["is_scanned"] == 1

    for bad_id, bad_name in ((unocred_id, "unocred-scan.pdf"), (hold_id, "hold-scan.pdf")):
        with pytest.raises(StudyMaterialError):
            sm.inspect_library_document(doc_id=bad_id)
        with pytest.raises(StudyMaterialError):
            sm.inspect_library_document(filename=bad_name)
        assert client.post("/api/materials/inspect-library", json={"document_id": bad_id}).status_code == 422

    with indexer.get_connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS corpus_page_quality "
            "(filename TEXT, page_num INTEGER, needs_review INTEGER, warnings_json TEXT)"
        )
        conn.execute(
            "INSERT INTO corpus_page_quality (filename, page_num, needs_review, warnings_json) VALUES (?, ?, ?, ?)",
            ("queue-cobalt-scan.pdf", 1, 1, json.dumps(["unrepresented_formula_or_diagram_suspected"])),
        )
        conn.commit()
    try:
        with pytest.raises(StudyMaterialError, match="trang 1"):
            sm.generate_from_library_document(doc_id=scan_id)
        gen_resp = client.post("/api/materials/generate-from-library", json={"document_id": scan_id})
        assert gen_resp.status_code == 422
    finally:
        with indexer.get_connection() as conn:
            conn.execute("DELETE FROM corpus_page_quality WHERE filename = ?", ("queue-cobalt-scan.pdf",))
            conn.commit()

    # 6. Watcher stale refusal (unhashed row), changed bytes on disk, and missing original on disk.
    unhashed_pdf = src / "unhashed-scan.pdf"
    unhashed_pdf.write_bytes(scan_pdf.read_bytes())
    with indexer.get_connection() as conn:
        unhashed_id = conn.execute(
            "INSERT INTO documents "
            "(filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("unhashed-scan.pdf", "Unhashed Scan", str(unhashed_pdf), unhashed_pdf.stat().st_size, "", 1, 1, 1, "indexed"),
        ).lastrowid
        conn.execute(
            "INSERT INTO chunks (doc_id, book_title, filename, page_num, chunk_index, text) VALUES (?, ?, ?, ?, ?, ?)",
            (unhashed_id, "Unhashed Scan", "unhashed-scan.pdf", 1, 0, expected_scan_text),
        )
        conn.commit()

    runtime_dir = data / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "src_watcher_state.json").write_text(
        json.dumps({"items": [{"filename": "unhashed-scan.pdf", "stale": True, "status": "needs_update"}]}),
        encoding="utf-8",
    )
    source_freshness.clear_freshness_cache()
    with pytest.raises(StudyMaterialError, match="cập nhật chỉ mục"):
        sm.inspect_library_document(doc_id=unhashed_id)
    with pytest.raises(StudyMaterialError, match="cập nhật chỉ mục"):
        sm.inspect_library_document(filename="unhashed-scan.pdf")
    assert client.post("/api/materials/inspect-library", json={"document_id": unhashed_id}).status_code == 422

    # Changed bytes on disk
    scan_pdf.write_bytes(scan_pdf.read_bytes() + b"\n% modified")
    source_freshness.clear_freshness_cache()
    with pytest.raises(StudyMaterialError):
        sm.inspect_library_document(doc_id=scan_id)
    with pytest.raises(StudyMaterialError):
        sm.inspect_library_document(filename="queue-cobalt-scan.pdf")
    assert client.post(
        "/api/materials/inspect-library",
        json={"filename": "queue-cobalt-scan.pdf", "persist_to_src": True},
    ).status_code == 422

    # Missing original on disk
    scan_pdf.unlink()
    source_freshness.clear_freshness_cache()
    with pytest.raises(StudyMaterialError):
        sm.inspect_library_document(doc_id=scan_id)
    with pytest.raises(StudyMaterialError):
        sm.inspect_library_document(filename="queue-cobalt-scan.pdf")
    assert client.post(
        "/api/materials/inspect-library",
        json={"document_id": scan_id, "persist_to_src": True},
    ).status_code == 422
