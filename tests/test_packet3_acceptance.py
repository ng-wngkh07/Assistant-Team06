"""Synthetic catalog/cache regressions; served model acceptance is separate."""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_CHAT_MODEL
from app.document_lookup import DocumentLookup
from app.learning import LearningService, _is_theory_chunk
from app.searcher import HybridSearcher
from app.server import app


def test_is_theory_chunk_rejects_noise_and_preserves_substantive_headings():
    """Theory check must reject covers, conventions, glossaries, TOCs without bypassing."""
    cover_text = "EXAMPLE UNIVERSITY\nCOURSE NOTES\nLẬP TRÌNH HƯỚNG ĐỐI TƯỢNG\nTài liệu minh họa"
    assert _is_theory_chunk(cover_text) is False

    convention_text = "EXAMPLE COURSE\nTrang 2\n0. Quy ước trình bày\nCách đọc ký hiệu trong ghi chú minh họa."
    assert _is_theory_chunk(convention_text) is False

    glossary_text = "EXAMPLE COURSE\nTrang 10\nGlossary\nDanh sách thuật ngữ tiếng Anh và tiếng Việt."
    assert _is_theory_chunk(glossary_text) is False

    toc_with_theory_heading = "# Mục lục\n1. Lý thuyết ......... 15\n2. Bài tập ........... 30"
    assert _is_theory_chunk(toc_with_theory_heading) is False

    substantive_theory = (
        "Tính đóng gói trong lập trình hướng đối tượng\n\n"
        "Một lớp đặt trạng thái và thao tác xử lý trạng thái vào cùng một đơn vị. "
        "Các hàm công khai tạo giao diện truy cập; dữ liệu riêng được bảo vệ "
        "để người gọi không tùy ý phá vỡ điều kiện hợp lệ của đối tượng."
    )
    assert _is_theory_chunk(substantive_theory) is True


def test_catalog_cache_invalidates_on_same_length_update_delete_and_wal(tmp_path):
    """Cache must observe actual SQLite modifications on same-length text updates."""
    old_text = "Lập trình hướng đối tượng nghiên cứu tính đa hình.".ljust(90)
    new_text = "Lập trình đồng thời nghiên cứu mutual exclusion.".ljust(90)

    for mode in ("DELETE", "WAL"):
        d = tmp_path / f"cache-{mode}"
        d.mkdir(parents=True, exist_ok=True)
        src = d / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "notes.txt").write_text(old_text)

        db_path = d / "kb.db"
        with sqlite3.connect(db_path) as writer:
            writer.execute(f"PRAGMA journal_mode={mode}")
            writer.executescript(
                """CREATE TABLE documents(id INTEGER PRIMARY KEY,filename TEXT,clean_title TEXT,total_pages INT,status TEXT,file_hash TEXT,updated_at TEXT);
                   CREATE TABLE chunks(id INTEGER PRIMARY KEY,doc_id INT,filename TEXT,book_title TEXT,page_num INT,chunk_index INT,text TEXT);"""
            )
            writer.execute("INSERT INTO documents VALUES(1,'notes.txt','Notes',1,'indexed','unchanged','unchanged')")
            writer.execute("INSERT INTO chunks VALUES(1,1,'notes.txt','Notes',1,0,?)", (old_text,))
            writer.commit()

        lookup = DocumentLookup(db_path, src)
        service = LearningService(lookup, None)

        def labels():
            return sorted(x["label"] for x in service.topics()["topics"])

        before = labels()
        assert "Lập trình hướng đối tượng" in before

        with sqlite3.connect(db_path) as writer:
            writer.execute("UPDATE chunks SET text=? WHERE id=1", (new_text,))
            writer.commit()

        after = labels()
        assert "Lập trình đồng thời" in after
        assert "Lập trình hướng đối tượng" not in after

        # Verify source file unlinking removes document from catalog
        (src / "notes.txt").unlink()
        unavailable = labels()
        assert not unavailable


