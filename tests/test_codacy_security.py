"""Security regressions use synthetic files; no models or private corpus are read."""

import ast
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

from app.pdf_extractor import PDFExtractor

ROOT = Path(__file__).resolve().parents[1]
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def office_parts():
    return {
        'ppt/presentation.xml': f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId r:id="s1"/><p:sldId r:id="s2"/></p:sldIdLst></p:presentation>',
        'ppt/_rels/presentation.xml.rels': '<Relationships><Relationship Id="s1" Target="slides/slide2.xml"/><Relationship Id="s2" Target="slides/slide1.xml"/></Relationships>',
        'ppt/slides/slide2.xml': f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><a:p><a:t>Tiếng Việt &amp; 2 &lt; 3</a:t></a:p></p:sld>',
        'ppt/slides/slide1.xml': f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><a:p><a:t>Second slide</a:t></a:p></p:sld>',
        'ppt/slides/_rels/slide1.xml.rels': f'<Relationships><Relationship Type="{R}/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>',
        'ppt/notesSlides/notesSlide1.xml': f'<p:notes xmlns:p="{P}" xmlns:a="{A}"><a:p><a:t>Ghi chú</a:t></a:p></p:notes>',
    }


def archive(path, parts):
    with zipfile.ZipFile(path, 'w') as stream:
        for name, xml in parts.items():
            stream.writestr(name, xml)
    return path


def malicious(xml, encoding, entity=True):
    declaration = '<!DOCTYPE root [<!ENTITY injected "FORGED_SOURCE">]>' if entity else '<!DOCTYPE root>'
    if entity:
        xml = xml.replace('</', '&injected;</', 1)
    return (f'<?xml version="1.0" encoding="{encoding}"?>' + declaration + xml).encode(encoding)


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
@pytest.mark.parametrize('entity', [True, False], ids=['entity', 'bare-dtd'])
def test_docx_rejects_dtd_before_extraction(tmp_path, encoding, entity):
    xml = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Original</w:t></w:r></w:p></w:body></w:document>'
    path = archive(tmp_path / 'source.docx', {'word/document.xml': malicious(xml, encoding, entity)})
    result = PDFExtractor.extract_docx(path)
    assert result.error_message
    assert result.extracted_pages == []
    assert result.total_chars == 0


@pytest.mark.parametrize('part', list(office_parts()))
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16'])
def test_pptx_rejects_dtd_in_every_xml_part_without_partial_content(tmp_path, part, encoding):
    parts = office_parts()
    parts[part] = malicious(parts[part], encoding, entity=False)
    result = PDFExtractor.extract_pptx(archive(tmp_path / 'source.pptx', parts))
    assert result.error_message
    assert result.extracted_pages == []
    assert result.total_chars == 0


def test_valid_office_unicode_escaping_slide_order_and_notes(tmp_path):
    xml = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Tiếng Việt &amp; 2 &lt; 3</w:t></w:r></w:p></w:body></w:document>'
    docx = PDFExtractor.extract_docx(archive(tmp_path / 'valid.docx', {'word/document.xml': xml}))
    assert not docx.error_message
    assert docx.extracted_pages[0]['text'] == 'Tiếng Việt & 2 < 3'
    pptx = PDFExtractor.extract_pptx(archive(tmp_path / 'valid.pptx', office_parts()))
    assert not pptx.error_message
    assert pptx.total_pages == 2
    assert pptx.extracted_pages[0]['text'] == 'Tiếng Việt & 2 < 3'
    assert pptx.extracted_pages[1]['text'] == 'Second slide\n\n[GHI CHÚ TRANG CHIẾU]\nGhi chú'


@pytest.mark.parametrize('extension', ['docx', 'pptx'])
def test_upload_boundary_does_not_cache_rejected_office_content(tmp_path, monkeypatch, extension):
    import app.study_materials as materials
    monkeypatch.setattr(materials, 'DATA_DIR', tmp_path / 'isolated-data')
    if extension == 'docx':
        xml = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{"Forged text " * 10}</w:t></w:r></w:p></w:body></w:document>'
        parts = {'word/document.xml': malicious(xml, 'utf-8')}
    else:
        parts = office_parts()
        parts['ppt/slides/slide2.xml'] = parts['ppt/slides/slide2.xml'].replace('Tiếng Việt', 'Synthetic readable content ' * 5)
        parts['ppt/notesSlides/notesSlide1.xml'] = malicious(parts['ppt/notesSlides/notesSlide1.xml'], 'utf-8')
    path = archive(tmp_path / f'unsafe.{extension}', parts)
    manager = materials.StudyMaterialManager()
    with pytest.raises(materials.StudyMaterialError):
        manager.extract_document(path.name, path.read_bytes())
    assert manager._raw_files == {}
    assert not list((tmp_path / 'isolated-data' / 'runtime' / 'user_uploads').glob('*'))


