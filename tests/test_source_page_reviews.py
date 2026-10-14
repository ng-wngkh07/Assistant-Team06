import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

from app.corpus_readiness import audit_corpus
from app.indexer import KnowledgeIndexer, pack_vector


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reviewed_corpus(tmp_path):
    import pymupdf
    src = tmp_path/'src'; src.mkdir()
    source = src/'scanned.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page(); page.insert_text((40, 40), 'Original native text to check.')
        pdf.save(source)
    folder = tmp_path/'data/evaluation/corpus-completion-20261004'
    folder.mkdir(parents=True)
    image = folder/'page.png'
    with pymupdf.open(source) as pdf:
        pdf[0].get_pixmap().save(image)
    raw = folder/'raw.json'
    raw.write_text(json.dumps({'source_relpath':'scanned.pdf','page_num':1,
                              'source_hash':digest(source),'image_hash':digest(image)}))
    review = dict(filename='scanned.pdf', page=1, source_sha256=digest(source),
                  image_path=str(image), image_sha256=digest(image), raw_capture_path=str(raw),
                  raw_capture_sha256=digest(raw), reviewed_at='2026-10-04T00:00:00Z',
                  image_reviewed=True, reviewer='direct visual review', transcript='Verified text with equation x = 2.',
                  editorial='', unresolved=[], completeness='complete', kind='printed')
    reviews = folder/'source-reviews'/digest(source); reviews.mkdir(parents=True)
    old = reviews/'001-old.json'; old.write_text(json.dumps(review))
    idx = KnowledgeIndexer(tmp_path/'data/knowledge_base.db', src)
    idx.index_all(embed_model=None)
    handwriting = tmp_path/'data/evaluation/handwriting-complete-20261004'; handwriting.mkdir()
    (handwriting/'coverage.json').write_text('{"documents":[]}')
    runtime = tmp_path/'data/runtime'; runtime.mkdir()
    (runtime/'corpus_completion_policy.json').write_text(json.dumps({'require_complete_corpus':True,'embedding_model_digest':'pinned'}))
    with idx.get_connection() as db:
        db.execute('CREATE TABLE corpus_source_pages(filename,page_num,source_sha256,state,chunks_sha256,review_path,review_sha256,checked_at,PRIMARY KEY(filename,page_num))')
        db.execute('CREATE TABLE corpus_vector_provenance(chunk_id,text_sha256,model_digest,vector_sha256,dimensions,full_text,embedded_at)')
        texts = [r['text'] for r in db.execute('SELECT text FROM chunks ORDER BY chunk_index')]
        db.execute('INSERT INTO corpus_source_pages VALUES(?,?,?,?,?,?,?,?)',('scanned.pdf',1,digest(source),'text_verified',hashlib.sha256(json.dumps(texts,ensure_ascii=False).encode()).hexdigest(),str(old),digest(old),'now'))
        for r in db.execute('SELECT id,text FROM chunks').fetchall():
            blob = pack_vector([1.]+[0.]*1023)
            db.execute('UPDATE chunks SET embedding=? WHERE id=?',(blob,r['id']))
            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',(r['id'],hashlib.sha256(r['text'].encode()).hexdigest(),'pinned',hashlib.sha256(blob).hexdigest(),1024,1,'now'))
    return idx, review, old, reviews


def test_new_printed_review_blocks_superseded_index(tmp_path):
    _, review, _, reviews = reviewed_corpus(tmp_path)
    review['reviewed_at'] = '2026-10-04T01:00:00Z'
    review['transcript'] = 'A corrected sign: x = -2.'
    (reviews/'001-new.json').write_text(json.dumps(review))
    report = audit_corpus(tmp_path)
    assert not report['complete']
    assert any(f['kind']=='superseded_page_review' for f in report['failures'])


def test_review_for_another_page_cannot_authorize_training(tmp_path):
    idx, review, old, _ = reviewed_corpus(tmp_path)
    review['page'] = 2
    old.write_text(json.dumps(review))
    with idx.get_connection() as db:
        db.execute('UPDATE corpus_source_pages SET review_sha256=?',(digest(old),))
    report = audit_corpus(tmp_path)
    assert not report['complete']
    assert any(f['kind']=='review_not_complete_or_bound' for f in report['failures'])