def test_health_reports_truthful_index_coverage(tmp_path, monkeypatch):
    """Health check endpoint exposes honest coverage ratio, total chunks, and status."""
    from app import config, server
    database=tmp_path/'empty.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE chunks(id INTEGER PRIMARY KEY,embedding BLOB)')
    monkeypatch.setattr(config,'DB_PATH',database)
    monkeypatch.setattr(server.TrainedModelClient,'available',classmethod(lambda *a:False))
    monkeypatch.setattr(server,'probe_mlx_runtime',lambda **kw:{'ready':False,'message':'fixture'})
    monkeypatch.setattr(server.ollama,'check_health',lambda:False)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "index_coverage" in data
    cov = data["index_coverage"]
    assert "total_chunks" in cov
    assert "compatible_native_chunks" in cov
    assert "coverage_ratio" in cov
    assert cov["status"] in {"full", "partial", "keyword_only"}
    assert isinstance(cov["degraded_fallback"], bool)


def test_health_rejects_vectors_when_actual_native_artifact_has_changed(tmp_path, monkeypatch):
    import hashlib
    import sys
    from app import config, server
    from app.indexer import pack_vector
    from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient
    database = tmp_path / 'coverage.db'
    text = 'A permitted source passage with a complete current vector.'
    blob = pack_vector([1.] + [0.] * 1023)
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE chunks(id INTEGER PRIMARY KEY,text TEXT,embedding BLOB)')
        db.execute('CREATE TABLE corpus_vector_provenance(chunk_id INTEGER,text_sha256 TEXT,model_digest TEXT,vector_sha256 TEXT,dimensions INTEGER,full_text INTEGER)')
        db.execute('INSERT INTO chunks VALUES(1,?,?)', (text, blob))
        db.execute('INSERT INTO corpus_vector_provenance VALUES(1,?,?,?,1024,1)',
                   (hashlib.sha256(text.encode()).hexdigest(), BGE_NATIVE_IDENTITY, hashlib.sha256(blob).hexdigest()))
    monkeypatch.setattr(config, 'DB_PATH', database)
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(server.TrainedModelClient, 'available', classmethod(lambda *a: False))
    monkeypatch.setattr(server, 'probe_mlx_runtime', lambda **kw: {'ready': False, 'message': 'fixture'})
    monkeypatch.setattr(NativeEmbeddingClient, 'get_identity', lambda self: BGE_NATIVE_IDENTITY)
    client = TestClient(app)
    assert client.get('/api/health').json()['index_coverage']['status'] == 'full'
    monkeypatch.setattr(NativeEmbeddingClient, 'get_identity', lambda self: 'bge-m3-custom@changed')
    coverage = client.get('/api/health').json()['index_coverage']
    assert coverage['total_chunks'] == 1 and coverage['compatible_chunks'] == 0
    assert coverage['status'] == 'keyword_only' and coverage['degraded_fallback'] is True


def test_keyword_search_excludes_unrelated_subjects(tmp_path):
    """An isolated authored corpus exercises actual SQLite/FTS, without a GPU."""
    from app.indexer import KnowledgeIndexer
    sources=tmp_path/'src'; sources.mkdir()
    (sources/'objects.md').write_text('# Lập trình hướng đối tượng\n\nLập trình hướng đối tượng sử dụng lớp và đối tượng; đóng gói bảo vệ trạng thái.\n')
    (sources/'calculus.md').write_text('# Calculus\n\nĐạo hàm mô tả tốc độ biến thiên; tích phân mô tả sự tích lũy.\n')
    (sources/'cuecards.md').write_text('# Cue cards\n\nEnglish speaking practice describes a favorite place.\n')
    database=tmp_path/'kb.db'
    KnowledgeIndexer(database,sources).index_all(ocr=False,embed_model=None)
    results=HybridSearcher(database).search_hybrid('lập trình hướng đối tượng',top_k=5,embed_model=None)
    assert results
    assert {r['filename'] for r in results} == {'objects.md'}
