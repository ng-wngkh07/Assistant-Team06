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


def test_attachment_filename_does_not_select_a_similar_document(lookup):
    # An explicit attachment is a source identity, not a search query. A stale
    # name must not silently select a different edition through SQL LIKE.
    assert lookup.document_by_filename('Sách cần tìm - missing edition.pdf') is None
    assert lookup.document_by_filename('Sách cần.pdf') is None
    assert lookup.document_by_filename('%') is None
    assert lookup.document_by_filename('Sách cần tìm.pdf')['id'] == 2


def test_attachment_path_does_not_fall_back_to_another_folder(lookup):
    assert lookup.document_by_filename('missing-folder/Sách cần tìm.pdf') is None


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


def test_hybrid_scopes_vectors_to_selected_book(client, lookup, monkeypatch):
    # Both books have valid current-contract provenance. Filter the selected
    # book before applying the limit; another book's earlier ID must not win.
    from types import SimpleNamespace
    from app import document_lookup
    monkeypatch.setattr(document_lookup, "sys", SimpleNamespace(platform="win32"))
    identity = 'ollama-sha256:' + 'a' * 64
    vector = _library_vector_provenance(lookup, identity)
    with sqlite3.connect(lookup.db_path) as db:
        blob = db.execute('SELECT embedding FROM chunks WHERE id=1').fetchone()[0]
        db.execute('UPDATE chunks SET embedding=? WHERE id=24', (blob,))
        db.execute('INSERT INTO corpus_vector_provenance SELECT 24,text_sha256,model_digest,vector_sha256,dimensions,full_text FROM corpus_vector_provenance WHERE chunk_id=1')
    monkeypatch.setattr(lookup.ollama, 'embedding_identity', lambda model: identity)
    monkeypatch.setattr(lookup.ollama, 'get_embedding', lambda *a, **k: vector)
    response = client.get('/api/documents/search', params={'q': 'khác', 'document_id': 2, 'method': 'hybrid', 'limit': 1})
    assert response.status_code == 200
    assert response.json()['method'] == 'hybrid'
    assert [r['doc_id'] for r in response.json()['results']] == [2]
    assert [r['chunk_id'] for r in response.json()['results']] == [24]


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


def test_optional_library_embedding_failure_keeps_keyword_results(lookup, monkeypatch):
    from app import document_lookup as module
    from types import SimpleNamespace
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(lookup.ollama, 'embedding_identity', lambda model: 'ollama-sha256:' + 'a' * 64)
    def unavailable(*args, **kwargs):
        raise RuntimeError('local model worker cannot start')
    monkeypatch.setattr(lookup.ollama, 'get_embedding', unavailable)
    result = lookup.search('trí nhớ', method='hybrid')
    assert result['results']
    assert result['method'] == 'fts'
    assert result['notice']


def test_library_hybrid_never_scores_unprovenanced_vectors(lookup, monkeypatch):
    from app import document_lookup as module
    from types import SimpleNamespace
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(lookup.ollama, 'embedding_identity', lambda model: 'ollama-sha256:' + 'a' * 64)
    monkeypatch.setattr(lookup.ollama, 'get_embedding', lambda *args, **kwargs: [1., 0.])
    # No lexical match. The fixture's raw vectors have no model/text provenance.
    result = lookup.search('unrelated-search', method='hybrid')
    assert result['results'] == []
    assert result['method'] == 'fts'


def test_library_hybrid_rejects_changed_native_basis(lookup, monkeypatch):
    from app import document_lookup as module
    from app.native_embedding import NativeEmbeddingClient
    from types import SimpleNamespace
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(NativeEmbeddingClient, 'available', lambda *args: True)
    monkeypatch.setattr(NativeEmbeddingClient, 'get_identity', lambda *args: 'changed-basis')
    monkeypatch.setattr(NativeEmbeddingClient, 'get_embedding', lambda *args, **kwargs: pytest.fail('Changed basis started inference'))
    result = lookup.search('trí nhớ', method='hybrid')
    assert result['results']
    assert result['method'] == 'fts'


