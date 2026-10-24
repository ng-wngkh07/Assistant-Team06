"""Unlabelled old vectors must never be compared with native MLX query vectors."""
import sqlite3
import hashlib
import pytest
from app.indexer import KnowledgeIndexer, pack_vector
from app.searcher import HybridSearcher


@pytest.mark.parametrize('entrypoint', ['semantic', 'selected_page'])
def test_changed_query_model_cannot_match_vectors_from_pinned_model(tmp_path, monkeypatch, entrypoint):
    from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
    sources = tmp_path / 'sources'
    sources.mkdir()
    (sources / 'entropy.md').write_text('# Entropy\n\nEntropy measures uncertainty.\n')
    database = tmp_path / 'library.db'
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    vector = pack_vector([1.0] + [0.0] * 1023)
    with sqlite3.connect(database) as db:
        for chunk_id, text in db.execute('SELECT id,text FROM chunks').fetchall():
            db.execute('UPDATE chunks SET embedding=? WHERE id=?', (vector, chunk_id))
            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                       (chunk_id, hashlib.sha256(text.encode()).hexdigest(), BGE_NATIVE_IDENTITY,
                        hashlib.sha256(vector).hexdigest(), 1024, 1, 'fixture'))
        doc_id = db.execute('SELECT id FROM documents').fetchone()[0]
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls, *a: True))
    identity = [BGE_NATIVE_IDENTITY]

    def query_vector(self, text, **kwargs):
        if kwargs.get('expected_identity', identity[0]) != identity[0]:
            return None
        return [1.0] + [0.0] * 1023

    monkeypatch.setattr(NativeEmbeddingClient, 'get_embedding', query_vector)
    searcher = HybridSearcher(database)

    def matches():
        if entrypoint == 'semantic':
            return searcher.search_semantic('Entropy', embed_model='bge-m3-mlx-fp16')
        chunks = searcher.source_page_chunks(doc_id, 1)
        searcher.score_page_chunks(chunks, 'Entropy', doc_id, 1, embed_model='bge-m3-mlx-fp16')
        return [c for c in chunks if c.get('semantic_score')]

    assert matches()  # Same vector values are valid only with the same model basis.
    identity[0] = 'bge-m3-custom@replaced-weights'
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    assert matches() == []
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_unknown_embedding_basis_cannot_produce_native_semantic_matches(tmp_path):
    sources = tmp_path/'sources'; sources.mkdir()
    (sources/'source.md').write_text('# Entropy\n\nEntropy Shannon đo độ bất định của một phân phối xác suất.\n')
    database = tmp_path/'library.db'
    indexer = KnowledgeIndexer(database, sources)
    result = indexer.index_all(ocr=False, embed_model=None)
    assert not result.get('error_files')
    vector = pack_vector([1.0] + [0.0]*1023)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE chunks SET embedding=?', (vector,))
        original = db.execute('SELECT id,embedding FROM chunks ORDER BY id').fetchall()
    assert original

    class NativeQueries:
        def get_embedding(self, text, model):
            # Coincidental matching dimension and values are not provenance.
            return [1.0] + [0.0]*1023

    searcher = HybridSearcher(database)
    searcher.ollama = NativeQueries()
    assert searcher.search_semantic('Entropy Shannon', embed_model='bge-m3-mlx-fp16') == []
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT id,embedding FROM chunks ORDER BY id').fetchall() == original


def test_native_provenance_is_checked_for_each_returned_chunk(tmp_path, monkeypatch):
    from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
    sources = tmp_path/'sources'; sources.mkdir()
    for name in ('approved', 'unknown', 'changed'):
        (sources/f'{name}.md').write_text(f'# {name}\n\nEntropy Shannon measures uncertainty in {name}.\n')
    database = tmp_path/'library.db'
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    vector = pack_vector([1.0]+[0.0]*1023)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE chunks SET embedding=?', (vector,))
        db.execute('''CREATE TABLE IF NOT EXISTS corpus_vector_provenance(
            chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            text_sha256 TEXT NOT NULL, model_digest TEXT NOT NULL, vector_sha256 TEXT NOT NULL,
            dimensions INTEGER NOT NULL, full_text INTEGER NOT NULL, embedded_at TEXT NOT NULL)''')
        for chunk_id, filename, text in db.execute('SELECT id,filename,text FROM chunks ORDER BY id').fetchall():
            if filename == 'unknown.md':
                continue
            text_hash = hashlib.sha256((text if filename=='approved.md' else 'OLD CONTENT').encode()).hexdigest()
            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                (chunk_id, text_hash, BGE_NATIVE_IDENTITY, hashlib.sha256(vector).hexdigest(),1024,1,'fixture'))
    monkeypatch.setattr(NativeEmbeddingClient, 'get_embedding', lambda *a, **kw: [1.0]+[0.0]*1023)
    results=HybridSearcher(database).search_semantic('Entropy', embed_model='bge-m3-mlx-fp16')
    assert results and {r['filename'] for r in results} == {'approved.md'}


