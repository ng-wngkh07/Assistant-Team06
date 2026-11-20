"""Regression tests for /api/health scan caching, single-flight coalescing, valid_blob speed/parity, and slow cold startup polling."""
from __future__ import annotations

import hashlib
import http.server
import json
import math
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np


def test_valid_blob_and_unpack_vector_parity():
    from app.indexer import pack_vector, unpack_vector
    from app.vector_rebuild import valid_blob

    unit = [1.0 / math.sqrt(1024)] * 1024
    blob = pack_vector(unit)
    digest = hashlib.sha256(blob).hexdigest()

    assert valid_blob(blob, digest) is True
    assert valid_blob(memoryview(blob), digest) is True
    assert valid_blob(blob, "0" * 64) is False
    assert valid_blob(blob[:-4], digest) is False

    bad_zero = pack_vector([0.0] * 1024)
    assert valid_blob(bad_zero, hashlib.sha256(bad_zero).hexdigest()) is False

    bad_nan = pack_vector([float("nan")] + [0.0] * 1023)
    assert valid_blob(bad_nan, hashlib.sha256(bad_nan).hexdigest()) is False

    bad_inf = pack_vector([float("inf")] + [0.0] * 1023)
    assert valid_blob(bad_inf, hashlib.sha256(bad_inf).hexdigest()) is False

    unpacked = unpack_vector(blob)
    assert unpacked.dtype == np.float32
    assert unpacked.shape == (1024,)
    assert unpacked.flags.writeable is True


def test_configured_index_coverage_caches_single_flight_and_invalidates_on_wal_or_text_change(tmp_path, monkeypatch):
    from app import config, server, vector_integrity
    from app.indexer import KnowledgeIndexer, pack_vector
    from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    db_path = tmp_path / "kb.db"
    KnowledgeIndexer(db_path=db_path, src_dir=src_dir, ocr_cache_dir=tmp_path / "ocr")

    vec = [1.0 / math.sqrt(1024)] * 1024
    blob = pack_vector(vec)
    vhash = hashlib.sha256(blob).hexdigest()
    t1 = "chunk one"
    t2 = "chunk two"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO documents (id, filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) "
            "VALUES (1, 'a.txt', 'A', ?, 10, 'h1', 1, 1, 0, 'indexed')",
            (str(src_dir / "a.txt"),),
        )
        for cid, txt in ((1, t1), (2, t2)):
            conn.execute(
                "INSERT INTO chunks (id, doc_id, book_title, filename, page_num, chunk_index, text, embedding) VALUES (?, 1, 'A', 'a.txt', 1, ?, ?, ?)",
                (cid, cid - 1, txt, blob),
            )
            conn.execute(
                "INSERT INTO corpus_vector_provenance (chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at) VALUES (?, ?, ?, ?, 1024, 1, '2026-11-16T00:00:00Z')",
                (cid, hashlib.sha256(txt.encode()).hexdigest(), BGE_NATIVE_IDENTITY, vhash),
            )
        conn.commit()

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(server, "sys", server.sys)
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.delenv("EMBED_BACKEND", raising=False)
    monkeypatch.setattr(NativeEmbeddingClient, "get_status_identity", lambda self: BGE_NATIVE_IDENTITY)

    scan_calls = []
    orig_index_coverage = vector_integrity.index_coverage

    def counting_scan(database, expected_identity):
        scan_calls.append(expected_identity)
        time.sleep(0.05)
        return orig_index_coverage(database, expected_identity)

    monkeypatch.setattr(vector_integrity, "index_coverage", counting_scan)

    # Concurrent cold callers must coalesce into a single DB scan.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: server._configured_index_coverage(), range(4)))
    assert len(scan_calls) == 1, f"Expected single-flight scan, got {len(scan_calls)} scans"
    assert all(r["status"] == "full" and r["compatible_chunks"] == 2 for r in results)

    # Returned dicts must not alias internal cached state.
    results[0]["compatible_chunks"] = 999
    warm = server._configured_index_coverage()
    assert len(scan_calls) == 1, f"Warm hit must not re-scan DB, got {len(scan_calls)}"
    assert warm["compatible_chunks"] == 2

    # Mutate one chunk in WAL mode (uncheckpointed) -> cache MUST invalidate immediately.
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE chunks SET text='tampered text' WHERE id=2")
        conn.commit()

    after_wal_edit = server._configured_index_coverage()
    assert len(scan_calls) == 2
    assert after_wal_edit["status"] == "partial"
    assert after_wal_edit["compatible_chunks"] == 1


