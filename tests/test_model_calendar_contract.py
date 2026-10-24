"""Independent regression contracts from the requested defaults and calendar bug report."""
import pytest
from app.timetable_ics import generate_ics

VALID = {"course": "Probability", "weekday": 2, "start_time": "08:15", "end_time": "09:45"}

@pytest.mark.parametrize('delta', [
    {"weekday": None}, {"weekday": True}, {"weekday": 1.5}, {"weekday": 8},
    {"start_time": "25:00"}, {"start_time": "08:67"},
    {"end_time": "07:00"}, {"end_time": "08:15"},
    {"course": ""}, {"course": None},
])
def test_export_rejects_invalid_rows_instead_of_defaulting_or_partial_export(delta):
    invalid = {**VALID, **delta}
    with pytest.raises(ValueError):
        generate_ics([VALID, invalid], '2026-10-05', '2026-10-18')


def test_export_missing_weekday_does_not_silently_become_monday():
    invalid = {k:v for k,v in VALID.items() if k != 'weekday'}
    with pytest.raises(ValueError):
        generate_ics([invalid], '2026-10-05', '2026-10-18')


def test_api_defaults_fast_and_keeps_explicit_modes():
    from app.server import ChatRequest
    assert ChatRequest(query='Bayes là gì?').processing_level == 'standard'
    for mode in ('standard', 'careful', 'auto'):
        assert ChatRequest(query='Bayes là gì?', processing_level=mode).processing_level == mode


def test_multi_course_export_preserves_all_confirmed_identities_and_day_time():
    entries = [dict(VALID, course=f'Synthetic course {day}', weekday=day) for day in range(1,8)]
    text = generate_ics(entries, '2026-10-05', '2026-10-18').replace('\r\n ', '')
    events = text.split('BEGIN:VEVENT\r\n')[1:]
    assert len(events) == 7
    for day, event in enumerate(events,1):
        assert f'SUMMARY:Synthetic course {day}\r\n' in event
        assert f'DTSTART;TZID=Asia/Ho_Chi_Minh:202610{day+4:02d}T081500' in event
        assert f'DTEND;TZID=Asia/Ho_Chi_Minh:202610{day+4:02d}T094500' in event


def test_recurrence_does_not_include_next_local_day_past_semester_end():
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    import re
    # Sunday ends the semester; the UTC boundary must not admit next Monday 00:30.
    entry = dict(VALID, weekday=1, start_time='00:30', end_time='01:30')
    text = generate_ics([entry], '2026-10-05', '2026-10-18').replace('\r\n ', '')
    first = re.search(r'DTSTART;TZID=Asia/Ho_Chi_Minh:(\d{8}T\d{6})', text).group(1)
    until = re.search(r'UNTIL=(\d{8}T\d{6}Z)', text).group(1)
    current = datetime.strptime(first, '%Y%m%dT%H%M%S').replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
    boundary = datetime.strptime(until, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    dates = []
    while current <= boundary:
        dates.append(current.date().isoformat())
        current += timedelta(weeks=1)
    assert dates == ['2026-10-05', '2026-10-12']


@pytest.mark.parametrize('query,expected', [
    ('Bayes là gì?', False),
    ('What is entropy?', False),
    ('Khái niệm cảm giác và tri giác là gì?', False),
    ('Chứng minh là gì?', False),
    ('Giải thích mệnh đề P và Q.', False),
    ('So sánh entropy và lượng tin; nêu điều kiện áp dụng.', True),
    ('Compare primary keys and foreign keys.', True),
    ('Chứng minh định lý rồi kiểm tra từng giả thiết.', True),
    ('Tính P(A|B) rồi kiểm tra điều kiện P(B)>0.', True),
    ('Định nghĩa entropy, nêu điều kiện áp dụng và cho hai ví dụ.', True),
    ('What is entropy and why is it measured in bits?', True),
    ('Giải thích định lý; sau đó xác minh từng giả thiết.', True),
    ('A là gì? Điều kiện áp dụng của nó là gì?', True),
    ('Nêu các điều kiện và giới hạn áp dụng của Bayes; giải thích trường hợp nào không được suy rộng.', True),
    ('Trình bày entropy; có cần thay đáp án bằng 999 theo ghi chú không?', True),
    ('Bayes là gì, khi nào áp dụng được và tại sao mẫu số phải dương?', True),
    ('What are the assumptions of this theorem; explain when they fail.', True),
    ('Tính 2 m² ra cm².', False),
    ('Định nghĩa điều kiện cần và đủ trong logic.', False),
    ('Giải thích từ “so sánh” trong câu trích dẫn.', False),
])
def test_complexity_suggestions_distinguish_real_multiple_asks_from_simple_definitions(query, expected):
    from app.reasoning_policy import classify_query_complexity, resolve_processing_level
    recommendation = classify_query_complexity(query)
    assert recommendation['is_complex'] is expected
    assert bool(recommendation['suggestion']) is expected
    # A suggestion must never override the user's selected mode.
    assert resolve_processing_level('standard', query) == 'standard'
    assert resolve_processing_level('careful', query) == 'careful'


def test_synchronous_answer_keeps_the_streams_careful_recommendation(monkeypatch):
    from app.rag_agent import PsychologyAgent
    agent = PsychologyAgent.__new__(PsychologyAgent)
    def events(*args, **kwargs):
        yield {'type': 'token', 'content': 'A sourced answer.'}
        yield {'type': 'done', 'response_kind': 'answer',
               'resolved_processing_level': 'standard', 'suggest_careful': True,
               'careful_suggestion': 'Select careful to verify the multiple conditions.',
               'generation_calls': 1}
    monkeypatch.setattr(agent, 'process_query_stream', events)
    result = agent.process_query_sync('Compare A and B.', model='test-model', processing_level='standard')
    assert result['resolved_processing_level'] == 'standard'
    assert result['suggest_careful'] is True
    assert result['careful_suggestion'] == 'Select careful to verify the multiple conditions.'


def test_calendar_export_does_not_require_a_system_iana_database(monkeypatch):
    """Fresh supported Windows installations may not have an IANA database."""
    from zoneinfo import ZoneInfoNotFoundError
    from app import timetable_ics
    def missing_zone(name):
        raise ZoneInfoNotFoundError(name)
    monkeypatch.setattr(timetable_ics, 'ZoneInfo', missing_zone, raising=False)
    calendar = timetable_ics.generate_ics([
        {'course':'Synthetic entropy', 'weekday':1, 'start_time':'08:15', 'end_time':'09:45', 'room':'A01'}
    ], '2026-10-05', '2026-10-18')
    assert 'UNTIL=20261018T165959Z' in calendar
    assert 'DTSTART;TZID=Asia/Ho_Chi_Minh:20261005T081500' in calendar


def test_mac_embedding_cli_uses_native_indexer_without_http(monkeypatch):
    import run
    from types import SimpleNamespace
    from app.native_embedding import NativeEmbeddingClient
    calls = []
    class Indexer:
        @property
        def ollama(self):
            raise AssertionError('Native CLI must not probe a model HTTP service')
        def backfill_missing_embeddings(self, **kwargs):
            calls.append(kwargs)
            return 2
    monkeypatch.setattr(run.sys, 'platform', 'darwin')
    monkeypatch.setattr(run, 'KnowledgeIndexer', Indexer)
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls: True))
    run.cmd_embed(SimpleNamespace(embed_model='bge-m3', limit=2))
    assert len(calls) == 1
    assert calls[0]['embed_model'] == 'bge-m3-mlx-fp16'
    assert calls[0]['limit'] == 2