def test_explicit_missing_attachment_does_not_inherit_old_session(lookup, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(server, 'document_lookup', lookup)
    monkeypatch.setattr(server, 'history_store', SimpleNamespace(get_session=lambda sid: {
        'turns': [{'search_scope': {'document_id': 1, 'attachment_filename': 'Sách phổ biến.pdf'}}]}))
    req = server.ChatRequest(query='Nội dung tài liệu này là gì?', attachment_filename='missing.pdf', session_id='audit-only')
    with pytest.raises(Exception) as caught:
        server.requested_source_scope(req)
    assert getattr(caught.value, 'status_code', None) == 404


def _library_vector_provenance(lookup, identity):
    import hashlib
    vector = struct.pack('1024f', 1., *([0.] * 1023))
    with sqlite3.connect(lookup.db_path) as db:
        text = db.execute('SELECT text FROM chunks WHERE id=1').fetchone()[0]
        db.execute('CREATE TABLE corpus_vector_provenance(chunk_id INTEGER PRIMARY KEY, text_sha256 TEXT, model_digest TEXT, vector_sha256 TEXT, dimensions INTEGER, full_text INTEGER)')
        db.execute('UPDATE chunks SET embedding=? WHERE id=1', (vector,))
        db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?)', (1, hashlib.sha256(text.encode()).hexdigest(), identity, hashlib.sha256(vector).hexdigest(), 1024, 1))
    return [1.] + [0.] * 1023


@pytest.mark.parametrize('platform', ['darwin', 'win32'])
def test_library_current_basis_provenance_is_retrievable(lookup, monkeypatch, platform):
    from app import document_lookup as module
    from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient
    from types import SimpleNamespace
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform=platform))
    identity = BGE_NATIVE_IDENTITY if platform == 'darwin' else 'ollama-sha256:' + 'a' * 64
    vector = _library_vector_provenance(lookup, identity)
    if platform == 'darwin':
        monkeypatch.setattr(NativeEmbeddingClient, 'available', lambda *args: True)
        monkeypatch.setattr(NativeEmbeddingClient, 'get_identity', lambda *args: identity)
        def query_embedding(self, query, *, expected_identity):
            assert expected_identity == identity
            return vector
        monkeypatch.setattr(NativeEmbeddingClient, 'get_embedding', query_embedding)
    else:
        monkeypatch.setattr(lookup.ollama, 'embedding_identity', lambda model: identity)
        monkeypatch.setattr(lookup.ollama, 'get_embedding', lambda *args, **kwargs: vector)
    result = lookup.search('unrelated-search', method='hybrid')
    assert [row['chunk_id'] for row in result['results']] == [1]
    assert result['method'] == 'hybrid'


@pytest.mark.parametrize('mutation', ['text_hash', 'vector_hash', 'identity', 'full_text', 'nonfinite', 'norm'])
def test_library_rejects_corrupted_or_incompatible_semantic_rows(lookup, monkeypatch, mutation):
    import hashlib
    from app import document_lookup as module
    from types import SimpleNamespace
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    identity = 'ollama-sha256:' + 'a' * 64
    vector = _library_vector_provenance(lookup, identity)
    monkeypatch.setattr(lookup.ollama, 'embedding_identity', lambda model: identity)
    monkeypatch.setattr(lookup.ollama, 'get_embedding', lambda *args, **kwargs: vector)
    with sqlite3.connect(lookup.db_path) as db:
        if mutation == 'text_hash':
            db.execute('UPDATE chunks SET text=? WHERE id=1', ('Changed source body',))
        elif mutation == 'vector_hash':
            db.execute('UPDATE chunks SET embedding=? WHERE id=1', (struct.pack('1024f', *reversed(vector)),))
        elif mutation == 'identity':
            db.execute('UPDATE corpus_vector_provenance SET model_digest=?', ('other-artifact',))
        elif mutation == 'full_text':
            db.execute('UPDATE corpus_vector_provenance SET full_text=0')
        else:
            invalid = struct.pack('1024f', float('nan') if mutation == 'nonfinite' else 2., *([0.] * 1023))
            db.execute('UPDATE chunks SET embedding=? WHERE id=1', (invalid,))
            db.execute('UPDATE corpus_vector_provenance SET vector_sha256=?', (hashlib.sha256(invalid).hexdigest(),))
    assert lookup.search('unrelated-search', method='hybrid')['results'] == []


def test_conflicting_attachment_and_document_id_is_rejected(lookup, monkeypatch):
    monkeypatch.setattr(server, 'document_lookup', lookup)
    req = server.ChatRequest(query='Nội dung tài liệu này là gì?', source_document_id=2,
                             attachment_filename='Sách phổ biến.pdf')
    with pytest.raises(Exception) as caught:
        server.requested_source_scope(req)
    assert getattr(caught.value, 'status_code', None) == 400
