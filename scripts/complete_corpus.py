"""Resumable CPU ingestion, page coverage and serial local embedding.

Raw OCR remains pending content review. This runner never starts training.
"""
import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import file_lock as fcntl
from app.pdf_extractor import PDFExtractor
from app.vision_ocr import VisionOCRManager, BACKEND_TESSERACT
from app.indexer import KnowledgeIndexer, pack_vector
from app.chunker import TextChunker
from app.gpu_lock import gpu_coordinator
from app.corpus_readiness import audit_corpus
from app.source_page_reviews import latest_page_reviews, validate_page_review, reviewed_page_text, reviewed_page_state
OUT = ROOT/'data/evaluation/corpus-completion-20261004'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def progress(stage, done, total, **extra):
    record = dict(stage=stage, done=done, total=total, updated_at=datetime.now(timezone.utc).isoformat(), **extra)
    temp = OUT/'progress.tmp'
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    temp.replace(OUT/'progress.json')
    print(json.dumps(record, ensure_ascii=False), flush=True)


def cpu_ocr(cls, page, file_hash, page_num, *args, **kwargs):
    import pymupdf
    manager = VisionOCRManager(backend=BACKEND_TESSERACT)
    return manager.ocr_image_bytes(page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes('png'),
        file_hash, kwargs.get('source_relpath') or file_hash, page_num,
        is_handwriting_suspected=kwargs.get('is_handwriting', False))


