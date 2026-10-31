"""Real SQLite migration and rollback boundaries; no serving database or GPU."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from app.indexer import KnowledgeIndexer, pack_vector
from app.vector_rebuild import apply_stage, nonvector_fingerprint, prepare_stage

IDENTITY = 'fixture-embedding@reviewed-bytes'


def staged_library(tmp_path):
    sources = tmp_path/'sources'; sources.mkdir()
    (sources/'objects.md').write_text('Encapsulation groups state and behavior behind an interface.\n')
    database = tmp_path/'library.db'
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE held_pages(page INTEGER PRIMARY KEY,state TEXT)')
        db.execute("INSERT INTO held_pages VALUES(1,'HOLD')")
        before = nonvector_fingerprint(db)
    stage = tmp_path/'stage.sqlite'
    prepare_stage(database, stage, IDENTITY)
    blob = pack_vector([1.0]+[0.0]*1023)
    with sqlite3.connect(stage) as db:
        db.execute('UPDATE texts SET vector=?,vector_sha256=?', (blob,hashlib.sha256(blob).hexdigest()))
    return database, stage, before


def test_complete_vector_migration_preserves_every_nonvector_record_and_backup(tmp_path):
    database, stage, before = staged_library(tmp_path)
    backup = tmp_path/'recovery.sqlite'
    original_bytes = database.read_bytes()
    result = apply_stage(database,stage,backup,expected_identity=IDENTITY)
    assert result['applied_chunks'] > 0
    with sqlite3.connect(database) as db:
        assert nonvector_fingerprint(db) == before
        assert db.execute('SELECT count(*) FROM chunks WHERE embedding IS NULL').fetchone()[0] == 0
        assert db.execute('SELECT DISTINCT model_digest FROM corpus_vector_provenance').fetchall() == [(IDENTITY,)]
        from app.vector_integrity import index_coverage
        assert index_coverage(db,IDENTITY)['status'] == 'full'
        assert index_coverage(db,'other-artifact')['compatible_chunks'] == 0
        db.execute("UPDATE chunks SET text='changed source after vector creation'")
        assert index_coverage(db,IDENTITY)['compatible_chunks'] == 0
        db.rollback()
    with sqlite3.connect(backup) as db:
        assert nonvector_fingerprint(db) == before
        assert db.execute('SELECT count(*) FROM chunks WHERE embedding IS NOT NULL').fetchone()[0] == 0
    assert database.read_bytes() != original_bytes


@pytest.mark.parametrize('fault', ['missing-vector','corrupt-vector','changed-text','wrong-model','serving-change'])
def test_migration_rejects_incomplete_changed_or_incompatible_inputs(tmp_path, fault):
    database, stage, _ = staged_library(tmp_path)
    with sqlite3.connect(stage) as db:
        if fault == 'missing-vector': db.execute('UPDATE texts SET vector=NULL')
        if fault == 'corrupt-vector': db.execute("UPDATE texts SET vector=x'00000000'")
        if fault == 'changed-text': db.execute("UPDATE texts SET text='different source'")
    if fault == 'serving-change':
        with sqlite3.connect(database) as db:
            db.execute("UPDATE held_pages SET state='REVOKED'")
    original_bytes = database.read_bytes()
    with pytest.raises(ValueError):
        apply_stage(database,stage,tmp_path/'recovery.sqlite',
                    expected_identity='wrong' if fault == 'wrong-model' else IDENTITY)
    assert database.read_bytes() == original_bytes


def test_resume_rejects_changed_source_or_embedding_basis(tmp_path):
    database, stage, _ = staged_library(tmp_path)
    with pytest.raises(ValueError):
        prepare_stage(database,stage,'another-artifact')
    with sqlite3.connect(database) as db:
        db.execute("UPDATE chunks SET text='Changed after staging'")
    with pytest.raises(ValueError):
        prepare_stage(database,stage,IDENTITY)


@pytest.mark.parametrize('fault', ['old-pipeline', 'missing-quality', 'changed-quality-source'])
def test_staging_rejects_obsolete_or_unbound_extraction_before_creating_stage(tmp_path, fault):
    database, _, _ = staged_library(tmp_path)
    with sqlite3.connect(database) as db:
        if fault == 'old-pipeline':
            db.execute("UPDATE documents SET pipeline_version='v1'")
        elif fault == 'missing-quality':
            db.execute('DELETE FROM document_source_quality')
        else:
            db.execute("UPDATE document_source_quality SET file_hash='different-original'")
    original_bytes = database.read_bytes()
    stage = tmp_path / 'rejected' / 'new-stage.sqlite'
    with pytest.raises(ValueError, match='extraction'):
        prepare_stage(database, stage, IDENTITY)
    assert not stage.exists()
    assert database.read_bytes() == original_bytes
