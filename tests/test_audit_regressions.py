"""Regression cases for the isolated F01–F05 audit findings; synthetic data only."""
import re
import sqlite3
from datetime import datetime

import pytest

from app.indexer import KnowledgeIndexer
from app.rag_agent import PsychologyAgent
from app.timetable_ics import generate_ics
from app.timetable_table import parse_registered_table


class Searcher:
    def search_hybrid(self, **kwargs):
        return [dict(book_title='Nguồn thử', filename='fixture.txt', page_num=1,
                     chunk_index=0, text='Hệ thống 1 xử lý nhanh.', safe_text='Hệ thống 1 xử lý nhanh.')]


class Generator:
    def __init__(self, answer): self.answer = answer
    def chat_stream(self, **kwargs): yield self.answer


class Translator:
    def translate(self, chunks): return {}


@pytest.mark.parametrize('answer', [
    'Hệ thống 1 xử lý nhanh [S1]. Mọi quyết định vì thế luôn đúng [S999].',
    'Hệ thống 1 xử lý nhanh. Mọi quyết định vì thế luôn đúng.',
    'Thông tin chưa có căn cứ [S999].',
])
def test_unsupported_source_answer_is_not_displayed(answer):
    agent = PsychologyAgent(searcher=Searcher(), ollama=Generator(answer), translator=Translator())
    result = agent.process_query_sync('Hệ thống 1 là gì?', embed_model=None, model='test-ollama')
    assert result['citations'] == []
    assert 'chưa có đủ thông tin' in result['answer']
    assert 'Mọi quyết định' not in result['answer']


@pytest.mark.parametrize('answer, expected_citations', [
    ('Hệ thống 1 xử lý nhanh [S1].', 1),
    ('Tôi chưa có đủ thông tin đáng tin cậy để trả lời chắc chắn.', 0),
])
def test_valid_citation_and_honest_refusal_remain_safe(answer, expected_citations):
    agent = PsychologyAgent(searcher=Searcher(), ollama=Generator(answer), translator=Translator())
    result = agent.process_query_sync('Hệ thống 1 là gì?', embed_model=None, model='test-ollama')
    assert result['error'] is None
    assert len(result['citations']) == expected_citations
    assert '[S1]' not in result['answer']


