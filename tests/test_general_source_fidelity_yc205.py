"""Source fidelity regressions from independent PDF review (YC-204).

Synthetic fixtures keep private course pages out of the test repository.
"""
from pathlib import Path
import hashlib
import json
from unittest.mock import Mock
import pytest
import pymupdf
from app.text_cleaner import normalize_vietnamese_text
from app.pdf_extractor import PDFExtractor
from app.indexer import KnowledgeIndexer


def test_code_identifiers_and_indentation_survive_source_normalization():
    source = 'class SinhVien {\nprivate:\n    string hoTen;\npublic:\n    void setDiem(float d);\n};'
    assert normalize_vietnamese_text(source) == source


def test_legacy_vni_definition_is_readable():
    source = 'Meänh ñeà toaùn hoïc laø moät khaúng ñònh coù giaù trò chaân lyù xaùc ñònh.'
    assert normalize_vietnamese_text(source) == 'Mệnh đề toán học là một khẳng định có giá trị chân lý xác định.'


@pytest.mark.parametrize('source', [
    'Tâm lý học nghiên cứu hành vi và nhận thức.',
    'class MyArray {\n    string personName;\n};',
    'Björk, Müller, François; x = a + b; sinhVien.setDiem(8.5);',
])
def test_unicode_and_foreign_names_are_not_legacy_decoded(source):
    assert normalize_vietnamese_text(source) == source


def test_text_heavy_page_retains_raw_and_flags_unread_image(tmp_path):
    f = tmp_path / 'mixed.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50,50),'A definition has enough native text to pass the thirty-character threshold.')
        pix = pymupdf.Pixmap(pymupdf.csRGB, (0,0,32,32), False)
        pix.clear_with(50)
        page.insert_image(pymupdf.Rect(50,80,150,180),stream=pix.tobytes('png'))
        doc.save(f)
    result = PDFExtractor.extract_pdf(f)
    assert result.error_message is None
    page = result.extracted_pages[0]
    raw = page.get('raw_capture', '')
    raw = raw.get('raw_text', '') if isinstance(raw, dict) else raw
    assert raw.startswith('A definition')
    assert page.get('blocks'), 'Native positional blocks must survive extraction'
    assert page.get('needs_review') is True, 'Image content cannot be approved from native character count'
    assert result.needs_review is True


def test_pre_version_index_is_not_silently_skipped(tmp_path):
    src = tmp_path / 'src'; src.mkdir()
    f = src / 'definition.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50,50),'Definition: an unchanged PDF requires processing when its pipeline changes.')
        doc.save(f)
    index = KnowledgeIndexer(src_dir=src, db_path=tmp_path/'index.db')
    index.ollama = Mock()
    index.ollama.find_best_embed_model.return_value = None
    result = PDFExtractor.extract_pdf(f)
    index._save_document_record(result, status='indexed', is_scanned=False, extracted_pages_count=1)
    # The old metadata has no pipeline binding; unchanged PDF bytes alone are insufficient.
    with index.get_connection() as db:
        if any(row['name'] == 'pipeline_version' for row in db.execute('PRAGMA table_info(documents)')):
            db.execute("UPDATE documents SET pipeline_version='pre-yc205'")
    report = index.index_all(embed_model=None)
    assert report['indexed_files'] == 1
    assert report['skipped_files'] == 0


def test_stale_ocr_cache_cannot_override_changed_source(tmp_path, monkeypatch):
    src = tmp_path/'src'; src.mkdir()
    f = src/'definition.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50,50),'Definition: the current source text belongs to this PDF version.')
        doc.save(f)
    cache = tmp_path/'cache'; cache.mkdir()
    (cache/'old.json').write_text(json.dumps(dict(is_verified=True,needs_review=False,text='WRONG_OLD_SOURCE_TEXT',
        page_num=1,source_relpath='definition.pdf',source_hash='old-source-hash')))
    monkeypatch.setattr('app.vision_ocr.CACHE_DIR',cache)
    index = KnowledgeIndexer(src_dir=src,db_path=tmp_path/'index.db')
    index.ollama = Mock(); index.ollama.find_best_embed_model.return_value = None
    index.index_all(embed_model=None)
    with index.get_connection() as db:
        text = ' '.join(r['text'] for r in db.execute('SELECT text FROM chunks'))
    assert 'WRONG_OLD_SOURCE_TEXT' not in text
    assert 'current source text' in text