def test_start_service_tolerates_cold_health_slower_than_1500ms(tmp_path, monkeypatch):
    from scripts import backend_service as bs

    class SlowColdHandler(http.server.BaseHTTPRequestHandler):
        calls = 0

        def do_GET(self):
            SlowColdHandler.calls += 1
            if SlowColdHandler.calls == 1:
                time.sleep(2.0)
            body = json.dumps({"status": "ok", "inference_ready": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowColdHandler)
    port = httpd.server_address[1]
    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()

    try:
        state_dir = tmp_path / "state"
        log_dir = tmp_path / "logs"
        state_dir.mkdir()
        log_dir.mkdir()
        cfg = {
            "config_path": tmp_path / "cfg.json",
            "code_dir": tmp_path,
            "python_bin": "python3",
            "host": "127.0.0.1",
            "port": port,
            "public_url": f"http://127.0.0.1:{port}",
            "forwarded_allow_ips": "127.0.0.1",
            "server_app": "app.server:app",
            "agent_src_dir": tmp_path,
            "agent_data_dir": tmp_path,
            "knowledge_base_db": tmp_path / "kb.db",
            "study_accounts_dir": tmp_path / "acct",
            "history_db_path": tmp_path / "hist.db",
            "timetable_db_path": tmp_path / "tt.db",
            "timetable_images_dir": tmp_path / "tt_img",
            "mlx_model_dir": tmp_path / "m1",
            "mlx_9b_model_dir": tmp_path / "m2",
            "mlx_bge_model_dir": tmp_path / "bge",
            "study_gpu_runtime_dir": tmp_path / "gpu",
            "state_dir": state_dir,
            "log_dir": log_dir,
            "study_src_auto_watch": False,
            "study_accounts_required": False,
            "study_allow_registration": False,
            "study_secure_cookies": False,
            "max_log_bytes": 4096,
            "max_log_backups": 1,
            "restart_backoff_seconds": 0.1,
            "max_restart_backoff_seconds": 0.5,
            "max_restarts": 1,
            "restart_window_seconds": 10.0,
            "startup_timeout_seconds": 8.0,
            "stop_timeout_seconds": 2.0,
        }

        class FakeProc:
            returncode = None
            def poll(self):
                return None

        monkeypatch.setattr(bs.subprocess, "Popen", lambda *a, **kw: FakeProc())
        monkeypatch.setattr(
            bs,
            "verify_managed_process",
            lambda state, **kw: (False, "not_running", None)
            if state is None or state is bs._read_json_safe(state_dir / "supervisor.json")
            else (False, "not_running", None),
        )
        # During poll loop, mark backend managed once started
        call_n = {"n": 0}
        def fake_verify(state, *, expected_cwd, required_cmd_tokens):
            call_n["n"] += 1
            if "app.server:app" in " ".join(required_cmd_tokens) and call_n["n"] > 2:
                return (True, "ok", {"pid": 12345})
            return (False, "not_running", None)

        monkeypatch.setattr(bs, "verify_managed_process", fake_verify)
        monkeypatch.setattr(bs, "detect_foreign_port_owner", lambda c, p: None)
        monkeypatch.setattr(bs, "stop_service", lambda *a, **kw: None)
        monkeypatch.setattr(
            bs,
            "get_status",
            lambda c: {"action": "status", "web_ready": True, "corpus_ready": True, "process": {"backend_pid": 12345}},
        )

        res = bs.start_service(cfg)
        assert res["action"] == "started"
        assert SlowColdHandler.calls == 1, f"Poll timed out early and retried ({SlowColdHandler.calls} calls)"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _seed_status_polling_corpus(tmp_path):
    from contextlib import closing
    from app.indexer import KnowledgeIndexer, pack_vector

    src_dir = tmp_path / "src"
    data_dir = tmp_path / "data"
    ocr_dir = data_dir / "ocr_cache"
    runtime_dir = data_dir / "runtime"
    src_dir.mkdir(parents=True, exist_ok=True)
    ocr_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "knowledge_base.db"

    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src_dir, ocr_cache_dir=ocr_dir)
    vec = [1.0 / math.sqrt(1024)] * 1024
    blob = pack_vector(vec)

    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        docs = [
            (1, "doc_ready.txt", "Ready Doc", str(src_dir / "doc_ready.txt"), 100, "h_ready", 2, 2, 0, "indexed"),
            (2, "scan_part.pdf", "Scan Partial", str(src_dir / "scan_part.pdf"), 200, "h_part", 4, 2, 1, "ocr_partial_reviewed"),
            (3, "scan_raw.pdf", "Scan Raw", str(src_dir / "scan_raw.pdf"), 300, "h_raw", 3, 0, 1, "scanned_unocred"),
            (4, "doc_stale.txt", "Stale Doc", str(src_dir / "doc_stale.txt"), 150, "h_stale", 1, 1, 0, "indexed"),
            (5, "doc_missing.txt", "Missing Doc", str(src_dir / "doc_missing.txt"), 120, "h_missing", 1, 1, 0, "indexed"),
        ]
        for row in docs:
            conn.execute(
                "INSERT INTO documents (id, filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        chunks = [
            (1, 1, "Ready Doc", "doc_ready.txt", 1, 0, "ready chunk 1", blob),
            (2, 1, "Ready Doc", "doc_ready.txt", 2, 0, "ready chunk 2", None),
            (3, 2, "Scan Partial", "scan_part.pdf", 1, 0, "scan part chunk 1", None),
            (4, 4, "Stale Doc", "doc_stale.txt", 1, 0, "stale chunk 1", blob),
            (5, 4, "Stale Doc", "doc_stale.txt", 1, 1, "stale chunk 2", None),
            (6, 5, "Missing Doc", "doc_missing.txt", 1, 0, "missing chunk 1", None),
        ]
        for row in chunks:
            conn.execute(
                "INSERT INTO chunks (id, doc_id, book_title, filename, page_num, chunk_index, text, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        conn.commit()

    (runtime_dir / "src_watcher_state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "items": {
                    "doc_stale.txt": {"rel_path": "doc_stale.txt", "status": "needs_update", "indexed_hash": "h_stale"},
                    "doc_missing.txt": {"rel_path": "doc_missing.txt", "status": "missing_source", "indexed_hash": "h_missing"},
                },
            }
        ),
        encoding="utf-8",
    )
    (ocr_dir / ("a" * 64 + ".json")).write_text(
        json.dumps(
            {
                "source_relpath": "scan_part.pdf",
                "source_hash": "h_part",
                "page_num": 2,
                "status": "CAPTURED_PENDING_REVIEW",
                "text": "pending review",
            }
        ),
        encoding="utf-8",
    )
    return src_dir, data_dir, db_path, runtime_dir, indexer


