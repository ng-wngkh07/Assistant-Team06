import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import server
from app.document_lookup import DocumentLookup
from app.gpu_lock import GPUBusyError


@pytest.fixture
def lookup(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    db_path = tmp_path / 'knowledge.db'
    with sqlite3.connect(db_path) as db:
        db.executescript('''
            CREATE TABLE documents(id INTEGER PRIMARY KEY, filename TEXT, clean_title TEXT,
              total_pages INTEGER, status TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, book_title TEXT,
              filename TEXT, page_num INTEGER, chunk_index INTEGER, text TEXT, embedding BLOB);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(text);
        ''')
        for did, name, state in [(1, 'Sách phổ biến.pdf', 'indexed'), (2, 'Sách cần tìm.pdf', 'indexed'),
                                 (3, 'Chưa lập chỉ mục.pdf', 'pending')]:
            (src / name).write_bytes(b'%PDF-1.4\nfixture\n%%EOF')
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?)', (did, name, name, 20, state))
        for cid in range(1, 25):
            doc_id = 2 if cid == 24 else 1
            name = 'Sách cần tìm.pdf' if doc_id == 2 else 'Sách phổ biến.pdf'
            text = 'trí nhớ chú ý và thông tin'
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',
                       (cid, doc_id, name, name, 7, 0, text, struct.pack('2f', 1, 0)))
            db.execute('INSERT INTO chunks_fts(rowid,text) VALUES(?,?)', (cid, text))
    return DocumentLookup(db_path, src)


@pytest.fixture
def client(lookup, monkeypatch):
    monkeypatch.setattr(server, 'document_lookup', lookup)
    class NoHistory:
        def __getattr__(self, name):
            raise AssertionError('Document lookup touched conversation history')
    monkeypatch.setattr(server, 'history_store', NoHistory())
    return TestClient(server.app)


def test_book_filter_is_applied_before_limit(client, lookup, monkeypatch):
    monkeypatch.setattr(lookup.ollama, 'get_embedding', lambda *a, **k: pytest.fail('FTS invoked Ollama'))
    response = client.get('/api/documents/search', params={'q': 'trí nhớ', 'document_id': 2, 'limit': 1})
    assert response.status_code == 200
    rows = response.json()['results']
    assert len(rows) == 1 and rows[0]['chunk_id'] == 24
    assert rows[0]['doc_id'] == 2
    assert rows[0]['text'] == 'trí nhớ chú ý và thông tin'
    assert rows[0]['pdf_url'].endswith('/2/pdf#page=7')


def test_reviewed_partial_scan_remains_searchable_and_openable(client, lookup):
    with sqlite3.connect(lookup.db_path) as db:
        db.execute("UPDATE documents SET status='ocr_partial_reviewed' WHERE id=2")
    assert lookup.document(2) is not None
    response = client.get('/api/documents/search', params={'q':'trí nhớ','document_id':2})
    assert response.status_code == 200
    assert [r['chunk_id'] for r in response.json()['results']] == [24]
    assert client.get('/api/documents/2/pdf').status_code == 200


def test_fts_stays_available_without_gpu_or_ollama(client, monkeypatch):
    monkeypatch.setattr(server.document_lookup.ollama, 'get_embedding', lambda *a, **k: pytest.fail('FTS uses GPU'))
    monkeypatch.setattr('app.document_lookup.gpu_coordinator.acquire_for_inference', lambda: pytest.fail('FTS locks GPU'))
    assert client.get('/api/documents/search', params={'q': 'trí nhớ'}).json()['results']


def test_busy_hybrid_falls_back_without_starting_embedding(client, monkeypatch):
    @contextmanager
    def busy():
        raise GPUBusyError('training active')
        yield
    monkeypatch.setattr('app.document_lookup.gpu_coordinator.acquire_for_inference', busy)
    monkeypatch.setattr(server.document_lookup.ollama, 'get_embedding', lambda *a, **k: pytest.fail('busy GPU invoked'))
    response = client.get('/api/documents/search', params={'q': 'trí nhớ', 'method': 'hybrid'})
    assert response.status_code == 200
    assert response.json()['method'] == 'fts'
    assert response.json()['results'] and response.json()['notice']


