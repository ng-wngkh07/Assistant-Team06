"""Registration administration must not become invented class appointments."""
from app import timetable_table

def lines(*texts):
    return [{'text':text,'bounds':[.1,.9-i*.04,.7,.03]} for i,text in enumerate(texts)]

def test_unknown_registration_without_clocks_is_explicitly_held():
    result=timetable_table.classify_registration_hold(lines('BẢNG ĐĂNG KÝ MÔN HỌC','IM1001 - Môn ví dụ','Thứ','Chưa biết','Tiết','--'))
    assert result['classification']=='incomplete_registration'  # nosec B101
    assert result['entries']==[]  # nosec B101
    assert result['uncertainties']  # nosec B101

def test_registration_window_dates_are_not_lesson_occurrences():
    result=timetable_table.classify_registration_hold(lines('Đăng ký môn học','Đợt đăng ký','Thời gian bắt đầu','Thời gian kết thúc','23/10/2026 09:00','24/10/2026 17:00'))
    assert result['classification']=='registration_windows'  # nosec B101
    assert result['entries']==[]  # nosec B101

def test_registration_with_actual_lesson_clocks_requires_row_level_parsing():
    assert timetable_table.classify_registration_hold(lines('Đăng ký môn học','Chưa biết','Thứ 2 07:00-09:00')) is None  # nosec B101

def test_other_calendar_layout_is_not_misclassified():
    assert timetable_table.classify_registration_hold(lines('Lịch học','Thứ 2','07:00 - 09:00','Môn ví dụ')) is None  # nosec B101

def test_registration_window_header_without_start_end_labels_is_also_held():
    result=timetable_table.classify_registration_hold(lines('ĐĂNG KÝ MÔN HỌC','Đợt Đăng ký','Thời gian đăng ký','12/10/2026 10:00'))
    assert result['classification']=='registration_windows'  # nosec B101

def test_tesseract_tsv_quotes_are_literal_not_csv_multiline_fields(tmp_path, monkeypatch):
    import subprocess  # nosec B404
    from PIL import Image
    monkeypatch.setattr(timetable_table,'BASE_DIR',tmp_path)
    engine_path=tmp_path/'tesseract';engine_path.write_bytes(b'local test OCR')
    monkeypatch.setattr(timetable_table.shutil,'which',lambda _: str(engine_path))
    path=tmp_path/'image.png';Image.new('RGB',(100,100),'white').save(path)
    header='block_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    data=header+'1\t1\t1\t10\t10\t20\t10\t80\t"literal\n2\t1\t1\t10\t30\t20\t10\t90\tsecond\n'
    monkeypatch.setattr(timetable_table.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0,data,''))
    observations,engine,_=timetable_table.read_local_lines(path)
    assert engine=='tesseract'  # nosec B101
    assert [x['text'] for x in observations]==['"literal','second']  # nosec B101

import pytest

@pytest.mark.parametrize(('label','expected'),[('Th 2, 19/10',1),('Th4, 21/10',3),('Th 6',5),('Th 21',None),('Chưa biết',None)])
def test_mobile_weekday_labels_keep_iso_weekday_convention(label,expected):
    from app.timetable_vision import normalize_weekday
    assert normalize_weekday(label)==expected  # nosec B101

def test_grid_weekday_uses_literal_header_aligned_with_unique_course_code():
    observations=[{'text':'Thứ tư','bounds':[.34,.85,.09,.02]}, {'text':'Thứ sáu','bounds':[.68,.85,.10,.02]}, {'text':'(ACC100001)','bounds':[.64,.30,.18,.02]}]
    evidence=timetable_table.grid_weekday_evidence(observations)
    assert evidence['ACC100001']['weekday']==5  # nosec B101
    assert evidence['ACC100001']['header']=='Thứ sáu'  # nosec B101
    # Repeated course identifiers cannot establish one occurrence's weekday.
    assert 'ACC100001' not in timetable_table.grid_weekday_evidence(observations+[{'text':'(ACC100001)','bounds':[.30,.50,.18,.02]}])  # nosec B101

def test_grid_weekday_does_not_infer_missing_columns_or_use_vertical_list_dates():
    observations=[{'text':'Thứ tư','bounds':[.34,.85,.09,.02]}, {'text':'Thứ sáu','bounds':[.68,.85,.10,.02]}, {'text':'(ACC100001)','bounds':[.48,.30,.18,.02]}]
    assert not timetable_table.grid_weekday_evidence(observations)  # nosec B101
    observations[1]['bounds'][1]=.4
    assert not timetable_table.grid_weekday_evidence(observations)  # nosec B101