@pytest.mark.parametrize("tamper", [False, True])
def test_source_bound_review_survives_pipeline_reindex(tmp_path, monkeypatch, tamper):
    from app.source_page_reviews import validate_page_review
    src = tmp_path/'src'; src.mkdir()
    f = src/'definition.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50,50),'Definition: native text has enough characters, but lacks the equation.')
        doc.save(f)
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    image = tmp_path/'page.png'; image.write_bytes(b'test-evidence-preserved')
    image_sha = hashlib.sha256(image.read_bytes()).hexdigest()
    raw = tmp_path/'raw.json'; raw.write_text(json.dumps(dict(source_relpath='definition.pdf',page_num=1,source_hash=sha,image_hash=image_sha)))
    review = dict(filename='definition.pdf',page=1,source_sha256=sha,image_reviewed=True,kind='text',completeness='complete',
        transcript='SOURCE_REVIEWED_EQUATION: f(x) = a*x + b.',editorial='EDITORIAL_NOT_SOURCE',unresolved=[],reviewer='Codex',
        image_path=str(image),image_sha256=image_sha,raw_capture_path=str(raw),raw_capture_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),reviewed_at='2026-10-12T00:00:00Z')
    validate_page_review(review,'definition.pdf',1,sha)
    review_path = tmp_path/'review.json'; review_path.write_text(json.dumps(review))
    monkeypatch.setattr('app.source_page_reviews.latest_page_reviews',lambda root: {('definition.pdf',1):(review_path,review)})
    index = KnowledgeIndexer(src_dir=src,db_path=tmp_path/'index.db')
    index.ollama = Mock(); index.ollama.find_best_embed_model.return_value = None
    if tamper:
        image.write_bytes(b'changed evidence')
        with pytest.raises(ValueError, match='Changed review evidence'):
            index.index_all(embed_model=None)
        with index.get_connection() as db:
            assert db.execute('SELECT count(*) FROM chunks').fetchone()[0] == 0
        return
    index.index_all(embed_model=None)
    with index.get_connection() as db:
        text = ' '.join(r['text'] for r in db.execute('SELECT text FROM chunks'))
        metadata = json.loads(db.execute('SELECT quality_metadata FROM document_source_quality').fetchone()[0])
        assert 'native text has enough characters' in metadata['pages'][0]['raw_capture']['raw_text']
    assert 'SOURCE_REVIEWED_EQUATION' in text
    assert 'EDITORIAL_NOT_SOURCE' not in text


@pytest.mark.parametrize(('encoded','expected'), [
    ('löôõng','lưỡng'),
    ('tí tì tæ tó tò','tí tì tỉ tĩ tị'),
    ('ÑAÏI ÑEÀ AÙ','ĐẠI ĐỀ Á'),
])
def test_vni_character_mapping_does_not_invent_letters(encoded,expected):
    from app.text_cleaner import decode_vni
    assert decode_vni(encoded) == expected


def test_force_ocr_keeps_original_native_capture(tmp_path,monkeypatch):
    f = tmp_path/'native.pdf'
    native = 'Native source text must remain available independently of OCR.'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50,50),native);doc.save(f)
    monkeypatch.setattr(PDFExtractor,'ocr_available',classmethod(lambda cls:True))
    monkeypatch.setattr(PDFExtractor,'_ocr_page',classmethod(lambda cls,*a,**k:dict(text='Uncertain OCR rendition.',needs_review=True)))
    result = PDFExtractor.extract_pdf(f,force_ocr=True)
    raw = result.extracted_pages[0].get('raw_capture','')
    raw = raw.get('raw_text','') if isinstance(raw,dict) else raw
    assert native in raw
    assert result.needs_review is True


def test_code_string_literals_keep_significant_whitespace():
    source = 'class MyClass {\n    const char* text = "A  B\\tC";\n    int subtraction = value-\n        otherValue;\n};'
    assert normalize_vietnamese_text(source) == source


@pytest.mark.parametrize('source',['f = a-\nb','x = 1-\n2','result = abc-\ndef'])
def test_line_breaks_cannot_erase_a_subtraction_operator(source):
    assert normalize_vietnamese_text(source) == source


def test_image_ocr_draft_keeps_review_required(tmp_path,monkeypatch):
    image=tmp_path/'scan.png';image.write_bytes(b'fixture image bytes; OCR boundary mocked')
    monkeypatch.setattr(PDFExtractor,'ocr_available',classmethod(lambda cls:True))
    monkeypatch.setattr('app.pdf_extractor.VisionOCRManager.ocr_image_bytes',lambda *a,**kw:{'text':'An uncertain OCR transcript.','needs_review':True,'is_verified':False})
    result=PDFExtractor.extract_image(image)
    assert result.error_message is None
    assert result.needs_review is True
    assert result.extracted_pages[0]['needs_review'] is True
