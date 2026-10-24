"""Portable integration gate for the newly authored public debugging corpus."""
import hashlib
import json
from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent / 'fixtures'


def test_extended_manifest_has_diverse_reviewable_cases_and_only_public_sources():
    data = json.loads((FIXTURES/'extended_cases.json').read_text())
    assert data['synthetic'] is True and data['training_allowed'] is False
    assert data['quality_holdout'] is False
    assert data['topics'] == 12
    assert len(data['cases']) == 96
    assert len({c['id'] for c in data['cases']}) == 96
    for record in data['documents']:
        path = (FIXTURES / record['file']).resolve()
        assert path.is_relative_to((FIXTURES/'documents').resolve())
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record['sha256']
        assert len(raw) == record['bytes']
        text = raw.decode()
        assert '## Bài tập 1' in text and '## Đáp án bài 1' in text
        assert 'Không có đáp án nguồn' in text
    by_topic = {}
    for case in data['cases']:
        assert case['source_file'].startswith('extended_')
        assert (FIXTURES/'documents'/case['source_file']).is_file()
        assert case['training_allowed'] is False
        assert case['rubric']
        by_topic.setdefault(case['topic'], []).append(case)
    assert len(by_topic) == 12
    assert all(len(items)==8 for items in by_topic.values())
    assert {c['question_complexity'] for c in data['cases']} == {'simple','complex'}
    assert len([c for c in data['cases'] if c['expected_outcome']=='insufficient_evidence']) == 24


def test_new_documents_are_really_indexable_and_searchable_without_private_data(tmp_path):
    import shutil
    from app.indexer import KnowledgeIndexer
    import sqlite3
    source = tmp_path/'sources'; source.mkdir()
    manifest = json.loads((FIXTURES/'extended_cases.json').read_text())
    for record in manifest['documents']:
        path=FIXTURES/record['file']; shutil.copy2(path, source/path.name)
    database = tmp_path/'library.db'
    report = KnowledgeIndexer(database, source).index_all(ocr=False, embed_model=None)
    assert not report.get('error') and not report.get('error_files'), report
    with sqlite3.connect(database) as db:
        assert db.execute('select count(*) from documents').fetchone()[0] == 12
        for record in manifest['documents']:
            count=db.execute('select count(*) from chunks c join documents d on c.doc_id=d.id where d.filename=?', (Path(record['file']).name,)).fetchone()[0]
            assert count > 0, record['file']
        assert db.execute('select count(*) from chunks_fts where chunks_fts match ?', ('Bayes',)).fetchone()[0] > 0


def test_fictional_multisubject_draft_confirm_export_preserves_user_edits(tmp_path, monkeypatch):
    from app import timetable_store, timetable_ics
    monkeypatch.setenv('TIMETABLE_DB_PATH',str(tmp_path/'timetable.db'))
    data = json.loads((FIXTURES/'timetables/multisubject.json').read_text())
    entries=data['entries']
    assert len(entries)==14 and len({e['course'] for e in entries})==14
    timetable_store.save_draft('synthetic-review', 'a'*64, 'fictional.png', entries, [])
    assert timetable_store.get_confirmed_entries()==[]
    edited=[dict(e) for e in entries]
    edited[0]['course']='Xác suất — corrected by user'
    timetable_store.confirm_draft('synthetic-review', edited, clear_existing=True)
    saved=timetable_store.get_confirmed_entries()
    assert {e['course'] for e in saved} == {e['course'] for e in edited}
    text=timetable_ics.generate_ics(saved,data['start_date'],data['end_date']).replace('\r\n ','')
    assert text.count('BEGIN:VEVENT')==14
    assert len({line for line in text.split('\r\n') if line.startswith('UID:')})==14
    for entry in edited:
        assert 'SUMMARY:'+entry['course']+'\r\n' in text
        assert 'LOCATION:'+entry['room']+'\r\n' in text
    assert all(len(line.encode())<=75 for line in timetable_ics.generate_ics(saved,data['start_date'],data['end_date']).split('\r\n'))


