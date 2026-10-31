"""Explicit library scopes retain original identities; no model inference."""
from pathlib import Path

import pytest

from app.indexer import KnowledgeIndexer
from app.study_materials import StudyMaterialManager, StudyMaterialError


@pytest.fixture
def library(tmp_path, monkeypatch):
    import app.config as cfg
    sources = tmp_path / 'sources'
    for relative, text in [('a/notes.txt', 'First original: entropy measures uncertainty.'),
                           ('b/notes.txt', 'Second original: a stack follows last-in-first-out order.')]:
        source = sources / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text, encoding='utf-8')
    database = tmp_path / 'library.db'
    indexer = KnowledgeIndexer(database, sources)
    indexer.index_all(ocr=False, embed_model=None)
    with indexer.get_connection() as db:
        ids = {r[1]: r[0] for r in db.execute('SELECT id,filename FROM documents')}
        db.execute("UPDATE documents SET clean_title='b/notes.txt' WHERE filename='a/notes.txt'")
    monkeypatch.setattr(cfg, 'DB_PATH', database)
    monkeypatch.setattr(cfg, 'SRC_DIR', sources)
    monkeypatch.setattr(cfg, 'BASE_DIR', tmp_path)
    manager = StudyMaterialManager()
    monkeypatch.setattr(manager, 'generate_materials_from_pages', lambda extracted, **kwargs: {
        'filename': extracted.filename, 'pages': extracted.extracted_pages})
    return manager, ids


def invoke(manager, operation, **kwargs):
    if operation == 'inspect':
        return manager.inspect_library_document(**kwargs)
    return manager.generate_from_library_document(**kwargs)


@pytest.mark.parametrize('operation', ['inspect', 'generate'])
def test_explicit_document_and_filename_must_agree(library, operation):
    manager, ids = library
    with pytest.raises(StudyMaterialError, match='khớp|khác|mâu thuẫn'):
        invoke(manager, operation, doc_id=ids['a/notes.txt'], filename='b/notes.txt')


@pytest.mark.parametrize('operation', ['inspect', 'generate'])
def test_missing_explicit_document_cannot_fall_back_to_filename(library, operation):
    manager, _ = library
    with pytest.raises(StudyMaterialError, match='tìm thấy'):
        invoke(manager, operation, doc_id=999999, filename='b/notes.txt')


@pytest.mark.parametrize('operation', ['inspect', 'generate'])
def test_exact_filename_wins_over_another_document_title(library, operation):
    manager, ids = library
    result = invoke(manager, operation, filename='b/notes.txt')
    assert result['filename'] == 'b/notes.txt'
    assert 'Second original' in result['pages'][0]['text']
    if operation == 'inspect':
        assert result['doc_id'] == ids['b/notes.txt']


@pytest.mark.parametrize('operation', ['inspect', 'generate'])
def test_cached_title_cannot_replace_an_explicit_original(library, operation):
    manager, ids = library
    manager._materials['unrelated'] = {'filename': 'unrelated.txt',
        'clean_title': 'b/notes.txt', 'doc_id': ids['a/notes.txt'],
        'pages': [{'page_num': 1, 'text': 'Unrelated cached private passage.'}]}
    result = invoke(manager, operation, doc_id=ids['b/notes.txt'])
    assert result['filename'] == 'b/notes.txt'
    assert 'Second original' in result['pages'][0]['text']


@pytest.mark.parametrize('operation', ['inspect', 'generate'])
def test_nested_original_retains_its_relative_filename(library, operation):
    manager, ids = library
    result = invoke(manager, operation, doc_id=ids['a/notes.txt'])
    assert result['filename'] == 'a/notes.txt'
    assert 'First original' in result['pages'][0]['text']


def test_missing_nested_download_cannot_use_another_basename_cache(library):
    manager, _ = library
    manager.cache_raw_file('notes.txt', b'Unrelated cached original')
    assert manager.resolve_raw_file(filename='missing/notes.txt') is None


def test_download_identifiers_must_agree(library):
    manager, ids = library
    assert manager.resolve_raw_file(doc_id=ids['a/notes.txt'], filename='b/notes.txt') is None
    assert manager.resolve_raw_file(doc_id=999999, filename='b/notes.txt') is None


def test_material_download_cannot_use_unrelated_material_bytes(library):
    manager, _ = library
    manager._materials['session'] = {'filename': 'private.txt'}
    manager.cache_raw_file('private.txt', b'Private unrelated bytes', 'session')
    assert manager.resolve_raw_file(filename='missing.txt', material_id='session') is None


def test_nested_cached_downloads_keep_separate_original_bytes(library):
    manager, _ = library
    manager.cache_raw_file('pending-a/notes.txt', b'Cached A')
    manager.cache_raw_file('pending-b/notes.txt', b'Cached B')
    assert manager.resolve_raw_file(filename='pending-a/notes.txt') == ('notes.txt', b'Cached A')
    assert manager.resolve_raw_file(filename='pending-b/notes.txt') == ('notes.txt', b'Cached B')


@pytest.mark.parametrize('operation', ['inspect', 'generate'])
def test_original_file_wins_over_stale_unindexed_cache(library, operation):
    import app.config as cfg
    manager, _ = library
    (cfg.SRC_DIR / 'fresh.txt').write_text('Current source bytes after editing: a queue follows first-in-first-out order.', encoding='utf-8')
    manager._materials['stale'] = {'filename': 'fresh.txt', 'pages': [
        {'page_num': 1, 'text': 'Old stale cached content.'}]}
    result = invoke(manager, operation, filename='fresh.txt')
    assert 'Current source bytes' in result['pages'][0]['text']