def test_incomplete_native_embedding_assets_are_unavailable(tmp_path):
    from app.native_embedding import NativeEmbeddingClient
    (tmp_path/'config.json').write_text('{}')
    (tmp_path/'model.safetensors.index.json').write_text('{"weight_map":{"x":"missing.safetensors"}}')
    assert NativeEmbeddingClient.available(tmp_path) is False


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='Native Mac deployment')
def test_new_native_index_records_every_vector_without_ollama(tmp_path, monkeypatch):
    from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
    from app.ollama_client import OllamaClient
    sources=tmp_path/'sources'; sources.mkdir()
    (sources/'entropy.md').write_text('# Entropy\n\nEntropy Shannon measures uncertainty.\n')
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls,*a,**kw:True))
    monkeypatch.setattr(NativeEmbeddingClient, 'get_batch_embeddings',lambda self,texts,**kw:[[1.0]+[0.0]*1023 for _ in texts])
    monkeypatch.setattr(NativeEmbeddingClient, 'get_embedding',lambda *a,**kw:[1.0]+[0.0]*1023)
    def forbidden(*a,**kw):
        raise AssertionError('Native indexing cannot probe Ollama')
    for name in ('find_best_embed_model','get_embedding','get_batch_embeddings','check_health','list_models'):
        monkeypatch.setattr(OllamaClient,name,forbidden)
    database=tmp_path/'library.db'
    report=KnowledgeIndexer(database,sources).index_all(ocr=False,embed_model='bge-m3-mlx-fp16')
    assert not report.get('error_files') and report['embedding_enabled'] is True
    with sqlite3.connect(database) as db:
        rows=db.execute('''SELECT c.text,c.embedding,p.text_sha256,p.vector_sha256,p.model_digest,p.dimensions,p.full_text
            FROM chunks c LEFT JOIN corpus_vector_provenance p ON p.chunk_id=c.id''').fetchall()
    assert rows
    for text,vector,text_hash,vector_hash,identity,dimensions,full_text in rows:
        assert vector and text_hash==hashlib.sha256(text.encode()).hexdigest()
        assert vector_hash==hashlib.sha256(vector).hexdigest()
        assert identity==BGE_NATIVE_IDENTITY and dimensions==1024 and full_text==1