def test_tokenizer_audit_refuses_custom_code_and_network(tmp_path, monkeypatch):
    from app.token_auditor import _run_exact_audit_local
    calls = []

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            calls.append((path, kwargs))
            raise ValueError('This tokenizer requires custom code')

    monkeypatch.setitem(sys.modules, 'transformers', types.SimpleNamespace(AutoTokenizer=AutoTokenizer))
    model = tmp_path / 'model'
    model.mkdir()
    with pytest.raises(RuntimeError, match='requires custom code'):
        _run_exact_audit_local(tmp_path, model)
    assert calls == [(str(model), {'trust_remote_code': False, 'local_files_only': True})]


def test_builtin_tokenizer_audit_stays_usable_offline(tmp_path, monkeypatch):
    from app.token_auditor import _run_exact_audit_local

    class Tokenizer:
        def apply_chat_template(self, messages, tokenize=True):
            return [1, 2, 3]

        def encode(self, content, add_special_tokens=False):
            return [3]

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            if kwargs != {'trust_remote_code': False, 'local_files_only': True}:
                raise ValueError('Unsafe loader policy')
            return Tokenizer()

    monkeypatch.setitem(sys.modules, 'transformers', types.SimpleNamespace(AutoTokenizer=AutoTokenizer))
    model = tmp_path / 'model'
    model.mkdir()
    row = {'messages': [{'role': 'user', 'content': 'Synthetic question'}, {'role': 'assistant', 'content': 'Synthetic answer'}]}
    for name in ('train.jsonl', 'valid.jsonl'):
        (tmp_path / name).write_text(json.dumps(row) + '\n', encoding='utf-8')
    report = _run_exact_audit_local(tmp_path, model)
    assert not report['has_violations']
    assert report['total_samples'] == 2


@pytest.mark.parametrize('record', ['{broken', '[]', '{"run_id":"run-bad","status":"FAILED"}', '{"signature_hash":"same","status":"FAILED"}', '{"run_id":"run-bad","signature_hash":"same","status":null}', '{"run_id":"run-bad","signature_hash":"same","status":[]}', '{"run_id":"run-bad","signature_hash":"same","status":{}}'])
def test_uninspectable_ledger_blocks_even_with_remediation(tmp_path, record):
    from app.run_ledger import RunLedger
    ledger = RunLedger(runs_dir=tmp_path / 'runs', index_file=tmp_path / 'index.jsonl')
    path = ledger.runs_dir / 'run-bad.json'
    path.write_text(record, encoding='utf-8')
    remediation = {'diagnosis': 'Synthetic diagnosis with detail', 'evidence': 'Synthetic evidence with detail', 'code_diff_hash': 'abc123'}
    blocked, message, _ = ledger.check_duplicate_signature('same', remediation)
    assert blocked
    assert path.name in message


def test_valid_ledger_duplicate_and_remediation_contract(tmp_path):
    from app.run_ledger import RunLedger
    ledger = RunLedger(runs_dir=tmp_path / 'runs', index_file=tmp_path / 'index.jsonl')
    entry = {'run_id': 'run-old', 'signature_hash': 'same', 'status': 'FAILED'}
    (ledger.runs_dir / 'run-old.json').write_text(json.dumps(entry), encoding='utf-8')
    assert ledger.check_duplicate_signature('different') == (False, None, None)
    blocked, _, matched = ledger.check_duplicate_signature('same')
    assert blocked and matched == entry
    remediation = {'diagnosis': 'Synthetic diagnosis with detail', 'evidence': 'Synthetic evidence with detail', 'code_diff_hash': 'abc123'}
    assert ledger.check_duplicate_signature('same', remediation) == (False, None, None)


@pytest.mark.parametrize('status', ['CREATED', 'PREFLIGHT', 'FAILED', 'OOM', 'BLOCKED', 'CANCELLED', 'KILLED_ABNORMAL'])
def test_legitimate_unbound_preflight_records_do_not_block_unrelated_runs(tmp_path, status):
    from app.run_ledger import RunLedger
    ledger = RunLedger(runs_dir=tmp_path / 'runs', index_file=tmp_path / 'index.jsonl')
    entry = {'run_id': 'run-preflight', 'signature_hash': None, 'status': status,
             'manifest': {'dataset': {'dataset_sha256': 'pending_preflight'}}}
    (ledger.runs_dir / 'run-preflight.json').write_text(json.dumps(entry), encoding='utf-8')
    assert ledger.check_duplicate_signature('unrelated-verified-signature') == (False, None, None)