def test_multisubject_image_draft_api_confirm_and_export_pipeline(tmp_path, monkeypatch):
    """Contract of the full API chain; VLM output is a fixture, not an OCR quality claim."""
    import base64
    from contextlib import nullcontext
    from fastapi.testclient import TestClient
    from app import server, timetable_vision as vision, timetable_table
    monkeypatch.setenv('TIMETABLE_DB_PATH', str(tmp_path/'timetable.db'))
    monkeypatch.setenv('TIMETABLE_IMAGES_DIR', str(tmp_path/'images'))
    data = json.loads((FIXTURES/'timetables/multisubject.json').read_text())
    raw = (FIXTURES/'timetables'/data['image_file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == data['image_sha256']
    output = {'entries': [dict(e, weekday_label='Chủ nhật' if e['weekday']==7 else f"Thứ {e['weekday']+1}")
                          for e in data['entries']], 'uncertainties': []}
    monkeypatch.setattr(timetable_table, 'extract_registered_table', lambda _: None)
    monkeypatch.setattr(vision, 'get_vlm_digest', lambda: 'd'*64)
    from app.native_vision import NativeVisionClient
    from types import SimpleNamespace
    monkeypatch.setattr(vision, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeVisionClient, 'generate_from_image', lambda *a, **kw: {
        'text': json.dumps(output), 'finish_reason': 'stop'})
    monkeypatch.setattr(vision.gpu_coordinator, 'check_inference_allowed', lambda: (True,''))
    monkeypatch.setattr(vision.gpu_coordinator, 'acquire_for_inference', lambda **kw: nullcontext())
    client = TestClient(server.app)
    old = dict(data['entries'][0], course='Previous semester course')
    assert client.post('/api/timetable/manual', json=old).status_code == 200
    request = {'filename':data['image_file'], 'image_base64':base64.b64encode(raw).decode()}
    response = client.post('/api/timetable/extract', json=request)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert len(draft['entries'])==14
    assert {e['course'] for e in draft['entries']} == {e['course'] for e in data['entries']}
    assert [e['course'] for e in client.get('/api/timetable').json()['entries']] == ['Previous semester course']
    # Reopening the same image creates a separate editable draft without another model call.
    cached = client.post('/api/timetable/extract', json=request).json()
    assert cached['cached'] is True and cached['draft_id'] != draft['draft_id']
    assert cached['entries'] == draft['entries']
    edited = [dict(e) for e in draft['entries']]
    edited[0]['course'] = 'User-corrected Bayes course'
    saved = client.post('/api/timetable/confirm', json={
        'draft_id':draft['draft_id'], 'entries':edited, 'replace_existing':True})
    assert saved.status_code == 200, saved.text
    confirmed = client.get('/api/timetable').json()['entries']
    assert {e['course'] for e in confirmed} == {e['course'] for e in edited}
    exported = client.get('/api/timetable/export.ics', params={
        'start_date':data['start_date'], 'end_date':data['end_date']})
    assert exported.status_code == 200, exported.text
    text = exported.text.replace('\r\n ', '')
    assert text.count('BEGIN:VEVENT') == 14
    assert 'Previous semester course' not in text
    assert all('SUMMARY:'+e['course']+'\r\n' in text for e in edited)


def test_registration_table_keeps_left_aligned_and_wrapped_subject_names():
    from app.timetable_table import parse_registered_table
    fixture = json.loads((FIXTURES/'timetables/registered_positions.json').read_text())
    result = parse_registered_table(fixture['lines'])
    assert result is not None
    assert [e['course'] for e in result['entries']] == fixture['expected_courses']
    assert [e['weekday'] for e in result['entries']] == [1,2,3]
    assert [e['room'] for e in result['entries']] == ['A101','B202','C303']
