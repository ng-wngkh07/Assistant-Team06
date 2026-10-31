"""A publisher pack must not copy unrelated private data or recompute embeddings."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

import pytest

from app.indexer import KnowledgeIndexer, pack_vector
from app.knowledge_pack import export_pack, install_pack, _page_passages_digest
from tests.knowledge_pack_review_helpers import (
    create_synthetic_review,
    create_synthetic_review_manifest,
    setup_reviewed_chunk_in_db,
)

IDENTITY = 'bge-m3-mlx-fp16@' + 'a' * 40


def publisher(tmp_path):
    sources = tmp_path / 'sources'; sources.mkdir()
    source_content = 'Encapsulation keeps state behind a public interface.\n'
    (sources / 'public.md').write_text(source_content)
    (sources / 'private.md').write_text('PRIVATE CUSTOMER RECORD DO NOT DISTRIBUTE\n')
    database = tmp_path / 'publisher.db'
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    source_sha = hashlib.sha256((sources / 'public.md').read_bytes()).hexdigest()
    _, rev = create_synthetic_review(
        tmp_path / 'reviews',
        'public.md',
        1,
        source_sha,
        transcript=source_content,
    )
    reviews_manifest = tmp_path / 'page-reviews.json'
    create_synthetic_review_manifest(reviews_manifest, [rev])

    blob = pack_vector([1.] + [0.] * 1023)
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE customer_history(secret TEXT)')
        db.execute("INSERT INTO customer_history VALUES('PRIVATE CHAT HISTORY')")
        doc_row = db.execute("SELECT id, clean_title FROM documents WHERE filename='public.md'").fetchone()
        setup_reviewed_chunk_in_db(db, doc_row[0], 'public.md', doc_row[1], 1, rev, IDENTITY)
        priv_row = db.execute("SELECT id FROM documents WHERE filename='private.md'").fetchone()
        if priv_row:
            for cid, text in db.execute('SELECT id,text FROM chunks WHERE doc_id=?', (priv_row[0],)).fetchall():
                db.execute('UPDATE chunks SET embedding=? WHERE id=?', (blob, cid))
                db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,1024,1,?)',
                           (cid, hashlib.sha256(text.encode()).hexdigest(), IDENTITY,
                            hashlib.sha256(blob).hexdigest(), '2026-01-01T00:00:00Z'))
    allowlist = tmp_path / 'allowed.json'
    allowlist.write_text(json.dumps([{'filename': 'public.md', 'sha256': source_sha,
                                      'license': 'CC0-1.0', 'redistribution_reviewed': True}]))
    return database, sources, allowlist


def build(tmp_path):
    database, sources, allowlist = publisher(tmp_path)
    reviews_manifest = tmp_path / 'page-reviews.json'
    archive = tmp_path / 'knowledge.zip'
    result = export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=reviews_manifest)
    return database, archive, result


def test_prebuilt_install_is_relocatable_and_contains_no_private_records(tmp_path, monkeypatch):
    database, archive, exported = build(tmp_path)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    # Installing is a data transfer, never source extraction, training or embedding.
    monkeypatch.setattr(KnowledgeIndexer, 'index_all', lambda *a, **k: pytest.fail('Must not index on user device'))
    target = tmp_path / 'another computer' / 'installed'
    result = install_pack(archive, target, expected_sha256=exported['sha256'], expected_identity=IDENTITY)
    assert result['documents'] == 1 and result['chunks'] > 0
    assert (target / 'sources/public.md').read_bytes() == b'Encapsulation keeps state behind a public interface.\n'
    assert not (target / 'sources/private.md').exists()
    with zipfile.ZipFile(archive) as z:
        assert b'PRIVATE' not in b''.join(z.read(n) for n in z.namelist())
        assert str(tmp_path).encode() not in b''.join(z.read(n) for n in z.namelist())
    with sqlite3.connect(target / 'knowledge_base.db') as db:
        assert db.execute('SELECT filename,filepath FROM documents').fetchall() == [('public.md', 'public.md')]
        assert db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'Encapsulation'").fetchone()[0] > 0
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='customer_history'").fetchall()
        from app.vector_integrity import index_coverage
        assert index_coverage(db, IDENTITY)['status'] == 'full'
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('fault', ['source-changed', 'missing-license', 'not-reviewed', 'old-vector', 'truncated-vector', 'held-source'])
def test_publisher_rejects_unreviewed_changed_or_incompatible_material(tmp_path, fault):
    database, sources, allowlist = publisher(tmp_path)
    reviews_manifest = tmp_path / 'page-reviews.json'
    records = json.loads(allowlist.read_text())
    if fault == 'source-changed': (sources / 'public.md').write_text('Changed after review')
    if fault == 'missing-license': records[0]['license'] = ''
    if fault == 'not-reviewed': records[0]['redistribution_reviewed'] = False
    allowlist.write_text(json.dumps(records))
    with sqlite3.connect(database) as db:
        if fault == 'old-vector': db.execute("UPDATE corpus_vector_provenance SET model_digest='old'")
        if fault == 'truncated-vector': db.execute('UPDATE corpus_vector_provenance SET full_text=0')
        if fault == 'held-source': db.execute('UPDATE document_source_quality SET needs_review=1')
    archive = tmp_path / 'bad.zip'
    with pytest.raises(ValueError):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=reviews_manifest)
    assert not archive.exists()


@pytest.mark.parametrize('fault', ['old-document-pipeline', 'old-quality-pipeline', 'unbound-quality-hash', 'unbound-quality-id'])
def test_export_binds_current_extraction_and_quality(tmp_path, fault):
    database, sources, allowlist = publisher(tmp_path)
    reviews_manifest = tmp_path / 'page-reviews.json'
    with sqlite3.connect(database) as db:
        if fault == 'old-document-pipeline':
            db.execute("UPDATE documents SET pipeline_version='obsolete-v1' WHERE filename='public.md'")
        elif fault == 'old-quality-pipeline':
            db.execute("UPDATE document_source_quality SET pipeline_version='obsolete-v1' WHERE filename='public.md'")
        elif fault == 'unbound-quality-hash':
            db.execute("UPDATE document_source_quality SET file_hash=? WHERE filename='public.md'", ('0'*64,))
        else:
            # A quality row for another document cannot approve this original.
            db.execute("UPDATE document_source_quality SET doc_id=10000 WHERE filename='public.md'")
    archive = tmp_path / 'rejected.zip'
    with pytest.raises(ValueError):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=reviews_manifest)
    assert not archive.exists()


def test_export_selection_isolation_ignores_unrelated_private_source_state(tmp_path):
    database, sources, allowlist = publisher(tmp_path)
    reviews_manifest = tmp_path / 'page-reviews.json'
    with sqlite3.connect(database) as db:
        db.execute("UPDATE documents SET pipeline_version='obsolete-v1' WHERE filename='private.md'")
        db.execute("DELETE FROM document_source_quality WHERE filename='private.md'")
    archive = tmp_path / 'isolated.zip'
    result = export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=reviews_manifest)
    assert result['documents'] == 1 and archive.exists()


@pytest.mark.parametrize('fault', ['digest', 'model', 'traversal', 'corrupt-vector', 'tampered-pipeline'])
def test_install_rejects_changed_pack_without_publishing_partial_data(tmp_path, fault):
    _, archive, result = build(tmp_path)
    if fault in {'traversal', 'corrupt-vector', 'tampered-pipeline'}:
        with zipfile.ZipFile(archive) as z:
            files = {name: z.read(name) for name in z.namelist()}
        if fault == 'traversal': files['../escaped.txt'] = b'bad'
        elif fault == 'corrupt-vector':
            lines = files['index.jsonl'].splitlines()
            record = json.loads(lines[0]); record['vector'] = 'AAAA'
            lines[0] = json.dumps(record).encode(); files['index.jsonl'] = b'\n'.join(lines) + b'\n'
        elif fault == 'tampered-pipeline':
            manifest = json.loads(files['manifest.json'])
            manifest['documents'][0]['pipeline_version'] = 'obsolete-v1'
            files['manifest.json'] = json.dumps(manifest).encode('utf-8')
        with zipfile.ZipFile(archive, 'w') as z:
            for name, value in files.items(): z.writestr(name, value)
        result['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    target = tmp_path / 'installed'
    with pytest.raises(ValueError):
        install_pack(archive, target, expected_sha256='0' * 64 if fault == 'digest' else result['sha256'],
                     expected_identity='wrong' if fault == 'model' else IDENTITY)
    assert not target.exists() and not (tmp_path / 'escaped.txt').exists()


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native publisher packs currently require macOS')
def test_launcher_installs_before_config_import_and_reuses_without_indexing(tmp_path):
    """A fresh process must select the shipped library, never the owner corpus."""
    _, archive, exported = build(tmp_path)
    checkout = tmp_path / 'fresh checkout'; checkout.mkdir()
    checkout.joinpath('.env').write_text(
        f'KNOWLEDGE_PACK_PATH="{archive}"\nKNOWLEDGE_PACK_SHA256={exported["sha256"]}\n')
    project = Path(__file__).resolve().parents[1]
    child = '''
import importlib.util, json, sys, types
from pathlib import Path
# Artifact resolution is the external boundary; installation itself is real.
native = types.ModuleType('app.native_embedding')
class NativeEmbeddingClient:
    def get_identity(self): return sys.argv[3]
native.NativeEmbeddingClient = NativeEmbeddingClient
sys.modules['app.native_embedding'] = native
spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2]); launcher.configure('local')
from app import config
from app.indexer import KnowledgeIndexer
def forbidden(*args, **kwargs): raise AssertionError('Unexpected reindex')
KnowledgeIndexer.index_all = forbidden
# Repeating configuration in-process and after restart must preserve local state.
launcher.configure('local')
print(json.dumps({'data': str(config.DATA_DIR), 'sources': str(config.SRC_DIR),
                  'model': str(config.MLX_9B_MODEL_DIR), 'bge': str(config.MLX_BGE_MODEL_DIR)}))
'''
    # Do not inherit private paths, provider overrides or the test database.
    env = {key: value for key, value in os.environ.items()
           if key not in {'AGENT_DATA_DIR', 'AGENT_SRC_DIR', 'HISTORY_DB_PATH',
                          'TIMETABLE_DB_PATH', 'TIMETABLE_IMAGES_DIR',
                          'KNOWLEDGE_PACK_PATH', 'KNOWLEDGE_PACK_SHA256',
                          'MLX_MODEL_DIR', 'MLX_9B_MODEL_DIR', 'MLX_BGE_MODEL_DIR', 'BGE_MODEL_DIR'}}
    env['PYTHONPATH'] = str(project)
    args = [sys.executable, '-c', child, str(project / 'scripts/dev.py'), str(checkout), IDENTITY]
    first = subprocess.run(args, env=env, cwd=checkout, text=True, capture_output=True, timeout=30)
    assert first.returncode == 0, first.stderr
    paths = json.loads(first.stdout.splitlines()[-1])
    target = checkout / 'data/knowledge-packs' / exported['sha256']
    assert paths == {'data': str(target), 'sources': str(target / 'sources'),
                     'model': str(checkout / 'data/models/qwen3.5-9b-4bit'),
                     'bge': str(checkout / 'data/models/bge-m3-mlx-fp16')}
    assert 'No document extraction, training or embedding was run.' in first.stdout
    personal = target / 'my-notes.txt'; personal.write_text('Keep my local notes')
    archive.unlink()  # An already installed library must not require the ZIP again.
    second = subprocess.run(args, env=env, cwd=checkout, text=True, capture_output=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.splitlines()[-1]) == paths
    assert 'No document extraction' not in second.stdout
    assert personal.read_text() == 'Keep my local notes'


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native publisher pack launcher requires macOS')
@pytest.mark.parametrize('first_failure', ['missing', 'io-error'])
def test_failed_artifact_read_can_be_retried_without_manual_data_deletion(tmp_path, first_failure):
    _, archive, exported = build(tmp_path)
    checkout = tmp_path / 'new checkout'
    checkout.mkdir()
    (checkout / '.env').write_text(
        f'KNOWLEDGE_PACK_PATH="{archive}"\nKNOWLEDGE_PACK_SHA256={exported["sha256"]}\n'
    )
    removed = {'AGENT_DATA_DIR', 'AGENT_SRC_DIR', 'HISTORY_DB_PATH', 'TIMETABLE_DB_PATH',
               'TIMETABLE_IMAGES_DIR', 'KNOWLEDGE_PACK_PATH', 'KNOWLEDGE_PACK_SHA256',
               'MLX_MODEL_DIR', 'MLX_9B_MODEL_DIR', 'MLX_BGE_MODEL_DIR', 'BGE_MODEL_DIR'}
    env = {key: value for key, value in os.environ.items() if key not in removed}
    project = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = str(project)
    child = r'''
import importlib.util, sys, types
from pathlib import Path
mode = sys.argv[4]
if mode != 'missing':
    native = types.ModuleType('app.native_embedding')
    class NativeEmbeddingClient:
        def get_identity(self):
            if mode == 'io-error':
                raise OSError('Temporary artifact read failure')
            return sys.argv[3]
    native.NativeEmbeddingClient = NativeEmbeddingClient
    sys.modules['app.native_embedding'] = native
spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2])
try:
    launcher.configure('local')
except (OSError, ValueError) as exc:
    print(type(exc).__name__ + ': ' + str(exc))
    raise SystemExit(2)
'''
    command = [sys.executable, '-c', child, str(project / 'scripts/dev.py'), str(checkout), IDENTITY]
    first = subprocess.run(command + [first_failure], env=env, cwd=checkout, text=True, capture_output=True, timeout=30)
    assert first.returncode == 2, first.stderr + first.stdout
    second = subprocess.run(command + ['ready'], env=env, cwd=checkout, text=True, capture_output=True, timeout=30)
    assert second.returncode == 0, second.stderr + second.stdout
    installed = checkout / 'data/knowledge-packs' / exported['sha256']
    assert (installed / 'knowledge_base.db').is_file()
    assert (installed / 'sources/public.md').read_bytes() == b'Encapsulation keeps state behind a public interface.\n'


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native publisher pack launcher requires macOS')
@pytest.mark.parametrize('existing', ['partial-db', 'bad-receipt'])
def test_retry_preserves_existing_nonempty_data(tmp_path, existing):
    _, archive, exported = build(tmp_path)
    checkout = tmp_path / 'new checkout'
    checkout.mkdir()
    (checkout / '.env').write_text(
        f'KNOWLEDGE_PACK_PATH="{archive}"\nKNOWLEDGE_PACK_SHA256={exported["sha256"]}\n'
    )
    installed = checkout / 'data/knowledge-packs' / exported['sha256']
    installed.mkdir(parents=True)
    marker = installed / ('knowledge_base.db' if existing == 'partial-db' else 'knowledge-pack.json')
    before = b'KEEP EXISTING LOCAL USER DATA\n'
    marker.write_bytes(before)
    removed = {'AGENT_DATA_DIR', 'AGENT_SRC_DIR', 'HISTORY_DB_PATH', 'TIMETABLE_DB_PATH',
               'TIMETABLE_IMAGES_DIR', 'KNOWLEDGE_PACK_PATH', 'KNOWLEDGE_PACK_SHA256',
               'MLX_MODEL_DIR', 'MLX_9B_MODEL_DIR', 'MLX_BGE_MODEL_DIR', 'BGE_MODEL_DIR'}
    env = {key: value for key, value in os.environ.items() if key not in removed}
    project = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = str(project)
    child = r'''
import importlib.util, sys, types
from pathlib import Path
mode = sys.argv[4]
if mode != 'missing':
    native = types.ModuleType('app.native_embedding')
    class NativeEmbeddingClient:
        def get_identity(self):
            if mode == 'io-error':
                raise OSError('Temporary artifact read failure')
            return sys.argv[3]
    native.NativeEmbeddingClient = NativeEmbeddingClient
    sys.modules['app.native_embedding'] = native
spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2])
try:
    launcher.configure('local')
except (OSError, ValueError) as exc:
    print(type(exc).__name__ + ': ' + str(exc))
    raise SystemExit(2)
'''
    command = [sys.executable, '-c', child,
               str(project / 'scripts/dev.py'),
               str(checkout), IDENTITY, 'ready']
    result = subprocess.run(command, env=env, cwd=checkout, text=True, capture_output=True, timeout=30)
    assert result.returncode == 2, result.stderr + result.stdout
    assert marker.read_bytes() == before
    assert not (installed / 'sources/public.md').exists()


def test_export_rejects_omitted_or_missing_page_reviews(tmp_path):
    database, sources, allowlist = publisher(tmp_path)
    archive = tmp_path / 'fail.zip'

    # Omitted page_reviews raises ValueError
    with pytest.raises(ValueError, match=r'(?i)(page|review)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY)

    # Missing file path
    with pytest.raises(ValueError, match=r'(?i)(page|review)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=tmp_path / 'nonexistent.json')

    # Empty reviews list in manifest
    empty_manifest = tmp_path / 'empty-reviews.json'
    empty_manifest.write_text(json.dumps({'reviews': []}))
    with pytest.raises(ValueError, match=r'(?i)(page|review)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=empty_manifest)


def test_export_rejects_duplicate_or_out_of_range_reviews(tmp_path):
    database, sources, allowlist = publisher(tmp_path)
    source_sha = hashlib.sha256((sources / 'public.md').read_bytes()).hexdigest()
    archive = tmp_path / 'fail.zip'

    # Duplicate page review
    _, rev1 = create_synthetic_review(tmp_path / 'revs_dup', 'public.md', 1, source_sha)
    manifest_dup = create_synthetic_review_manifest(tmp_path / 'dup-manifest.json', [rev1, rev1])
    with pytest.raises(ValueError, match=r'(?i)(duplicate)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_dup)

    # Out of range (page 2 when total_pages == 1)
    _, rev2 = create_synthetic_review(tmp_path / 'revs_oor', 'public.md', 2, source_sha)
    manifest_oor = create_synthetic_review_manifest(tmp_path / 'oor-manifest.json', [rev2])
    with pytest.raises(ValueError, match=r'(?i)(out-of-range)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_oor)

    # Out of range (page 0)
    _, rev0 = create_synthetic_review(tmp_path / 'revs_zero', 'public.md', 0, source_sha)
    manifest_zero = create_synthetic_review_manifest(tmp_path / 'zero-manifest.json', [rev0])
    with pytest.raises(ValueError, match=r'(?i)(out-of-range)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_zero)


def test_export_rejects_partial_or_unresolved_reviews(tmp_path):
    database, sources, allowlist = publisher(tmp_path)
    source_sha = hashlib.sha256((sources / 'public.md').read_bytes()).hexdigest()
    archive = tmp_path / 'fail.zip'

    # Partial completeness
    _, rev_partial = create_synthetic_review(tmp_path / 'revs_part', 'public.md', 1, source_sha, completeness='partial')
    manifest_part = create_synthetic_review_manifest(tmp_path / 'part-manifest.json', [rev_partial])
    with pytest.raises(ValueError, match=r'(?i)(complete|status|invalid)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_part)

    # Unresolved issues
    _, rev_unres = create_synthetic_review(tmp_path / 'revs_unres', 'public.md', 1, source_sha, unresolved=['diagram illegible'])
    manifest_unres = create_synthetic_review_manifest(tmp_path / 'unres-manifest.json', [rev_unres])
    with pytest.raises(ValueError, match=r'(?i)(unresolved|status|invalid)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_unres)

    # HOLD kind
    _, rev_hold = create_synthetic_review(tmp_path / 'revs_hold', 'public.md', 1, source_sha, kind='hold')
    manifest_hold = create_synthetic_review_manifest(tmp_path / 'hold-manifest.json', [rev_hold])
    with pytest.raises(ValueError, match=r'(?i)(hold|status|invalid)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_hold)


def test_export_rejects_tampered_review_evidence(tmp_path):
    database, sources, allowlist = publisher(tmp_path)
    source_sha = hashlib.sha256((sources / 'public.md').read_bytes()).hexdigest()
    archive = tmp_path / 'fail.zip'

    # Tampered image
    rev_path, rev_img = create_synthetic_review(tmp_path / 'revs_img', 'public.md', 1, source_sha)
    Path(rev_img['image_path']).write_bytes(b'TAMPERED IMAGE BYTES')
    manifest_img = create_synthetic_review_manifest(tmp_path / 'img-manifest.json', [rev_img])
    with pytest.raises(ValueError, match=r'(?i)(changed review evidence|evidence)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_img)

    # Tampered raw capture
    _, rev_raw = create_synthetic_review(tmp_path / 'revs_raw', 'public.md', 1, source_sha)
    Path(rev_raw['raw_capture_path']).write_bytes(b'TAMPERED RAW BYTES')
    manifest_raw = create_synthetic_review_manifest(tmp_path / 'raw-manifest.json', [rev_raw])
    with pytest.raises(ValueError, match=r'(?i)(changed review evidence|evidence)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_raw)

    # Source hash mismatch
    _, rev_src = create_synthetic_review(tmp_path / 'revs_src', 'public.md', 1, 'f' * 64)
    manifest_src = create_synthetic_review_manifest(tmp_path / 'src-manifest.json', [rev_src])
    with pytest.raises(ValueError, match=r'(?i)(hash|mismatch|source|status|invalid)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=manifest_src)


def test_export_rejects_altered_transcript_with_valid_vectors(tmp_path):
    database, sources, allowlist = publisher(tmp_path)
    archive = tmp_path / 'fail.zip'
    reviews_manifest = tmp_path / 'page-reviews.json'

    blob = pack_vector([1.0] + [0.0] * 1023)
    blob_hash = hashlib.sha256(blob).hexdigest()

    # Alter text in chunks table, and provide valid recomputed vector + provenance
    with sqlite3.connect(database) as db:
        altered_text = 'Tampered text that does not match reviewed page text.\n'
        cid = db.execute("SELECT id FROM chunks WHERE filename='public.md'").fetchone()[0]
        db.execute("UPDATE chunks SET text=? WHERE id=?", (altered_text, cid))
        text_sha = hashlib.sha256(altered_text.encode('utf-8')).hexdigest()
        db.execute(
            "UPDATE corpus_vector_provenance SET text_sha256=?, vector_sha256=? WHERE chunk_id=?",
            (text_sha, blob_hash, cid)
        )

    with pytest.raises(ValueError, match=r'(?i)(match reviewed|transcript)'):
        export_pack(database, sources, allowlist, archive, expected_identity=IDENTITY, page_reviews=reviews_manifest)


def test_export_handles_blank_pages_positive_and_negative(tmp_path):
    import pymupdf
    sources = tmp_path / 'sources_pdf'
    sources.mkdir()
    pdf_doc = pymupdf.open()
    p1 = pdf_doc.new_page()
    p1.insert_text((50, 50), "Encapsulation keeps state behind a public interface.\n")
    p2 = pdf_doc.new_page()  # page 2 is blank
    pdf_file = sources / 'sample.pdf'
    pdf_doc.save(str(pdf_file))
    source_sha = hashlib.sha256(pdf_file.read_bytes()).hexdigest()

    database = tmp_path / 'pdf.db'
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)

    _, p1_rev = create_synthetic_review(
        tmp_path / 'revs', 'sample.pdf', 1, source_sha,
        transcript="Encapsulation keeps state behind a public interface.\n"
    )
    _, p2_rev = create_synthetic_review(
        tmp_path / 'revs', 'sample.pdf', 2, source_sha,
        transcript="", kind='blank'
    )

    with sqlite3.connect(database) as db:
        doc_row = db.execute("SELECT id, clean_title FROM documents WHERE filename='sample.pdf'").fetchone()
        setup_reviewed_chunk_in_db(db, doc_row[0], 'sample.pdf', doc_row[1], 1, p1_rev, IDENTITY)

    allowlist = tmp_path / 'allowed_pdf.json'
    allowlist.write_text(json.dumps([{
        'filename': 'sample.pdf',
        'sha256': source_sha,
        'license': 'CC0-1.0',
        'redistribution_reviewed': True
    }]))

    manifest = create_synthetic_review_manifest(tmp_path / 'manifest.json', [p1_rev, p2_rev])

    # Positive: 2-page doc with 1 content page and 1 blank page exports and installs
    archive = tmp_path / 'sample.zip'
    exported = export_pack(
        database, sources, allowlist, archive,
        expected_identity=IDENTITY, page_reviews=manifest
    )
    assert exported['documents'] == 1
    assert exported['chunks'] == 1

    install_dest = tmp_path / 'installed_sample'
    install_res = install_pack(archive, install_dest, expected_sha256=exported['sha256'], expected_identity=IDENTITY)
    assert install_res['documents'] == 1
    assert install_res['chunks'] == 1

    # Negative 1: missing review for blank page 2
    manifest_missing_blank = create_synthetic_review_manifest(tmp_path / 'manifest_missing.json', [p1_rev])
    with pytest.raises(ValueError, match=r'(?i)(missing complete page review)'):
        export_pack(database, sources, allowlist, tmp_path / 'bad1.zip', expected_identity=IDENTITY, page_reviews=manifest_missing_blank)

    # Negative 2: blank page 2 has unexpected chunk in DB -> fails closed
    blob = pack_vector([1.0] + [0.0] * 1023)
    blob_hash = hashlib.sha256(blob).hexdigest()
    with sqlite3.connect(database) as db:
        doc_id = db.execute("SELECT id FROM documents WHERE filename='sample.pdf'").fetchone()[0]
        cur = db.execute(
            "INSERT INTO chunks(doc_id, book_title, filename, page_num, chunk_index, text, embedding) VALUES(?,?,?,?,?,?,?)",
            (doc_id, 'sample', 'sample.pdf', 2, 0, 'Unexpected chunk on blank page', blob)
        )
        cid = cur.lastrowid
        db.execute(
            "INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,1024,1,CURRENT_TIMESTAMP)",
            (cid, hashlib.sha256(b'Unexpected').hexdigest(), IDENTITY, blob_hash)
        )
    with pytest.raises(ValueError, match=r'(?i)(match reviewed expectation)'):
        export_pack(database, sources, allowlist, tmp_path / 'bad2.zip', expected_identity=IDENTITY, page_reviews=manifest)


def test_install_and_bundle_builder_reject_unreviewed_pack(tmp_path):
    database, archive, exported = build(tmp_path)

    # Tamper with archive by removing page_reviews
    with zipfile.ZipFile(archive, 'r') as zin:
        items = {item.filename: zin.read(item.filename) for item in zin.infolist()}
    manifest = json.loads(items['manifest.json'])
    for d in manifest.get('documents', []):
        d.pop('page_reviews', None)
    items['manifest.json'] = json.dumps(manifest).encode('utf-8')

    tampered_archive = tmp_path / 'tampered_no_reviews.zip'
    with zipfile.ZipFile(tampered_archive, 'w') as zout:
        for name, data in items.items():
            zout.writestr(name, data)

    tampered_sha = hashlib.sha256(tampered_archive.read_bytes()).hexdigest()

    # install_pack rejects pack without page_reviews
    with pytest.raises(ValueError, match=r'(?i)(page review)'):
        install_pack(tampered_archive, tmp_path / 'dest_no_rev', expected_sha256=tampered_sha, expected_identity=IDENTITY)

    # Tamper text_sha256 in page_reviews
    manifest_bad_digest = json.loads(zipfile.ZipFile(archive).read('manifest.json'))
    for d in manifest_bad_digest.get('documents', []):
        for pr in d.get('page_reviews', []):
            pr['text_sha256'] = '0' * 64
    items_bad_digest = dict(items)
    items_bad_digest['manifest.json'] = json.dumps(manifest_bad_digest).encode('utf-8')

    tampered_digest_archive = tmp_path / 'tampered_bad_digest.zip'
    with zipfile.ZipFile(tampered_digest_archive, 'w') as zout:
        for name, data in items_bad_digest.items():
            zout.writestr(name, data)

    tampered_digest_sha = hashlib.sha256(tampered_digest_archive.read_bytes()).hexdigest()

    # install_pack rejects pack with mismatched passage digest
    with pytest.raises(ValueError, match=r'(?i)(digest)'):
        install_pack(tampered_digest_archive, tmp_path / 'dest_bad_digest', expected_sha256=tampered_digest_sha, expected_identity=IDENTITY)


def test_page_passages_digest_unambiguous_boundaries_and_empty_list():
    # Disambiguates ordered passage boundaries with internal newlines
    assert _page_passages_digest(['alpha\n\nbeta', 'gamma']) != _page_passages_digest(['alpha', 'beta\n\ngamma'])
    # Empty list digest matches canonical empty JSON array
    empty_digest = _page_passages_digest([])
    assert empty_digest == hashlib.sha256(b'[]').hexdigest()
    assert _page_passages_digest(['']) != empty_digest


def test_installer_rejects_permuted_and_gapped_chunk_indices(tmp_path):
    database, sources, allowed = publisher(tmp_path)
    text = '\n\n'.join(f'Paragraph {i}: ' + ('Source facts and explicit conditions. ' * 65) for i in range(5))
    (sources / 'public.md').write_text(text)
    digest = hashlib.sha256((sources / 'public.md').read_bytes()).hexdigest()
    _, rev = create_synthetic_review(tmp_path / 'long-reviews', 'public.md', 1, digest, transcript=text)
    create_synthetic_review_manifest(tmp_path / 'page-reviews.json', [rev])
    with sqlite3.connect(database) as db:
        doc_id, title = db.execute("SELECT id,clean_title FROM documents WHERE filename='public.md'").fetchone()
        db.execute('UPDATE documents SET file_hash=?,file_size=? WHERE id=?', (digest, (sources / 'public.md').stat().st_size, doc_id))
        db.execute('UPDATE document_source_quality SET file_hash=? WHERE doc_id=?', (digest, doc_id))
        chunks = setup_reviewed_chunk_in_db(db, doc_id, 'public.md', title, 1, rev, IDENTITY)
        assert len(chunks) > 1
    records = json.loads(allowed.read_text())
    records[0]['sha256'] = digest
    allowed.write_text(json.dumps(records))

    archive = tmp_path / 'reviewed.zip'
    export_pack(database, sources, allowed, archive, expected_identity=IDENTITY, page_reviews=tmp_path / 'page-reviews.json')

    with zipfile.ZipFile(archive) as z:
        files = {n: z.read(n) for n in z.namelist()}

    # Permuted indices (reverse order)
    rows_permuted = [json.loads(line) for line in files['index.jsonl'].splitlines()]
    for r in rows_permuted:
        r['index'] = len(rows_permuted) - 1 - r['index']
    files_permuted = dict(files)
    files_permuted['index.jsonl'] = b''.join((json.dumps(r, ensure_ascii=False) + '\n').encode() for r in rows_permuted)
    permuted_archive = tmp_path / 'permuted.zip'
    with zipfile.ZipFile(permuted_archive, 'w') as z:
        for n, data in files_permuted.items():
            z.writestr(n, data)
    with pytest.raises(ValueError, match=r'(?i)(digest|indices)'):
        install_pack(permuted_archive, tmp_path / 'inst_permuted',
                     expected_sha256=hashlib.sha256(permuted_archive.read_bytes()).hexdigest(),
                     expected_identity=IDENTITY)
    assert not (tmp_path / 'inst_permuted').exists()

    # Gapped indices (e.g. 0, 2, 4...)
    rows_gapped = [json.loads(line) for line in files['index.jsonl'].splitlines()]
    for r in rows_gapped:
        r['index'] = r['index'] * 2
    files_gapped = dict(files)
    files_gapped['index.jsonl'] = b''.join((json.dumps(r, ensure_ascii=False) + '\n').encode() for r in rows_gapped)
    gapped_archive = tmp_path / 'gapped.zip'
    with zipfile.ZipFile(gapped_archive, 'w') as z:
        for n, data in files_gapped.items():
            z.writestr(n, data)
    with pytest.raises(ValueError, match=r'(?i)(non-canonical|gapped|indices)'):
        install_pack(gapped_archive, tmp_path / 'inst_gapped',
                     expected_sha256=hashlib.sha256(gapped_archive.read_bytes()).hexdigest(),
                     expected_identity=IDENTITY)
    assert not (tmp_path / 'inst_gapped').exists()


def test_export_rejects_out_of_range_and_extra_chunks_without_output(tmp_path):
    database, sources, allowed = publisher(tmp_path)
    # Extra chunk on nonexistent page 2 (in a 1-page document)
    with sqlite3.connect(database) as db:
        row = db.execute("SELECT doc_id,book_title,filename,text,embedding FROM chunks WHERE filename='public.md' LIMIT 1").fetchone()
        cur = db.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text,embedding) VALUES(?,?,?,2,0,?,?)', row)
        old = db.execute('SELECT p.text_sha256,p.model_digest,p.vector_sha256,p.dimensions,p.full_text,p.embedded_at FROM corpus_vector_provenance p JOIN chunks c ON c.id=p.chunk_id WHERE c.filename=? LIMIT 1', ('public.md',)).fetchone()
        db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)', (cur.lastrowid, *old))

    out_archive = tmp_path / 'extra.zip'
    with pytest.raises(ValueError, match=r'(?i)(outside physical page range|invalid or out-of-range)'):
        export_pack(database, sources, allowed, out_archive, expected_identity=IDENTITY, page_reviews=tmp_path / 'page-reviews.json')
    assert not out_archive.exists()

    # Extra chunk with NULL or 0 page
    with sqlite3.connect(database) as db:
        db.execute('DELETE FROM chunks WHERE page_num=2')
        cur0 = db.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text,embedding) VALUES(?,?,?,0,0,?,?)', row)
        db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)', (cur0.lastrowid, *old))

    out_archive0 = tmp_path / 'extra0.zip'
    with pytest.raises(ValueError, match=r'(?i)(outside physical page range|invalid or out-of-range)'):
        export_pack(database, sources, allowed, out_archive0, expected_identity=IDENTITY, page_reviews=tmp_path / 'page-reviews.json')
    assert not out_archive0.exists()
