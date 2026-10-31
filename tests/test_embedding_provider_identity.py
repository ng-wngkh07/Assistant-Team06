"""Provider routing simulation and artifact/text integrity; Windows runtime is separate."""
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from app.indexer import KnowledgeIndexer, pack_vector
from app.searcher import HybridSearcher


@pytest.mark.parametrize('entrypoint', ['semantic','selected_page'])
def test_windows_bge_uses_local_ollama_and_never_compares_other_artifacts(tmp_path, monkeypatch, entrypoint):
    from app import searcher
    sources=tmp_path/'sources'; sources.mkdir()
    (sources/'entropy.md').write_text('Entropy measures uncertainty in an information source.\n')
    database=tmp_path/'library.db'
    KnowledgeIndexer(database,sources).index_all(ocr=False,embed_model=None)
    identity='ollama-sha256:'+'a'*64
    blob=pack_vector([1.0]+[0.0]*1023)
    with sqlite3.connect(database) as db:
        for chunk_id,text in db.execute('SELECT id,text FROM chunks').fetchall():
            db.execute('UPDATE chunks SET embedding=? WHERE id=?',(blob,chunk_id))
            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                       (chunk_id,hashlib.sha256(text.encode()).hexdigest(),identity,
                        hashlib.sha256(blob).hexdigest(),1024,1,'fixture'))
        doc_id=db.execute('SELECT id FROM documents').fetchone()[0]
    calls=[]
    current=[identity]
    client=SimpleNamespace(embedding_identity=lambda model:current[0],
        get_embedding=lambda text,model:(calls.append(model) or [1.0]+[0.0]*1023))
    service=HybridSearcher(database); service.ollama=client
    monkeypatch.setattr(searcher,'sys',SimpleNamespace(platform='win32'))
    def matches():
        if entrypoint=='semantic': return service.search_semantic('Entropy',embed_model='bge-m3')
        chunks=service.source_page_chunks(doc_id,1)
        service.score_page_chunks(chunks,'Entropy',doc_id,1,embed_model='bge-m3')
        return [c for c in chunks if c.get('semantic_score')]
    assert matches() and calls == ['bge-m3']
    current[0]='ollama-sha256:'+'b'*64
    assert matches() == []
    current[0]=identity
    with sqlite3.connect(database) as db:
        db.execute("UPDATE chunks SET text='Changed extraction without a matching vector'")
    assert matches() == []


def test_ollama_embedding_identity_requires_exact_tag_and_artifact_digest(monkeypatch):
    from app.ollama_client import OllamaClient
    client=OllamaClient()
    monkeypatch.setattr(client,'_verify_model_locality',lambda model:None)
    monkeypatch.setattr(client,'list_models',lambda:[{'name':'bge-m3:latest','digest':'a'*64}])
    assert client.embedding_identity('bge-m3') == 'ollama-sha256:'+'a'*64
    assert client.embedding_identity('bge-m3:other') is None
    monkeypatch.setattr(client,'list_models',lambda:[{'name':'bge-m3:latest','digest':'unknown'}])
    assert client.embedding_identity('bge-m3') is None


@pytest.mark.parametrize('entrypoint', ['semantic', 'selected_page'])
def test_unavailable_embedding_identity_cannot_break_keyword_retrieval(tmp_path, monkeypatch, entrypoint):
    from app import searcher
    sources = tmp_path/'sources'; sources.mkdir()
    (sources/'entropy.md').write_text('Entropy measures uncertainty in an information source.\n')
    database = tmp_path/'library.db'
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    service = HybridSearcher(database)
    monkeypatch.setattr(searcher, 'sys', SimpleNamespace(platform='win32'))
    def unavailable(model):
        raise RuntimeError('Local embedding service is unavailable')
    service.ollama = SimpleNamespace(embedding_identity=unavailable)
    if entrypoint == 'semantic':
        found = service.search_hybrid('Entropy', embed_model='bge-m3')
        assert found and all(c['source_type'] != 'semantic' for c in found)
    else:
        with sqlite3.connect(database) as db:
            doc_id = db.execute('SELECT id FROM documents').fetchone()[0]
        chunks = service.source_page_chunks(doc_id, 1)
        service.score_page_chunks(chunks, 'Entropy', doc_id, 1, embed_model='bge-m3')
        assert chunks and all('semantic_score' not in c for c in chunks)


@pytest.mark.parametrize('entrypoint', ['semantic', 'selected_page'])
def test_provider_replacement_during_query_cannot_score_old_index(tmp_path, monkeypatch, entrypoint):
    from app import searcher
    sources = tmp_path / 'sources'; sources.mkdir()
    (sources / 'entropy.md').write_text('Entropy measures uncertainty in an information source.\n')
    database = tmp_path / 'library.db'
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    identity = 'ollama-sha256:' + 'a' * 64
    blob = pack_vector([1.] + [0.] * 1023)
    with sqlite3.connect(database) as db:
        cid, did, text = db.execute('SELECT id,doc_id,text FROM chunks').fetchone()
        db.execute('UPDATE chunks SET embedding=?', (blob,))
        db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                   (cid, hashlib.sha256(text.encode()).hexdigest(), identity,
                    hashlib.sha256(blob).hexdigest(), 1024, 1, 'fixture'))
    current = [identity]
    def embed(text, model):
        current[0] = 'ollama-sha256:' + 'b' * 64
        return [1.] + [0.] * 1023
    service = HybridSearcher(database)
    service.ollama = SimpleNamespace(embedding_identity=lambda model: current[0], get_embedding=embed)
    monkeypatch.setattr(searcher, 'sys', SimpleNamespace(platform='win32'))
    if entrypoint == 'semantic':
        assert service.search_semantic('Entropy', embed_model='bge-m3') == []
    else:
        chunks = service.source_page_chunks(did, 1)
        service.score_page_chunks(chunks, 'Entropy', did, 1, embed_model='bge-m3')
        assert chunks and all('semantic_score' not in c for c in chunks)


def test_search_on_new_installation_initializes_empty_schema_without_indexing(tmp_path, monkeypatch):
    from app.pdf_extractor import PDFExtractor
    monkeypatch.setattr(PDFExtractor, 'extract_file', lambda *a, **kw: pytest.fail('Search initiated indexing'))
    service = HybridSearcher(tmp_path / 'empty-library.db')
    assert service.search_fts('Entropy') == []
    with service.get_connection() as db:
        assert db.execute('SELECT count(*) FROM documents').fetchone()[0] == 0
