from app.timetable_ics import generate_ics
import pytest


def test_deadlines_have_nonrecurring_alarms_and_stable_private_uids():
    tasks=[{'id':7,'title':'Essay, draft\nBEGIN:BAD','course':'Writing','deadline_date':'2026-11-12','deadline_time':'17:00'}]
    text=generate_ics([], '2026-11-09','2026-12-01',deadlines=tasks,reminder_minutes=1440,namespace='account-a')
    assert 'BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT1440M' in text
    assert 'DTSTART;TZID=Asia/Ho_Chi_Minh:20261112T170000' in text
    assert 'RRULE:' not in text
    assert '\r\nBEGIN:BAD' not in text
    uid=next(line for line in text.splitlines() if line.startswith('UID:'))
    again=generate_ics([], '2026-11-09','2026-12-01',deadlines=tasks,namespace='account-a')
    other=generate_ics([], '2026-11-09','2026-12-01',deadlines=tasks,namespace='account-b')
    assert uid in again and uid not in other


def test_study_sessions_only_export_once_in_planned_week():
    text=generate_ics([{'uid':'study:3','course':'Revision','weekday':2,'event_date':'2026-11-10','start_time':'09:00','end_time':'10:00'}], '2026-11-01','2026-12-01')
    assert 'DTSTART;TZID=Asia/Ho_Chi_Minh:20261110T090000' in text
    assert 'RRULE:' not in text
    with pytest.raises(ValueError):
        generate_ics([{'course':'Revision','weekday':2,'event_date':'2026-11-11','start_time':'09:00','end_time':'10:00'}],'2026-11-01','2026-12-01')


def test_deadline_date_required_and_completed_tasks_excluded():
    with pytest.raises(ValueError,match='ngày cụ thể'):
        generate_ics([], '2026-11-09','2026-12-01',deadlines=[{'title':'Legacy','deadline_weekday':3}])
    task={'id':2,'title':'Todo','deadline_date':'2026-11-12','deadline_time':'17:00'}
    text=generate_ics([], '2026-11-09','2026-12-01',deadlines=[task,{**task,'title':'Done','completed':True}],reminder_minutes=0)
    assert 'VALARM' not in text and '[Deadline] Todo' in text and 'Done' not in text
    assert max(len(line.encode()) for line in text.split('\r\n')) <=75


def test_ics_rfc5545_apple_google_calendar_compliance():
    # 1. Deadlines must include valid DTEND after DTSTART for Apple/Google Calendar compatibility
    task = {'id': 9, 'title': 'Nop bai tap', 'course': 'Dai so', 'deadline_date': '2026-11-12', 'deadline_time': '17:00'}
    deadline_ics = generate_ics([], '2026-11-09', '2026-12-01', deadlines=[task], reminder_minutes=30)
    unfolded_deadline = deadline_ics.replace('\r\n ', '')
    assert 'DTSTART;TZID=Asia/Ho_Chi_Minh:20261112T170000' in unfolded_deadline
    assert 'DTEND;TZID=Asia/Ho_Chi_Minh:20261112T171500' in unfolded_deadline

    # 2. Blank room must omit empty LOCATION:, and relative PDF URL must only emit URL/ATTACH when absolute
    session = {
        'uid': 'study:9',
        'course': '[Tự học - Đại số tuyến tính] Ôn tập Không gian vector',
        'weekday': 2,
        'event_date': '2026-11-10',
        'start_time': '19:30',
        'end_time': '21:00',
        'room': '',
        'pdf_url': '/api/learning/export-pdf/prac_123',
    }
    rel_only_ics = generate_ics([session], '2026-11-09', '2026-12-01').replace('\r\n ', '')
    assert 'LOCATION:\r\n' not in rel_only_ics
    assert 'URL:/api/' not in rel_only_ics
    assert 'ATTACH;FMTTYPE=application/pdf:/api/' not in rel_only_ics
    assert '/api/learning/export-pdf/prac_123' in rel_only_ics

    abs_ics = generate_ics(
        [session], '2026-11-09', '2026-12-01', base_url='https://study.example'
    ).replace('\r\n ', '')
    assert 'URL:https://study.example/api/learning/export-pdf/prac_123' in abs_ics
    assert 'ATTACH;FMTTYPE=application/pdf:https://study.example/api/learning/export-pdf/prac_123' in abs_ics
    assert 'LOCATION:\r\n' not in abs_ics
    assert max(len(line.encode('utf-8')) for line in generate_ics(
        [session], '2026-11-09', '2026-12-01', deadlines=[task], base_url='https://study.example'
    ).split('\r\n')) <= 75