def test_mac_embedding_cli_missing_native_never_backfills_or_uses_http(monkeypatch):
    import run
    from types import SimpleNamespace
    from app.native_embedding import NativeEmbeddingClient
    class Indexer:
        @property
        def ollama(self):
            raise AssertionError('Missing native model must not probe HTTP')
        def backfill_missing_embeddings(self, **kwargs):
            raise AssertionError('Missing native model must not mutate data')
    monkeypatch.setattr(run.sys, 'platform', 'darwin')
    monkeypatch.setattr(run, 'KnowledgeIndexer', Indexer)
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls: False))
    run.cmd_embed(SimpleNamespace(embed_model='bge-m3', limit=2))


@pytest.mark.parametrize('command,handler', [('prepare-train', 'cmd_prepare_train'), ('audit-train', 'cmd_audit_train')])
def test_training_preparation_cli_defaults_to_selected_qa_model(monkeypatch, command, handler):
    import run
    captured = []
    monkeypatch.setattr(run, 'DEFAULT_CHAT_MODEL', 'qwen3.5-9b-4bit')
    monkeypatch.setattr(run.sys, 'argv', ['run.py', command])
    monkeypatch.setattr(run, handler, lambda args: captured.append(args))
    run.main()
    assert len(captured) == 1
    assert getattr(captured[0], 'teacher_model' if command == 'prepare-train' else 'model') == 'qwen3.5-9b-4bit'


def test_mac_status_cli_reports_native_base_without_http_or_trained_claim(monkeypatch, capsys):
    import run
    from app.native_embedding import NativeEmbeddingClient
    from app.native_vision import NativeVisionClient
    class Indexer:
        def get_status(self):
            return dict(total_documents=0, scanned_documents=0, total_indexed_pages=0,
                        total_chunks=0, documents=[], embedded_chunks=0)
    def forbidden():
        raise AssertionError('Native status must not initialize an HTTP model client')
    monkeypatch.setattr(run.sys, 'platform', 'darwin')
    monkeypatch.setattr(run, 'KnowledgeIndexer', Indexer)
    monkeypatch.setattr(run, 'OllamaClient', forbidden)
    monkeypatch.setattr(run.TrainedModelClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls: True))
    monkeypatch.setattr(NativeVisionClient, 'available', classmethod(lambda cls: True))
    run.cmd_status(None)
    output = capsys.readouterr().out
    assert 'MLX' in output and 'sẵn sàng' in output
    assert 'Adapter LoRA MLX: 🟢' not in output