def test_status_summary_authenticated_equivalent_counts_and_detailed_contract(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import runtime_health, server, source_page_reviews
    from app.history import HistoryStore
    from app.native_embedding import NativeEmbeddingClient
    from app.src_watcher import SrcWatcher

    src_dir, data_dir, db_path, runtime_dir, indexer = _seed_status_polling_corpus(tmp_path)
    watcher = SrcWatcher(src_dir=src_dir, db_path=db_path, data_dir=data_dir, enabled_override=False)

    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(tmp_path / "accounts"))
    monkeypatch.setattr(server, "SRC_DIR", src_dir)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "indexer", indexer)
    monkeypatch.setattr(server, "src_watcher", watcher)
    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "hist.db"))
    monkeypatch.setattr(server, "_requires_mlx_model", lambda: True)
    monkeypatch.setattr(server, "probe_mlx_runtime", lambda: {"ready": True, "code": "ready", "message": "OK"})
    monkeypatch.setattr(server.TrainedModelClient, "available", lambda *a, **kw: True)
    monkeypatch.setattr(server.ollama, "check_health", lambda: False)

    gpu_state = {"state": "idle", "busy": False, "pid": None, "message": "Sẵn sàng"}
    monkeypatch.setattr(server.gpu_coordinator, "get_status", lambda: dict(gpu_state))

    # Guard against model init/load, strict vector scans, page reviews, or infra probes in summary mode.
    def fail_if_called(*args, **kwargs):
        raise AssertionError("Summary path must not invoke strict scan, model init, page review, or infra probe")

    monkeypatch.setattr(server.TrainedModelClient, "__init__", fail_if_called)
    monkeypatch.setattr(NativeEmbeddingClient, "__init__", fail_if_called)
    monkeypatch.setattr(source_page_reviews, "latest_page_reviews", fail_if_called)

    with monkeypatch.context() as m_guard:
        m_guard.setattr(indexer, "get_status", fail_if_called)
        m_guard.setattr(indexer, "_compatible_vectors_summary", fail_if_called)
        m_guard.setattr(runtime_health, "inspect_infrastructure_health", fail_if_called)

        with TestClient(server.app, client=("127.0.0.1", 54100)) as client:
            unauth = client.get("/api/status?summary=1")
            assert unauth.status_code == 401  # nosec B101

            reg = client.post(
                "/api/account/register",
                json={"username": "summary_user_a", "password": "test-only-poll-credential-1"},
                headers={"X-Study-Request": "1"},
            )
            assert reg.status_code == 200  # nosec B101

            summary_resp = client.get("/api/status?summary=1")
            assert summary_resp.status_code == 200  # nosec B101
            s_data = summary_resp.json()

            assert s_data["service_state"] == "healthy"  # nosec B101
            assert s_data["inference_ready"] is True  # nosec B101
            assert "infrastructure" not in s_data  # nosec B101
            assert "admission" not in s_data  # nosec B101
            assert "documents" not in s_data["knowledge_base"]  # nosec B101
            assert "items" not in s_data["watcher"]  # nosec B101
            assert "items" not in s_data["storage"]["watcher"]  # nosec B101
            assert "sources_dir" not in s_data["storage"]  # nosec B101
            assert "data_dir" not in s_data["storage"]  # nosec B101
            assert s_data["watcher"]["needs_update_count"] == 1  # nosec B101
            assert s_data["watcher"]["missing_source_count"] == 1  # nosec B101

            # GPU / readiness changes must never be cached inside the library summary snapshot.
            gpu_state.update({"state": "training", "busy": True, "pid": 9999, "message": "Training"})
            busy_summary = client.get("/api/status?summary=1")
            assert busy_summary.status_code == 200  # nosec B101
            b_data = busy_summary.json()
            assert b_data["gpu"]["busy"] is True  # nosec B101
            assert b_data["inference_ready"] is False  # nosec B101
            assert b_data["service_state"] == "degraded"  # nosec B101

    # Compare summary knowledge_base counts against full authenticated GET /api/status.
    gpu_state.update({"state": "idle", "busy": False, "pid": None, "message": "Sẵn sàng"})
    with TestClient(server.app, client=("127.0.0.1", 54101)) as client:
        login = client.post(
            "/api/account/login",
            json={"username": "summary_user_a", "password": "test-only-poll-credential-1"},
            headers={"X-Study-Request": "1"},
        )
        assert login.status_code == 200  # nosec B101
        full_resp = client.get("/api/status")
        assert full_resp.status_code == 200  # nosec B101
        f_data = full_resp.json()

        assert "infrastructure" in f_data  # nosec B101
        assert "admission" in f_data  # nosec B101
        assert isinstance(f_data["knowledge_base"]["documents"], list)  # nosec B101
        assert len(f_data["knowledge_base"]["documents"]) == 5  # nosec B101
        assert isinstance(f_data["watcher"]["items"], list)  # nosec B101
        assert "sources_dir" not in f_data["storage"]  # nosec B101

        for key in (
            "total_documents",
            "registered_documents",
            "searchable_documents",
            "searchable_passages",
            "scanned_documents",
            "total_indexed_pages",
            "total_chunks",
            "embedded_chunks",
            "stale_documents",
            "discovered_files",
        ):
            assert s_data["knowledge_base"][key] == f_data["knowledge_base"][key], f"Mismatch on {key}"  # nosec B101

        assert s_data["knowledge_base"]["searchable_documents"] == 2  # nosec B101
        assert s_data["knowledge_base"]["searchable_passages"] == 3  # nosec B101
        assert s_data["knowledge_base"]["stale_documents"] == 2  # nosec B101
        assert s_data["knowledge_base"]["embedded_chunks"] == 2  # nosec B101

    # Local loopback non-account request gets host paths without leaking them into cached summary or account responses.
    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "0")
    with TestClient(server.app, client=("127.0.0.1", 54102)) as local_client:
        local_sum = local_client.get("/api/status?summary=1").json()
        assert local_sum["storage"].get("sources_dir") == str(src_dir)  # nosec B101
        assert local_sum["storage"].get("data_dir") == str(data_dir)  # nosec B101
        cached_values_text = json.dumps(list(server._STATUS_SUMMARY_CACHE.values()), default=str)
        assert "sources_dir" not in cached_values_text  # nosec B101
        assert "data_dir" not in cached_values_text  # nosec B101
        assert "summary_user_a" not in cached_values_text  # nosec B101
        assert "inference_ready" not in cached_values_text  # nosec B101

    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "1")
    with TestClient(server.app, client=("127.0.0.1", 54104)) as acct_after:
        login_after = acct_after.post(
            "/api/account/login",
            json={"username": "summary_user_a", "password": "test-only-poll-credential-1"},
            headers={"X-Study-Request": "1"},
        )
        assert login_after.status_code == 200  # nosec B101
        acct_sum = acct_after.get("/api/status?summary=1").json()
        assert "sources_dir" not in acct_sum["storage"]  # nosec B101
        assert "data_dir" not in acct_sum["storage"]  # nosec B101