@pytest.mark.parametrize(('status', 'manifest'), [
    ('FAILED', {'dataset': []}), ('FAILED', {'dataset': None}),
    ('RUNNING', {'dataset': {'dataset_sha256': 'pending_preflight'}}),
    ('COMPLETED', {'dataset': {'dataset_sha256': 'pending_preflight'}}),
])
def test_invalid_null_signature_schema_blocks_cleanly(tmp_path, status, manifest):
    from app.run_ledger import RunLedger
    ledger = RunLedger(runs_dir=tmp_path / 'runs', index_file=tmp_path / 'index.jsonl')
    entry = {'run_id': 'run-bad', 'signature_hash': None, 'status': status, 'manifest': manifest}
    (ledger.runs_dir / 'run-bad.json').write_text(json.dumps(entry), encoding='utf-8')
    blocked, message, _ = ledger.check_duplicate_signature('same')
    assert blocked
    assert 'run-bad.json' in message


def test_valid_source_physical_page_checks_still_work(tmp_path):
    import pymupdf
    from app.corpus_scope import validate_page_number
    (tmp_path / 'src').mkdir()
    with pymupdf.open() as document:
        document.new_page()
        document.save(tmp_path / 'src' / 'valid.pdf')
    assert validate_page_number(1, 'valid.pdf', root=tmp_path) == 1
    with pytest.raises(ValueError, match='out of range'):
        validate_page_number(2, 'valid.pdf', root=tmp_path)


def test_unreadable_source_cannot_pass_physical_page_validation(tmp_path):
    from app.corpus_scope import validate_page_number
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'corrupt.pdf').write_bytes(b'not a PDF')
    with pytest.raises(ValueError):
        validate_page_number(999, 'corrupt.pdf', root=tmp_path)


def test_failed_office_source_extraction_cannot_validate_a_page(tmp_path, monkeypatch):
    from app.corpus_scope import validate_page_number
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'failed.docx').write_bytes(b'synthetic placeholder')
    monkeypatch.setattr(PDFExtractor, 'extract_file', lambda path: types.SimpleNamespace(
        total_pages=2, error_message='Unsafe XML part', extracted_pages=[]))
    with pytest.raises(ValueError):
        validate_page_number(1, 'failed.docx', root=tmp_path)


def test_source_free_form_page_validation_remains_supported(tmp_path):
    from app.corpus_scope import validate_page_number
    assert validate_page_number('2', 'absent.pdf') == 2
    assert validate_page_number('2', 'absent.pdf', root=tmp_path) == 2


@pytest.mark.parametrize(('file', 'marker', 'bad', 'good'), [
    ('app/prepare_v7.py', 'Train samples (', {'train_unique': []}, {'train_unique': list(range(2048))}),
    ('app/prepare_v7.py', 'Concept groups (', {'concept_groups': 0}, {'concept_groups': 400}),
    ('scripts/curate_v8_release.py', 'Token audit có vi phạm:', {'report': {'has_violations': True, 'violations': ['unsafe']}}, {'report': {'has_violations': False}}),
    ('scripts/rejudge_v7_candidates.py', 'Defect ', {'def_id': 'bad', 'accepted_ids': {'bad'}}, {'def_id': 'bad', 'accepted_ids': set()}),
])
def test_acceptance_gates_still_reject_under_python_optimization(file, marker, bad, good):
    # Execute the actual gate in isolation, without running dataset-producing scripts.
    tree = ast.parse((ROOT / file).read_text(encoding='utf-8'))
    candidates = [node for node in ast.walk(tree) if isinstance(node, (ast.Assert, ast.If))
                  and any(isinstance(child, ast.Constant) and isinstance(child.value, str)
                          and child.value.startswith(marker) for child in ast.walk(node))]
    gate = min(candidates, key=lambda node: node.end_lineno - node.lineno)
    code = compile(ast.fix_missing_locations(ast.Module(body=[gate], type_ignores=[])), file, 'exec', optimize=2)
    with pytest.raises((ValueError, RuntimeError, AssertionError)):
        exec(code, bad)
    exec(code, good)