def test_hybrid_scopes_vectors_to_selected_book(client, monkeypatch):
    # Keep this 2-dimensional legacy fixture explicit; native 1024D provenance has its own tests.
    from types import SimpleNamespace
    from app import document_lookup
    monkeypatch.setattr(document_lookup, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(document_lookup, "DEFAULT_EMBED_MODEL", "legacy-test-embedding")
    monkeypatch.setattr(server.document_lookup.ollama, 'get_embedding', lambda *a, **k: [1, 0])
    response = client.get('/api/documents/search', params={'q': 'khác', 'document_id': 2, 'method': 'hybrid', 'limit': 1})
    assert response.status_code == 200
    assert response.json()['method'] == 'hybrid'
    assert [r['doc_id'] for r in response.json()['results']] == [2]


@pytest.mark.parametrize('params,code', [({'q': ' '}, 400), ({'q': ''}, 422),
    ({'q': 'x' * 501}, 422), ({'q': 'nhớ', 'limit': 0}, 422),
    ({'q': 'nhớ', 'limit': 31}, 422), ({'q': 'nhớ', 'document_id': -1}, 422),
    ({'q': 'nhớ', 'method': 'other'}, 422), ({'q': 'nhớ', 'document_id': 99}, 404)])
def test_parameter_boundaries(client, params, code):
    assert client.get('/api/documents/search', params=params).status_code == code


def test_no_results_and_punctuation(client):
    assert client.get('/api/documents/search', params={'q': 'xyz987654'}).json()['results'] == []
    assert client.get('/api/documents/search', params={'q': '"***[]'}).json()['results'] == []


def test_unicode_pdf_inline_and_unindexed_rejected(client):
    response = client.get('/api/documents/2/pdf')
    assert response.status_code == 200 and response.content.startswith(b'%PDF')
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'].startswith('inline;')
    assert 'filename*=utf-8' in response.headers['content-disposition']
    assert client.get('/api/documents/3/pdf').status_code == 404
    assert client.get('/api/documents/99/pdf').status_code == 404
    assert [d['id'] for d in client.get('/api/documents').json()['documents']] == [2, 1]


def test_paths_unsupported_and_traversal_fail_closed(client, lookup, tmp_path):
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(b'%PDF-1.4 private')
    (lookup.src_dir / 'notes.bin').write_bytes(b'private binary')
    with sqlite3.connect(lookup.db_path) as db:
        for did, name in [(4, '../outside.pdf'), (6, 'notes.bin')]:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?)', (did, name, name, 1, 'indexed'))
    for did in [4, 6]:
        assert client.get(f'/api/documents/{did}/pdf').status_code == 404
        assert client.get('/api/documents/search', params={'q': 'nhớ', 'document_id': did}).status_code == 404
    assert len(client.get('/api/documents').json()['documents']) == 2


def test_symlink_paths_fail_closed(client, lookup, tmp_path):
    outside = tmp_path / 'outside.pdf'
    outside.write_bytes(b'%PDF-1.4 private')
    link = lookup.src_dir / 'escape.pdf'
    try:
        link.symlink_to(outside)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Creating symlinks requires Windows Developer Mode or elevated privilege')
        raise
    with sqlite3.connect(lookup.db_path) as db:
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?)', (5, 'escape.pdf', 'escape.pdf', 1, 'indexed'))
    assert client.get('/api/documents/5/pdf').status_code == 404
    assert client.get('/api/documents/search', params={'q': 'nhớ', 'document_id': 5}).status_code == 404


def test_multiformat_documents_are_available(client, lookup):
    (lookup.src_dir / 'guide.txt').write_text('nội dung hướng dẫn học tập', encoding='utf-8')
    with sqlite3.connect(lookup.db_path) as db:
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?)', (10, 'guide.txt', 'Hướng dẫn', 1, 'indexed'))
    docs = client.get('/api/documents').json()['documents']
    assert any(d['id'] == 10 for d in docs)
    assert client.get('/api/documents/10/pdf').status_code == 404
