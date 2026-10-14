"""Live, fail-closed completeness checks; saved success alone cannot authorize training."""
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
import numpy as np
from app.pdf_extractor import PDFExtractor
from app.source_page_reviews import latest_page_reviews, validate_page_review, reviewed_page_text, reviewed_page_state
from app.chunker import TextChunker


def audit_corpus(root: Path, dimensions: int = 1024) -> dict:
    src = root/'src'
    discovery = PDFExtractor.discover_source_files(src)
    files = {str(p.relative_to(src)): p for p in discovery['supported']}
    failures = []
    policy_path = root/'data/runtime/corpus_completion_policy.json'
    policy = json.loads(policy_path.read_text()) if policy_path.exists() else {}
    expected_model_digest = policy.get('embedding_model_digest')

    excluded_page_set = set()
    excluded_pages_list = []
    exclusion_decision_id = None
    manifest_entry = policy.get('page_exclusion_manifest')
    if manifest_entry:
        from app.corpus_scope import validate_page_exclusion_manifest
        try:
            scope_info = validate_page_exclusion_manifest(root, manifest_entry, files)
            excluded_page_set = scope_info['page_set']
            excluded_pages_list = scope_info['pages']
            exclusion_decision_id = scope_info['decision_id']
        except Exception as exc:
            failures.append({'kind': 'invalid_page_exclusion_manifest', 'message': str(exc)})
    try:
        latest_reviews = latest_page_reviews(root)
    except (OSError, ValueError, KeyError) as exc:
        failures.append({'kind': 'invalid_page_review', 'message': str(exc)})
        latest_reviews = {}
    result = {'supported_documents': len(files), 'unsupported': discovery['unsupported'],
              'symlink_violations': discovery['symlink_violations'], 'failures': failures}
    if not files: failures.append({'kind': 'empty_corpus'})
    if discovery['unsupported']: failures.append({'kind': 'unsupported_documents'})
    if discovery['symlink_violations']: failures.append({'kind': 'unsafe_source_paths'})
    path = root/'data/knowledge_base.db'
    if not path.is_file():
        failures.append({'kind': 'missing_index'})
        result['complete'] = False
        return result
    try:
        db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        with db:
            docs = {r['filename']: dict(r) for r in db.execute('SELECT * FROM documents')}
            for name in sorted(set(files)-set(docs)):
                failures.append({'kind': 'missing_document', 'file': name})
            for name in sorted(set(docs)-set(files)):
                failures.append({'kind': 'obsolete_document', 'file': name})
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            pages = {}
            if 'corpus_source_pages' in tables:
                pages = {(r['filename'], r['page_num']): dict(r) for r in db.execute('SELECT * FROM corpus_source_pages')}
            chunk_groups = {}
            vectors = Counter()
            provenance = {r['chunk_id']: dict(r) for r in db.execute('SELECT * FROM corpus_vector_provenance')} if 'corpus_vector_provenance' in tables else {}
            for row in db.execute('SELECT id,filename,page_num,chunk_index,text,embedding FROM chunks ORDER BY filename,page_num,chunk_index'):
                chunk_groups.setdefault((row['filename'], row['page_num']), []).append(row['text'])
                blob = row['embedding']
                if not blob:
                    vectors['missing'] += 1
                elif len(blob) != dimensions*4:
                    vectors['wrong_dimension'] += 1
                else:
                    v = np.frombuffer(blob, dtype=np.float32)
                    vectors['invalid'] += int(not np.isfinite(v).all() or not np.any(v))
                proof = provenance.get(row['id'])
                if (not blob or not proof or not expected_model_digest or proof['model_digest'] != expected_model_digest
                    or proof['full_text'] != 1 or proof['dimensions'] != dimensions
                    or proof['text_sha256'] != hashlib.sha256(row['text'].encode()).hexdigest()
                    or proof['vector_sha256'] != hashlib.sha256(blob).hexdigest()):
                    vectors['unbound_full_text'] += 1
                vectors['total'] += 1
            result['vectors'] = dict(vectors)
            if vectors['missing'] or vectors['wrong_dimension'] or vectors['invalid'] or vectors['unbound_full_text']:
                failures.append({'kind': 'incomplete_or_invalid_vectors', 'counts': dict(vectors)})
            for name, path in files.items():
                if name not in docs: continue
                doc = docs[name]
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != doc['file_hash']:
                    failures.append({'kind': 'source_changed', 'file': name})
                    continue
                if doc['status'] not in ('indexed', 'ocr_partial_reviewed'):
                    failures.append({'kind': 'document_not_indexed', 'file': name, 'status': doc['status']})
                if path.suffix.lower() == '.pdf':
                    import pymupdf
                    with pymupdf.open(path) as pdf:
                        physical_pages = len(pdf)
                else:
                    physical_pages = PDFExtractor.extract_file(path).total_pages
                if physical_pages != doc['total_pages']:
                    failures.append({'kind': 'physical_page_count_mismatch', 'file': name})
                for page in range(1, doc['total_pages']+1):
                    record = pages.get((name, page))
                    if not record or record['source_sha256'] != digest:
                        failures.append({'kind': 'page_coverage_unverified', 'file': name, 'page': page})
                        continue
                    is_excluded = (name, page) in excluded_page_set
                    if record['state'] not in ('text_verified', 'blank_verified', 'visual_verified'):
                        if not is_excluded:
                            failures.append({'kind': 'page_content_pending', 'file': name, 'page': page, 'state': record['state']})
                    actual = chunk_groups.get((name, page), [])
                    text_hash = hashlib.sha256(json.dumps(actual, ensure_ascii=False).encode()).hexdigest()
                    if text_hash != record['chunks_sha256']:
                        failures.append({'kind': 'page_index_changed', 'file': name, 'page': page})
                    if record['state'] == 'text_verified' and not actual:
                        failures.append({'kind': 'text_page_without_chunks', 'file': name, 'page': page})
                    review_path = record['review_path']
                    if record['state'] == 'visual_verified' and not review_path:
                        failures.append({'kind': 'visual_page_without_review', 'file': name, 'page': page})
                    latest = latest_reviews.get((name, page))
                    if latest and not review_path:
                        failures.append({'kind': 'review_not_indexed', 'file': name, 'page': page})
                    if review_path:
                        p = Path(review_path)
                        if latest and latest[0].resolve() != p.resolve():
                            failures.append({'kind': 'superseded_page_review', 'file': name, 'page': page})
                        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != record['review_sha256']:
                            failures.append({'kind': 'page_review_changed', 'file': name, 'page': page})
                        else:
                            review = json.loads(p.read_text())
                            try:
                                validate_page_review(review, name, page, digest)
                                if not is_excluded:
                                    if review['completeness'] != 'complete':
                                        raise ValueError('Page review remains partial')
                                    if record['state'] != reviewed_page_state(review):
                                        raise ValueError('Page state and reviewed content differ')
                                    expected = [c['text'] for c in TextChunker.chunk_page(doc['clean_title'], name, page, reviewed_page_text(dict(review, editorial='') if doc.get('pipeline_version', '').startswith('rag-pipeline-yc205-') else review))]
                                    if actual != expected:
                                        failures.append({'kind': 'review_text_not_indexed', 'file': name, 'page': page})
                            except (OSError, ValueError, KeyError):
                                failures.append({'kind': 'review_not_complete_or_bound', 'file': name, 'page': page})
            result['indexed_documents'] = len(docs)
            result['page_states'] = dict(Counter(p['state'] for p in pages.values()))
            result['total_pages'] = sum(d['total_pages'] for d in docs.values())
            result['integrity'] = db.execute('PRAGMA quick_check').fetchone()[0]
            if result['integrity'] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
                failures.append({'kind': 'database_integrity'})
        db.close()
    except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
        failures.append({'kind': 'audit_error', 'message': str(exc)})
    total_pages = result.get('total_pages', 0)
    result['total_pages'] = total_pages
    result['complete'] = not failures
    result['excluded_page_count'] = len(excluded_page_set)
    result['in_scope_pages'] = max(0, total_pages - len(excluded_page_set))
    result['complete_all_sources'] = (len(excluded_page_set) == 0 and result['complete'] and total_pages > 0)
    result['excluded_pages'] = excluded_pages_list
    result['exclusion_decision_id'] = exclusion_decision_id
    return result


def require_complete_corpus(root: Path) -> dict | None:
    policy = root/'data/runtime/corpus_completion_policy.json'
    if not policy.exists():
        return None  # Compatibility for workspaces without this user requirement.
    settings = json.loads(policy.read_text())
    if settings.get('require_complete_corpus') is not True:
        raise RuntimeError('CORPUS_INCOMPLETE: chính sách hoàn thành nguồn không hợp lệ')
    report = audit_corpus(root)
    if not report['complete']:
        counts = dict(Counter(x['kind'] for x in report['failures']))
        raise RuntimeError('CORPUS_INCOMPLETE: chưa đủ tài liệu/trang/vector: '+json.dumps(counts, ensure_ascii=False))
    return report
