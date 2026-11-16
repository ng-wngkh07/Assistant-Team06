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