def test_grid_room_corroboration_replaces_lhp_code_with_literal_cell_room(monkeypatch, tmp_path):
    import io
    import json as _json
    from PIL import Image
    from app import timetable_store, timetable_vision

    monkeypatch.setenv('TIMETABLE_DB_PATH', str(tmp_path / 'tt.db'))
    with timetable_store.get_connection():
        pass
    monkeypatch.setattr(timetable_table, 'extract_registered_table', lambda _b: None)

    observations = [
        {'text': 'Thứ 2', 'bounds': [0.14, 0.59, 0.05, 0.02]},
        {'text': 'Thứ 4', 'bounds': [0.39, 0.59, 0.05, 0.02]},
        {'text': 'F1-509', 'bounds': [0.13, 0.355, 0.06, 0.02]},
        {'text': '(MATH132901E)', 'bounds': [0.12, 0.301, 0.09, 0.02]},
        {'text': 'LHP: 261MATH132901E_58FIE', 'bounds': [0.09, 0.276, 0.14, 0.02]},
    ]
    assert timetable_table.grid_weekday_evidence(observations)['MATH132901E']['weekday'] == 1  # nosec B101
    assert timetable_table.grid_room_evidence(observations)['MATH132901E']['room'] == 'F1-509'  # nosec B101

    monkeypatch.setattr(
        timetable_table,
        'read_grid_cell_evidence',
        lambda _b: (
            timetable_table.grid_weekday_evidence(observations),
            timetable_table.grid_room_evidence(observations),
        ),
    )

    class _FakeNativeVision:
        @classmethod
        def available(cls):
            return True

        def get_identity(self):
            return 'fake-vlm-digest'

        def generate_from_image(self, _img, **_kwargs):
            return {
                'text': _json.dumps({
                    'entries': [{
                        'course': 'Mathematical Statistics for Engineers (MATH132901E)',
                        'weekday_label': 'Thứ 2',
                        'start_time': '07:30',
                        'end_time': None,
                        'period': '1-5',
                        'room': '261MATH132901E_58FIE',
                    }],
                    'uncertainties': [],
                })
            }

    from app import native_vision
    monkeypatch.setattr(native_vision, 'NativeVisionClient', _FakeNativeVision)
    monkeypatch.setattr(timetable_vision.gpu_coordinator, 'check_inference_allowed', lambda: (True, ''))
    monkeypatch.setattr(timetable_vision.sys, 'platform', 'darwin')

    buf = io.BytesIO()
    sample = Image.new('RGB', (120, 80), 'white')
    sample.putpixel((10, 10), (20, 20, 20))
    sample.save(buf, format='PNG')
    draft = timetable_vision.call_vlm_extract(buf.getvalue(), 'img_hash_lhp', 'week.jpg')
    assert draft['entries'][0]['room'] == 'F1-509'  # nosec B101
    assert draft['entries'][0]['weekday'] == 1  # nosec B101


def test_hcmute_registration_results_table_is_held_out_of_confirmed_schedule_entries():
    obs = [
        {'text': 'KẾT QUẢ ĐĂNG KÝ HỌC PHẦN', 'bounds': [0.03, 0.90, 0.28, 0.04]},
        {'text': 'Mã học phần', 'bounds': [0.12, 0.67, 0.07, 0.03]},
        {'text': 'Tên học phần', 'bounds': [0.38, 0.67, 0.07, 0.03]},
        {'text': 'Số tín chỉ', 'bounds': [0.61, 0.67, 0.05, 0.03]},
        {'text': 'Ngày đăng ký', 'bounds': [0.72, 0.67, 0.07, 0.03]},
        {'text': '261DSAL220229E_03FIE', 'bounds': [0.09, 0.55, 0.12, 0.02]},
        {'text': 'Data Structure and Algorithm', 'bounds': [0.35, 0.55, 0.14, 0.02]},
        {'text': '11/08/2026 09:03:48', 'bounds': [0.71, 0.55, 0.10, 0.02]},
    ]
    held = timetable_table.classify_registration_hold(obs)
    assert held is not None  # nosec B101
    assert held['classification'] == 'incomplete_registration'  # nosec B101
    assert held['entries'] == []  # nosec B101
    assert any(p['course_code'] == 'DSAL220229E' and 'Data Structure' in p['course'] for p in held['partial_entries'])  # nosec B101