def test_status_summary_coalescing_wal_db_replace_and_watcher_drift(tmp_path, monkeypatch):
    from contextlib import closing
    import os
    from fastapi.testclient import TestClient
    from app import server
    from app.history import HistoryStore
    from app.indexer import KnowledgeIndexer, pack_vector
    from app.src_watcher import SrcWatcher

    src_dir, data_dir, db_path, runtime_dir, indexer = _seed_status_polling_corpus(tmp_path)
    watcher = SrcWatcher(src_dir=src_dir, db_path=db_path, data_dir=data_dir, enabled_override=False)

    monkeypatch.setenv("STUDY_ACCOUNTS_REQUIRED", "0")
    monkeypatch.setattr(server, "SRC_DIR", src_dir)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "indexer", indexer)
    monkeypatch.setattr(server, "src_watcher", watcher)
    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "hist2.db"))
    monkeypatch.setattr(server, "_requires_mlx_model", lambda: True)
    monkeypatch.setattr(server, "probe_mlx_runtime", lambda: {"ready": True, "code": "ready", "message": "OK"})
    monkeypatch.setattr(server.TrainedModelClient, "available", lambda *a, **kw: True)
    monkeypatch.setattr(server.gpu_coordinator, "get_status", lambda: {"state": "idle", "busy": False, "pid": None, "message": "Sẵn sàng"})

    summary_calls = []
    orig_summary = KnowledgeIndexer.get_status_summary

    def counting_summary(self):
        summary_calls.append(1)
        time.sleep(0.05)
        return orig_summary(self)

    monkeypatch.setattr(KnowledgeIndexer, "get_status_summary", counting_summary)

    with TestClient(server.app, client=("127.0.0.1", 54103)) as client:
        # 1. Concurrent cold requests coalesce into one summary scan.
        with ThreadPoolExecutor(max_workers=4) as pool:
            resps = list(pool.map(lambda _: client.get("/api/status?summary=1"), range(4)))
        assert all(r.status_code == 200 for r in resps)  # nosec B101
        assert len(summary_calls) == 1  # nosec B101

        # 2. Warm cache hit does not re-scan SQLite.
        warm = client.get("/api/status?summary=1").json()
        assert len(summary_calls) == 1  # nosec B101
        assert warm["knowledge_base"]["embedded_chunks"] == 2  # nosec B101

        # 3. Uncheckpointed WAL amount-only update (same row count, embedding added) invalidates snapshot
        #    and read-only summary query keeps the open WAL state intact.
        vec_blob = pack_vector([1.0 / math.sqrt(1024)] * 1024)
        wal_conn = sqlite3.connect(db_path)
        try:
            wal_conn.execute("UPDATE chunks SET embedding = ? WHERE id = 2", (vec_blob,))
            wal_conn.commit()
            after_wal = client.get("/api/status?summary=1").json()
            assert len(summary_calls) == 2  # nosec B101
            assert after_wal["knowledge_base"]["embedded_chunks"] == 3  # nosec B101
            # Repeat call hits cached snapshot cleanly even while WAL remains uncheckpointed.
            after_wal_warm = client.get("/api/status?summary=1").json()
            assert len(summary_calls) == 2  # nosec B101
            assert after_wal_warm["knowledge_base"]["embedded_chunks"] == 3  # nosec B101
        finally:
            wal_conn.close()

        # 4. Watcher drift transitions: same-size/amount-only edit -> remove -> restore -> replace.
        state_file = runtime_dir / "src_watcher_state.json"
        # Restore doc_missing.txt to indexed while keeping exact byte structure of JSON entry changed (same-size/amount-only drift change).
        payload_a = {
            "version": 1,
            "items": {
                "doc_stale.txt": {"rel_path": "doc_stale.txt", "status": "needs_update", "indexed_hash": "a" * 64},
            },
        }
        # Note: doc_stale.txt has file_hash='h_stale', so mismatch in indexed_hash keeps it non-matching; use None or 'h_stale' first.
        payload_a["items"]["doc_stale.txt"]["indexed_hash"] = "h_stale"
        state_file.write_text(json.dumps(payload_a), encoding="utf-8")
        st_one_stale = client.get("/api/status?summary=1").json()
        assert len(summary_calls) == 3  # nosec B101
        assert st_one_stale["knowledge_base"]["stale_documents"] == 1  # nosec B101
        assert st_one_stale["knowledge_base"]["searchable_documents"] == 3  # nosec B101

        # Same-size / amount-only update inside watcher state file (indexed_hash length unchanged).
        payload_same_len = {
            "version": 1,
            "items": {
                "doc_stale.txt": {"rel_path": "doc_stale.txt", "status": "needs_update", "indexed_hash": "x_stale"},
            },
        }
        assert len(json.dumps(payload_same_len)) == len(json.dumps(payload_a))  # nosec B101
        state_file.write_text(json.dumps(payload_same_len), encoding="utf-8")
        st_same_len = client.get("/api/status?summary=1").json()
        assert len(summary_calls) == 4  # nosec B101
        assert st_same_len["knowledge_base"]["stale_documents"] == 0  # nosec B101
        assert st_same_len["knowledge_base"]["searchable_documents"] == 4  # nosec B101

        # Remove watcher state file -> invalidates snapshot cleanly.
        state_file.unlink()
        st_removed = client.get("/api/status?summary=1").json()
        assert len(summary_calls) == 5  # nosec B101
        assert st_removed["knowledge_base"]["stale_documents"] == 0  # nosec B101

        # Restore watcher state file -> invalidates snapshot and restores drift count.
        state_file.write_text(json.dumps(payload_a), encoding="utf-8")
        st_restored = client.get("/api/status?summary=1").json()
        assert len(summary_calls) == 6  # nosec B101
        assert st_restored["knowledge_base"]["stale_documents"] == 1  # nosec B101

        # Atomic replacement of watcher state file -> invalidates snapshot.
        tmp_state = runtime_dir / "src_watcher_state.json.tmp"
        tmp_state.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": {
                        "doc_stale.txt": {"rel_path": "doc_stale.txt", "status": "missing_source", "indexed_hash": "h_stale"},
                        "doc_missing.txt": {"rel_path": "doc_missing.txt", "status": "missing_source", "indexed_hash": "h_missing"},
                    },
                }
            ),
            encoding="utf-8",
        )
        os.replace(tmp_state, state_file)
        st_replaced = client.get("/api/status?summary=1").json()
        assert len(summary_calls) == 7  # nosec B101
        assert st_replaced["knowledge_base"]["stale_documents"] == 2  # nosec B101

        # 5. Atomic DB replacement invalidates snapshot immediately.
        new_db_path = tmp_path / "replacement.db"
        KnowledgeIndexer(db_path=new_db_path, src_dir=src_dir, ocr_cache_dir=tmp_path / "ocr2")
        with closing(sqlite3.connect(new_db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO documents (id, filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) "
                "VALUES (1, 'new_only.txt', 'New Only', ?, 50, 'h_new', 1, 1, 0, 'indexed')",
                (str(src_dir / "new_only.txt"),),
            )
            conn.execute(
                "INSERT INTO chunks (id, doc_id, book_title, filename, page_num, chunk_index, text, embedding) "
                "VALUES (1, 1, 'New Only', 'new_only.txt', 1, 0, 'new chunk', ?)",
                (vec_blob,),
            )
            conn.commit()
        os.replace(new_db_path, db_path)
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.parent / (db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

        after_replace = client.get("/api/status?summary=1").json()
        assert len(summary_calls) == 8  # nosec B101
        assert after_replace["knowledge_base"]["total_documents"] == 1  # nosec B101
        assert after_replace["knowledge_base"]["searchable_documents"] == 1  # nosec B101
        assert after_replace["knowledge_base"]["embedded_chunks"] == 1  # nosec B101