def snapshot(index):
    with index.get_connection() as db:
        return (list(map(tuple, db.execute('SELECT * FROM documents ORDER BY id'))),
                list(map(tuple, db.execute('SELECT * FROM chunks ORDER BY id'))),
                db.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'oldsentinel'").fetchone()[0])


@pytest.mark.parametrize('failure', ['embedding', 'missing_vectors', 'database'])
def test_reindex_failure_preserves_old_index_and_retry_succeeds(tmp_path, monkeypatch, failure):
    src = tmp_path / 'src'; src.mkdir()
    source = src / 'fixture.txt'
    source.write_text('oldsentinel First version of our synthetic source. ' * 70)
    index = KnowledgeIndexer(tmp_path / 'index.db', src)
    index.index_all(embed_model=None)
    with index.get_connection() as db:
        db.execute("UPDATE chunks SET embedding = ?", (b'\x00\x00\x80?\x00\x00\x00@',))
    before = snapshot(index)
    assert len(before[1]) > 1 and before[2] > 0
    source.write_text('newsentinel Changed version of our synthetic source. ' * 70)
    if failure in ('embedding', 'missing_vectors'):
        monkeypatch.setattr(index.ollama, 'find_best_embed_model', lambda _: 'fixture-embed')
        monkeypatch.setattr(index.ollama, 'get_embedding', lambda *a, **k: [1., 2.])
        def fail(*args, **kwargs): raise RuntimeError('Controlled embedding failure')
        monkeypatch.setattr(index.ollama, 'get_batch_embeddings', fail if failure == 'embedding' else lambda texts, **k: [None] * len(texts))
        with pytest.raises(RuntimeError):
            index.index_all(embed_model='fixture-embed')
    else:
        with index.get_connection() as db:
            db.execute("CREATE TRIGGER fail_new BEFORE INSERT ON chunks WHEN NEW.text LIKE '%newsentinel%' BEGIN SELECT RAISE(ABORT, 'Controlled write failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match='Controlled write failure'):
            index.index_all(embed_model=None)
        with index.get_connection() as db: db.execute('DROP TRIGGER fail_new')
    assert snapshot(index) == before
    result = index.index_all(embed_model=None)
    assert result['indexed_files'] == 1 and result['skipped_files'] == 0
    with index.get_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'oldsentinel'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'newsentinel'").fetchone()[0] > 0
    assert snapshot(index)[0][0][5] != before[0][0][5]


def entry(**values):
    return dict(course='Môn thử', weekday=1, start_time='09:00', end_time='10:30', **values)


def unfold(ics):
    return re.sub(r'\r\n[ \t]', '', ics).split('\r\n')


def decode_text(value):
    return re.sub(r'\\([nN,;\\])', lambda m: '\n' if m[1].lower() == 'n' else m[1], value)


def test_ics_timestamp_is_utc_datetime():
    ics = generate_ics([entry()], '2026-10-05', '2026-10-30')
    stamp = next(line[8:] for line in unfold(ics) if line.startswith('DTSTAMP:'))
    assert re.fullmatch(r'\d{8}T\d{6}Z', stamp)
    datetime.strptime(stamp, '%Y%m%dT%H%M%SZ')


def test_ics_text_roundtrip_and_folding_preserve_unicode_without_new_properties():
    course = 'Toán, xác suất; \\ cơ bản\r\nX-AUDIT-INJECTED:unexpected ' + 'Tiếng Việt ' * 35 + 'kết thúc'
    room = 'Phòng, A; \\ B\rC'
    notes = 'Dòng 1\nDòng 2, ghi chú; \\n literal'
    event = entry(); event.update(course=course, room=room, notes=notes)
    ics = generate_ics([event], '2026-10-05', '2026-10-30')
    assert all(len(line.encode('utf-8')) <= 75 for line in ics.split('\r\n'))
    properties = unfold(ics)
    assert not any(line.startswith('X-AUDIT-INJECTED:') for line in properties)
    values = dict(line.split(':', 1) for line in properties if ':' in line)
    assert decode_text(values['SUMMARY']) == course.replace('\r\n', '\n')
    assert decode_text(values['LOCATION']) == room.replace('\r', '\n')
    assert decode_text(values['DESCRIPTION']) == 'Môn học: ' + course.replace('\r\n', '\n') + '\nPhòng: ' + room.replace('\r', '\n') + '\nGhi chú: ' + notes


def table_lines(start, end):
    return [
        {'text': text, 'bounds': bounds}
        for text, bounds in [
            ('Tên MH', [.28, .9, .04, .025]), ('Lịch LT', [.55, .9, .1, .025]),
            ('Lịch TH', [.8, .9, .1, .025]), ('MTH001', [.1, .75, .12, .025]),
            ('Môn thử', [.28, .75, .04, .025]), (f'T2 {start}-{end}', [.55, .75, .1, .025]),
        ]
    ]


@pytest.mark.parametrize('start,end', [('9:00','11:10'), ('09:00','11:10'), ('9:00','9:30')])
def test_valid_single_digit_hour_is_normalized(start, end):
    result = parse_registered_table(table_lines(start, end))
    assert result['entries'][0]['weekday'] == 1
    assert result['entries'][0]['start_time'] == '09:00'
    assert result['entries'][0]['end_time'] == end.zfill(5)


@pytest.mark.parametrize('start,end', [('9:00','9:00'), ('11:10','9:00'), ('24:00','25:00'), ('9:60','11:10')])
def test_invalid_clock_range_stays_unresolved(start, end):
    result = parse_registered_table(table_lines(start, end))
    assert result['entries'][0]['start_time'] is None
    assert result['uncertainties']


def test_extraction_failure_preserves_previous_index(tmp_path, monkeypatch):
    src = tmp_path / 'src'; src.mkdir()
    source = src / 'fixture.txt'
    source.write_text('oldsentinel Synthetic text that must remain searchable. ' * 35)
    index = KnowledgeIndexer(tmp_path / 'index.db', src)
    index.index_all(embed_model=None)
    before = snapshot(index)
    source.write_text('Changed source that cannot be extracted in this controlled test.')
    from app.pdf_extractor import PDFExtractor
    real_extract = PDFExtractor.extract_file
    def fail(path):
        result = real_extract(path)
        result.error_message = 'Controlled extraction failure'
        return result
    monkeypatch.setattr(PDFExtractor, 'extract_file', fail)
    result = index.index_all(embed_model=None)
    assert result['error_files'][0]['error'] == 'Controlled extraction failure'
    assert snapshot(index) == before
    monkeypatch.setattr(PDFExtractor, 'extract_file', real_extract)
    assert index.index_all(embed_model=None)['indexed_files'] == 1


def test_translator_handles_gpu_busy_gracefully(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.bilingual import LocalTranslator
    from app.gpu_lock import GPUBusyError
    @contextmanager
    def fake_acquire():
        raise GPUBusyError("GPU busy with training")
        yield

    monkeypatch.setattr("app.bilingual.gpu_coordinator.acquire_for_inference", fake_acquire)
    translator = LocalTranslator(cache_path=tmp_path / "translations.db", model="qwen2.5:7b")
    chunk = {
        "safe_text": "The mind and body respond to trauma, and the response of the nervous system changes with experience and the environment."
    }
    result = translator.translate([chunk])
    assert result == {}


def test_vision_ocr_detects_length_truncation_and_keep_alive(monkeypatch, legacy_model_http):
    from app.vision_ocr import VisionOCRManager
    from pathlib import Path
    captured_payloads = []

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"response": "Truncated code...", "done_reason": "length"}

    def fake_post(url, json=None, **kwargs):
        captured_payloads.append(json)
        return FakeResponse()

    legacy_model_http(fake_post)
    mgr = VisionOCRManager()
    # Exercise truncation after image validation with a real, nonblank fixture.
    image_bytes = (Path(__file__).parent / "fixtures/timetables/demo.png").read_bytes()
    with pytest.raises(RuntimeError, match="giới hạn độ dài"):
        mgr._ocr_ollama_vlm(image_bytes)

    assert len(captured_payloads) == 1
    assert captured_payloads[0].get("keep_alive") == "0s"