def test_grid_corroborates_cyrillic_room_tiet_range_12g45_and_clears_equal_end_time(monkeypatch, tmp_path):
    import io
    import json as _json
    from PIL import Image
    from app import timetable_store, timetable_vision

    monkeypatch.setenv('TIMETABLE_DB_PATH', str(tmp_path / 'tt_afternoon.db'))
    with timetable_store.get_connection():
        pass
    monkeypatch.setattr(timetable_table, 'extract_registered_table', lambda _b: None)

    assert timetable_vision.format_clock_time('12g45') == '12:45'  # nosec B101

    # Note Cyrillic 'А' (\u0410) in room 'А3-303' and 'Số tiết: 2' above 'Tiết: 7-8'
    observations = [
        {'text': '\u04103-303', 'bounds': [0.70, 0.72, 0.06, 0.02]},
        {'text': 'Data Structure and Algorithm', 'bounds': [0.66, 0.68, 0.14, 0.02]},
        {'text': '(DSAL220229E)', 'bounds': [0.69, 0.66, 0.09, 0.02]},
        {'text': 'Số tiết: 2', 'bounds': [0.71, 0.62, 0.05, 0.02]},
        {'text': 'Tiết: 7-8', 'bounds': [0.71, 0.60, 0.05, 0.02]},
        {'text': 'Giờ bắt đầu: 12g45', 'bounds': [0.69, 0.58, 0.09, 0.02]},
    ]
    ev = timetable_table.grid_room_evidence(observations)['DSAL220229E']
    assert ev['room'] == 'A3-303'  # nosec B101
    assert ev['period'] == '7-8'  # nosec B101
    assert ev['start_time'] == '12:45'  # nosec B101

    monkeypatch.setattr(timetable_table, 'read_grid_cell_evidence', lambda _b: ({}, {'DSAL220229E': ev}))

    class _FakeVision:
        @classmethod
        def available(cls):
            return True

        def get_identity(self):
            return 'fake-vlm-afternoon'

        def generate_from_image(self, _img, **_kwargs):
            return {
                'text': _json.dumps({
                    'entries': [
                        {
                            'course': 'Data Structure and Algorithm (DSAL220229E)',
                            'weekday_label': None,
                            'start_time': None,
                            'end_time': None,
                            'period': '2',
                            'room': '\u04103-303',
                        },
                        {
                            'course': 'BAFI3182',
                            'weekday_label': 'Thứ 3',
                            'start_time': '12:00',
                            'end_time': '12:00',
                            'period': None,
                            'room': '05',
                        },
                    ],
                    'uncertainties': [],
                })
            }

    from app import native_vision
    monkeypatch.setattr(native_vision, 'NativeVisionClient', _FakeVision)
    monkeypatch.setattr(timetable_vision.gpu_coordinator, 'check_inference_allowed', lambda: (True, ''))
    monkeypatch.setattr(timetable_vision.sys, 'platform', 'darwin')

    buf = io.BytesIO()
    img = Image.new('RGB', (120, 80), 'white')
    img.putpixel((10, 10), (20, 20, 20))
    img.save(buf, format='PNG')
    draft = timetable_vision.call_vlm_extract(buf.getvalue(), 'img_hash_hcmute', 'afternoon.jpg')
    e0, e1 = draft['entries']
    assert e0['room'] == 'A3-303'  # nosec B101
    assert e0['period'] == '7-8'  # nosec B101
    assert e0['start_time'] == '12:45'  # nosec B101
    assert e1['start_time'] == '12:00' and e1['end_time'] is None  # nosec B101
    assert any('không lớn hơn giờ bắt đầu' in u for u in draft['uncertainties'])  # nosec B101


def test_timetable_vlm_passes_output_schema_to_native_mlx(monkeypatch):
    import io
    import json
    from contextlib import nullcontext
    from PIL import Image
    from app import timetable_vision

    captured = {}

    class FakeNativeVision:
        def get_identity(self):
            return 'fake-vlm-digest'

        @classmethod
        def available(cls):
            return True

        def generate_from_image(self, image_bytes, *, prompt, timeout_seconds=360, response_schema=None, **kwargs):
            captured['schema'] = response_schema
            captured['prompt'] = prompt
            return {'text': json.dumps({'entries': [{'course': 'Toán', 'weekday_label': 'Thứ 2', 'start_time': '08:00', 'end_time': '09:30', 'period': None, 'room': 'B1'}], 'uncertainties': []})}

    monkeypatch.setattr(timetable_vision.sys, 'platform', 'darwin')
    monkeypatch.setattr('app.native_vision.NativeVisionClient', FakeNativeVision)
    monkeypatch.setattr('app.timetable_table.extract_registered_table', lambda *a: None)
    monkeypatch.setattr(timetable_vision.gpu_coordinator, 'acquire_for_inference', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(timetable_vision.timetable_store, 'get_vlm_cache', lambda *a: None)
    monkeypatch.setattr(timetable_vision.timetable_store, 'set_vlm_cache', lambda *a: None)
    monkeypatch.setattr(timetable_vision.timetable_store, 'save_draft', lambda **kw: None)

    buf = io.BytesIO()
    img = Image.new('RGB', (120, 80), 'white')
    img.putpixel((5, 5), (0, 0, 0))
    img.save(buf, format='PNG')
    res = timetable_vision.call_vlm_extract(buf.getvalue(), 'h123', 'test.png')
    assert captured['schema'] == timetable_vision.OUTPUT_SCHEMA  # nosec B101
    assert res['entries'][0]['course'] == 'Toán'  # nosec B101
