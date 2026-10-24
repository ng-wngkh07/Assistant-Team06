"""Verify migration isolation, identity and vector invalidation independently."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest
import pymupdf
from app.indexer import KnowledgeIndexer

spec = importlib.util.spec_from_file_location('corpus_repair', Path(__file__).resolve().parents[1]/'scripts/repair_rag_corpus.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_db(tmp_path):
    src = tmp_path/'src'; src.mkdir()
    pdf = src/'code.pdf'
    raw = 'class SinhVien {\n    int hoTen;\n};'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50,50), raw)
        doc.save(pdf)
    db_path = tmp_path/'original.db'
    index = KnowledgeIndexer(db_path=db_path,src_dir=src)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    with index.get_connection() as db:
        db.execute('''INSERT INTO documents(id,filename,clean_title,filepath,file_size,file_hash,total_pages,extracted_pages_count,status)
          VALUES(41,'code.pdf','Code',?,?,?,1,1,'indexed')''',(str(pdf),pdf.stat().st_size,sha))
        db.execute("INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text,embedding) VALUES(17,41,'Code','code.pdf',1,0,'class Sinh Vien { int ho Ten; };',?)",(b'old-vector',))
        db.execute('CREATE TABLE corpus_source_pages(filename TEXT,page_num INTEGER,source_sha256 TEXT,state TEXT,chunks_sha256 TEXT,review_path TEXT,review_sha256 TEXT,checked_at TEXT,PRIMARY KEY(filename,page_num))')
        db.execute("INSERT INTO corpus_source_pages VALUES('code.pdf',1,?,'text_verified','','','','')",(sha,))
        db.execute('CREATE TABLE IF NOT EXISTS corpus_vector_provenance(chunk_id INTEGER PRIMARY KEY,text_sha256 TEXT,model_digest TEXT,vector_sha256 TEXT,dimensions INTEGER,full_text INTEGER,embedded_at TEXT)')
        db.execute("INSERT INTO corpus_vector_provenance VALUES(17,'old-text','model','old-vector',1024,1,'')")
    return db_path, src, pdf


def test_repair_changes_shadow_only_and_invalidates_stale_vector(tmp_path):
    original, src, pdf = fixture_db(tmp_path)
    before = original.read_bytes()
    shadow = tmp_path/'shadow.db'
    result = module.repair(original,shadow,src,tmp_path,tmp_path/'report.json')
    assert original.read_bytes() == before
    assert result['status'] == 'TEXT_REPAIRED_NOT_PUBLISHED'
    with sqlite3.connect(shadow) as db:
        row = db.execute('SELECT id,doc_id,text,embedding FROM chunks').fetchone()
        assert row[:2] == (17,41)
        assert 'SinhVien' in row[2] and 'hoTen' in row[2]
        assert row[3] is None
        assert db.execute('SELECT count(*) FROM corpus_vector_provenance').fetchone()[0] == 0
        assert db.execute('SELECT raw_capture FROM corpus_page_quality').fetchone()[0].startswith('class SinhVien')
    with pytest.raises(ValueError):
        module.repair(original,original,src,tmp_path,tmp_path/'report.json')


def test_changed_pdf_is_rejected_without_touching_original(tmp_path):
    original, src, pdf = fixture_db(tmp_path)
    before = original.read_bytes()
    pdf.write_bytes(pdf.read_bytes()+b'\nchanged-source')
    with pytest.raises(ValueError,match='Changed source bytes'):
        module.repair(original,tmp_path/'shadow.db',src,tmp_path,tmp_path/'report.json')
    assert original.read_bytes() == before
    assert json.loads((tmp_path/'report.json').read_text())['status'] == 'FAILED_NOT_PUBLISHED'
