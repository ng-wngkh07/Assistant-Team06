"""Rebuild source text in a separate database; never publish it automatically.

Preserves document/page identities, bound visual reviews and unchanged vectors.
Native extraction quality is evidence of risk, never approval for training.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(value):
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def source_text(review):
    # Editorial explanations remain in the bound review, outside source evidence.
    from app.source_page_reviews import reviewed_page_text
    original = dict(review, editorial='')
    return reviewed_page_text(original)


def repair(source_db, shadow_db, src, review_root, report_path, extra_reviews=None):
    from app.chunker import TextChunker
    from app.indexer import KnowledgeIndexer
    from app.pdf_extractor import PDFExtractor
    from app.source_page_reviews import latest_page_reviews, validate_page_review, reviewed_page_state
    from app.source_quality import PIPELINE_VERSION

    source_db, shadow_db = Path(source_db).resolve(), Path(shadow_db).resolve()
    if source_db == shadow_db or shadow_db.exists():
        raise ValueError('Use a new shadow database path; the live database is never edited')
    shadow_db.parent.mkdir(parents=True, exist_ok=True)
    # Consistent SQLite snapshot, including committed WAL contents.
    with sqlite3.connect(f'file:{source_db}?mode=ro', uri=True) as original, connect(shadow_db) as shadow:
        original.backup(shadow)
    index = KnowledgeIndexer(db_path=shadow_db, src_dir=Path(src))
    reviews = latest_page_reviews(Path(review_root))
    if extra_reviews:
        for path in Path(extra_reviews).glob('*/*.json'):
            if path.name.endswith('-raw.json'):
                continue
            record = json.loads(path.read_text())
            key = (record['filename'], record['page'])
            if key not in reviews or record['reviewed_at'] > reviews[key][1]['reviewed_at']:
                reviews[key] = (path, record)
    counts, warning_counts = Counter(), Counter()
    report = dict(status='RUNNING', pipeline_version=PIPELINE_VERSION, started_at=now(),
                  source_db=str(source_db), shadow_db=str(shadow_db), documents=[], counts={})
    with connect(shadow_db) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS corpus_page_quality(
          filename TEXT NOT NULL,page_num INTEGER NOT NULL,source_sha256 TEXT NOT NULL,
          pipeline_version TEXT NOT NULL,raw_sha256 TEXT NOT NULL,raw_capture TEXT NOT NULL,
          blocks_json TEXT NOT NULL,warnings_json TEXT NOT NULL,needs_review INTEGER NOT NULL,
          review_path TEXT NOT NULL,checked_at TEXT NOT NULL,PRIMARY KEY(filename,page_num))''')
        docs = [dict(x) for x in db.execute('SELECT * FROM documents ORDER BY id')]
    try:
        for document in docs:
            name = document['filename']
            source = (Path(src)/name).resolve()
            if not source.is_relative_to(Path(src).resolve()):
                raise ValueError('Source escaped root: '+name)
            source_hash = digest(source.read_bytes())
            if source_hash != document['file_hash']:
                raise ValueError('Changed source bytes: '+name)
            result = PDFExtractor.extract_file(source)
            if result.error_message or result.failed_pages:
                raise RuntimeError(result.error_message or str(result.failed_pages))
            pages = {p['page_num']: p for p in result.extracted_pages}
            if len(pages) != document['total_pages']:
                raise RuntimeError('Incomplete extraction: '+name)
            changed = 0
            with connect(shadow_db) as db:
                # Each document is atomic; earlier completed documents remain resumable evidence.
                for number in range(1, document['total_pages']+1):
                    p = pages[number]
                    rows = list(db.execute('SELECT id,text,chunk_index FROM chunks WHERE doc_id=? AND page_num=? ORDER BY chunk_index,id', (document['id'], number)))
                    old = [r['text'] for r in rows]
                    bound = reviews.get((name, number))
                    old_binding = db.execute('SELECT * FROM corpus_source_pages WHERE filename=? AND page_num=?', (name, number)).fetchone()
                    if old_binding and old_binding['review_path'] and not bound:
                        path = Path(old_binding['review_path'])
                        bound = (path, json.loads(path.read_text()))
                    review_path = review_hash = ''
                    warnings = list(p.get('quality_warnings', p.get('warnings', [])))
                    needs_review = bool(p.get('needs_review'))
                    if bound:
                        path, review = bound
                        validate_page_review(review, name, number, source_hash)
                        text = source_text(review)
                        state = reviewed_page_state(review)
                        review_path, review_hash = str(path.resolve()), digest(path.read_bytes())
                        needs_review = review['completeness'] != 'complete'
                        if review.get('editorial'):
                            warnings.append('editorial_kept_separate_from_source')
                    elif old_binding and old_binding['state'] in ('ocr_pending_review', 'handwriting_unreviewed', 'visual_content_pending'):
                        # A native extractor must not replace a retained OCR capture with empty text.
                        text = None
                        state = old_binding['state']
                        needs_review = True
                    else:
                        text = p.get('text', '')
                        if needs_review:
                            state = 'native_pending_review'
                        elif text.strip():
                            current_chunks = [c['text'] for c in TextChunker.chunk_page(document['clean_title'], name, number, text)]
                            # Old acceptance binds the old passage. A new extractor is
                            # not a human reviewer and cannot approve replacement text.
                            state = ('text_verified' if old_binding and old_binding['state'] == 'text_verified'
                                     and current_chunks == old else 'native_text_extracted')
                        else:
                            state = 'blank_verified' if old_binding and old_binding['state'] == 'blank_verified' else 'visual_content_pending'
                    new = old if text is None else [c['text'] for c in TextChunker.chunk_page(document['clean_title'], name, number, text)]
                    if new != old:
                        changed += 1
                        for position, value in enumerate(new):
                            if position < len(rows):
                                row = rows[position]
                                if row['text'] != value:
                                    db.execute('DELETE FROM corpus_vector_provenance WHERE chunk_id=?', (row['id'],))
                                    db.execute('UPDATE chunks SET text=?,embedding=NULL,chunk_index=? WHERE id=?', (value, position, row['id']))
                                    counts['changed_chunks'] += 1
                            else:
                                db.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(?,?,?,?,?,?)',
                                           (document['id'],document['clean_title'],name,number,position,value))
                                counts['added_chunks'] += 1
                        for row in rows[len(new):]:
                            db.execute('DELETE FROM corpus_vector_provenance WHERE chunk_id=?', (row['id'],))
                            db.execute('DELETE FROM chunks WHERE id=?', (row['id'],))
                            counts['removed_chunks'] += 1
                    raw = p.get('raw_capture', '')
                    raw = raw.get('raw_text', '') if isinstance(raw, dict) else raw
                    db.execute('INSERT OR REPLACE INTO corpus_page_quality VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (name,number,source_hash,PIPELINE_VERSION,digest(raw.encode()),raw,
                                json.dumps(p.get('blocks', []),ensure_ascii=False),json.dumps(warnings,ensure_ascii=False),
                                int(needs_review),review_path,now()))
                    db.execute('INSERT OR REPLACE INTO corpus_source_pages VALUES(?,?,?,?,?,?,?,?)',
                               (name,number,source_hash,state,digest(json.dumps(new,ensure_ascii=False).encode()),review_path,review_hash,now()))
                    counts['pages'] += 1
                    counts['needs_review_pages'] += int(needs_review)
                    counts['bound_review_pages'] += int(bool(bound))
                    warning_counts.update(str(w).split(':')[0] for w in warnings)
                result.filename = name
                index._save_quality_record(db, document['id'], result)
                db.execute('UPDATE documents SET extracted_pages_count=(SELECT count(DISTINCT page_num) FROM chunks WHERE doc_id=?),pipeline_version=?,filepath=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                           (document['id'],PIPELINE_VERSION,str(source),document['id']))
            counts['changed_pages'] += changed
            report['documents'].append(dict(filename=name,changed_pages=changed,total_pages=document['total_pages']))
            report.update(counts=dict(counts),warning_counts=dict(warning_counts),updated_at=now())
            Path(report_path).write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(dict(document=name,done=len(report['documents']),total=len(docs),counts=dict(counts)),ensure_ascii=False),flush=True)
        with connect(shadow_db) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('Shadow database integrity failed')
            db.execute("INSERT INTO chunks_fts(chunks_fts,rank) VALUES('integrity-check',1)")
            counts['pending_vectors'] = db.execute('SELECT count(*) FROM chunks WHERE embedding IS NULL').fetchone()[0]
        report.update(status='TEXT_REPAIRED_NOT_PUBLISHED',counts=dict(counts),completed_at=now())
    except Exception as exc:
        report.update(status='FAILED_NOT_PUBLISHED',error=str(exc),counts=dict(counts),updated_at=now())
        raise
    finally:
        Path(report_path).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['text'])
    parser.add_argument('--source-db', type=Path, required=True)
    parser.add_argument('--shadow-db', type=Path, required=True)
    parser.add_argument('--src', type=Path, default=ROOT / 'src')
    parser.add_argument('--review-root', type=Path, default=ROOT)
    parser.add_argument('--extra-reviews', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    repair(args.source_db, args.shadow_db, args.src, args.review_root, args.report, args.extra_reviews)