@pytest.mark.parametrize('bad_vector', [
    [float('nan')]+[0.0]*1023,
    [float('inf')]+[0.0]*1023,
    [0.0]*1024,
    [2.0]+[0.0]*1023,
    [1.0]+[0.0]*511,
], ids=['nan','infinity','zero','not_normalized','wrong_dimension'])
def test_hash_bound_but_invalid_native_vector_is_not_a_semantic_match(tmp_path, monkeypatch, bad_vector):
    from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
    sources=tmp_path/'sources'; sources.mkdir()
    (sources/'source.md').write_text('# Entropy\n\nEntropy measures uncertainty.\n')
    database=tmp_path/'library.db'
    KnowledgeIndexer(database,sources).index_all(ocr=False,embed_model=None)
    valid_vector=pack_vector([1.0]+[0.0]*1023)
    vector=pack_vector(bad_vector)
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS corpus_vector_provenance(
            chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            text_sha256 TEXT NOT NULL, model_digest TEXT NOT NULL, vector_sha256 TEXT NOT NULL,
            dimensions INTEGER NOT NULL, full_text INTEGER NOT NULL, embedded_at TEXT NOT NULL)''')
        for chunk_id,text in db.execute('SELECT id,text FROM chunks').fetchall():
            db.execute('UPDATE chunks SET embedding=? WHERE id=?',(valid_vector,chunk_id))
            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                (chunk_id,hashlib.sha256(text.encode()).hexdigest(),BGE_NATIVE_IDENTITY,
                 hashlib.sha256(valid_vector).hexdigest(),1024,1,'fixture'))
    monkeypatch.setattr(NativeEmbeddingClient,'get_embedding',lambda *a,**kw:[1.0]+[0.0]*1023)
    # Positive control prevents an always-empty search from passing this safety check.
    assert HybridSearcher(database).search_semantic('Entropy',embed_model='bge-m3-mlx-fp16')
    with sqlite3.connect(database) as db:
        db.execute('UPDATE chunks SET embedding=?',(vector,))
        db.execute('UPDATE corpus_vector_provenance SET vector_sha256=?',(hashlib.sha256(vector).hexdigest(),))
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    assert HybridSearcher(database).search_semantic('Entropy',embed_model='bge-m3-mlx-fp16')==[]
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before


def test_native_embedding_identity_checks_weight_bytes_not_only_size(tmp_path, monkeypatch):
    from app import native_embedding
    files={'config.json':b'{}','tokenizer.json':b'{}'}
    for name,data in files.items():
        (tmp_path/name).write_bytes(data)
    weights=tmp_path/'model.safetensors'
    with weights.open('wb') as stream:
        stream.write(b'A'); stream.truncate(50_000_001)
    with weights.open('rb') as stream:
        original_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    metadata={name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()}
    metadata['model.safetensors']={'bytes':weights.stat().st_size,'sha256':original_hash}
    monkeypatch.setattr(native_embedding,'PINNED_BGE_FILES',metadata)
    assert native_embedding.NativeEmbeddingClient.verify_identity(tmp_path)==native_embedding.BGE_NATIVE_IDENTITY
    with weights.open('r+b') as stream:
        stream.write(b'B')
    assert weights.stat().st_size==metadata['model.safetensors']['bytes']
    assert native_embedding.NativeEmbeddingClient.verify_identity(tmp_path)!=native_embedding.BGE_NATIVE_IDENTITY


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='Native Mac deployment')
@pytest.mark.parametrize('entrypoint',['library','selected_page'])
def test_native_embeddings_reach_both_library_and_selected_page_search(tmp_path, monkeypatch, entrypoint):
    from app import document_lookup
    from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
    from app.ollama_client import OllamaClient
    sources=tmp_path/'sources'; sources.mkdir()
    (sources/'entropy.md').write_text('# Entropy\n\nEntropy Shannon measures uncertainty.\n')
    database=tmp_path/'library.db'
    KnowledgeIndexer(database,sources).index_all(ocr=False,embed_model=None)
    vector=pack_vector([1.0]+[0.0]*1023)
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS corpus_vector_provenance(
            chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            text_sha256 TEXT NOT NULL, model_digest TEXT NOT NULL, vector_sha256 TEXT NOT NULL,
            dimensions INTEGER NOT NULL, full_text INTEGER NOT NULL, embedded_at TEXT NOT NULL)''')
        for chunk_id,text in db.execute('SELECT id,text FROM chunks').fetchall():
            db.execute('UPDATE chunks SET embedding=? WHERE id=?',(vector,chunk_id))
            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                (chunk_id,hashlib.sha256(text.encode()).hexdigest(),BGE_NATIVE_IDENTITY,
                 hashlib.sha256(vector).hexdigest(),1024,1,'fixture'))
        doc_id=db.execute('SELECT id FROM documents').fetchone()[0]
    monkeypatch.setattr(NativeEmbeddingClient,'available',classmethod(lambda cls,*a,**kw:True))
    monkeypatch.setattr(NativeEmbeddingClient,'get_embedding',lambda *a,**kw:[1.0]+[0.0]*1023)
    monkeypatch.setattr(document_lookup,'DEFAULT_EMBED_MODEL','bge-m3-mlx-fp16')
    def forbidden(*a,**kw):raise AssertionError('Native document search cannot call Ollama')
    monkeypatch.setattr(OllamaClient,'get_embedding',forbidden)
    if entrypoint=='library':
        result=document_lookup.DocumentLookup(database,sources).search('unpredictability',method='hybrid')
        assert result['method']=='hybrid' and result['results']
        assert {r['filename'] for r in result['results']}=={'entropy.md'}
    else:
        searcher=HybridSearcher(database)
        chunks=searcher.source_page_chunks(doc_id,1)
        assert chunks
        searcher.score_page_chunks(chunks,'unpredictability',doc_id,1,embed_model='bge-m3-mlx-fp16')
        assert all(c.get('semantic_score')==pytest.approx(1.0) for c in chunks)


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='Native Mac deployment')
@pytest.mark.parametrize('entrypoint',['library','selected_page'])
def test_missing_native_assets_never_probe_legacy_embeddings(tmp_path, monkeypatch, entrypoint):
    from app import document_lookup
    from app.native_embedding import NativeEmbeddingClient
    from app.ollama_client import OllamaClient
    sources=tmp_path/'sources'; sources.mkdir()
    (sources/'entropy.md').write_text('# Entropy\n\nEntropy measures uncertainty.\n')
    database=tmp_path/'library.db'
    KnowledgeIndexer(database,sources).index_all(ocr=False,embed_model=None)
    monkeypatch.setattr(NativeEmbeddingClient,'available',classmethod(lambda cls,*a,**kw:False))
    monkeypatch.setattr(document_lookup,'DEFAULT_EMBED_MODEL','legacy-configured-model')
    calls=[]
    def forbidden(*a,**kw):
        calls.append((a,kw)); raise AssertionError('Missing native assets cannot trigger legacy fallback')
    monkeypatch.setattr(OllamaClient,'get_embedding',forbidden)
    if entrypoint=='library':
        result=document_lookup.DocumentLookup(database,sources).search('Entropy',method='hybrid')
        assert result['results']
    else:
        searcher=HybridSearcher(database)
        with sqlite3.connect(database) as db:doc_id=db.execute('SELECT id FROM documents').fetchone()[0]
        chunks=searcher.source_page_chunks(doc_id,1)
        searcher.score_page_chunks(chunks,'Entropy',doc_id,1,embed_model='legacy-configured-model')
        assert all(not c.get('semantic_score') for c in chunks)
    assert calls==[]


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='Native Mac deployment')
def test_native_routing_is_independent_of_injected_client_class_name(tmp_path, monkeypatch):
    from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
    sources=tmp_path/'sources';sources.mkdir()
    (sources/'entropy.md').write_text('# Entropy\n\nEntropy measures uncertainty.\n')
    database=tmp_path/'library.db'
    KnowledgeIndexer(database,sources).index_all(ocr=False,embed_model=None)
    vector=pack_vector([1.0]+[0.0]*1023)
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS corpus_vector_provenance(
            chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            text_sha256 TEXT NOT NULL, model_digest TEXT NOT NULL, vector_sha256 TEXT NOT NULL,
            dimensions INTEGER NOT NULL, full_text INTEGER NOT NULL, embedded_at TEXT NOT NULL)''')
        for chunk_id,text in db.execute('SELECT id,text FROM chunks').fetchall():
            db.execute('UPDATE chunks SET embedding=? WHERE id=?',(vector,chunk_id))
            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                (chunk_id,hashlib.sha256(text.encode()).hexdigest(),BGE_NATIVE_IDENTITY,
                 hashlib.sha256(vector).hexdigest(),1024,1,'fixture'))
    called=[]
    monkeypatch.setattr(NativeEmbeddingClient,'get_embedding',lambda *a,**kw:called.append('native') or [1.0]+[0.0]*1023)
    class NativeQueries:
        def get_embedding(self,*a,**kw):
            called.append('legacy');return [1.0]+[0.0]*1023
    searcher=HybridSearcher(database); searcher.ollama=NativeQueries()
    assert searcher.search_semantic('Entropy',embed_model='bge-m3-mlx-fp16')
    assert called==['native']


@pytest.mark.parametrize('asset', ['model.safetensors', 'tokenizer.json'])
def test_custom_embedding_identity_changes_with_actual_asset_bytes(tmp_path, asset):
    from app.native_embedding import NativeEmbeddingClient
    (tmp_path/'config.json').write_text('{"hidden_size":1024}')
    (tmp_path/'tokenizer.json').write_text('{"fixture":1}')
    (tmp_path/'model.safetensors').write_bytes(b'custom-weight-A')
    first = NativeEmbeddingClient.verify_identity(tmp_path)
    target = tmp_path/asset
    raw = target.read_bytes()
    target.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    second = NativeEmbeddingClient.verify_identity(tmp_path)
    assert first != second, 'Different vector bases cannot share a content identity'


def test_native_embedding_explicit_path_still_requires_local_runtime(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import native_embedding
    model = tmp_path/'model'; model.mkdir()
    (model/'config.json').write_text('{"hidden_size":1024}')
    (model/'tokenizer.json').write_text('{"fixture":true}')
    (model/'model.safetensors').write_bytes(b'fixture-weights')
    monkeypatch.setattr(native_embedding, 'BASE_DIR', tmp_path/'without-runtime')
    monkeypatch.setattr(native_embedding, 'sys', SimpleNamespace(platform='darwin'))
    assert native_embedding.NativeEmbeddingClient.available(model) is False


@pytest.mark.parametrize('asset', ['tokenizer_config.json', 'special_tokens_map.json'])
def test_embedding_identity_binds_tokenizer_preprocessing_assets(tmp_path, asset):
    from app.native_embedding import NativeEmbeddingClient
    (tmp_path/'config.json').write_text('{"hidden_size":1024}')
    (tmp_path/'tokenizer.json').write_text('{"fixture":1}')
    (tmp_path/'model.safetensors').write_bytes(b'custom-weights')
    (tmp_path/asset).write_text('{"fixture":"A"}')
    first = NativeEmbeddingClient.verify_identity(tmp_path)
    (tmp_path/asset).write_text('{"fixture":"B"}')
    assert NativeEmbeddingClient.verify_identity(tmp_path) != first


def test_embedding_identity_cache_cannot_hide_same_size_replacement(tmp_path):
    import os
    from app.native_embedding import NativeEmbeddingClient
    (tmp_path/'config.json').write_text('{"hidden_size":1024}')
    (tmp_path/'tokenizer.json').write_text('{"fixture":1}')
    weights = tmp_path/'model.safetensors'
    weights.write_bytes(b'custom-weight-A')
    first = NativeEmbeddingClient.verify_identity(tmp_path)
    before = weights.stat()
    weights.write_bytes(b'custom-weight-B')
    os.utime(weights, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert weights.stat().st_size == before.st_size
    assert weights.stat().st_mtime_ns == before.st_mtime_ns
    assert NativeEmbeddingClient.verify_identity(tmp_path) != first


def test_embedding_worker_and_parent_use_the_same_actual_vector_basis(tmp_path, monkeypatch):
    import runpy
    import socket
    import sys
    import types
    from pathlib import Path
    from app.native_embedding import NativeEmbeddingClient
    # Import the real worker contract without loading Metal or model weights.
    fake_core = types.ModuleType('mlx.core')
    fake_mlx = types.ModuleType('mlx'); fake_mlx.core = fake_core
    fake_embeddings = types.ModuleType('mlx_embeddings')
    fake_embeddings.load = lambda *a, **kw: pytest.fail('Identity lookup cannot load a model')
    monkeypatch.setitem(sys.modules, 'mlx', fake_mlx)
    monkeypatch.setitem(sys.modules, 'mlx.core', fake_core)
    monkeypatch.setitem(sys.modules, 'mlx_embeddings', fake_embeddings)
    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
        monkeypatch.setenv(name, '1')
    worker = runpy.run_path(str(Path(__file__).resolve().parents[1]/'app/mlx_embedding.py'), run_name='identity_contract')
    (tmp_path/'config.json').write_text('{"hidden_size":1024}')
    (tmp_path/'tokenizer.json').write_text('{"fixture":1}')
    weights = tmp_path/'model.safetensors'; weights.write_bytes(b'custom-weight-A')
    first = worker['determine_identity'](tmp_path)
    weights.write_bytes(b'custom-weight-B')
    second = worker['determine_identity'](tmp_path)
    assert second != first, 'Worker cannot identify different weights from config alone'
    assert second == NativeEmbeddingClient.verify_identity(tmp_path)


def test_pinned_embedding_cache_rechecks_replaced_weight_bytes(tmp_path, monkeypatch):
    import os
    from app import native_embedding
    assets = {'config.json': b'{}', 'tokenizer.json': b'{}', 'model.safetensors': b'pinned-weight-A'}
    for name, raw in assets.items():
        (tmp_path/name).write_bytes(raw)
    monkeypatch.setattr(native_embedding, 'PINNED_BGE_FILES', {
        name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        for name, raw in assets.items()
    })
    assert native_embedding.NativeEmbeddingClient.verify_identity(tmp_path) == native_embedding.BGE_NATIVE_IDENTITY
    weights = tmp_path/'model.safetensors'; before = weights.stat()
    weights.write_bytes(b'pinned-weight-B')
    os.utime(weights, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert weights.stat().st_size == before.st_size
    assert weights.stat().st_mtime_ns == before.st_mtime_ns
    assert native_embedding.NativeEmbeddingClient.verify_identity(tmp_path) != native_embedding.BGE_NATIVE_IDENTITY