def test_page_sync_publishes_printed_review_instead_of_native_text(tmp_path, monkeypatch):
    idx, review, _, _ = reviewed_corpus(tmp_path)
    spec = importlib.util.spec_from_file_location('completion_test',Path(__file__).resolve().parents[1]/'scripts/complete_corpus.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'OUT',tmp_path/'data/evaluation/corpus-completion-20261004')
    monkeypatch.setattr(module,'KnowledgeIndexer',lambda:idx)
    module.page_coverage()
    with idx.get_connection() as db:
        texts = [r['text'] for r in db.execute('SELECT text FROM chunks ORDER BY chunk_index')]
        record = db.execute('SELECT * FROM corpus_source_pages').fetchone()
    assert any(review['transcript'] in t for t in texts)
    assert record['review_path']
    assert record['state']=='text_verified'
    with idx.get_connection() as db:
        for row in db.execute('SELECT id,text FROM chunks').fetchall():
            blob = pack_vector([1.]+[0.]*1023)
            db.execute('UPDATE chunks SET embedding=? WHERE id=?',(blob,row['id']))
            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',(row['id'],hashlib.sha256(row['text'].encode()).hexdigest(),'pinned',hashlib.sha256(blob).hexdigest(),1024,1,'now'))
    assert audit_corpus(tmp_path)['complete']


@pytest.mark.parametrize('fault', ['raw_page','unresolved','image_not_reviewed'])
def test_invalid_visual_evidence_cannot_authorize_training(tmp_path, fault):
    idx, review, old, _ = reviewed_corpus(tmp_path)
    if fault == 'raw_page':
        raw = Path(review['raw_capture_path']); data = json.loads(raw.read_text())
        data['page_num'] = 2; raw.write_text(json.dumps(data))
        review['raw_capture_sha256'] = digest(raw)
    elif fault == 'unresolved':
        review['unresolved'] = ['Unreadable equation sign']
    else:
        review['image_reviewed'] = False
    old.write_text(json.dumps(review))
    with idx.get_connection() as db:
        db.execute('UPDATE corpus_source_pages SET review_sha256=?',(digest(old),))
    report = audit_corpus(tmp_path)
    assert not report['complete']
    assert any(f['kind']=='review_not_complete_or_bound' for f in report['failures'])


def test_matching_hash_of_unpublished_text_is_insufficient(tmp_path):
    idx, _, _, _ = reviewed_corpus(tmp_path)
    # Reindexing now honors the review; explicitly simulate a stale published page
    # whose own text/vector hashes are internally consistent but not the review.
    with idx.get_connection() as db:
        db.execute("UPDATE chunks SET text='Original native text to check.'")
        texts = [r['text'] for r in db.execute('SELECT text FROM chunks ORDER BY chunk_index')]
        db.execute('UPDATE corpus_source_pages SET chunks_sha256=?',
                   (hashlib.sha256(json.dumps(texts,ensure_ascii=False).encode()).hexdigest(),))
        db.execute('UPDATE corpus_vector_provenance SET text_sha256=?',
                   (hashlib.sha256(texts[0].encode()).hexdigest(),))
    report = audit_corpus(tmp_path)
    assert not report['complete']
    assert any(f['kind']=='review_text_not_indexed' for f in report['failures'])


def test_new_review_cannot_be_ignored_by_native_coverage(tmp_path):
    idx, _, _, _ = reviewed_corpus(tmp_path)
    with idx.get_connection() as db:
        db.execute("UPDATE corpus_source_pages SET review_path='',review_sha256=''")
    report = audit_corpus(tmp_path)
    assert not report['complete']
    assert any(f['kind']=='review_not_indexed' for f in report['failures'])


def test_visual_state_needs_bound_review_not_just_state_label(tmp_path):
    idx, _, _, _ = reviewed_corpus(tmp_path)
    with idx.get_connection() as db:
        db.execute("UPDATE corpus_source_pages SET state='visual_verified',review_path='',review_sha256=''")
    report = audit_corpus(tmp_path)
    assert not report['complete']
    assert any(f['kind']=='visual_page_without_review' for f in report['failures'])


def test_complete_visual_review_survives_sync_and_audit(tmp_path, monkeypatch):
    idx, review, old, _ = reviewed_corpus(tmp_path)
    review.update(kind='visual', transcript='', visible_text_status='none',
                  visual_description='Decorative silhouette; image retained.')
    old.write_text(json.dumps(review))
    spec = importlib.util.spec_from_file_location('visual_sync_test', Path(__file__).resolve().parents[1]/'scripts/complete_corpus.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'OUT', tmp_path/'data/evaluation/corpus-completion-20261004')
    monkeypatch.setattr(module, 'KnowledgeIndexer', lambda: idx)
    module.page_coverage()
    with idx.get_connection() as db:
        assert db.execute('SELECT state FROM corpus_source_pages').fetchone()[0] == 'visual_verified'
        for row in db.execute('SELECT id,text FROM chunks').fetchall():
            blob = pack_vector([1.]+[0.]*1023)
            db.execute('UPDATE chunks SET embedding=? WHERE id=?', (blob, row['id']))
            db.execute('INSERT OR REPLACE INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                       (row['id'], hashlib.sha256(row['text'].encode()).hexdigest(), 'pinned', digest_blob(blob), 1024, 1, 'now'))
    assert audit_corpus(tmp_path)['complete']
    Path(review['image_path']).write_bytes(b'changed original image')
    assert not audit_corpus(tmp_path)['complete']


def digest_blob(blob):
    return hashlib.sha256(blob).hexdigest()


def test_sync_runner_uses_portable_lock_without_direct_fcntl_import(monkeypatch):
    import builtins
    from app import file_lock
    original_import = builtins.__import__

    def without_posix_lock(name, *args, **kwargs):
        if name == 'fcntl':
            raise ModuleNotFoundError("No module named 'fcntl'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', without_posix_lock)
    spec = importlib.util.spec_from_file_location(
        'portable_sync_test', Path(__file__).resolve().parents[1]/'scripts/complete_corpus.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.fcntl is file_lock