def index():
    # Explicit print-only OCR, never relabelled as a vision-model review.
    PDFExtractor._ocr_page = classmethod(cpu_ocr)
    report = KnowledgeIndexer().index_all(ocr=True, embed_model=None,
        progress_callback=lambda i, n, name, msg: progress('INDEXING', i, n, file=name, message=msg))
    (OUT/'index-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if report['error_files'] or report['unsupported_files'] or report['symlink_violations']:
        raise RuntimeError('Incomplete ingestion; see index-report.json')


def page_coverage():
    """Compare each native page, retain source-reviewed handwriting and raw OCR separately."""
    import pymupdf
    idx = KnowledgeIndexer()
    reviews = latest_page_reviews(ROOT)
    handwritten = {x['filename'] for x in json.loads((ROOT/'data/evaluation/handwriting-complete-20261004/coverage.json').read_text())['documents']}
    with idx.get_connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS corpus_source_pages(
          filename TEXT NOT NULL,page_num INTEGER NOT NULL,source_sha256 TEXT NOT NULL,
          state TEXT NOT NULL,chunks_sha256 TEXT NOT NULL,review_path TEXT NOT NULL,
          review_sha256 TEXT NOT NULL,checked_at TEXT NOT NULL,PRIMARY KEY(filename,page_num))''')
        docs = [dict(r) for r in db.execute('SELECT * FROM documents ORDER BY filename')]
    adjusted = 0
    for i, doc in enumerate(docs, 1):
        name = doc['filename']; source = ROOT/'src'/name
        digest = sha(source)
        if digest != doc['file_hash']: raise RuntimeError('Source changed: '+name)
        extraction = None if name in handwritten else PDFExtractor.extract_file(source)
        if extraction and extraction.error_message: raise RuntimeError(extraction.error_message)
        native = {p['page_num']: p for p in extraction.extracted_pages} if extraction else {}
        pdf = pymupdf.open(source) if source.suffix.lower() == '.pdf' and name not in handwritten else None
        try:
            for page in range(1, doc['total_pages']+1):
                key = (name, page); review_path = review_hash = ''
                with idx.get_connection() as db:
                    existing = [dict(r) for r in db.execute('SELECT id,text,chunk_index FROM chunks WHERE doc_id=? AND page_num=? ORDER BY chunk_index', (doc['id'], page))]
                    actual = [r['text'] for r in existing]
                    if name in handwritten or key in reviews:
                        entry = reviews.get(key)
                        state = 'handwriting_unreviewed'
                        if entry:
                            path, x = entry; review_path = str(path.resolve()); review_hash = sha(path)
                            validate_page_review(x, name, page, digest)
                            expected = TextChunker.chunk_page(doc['clean_title'], name, page, reviewed_page_text(x))
                            expected_text = [c['text'] for c in expected]
                            if actual != expected_text:
                                db.execute('DELETE FROM chunks WHERE doc_id=? AND page_num=?', (doc['id'], page))
                                db.executemany('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(?,?,?,?,?,?)',
                                    [(doc['id'], doc['clean_title'], name, page, c['chunk_index'], c['text']) for c in expected])
                                actual = expected_text; adjusted += 1
                            state = reviewed_page_state(x)
                    else:
                        p = native.get(page)
                        if p is None: raise RuntimeError(f'Missing physical page {name}:{page}')
                        text = p['text'].strip()
                        visible = bool(pdf and (pdf[page-1].get_images() or pdf[page-1].get_drawings()))
                        if text and not (pdf and len(text) < 30 and visible):
                            expected = TextChunker.chunk_page(doc['clean_title'], name, page, text)
                            expected_text = [c['text'] for c in expected]
                            if actual != expected_text:
                                db.execute('DELETE FROM chunks WHERE doc_id=? AND page_num=?', (doc['id'], page))
                                db.executemany('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(?,?,?,?,?,?)',
                                    [(doc['id'], doc['clean_title'], name, page, c['chunk_index'], c['text']) for c in expected])
                                actual = expected_text; adjusted += 1
                            state = 'text_verified'
                        elif actual:
                            state = 'ocr_pending_review'
                        elif (pdf and not text and not visible):
                            state = 'blank_verified'
                        else:
                            state = 'visual_content_pending'
                    db.execute('INSERT OR REPLACE INTO corpus_source_pages VALUES(?,?,?,?,?,?,?,?)',
                        (name, page, digest, state, hashlib.sha256(json.dumps(actual, ensure_ascii=False).encode()).hexdigest(), review_path, review_hash, datetime.now(timezone.utc).isoformat()))
            with idx.get_connection() as db:
                db.execute('UPDATE documents SET extracted_pages_count=(SELECT count(DISTINCT page_num) FROM chunks WHERE doc_id=?) WHERE id=?', (doc['id'], doc['id']))
        finally:
            if pdf: pdf.close()
        progress('PAGE_COVERAGE', i, len(docs), file=name, adjusted_pages=adjusted)
    (OUT/'page-sync.json').write_text(json.dumps({'documents':len(docs),'adjusted_pages':adjusted},indent=2))


def embed(batch_size=16):
    idx = KnowledgeIndexer()
    model = next(m for m in idx.ollama.list_models() if m['name'] in ('bge-m3:latest', 'bge-m3'))
    policy_path = ROOT/'data/runtime/corpus_completion_policy.json'
    policy = json.loads(policy_path.read_text())
    if policy.get('require_complete_corpus') is not True: raise RuntimeError('Missing corpus completion policy')
    policy['embedding_model_digest'] = model['digest']
    policy['embedding_model'] = model['name']
    policy_path.write_text(json.dumps(policy, indent=2))
    with idx.get_connection() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS corpus_vector_provenance(
          chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
          text_sha256 TEXT NOT NULL,model_digest TEXT NOT NULL,vector_sha256 TEXT NOT NULL,
          dimensions INTEGER NOT NULL,full_text INTEGER NOT NULL,embedded_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS corpus_embedding_cache(
          text_sha256 TEXT NOT NULL,model_digest TEXT NOT NULL,embedding BLOB NOT NULL,
          vector_sha256 TEXT NOT NULL,PRIMARY KEY(text_sha256,model_digest))''')
        rows = [dict(r) for r in db.execute('''SELECT c.id,c.text,c.embedding,v.text_sha256,v.model_digest,v.vector_sha256,v.full_text
             FROM chunks c LEFT JOIN corpus_vector_provenance v ON c.id=v.chunk_id ORDER BY c.id''')]
    pending = []
    for r in rows:
        valid = (r['embedding'] and len(r['embedding'])==4096 and r['full_text']==1
            and r['text_sha256']==hashlib.sha256(r['text'].encode()).hexdigest()
            and r['model_digest']==model['digest']
            and r['vector_sha256']==hashlib.sha256(r['embedding']).hexdigest())
        if not valid: pending.append(r)
    done = 0; started = time.monotonic()
    with gpu_coordinator.acquire_for_inference(timeout=10):
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start+batch_size]
            unique = {hashlib.sha256(r['text'].encode()).hexdigest(): r['text'] for r in batch}
            cached = {}
            with idx.get_connection() as db:
                for digest in unique:
                    r = db.execute('SELECT embedding,vector_sha256 FROM corpus_embedding_cache WHERE text_sha256=? AND model_digest=?',(digest,model['digest'])).fetchone()
                    if r and len(r['embedding'])==4096 and sha_blob(r['embedding'])==r['vector_sha256']:
                        cached[digest] = r['embedding']
            new = [(digest, text) for digest,text in unique.items() if digest not in cached]
            vectors = idx.ollama.get_batch_embeddings([text for _,text in new], model=model['name']) if new else []
            if len(vectors)!=len(new): raise RuntimeError('Incomplete embedding response')
            with idx.get_connection() as db:
                for (digest, _), v in zip(new, vectors):
                    if not v or len(v)!=1024 or not all(math.isfinite(x) for x in v) or not any(v):
                        raise RuntimeError('Invalid vector for text '+digest)
                    blob = pack_vector(v)
                    db.execute('INSERT OR REPLACE INTO corpus_embedding_cache VALUES(?,?,?,?)',(digest,model['digest'],blob,sha_blob(blob)))
                    cached[digest] = blob
                for row in batch:
                    blob = cached[hashlib.sha256(row['text'].encode()).hexdigest()]
                    cursor = db.execute('UPDATE chunks SET embedding=? WHERE id=? AND text=?',(blob, row['id'], row['text']))
                    if cursor.rowcount != 1: raise RuntimeError('Chunk changed during embedding')
                    db.execute('INSERT OR REPLACE INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)',
                        (row['id'], hashlib.sha256(row['text'].encode()).hexdigest(), model['digest'], hashlib.sha256(blob).hexdigest(), 1024, 1, datetime.now(timezone.utc).isoformat()))
                    done += 1
            progress('EMBEDDING_FULL_TEXT', done, len(pending), model=model['name'], elapsed_seconds=round(time.monotonic()-started,1))
    progress('EMBEDDING_COMPLETE', done, len(pending), model=model['name'])


def sha_blob(blob):
    return hashlib.sha256(blob).hexdigest()


def audit():
    report = audit_corpus(ROOT)
    (OUT/'readiness.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    from collections import Counter
    print(json.dumps({k:v for k,v in report.items() if k!='failures'},ensure_ascii=False))
    print('FAILURE_COUNTS', dict(Counter(x['kind'] for x in report['failures'])))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['index','pages','embed','audit','all']);parser.add_argument('--batch-size',type=int,default=16)
    args=parser.parse_args();OUT.mkdir(exist_ok=True)
    with (OUT/'runner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            if args.phase in ('index','all'): index()
            if args.phase in ('pages','all'): page_coverage()
            if args.phase in ('embed','all'): embed(args.batch_size)
            if args.phase in ('audit','all'): audit()
        except Exception as exc:
            progress('FAILED',0,0,error=str(exc));raise
