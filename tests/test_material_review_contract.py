"""Source-authored transport/review contracts; no claims of real model quality."""
import json
from pathlib import Path

import pytest

from app.pdf_extractor import PDFExtractionResult
from app.study_materials import StudyMaterialManager, StudyMaterialError, build_quiz_prompt, resolve_safe_source_file


RULES = ('Máy chỉ chạy khi cửa bảo vệ đóng, khóa an toàn đã cài và nhiệt độ bình thường. '
         'Nút ghi đè chỉ bỏ qua cảnh báo của cảm biến nhiệt, không bỏ qua điều kiện cửa hoặc khóa.')


def source():
    result = PDFExtractionResult(filename='authored.txt', filepath=Path('authored.txt'))
    result.extracted_pages = [{'page_num':1,'text':RULES}]
    result.total_pages = 1
    result.total_chars = len(RULES)
    return result


SCENARIOS = [
    ('Cửa đóng, khóa chưa cài, nhiệt độ cao, ghi đè bật.',
     ['Không chạy vì khóa chưa cài','Chạy vì ghi đè bỏ qua khóa','Chạy vì cửa đóng','Chạy nếu mở cửa'], 'A',
     'Khóa chưa cài nên thiếu điều kiện an toàn; ghi đè chỉ bỏ qua cảnh báo nhiệt.'),
    ('Cửa mở, khóa đã cài, nhiệt độ cao, ghi đè bật.',
     ['Chạy vì khóa đã cài','Chạy vì bật ghi đè','Không chạy vì cửa mở','Chỉ cần tắt ghi đè là chạy'], 'C',
     'Cửa mở vi phạm điều kiện bắt buộc dù khóa đã cài và ghi đè đang bật.'),
    ('Cửa đóng, khóa đã cài, cảm biến cảnh báo nhiệt độ cao, ghi đè tắt.',
     ['Chạy vì cửa và khóa hợp lệ','Không chạy vì cảnh báo nhiệt chưa được bỏ qua','Chạy nếu tháo khóa','Chạy nếu mở cửa'], 'B',
     'Cửa và khóa đạt yêu cầu nhưng nhiệt độ cao, ghi đè tắt nên chưa đủ điều kiện chạy.'),
    ('Cửa đóng, khóa đã cài, cảm biến cảnh báo nhiệt độ cao, ghi đè bật.',
     ['Không chạy vì nhiệt độ luôn chặn máy','Không chạy vì ghi đè vô hiệu hóa cửa','Chạy dù tháo khóa','Chạy vì đủ cửa và khóa, ghi đè bỏ qua cảnh báo nhiệt'], 'D',
     'Cửa đóng và khóa đã cài; ghi đè cho phép bỏ qua cảnh báo nhiệt nên máy được chạy.'),
    ('Cửa mở, khóa chưa cài, nhiệt độ bình thường, ghi đè tắt.',
     ['Chỉ đóng cửa là đủ','Chỉ cài khóa là đủ','Phải đóng cửa và cài khóa','Chỉ bật ghi đè là đủ'], 'C',
     'Nhiệt độ bình thường nhưng thiếu cả cửa đóng lẫn khóa đã cài, phải đáp ứng cả hai.'),
]


def question(number):
    scenario, alternatives, answer, explanation = SCENARIOS[number-1]
    return {'question':scenario+' Theo các điều kiện, kết luận hoặc cách xử lý nào đúng?',
        'choices':dict(zip('ABCD',alternatives)), 'answer':answer,'explanation':explanation,
        'evidence_id':'Q1','difficulty':'hard'}


class Batches:
    last_done_reason = 'stop'
    def __init__(self, accept=True):
        self.calls=[]
        self.turn=0
        self.accept=accept
        self.review_turn=0
    def check_health(self): return True
    def chat_complete(self, **kwargs):
        prompt = kwargs['messages'][0]['content']
        self.calls.append(prompt)
        if 'Kiểm định độc lập từng câu' in prompt:
            assert RULES in prompt
            schema = kwargs['response_schema']
            assert schema['properties']['checks']['minItems'] == 1
            assert 'difficulty_matches' in schema['properties']['checks']['items']['required']
            answer=question(self.review_turn+1)['answer']
            self.review_turn+=1
            return json.dumps({'checks':[{'index':1,
                'input_audit':'Đề bài nêu rõ đầy đủ trạng thái cửa, khóa, nhiệt độ và ghi đè.',
                'independent_solution':'Theo quy tắc an toàn trong nguồn, suy luận độc lập xác định đáp án đúng.',
                'choice_comparison':f'Kết quả độc lập khớp duy nhất với lựa chọn {answer}.',
                'explanation_audit':'Mọi bước suy luận trong giải thích đều chính xác theo nguồn.',
                'supported':True,
                'explanation_supported':True,'unique_answer':True,'qualifiers_preserved':True,
                'difficulty_matches':self.accept,'verified_answer':answer}]})
        self.turn+=1
        if self.turn == 1:
            return json.dumps({'questions':[question(i) for i in range(1,4)]})
        if self.turn == 2:
            return json.dumps({'questions':[question(i) for i in range(4,6)]})
        answers = ['Máy','cửa bảo vệ','đóng','khóa an toàn','đã cài'] if self.turn == 3 else ['cảm biến nhiệt','ghi đè']
        return json.dumps({'flashcards':[{'answer':a,'evidence_id':'Q1'} for a in answers]})


def test_five_questions_seven_cards_keep_requested_counts_and_hard_review(monkeypatch):
    import app.token_counter as token_counter
    monkeypatch.setattr(token_counter, 'get_exact_token_counter',
                        lambda: lambda messages: sum(len(m['content']) for m in messages))
    client=Batches()
    record=StudyMaterialManager(client).generate_materials_from_pages(
        source(),quiz_count=5,flashcard_count=7,difficulty='hard',model='test-remote')
    assert record['actual_quiz_count'] == record['requested_quiz_count'] == 5
    assert record['actual_flashcard_count'] == record['requested_flashcard_count'] == 7
    assert record['complete'] is True
    assert all(q['difficulty']=='hard' and q['evidence_quote']==RULES for q in record['quiz'])
    assert 'đúng 3 câu' in client.calls[0] and 'đúng 2 câu' in client.calls[1]
    assert sum('Kiểm định độc lập từng câu' in p for p in client.calls)==5


def test_reviewer_can_reject_every_self_labelled_hard_question():
    with pytest.raises(StudyMaterialError,match='khó'):
        StudyMaterialManager(Batches(accept=False)).generate_materials_from_pages(
            source(),quiz_count=5,flashcard_count=7,difficulty='hard',model='test-remote')


@pytest.mark.parametrize('difficulty',['medium','hard'])
def test_application_blueprints_do_not_request_cloze_or_accept_self_labels(difficulty):
    prompt=build_quiz_prompt([{'page_num':1,'text':RULES}],count=5,difficulty=difficulty)
    assert 'source_cloze' not in prompt
    assert 'explanation' in prompt and 'evidence_id' in prompt
    assert 'Bốn lựa chọn' in prompt and 'không dùng điền khuyết' in prompt


def test_originals_require_exact_path_or_unique_basename(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    (a/'same.pdf').write_bytes(b'first original')
    (b/'same.pdf').write_bytes(b'second original')
    assert resolve_safe_source_file('same.pdf',[tmp_path]) is None
    assert resolve_safe_source_file('a/same.pdf',[tmp_path]).read_bytes()==b'first original'
    assert resolve_safe_source_file('missing/same.pdf',[tmp_path]) is None
    assert resolve_safe_source_file('../a/same.pdf',[tmp_path]) is None
    (tmp_path/'.env').write_text('private configuration')
    assert resolve_safe_source_file('.env',[tmp_path]) is None


def test_short_evidence_quote_cannot_hide_source_exceptions_from_reviewer(monkeypatch):
    import app.token_counter as token_counter
    monkeypatch.setattr(token_counter, 'get_exact_token_counter',
                        lambda: lambda messages: sum(len(m['content']) for m in messages))
    quote = RULES.split('. ', 1)[0] + '.'
    proposal = {**question(1), 'page_num': 1, 'evidence_quote': quote}
    proposal.pop('evidence_id')
    class ShortQuote:
        last_done_reason = 'stop'
        def __init__(self): self.calls = 0; self.reviewed = False
        def check_health(self): return True
        def chat_complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({'questions': [proposal]})
            if self.calls == 2:
                return json.dumps({'flashcards': [{'answer': 'Máy', 'evidence_id': 'Q1'}]})
            self.reviewed = True
            prompt = kwargs['messages'][0]['content']
            records = json.JSONDecoder().raw_decode(prompt.split('Dữ liệu cần kiểm định:\n', 1)[1])[0]
            assert records[0]['source'] == quote
            assert records[0]['context'] == RULES
            assert records[0]['evidence_quote'] == quote
            return json.dumps({'checks': [{'index': 1,
                'input_audit': 'Đề bài nêu rõ trạng thái cửa, khóa, nhiệt độ và ghi đè.',
                'independent_solution': 'Suy luận độc lập theo quy tắc an toàn trong nguồn.',
                'choice_comparison': 'Kết quả độc lập khớp duy nhất với lựa chọn A.',
                'explanation_audit': 'Giải thích khớp với nguồn.',
                'supported': True,
                'explanation_supported': True, 'unique_answer': True,
                'qualifiers_preserved': True, 'difficulty_matches': True, 'verified_answer': 'A'}]})
    client = ShortQuote()
    result = StudyMaterialManager(client).generate_materials_from_pages(
        source(), quiz_count=1, flashcard_count=1, difficulty='hard', model='test-remote')
    assert client.reviewed
    assert result['actual_quiz_count'] == 1


def _long_three_page_source():
    pages = [
        {
            'page_num': 1,
            'text': (
                'Mục 1. Nguyên lý hoạt động tổng quát của hệ thống lưu trữ đệm.\n'
                + 'Ngữ cảnh bổ trợ cho trang một nhằm đảm bảo độ dài thực tế trên 450 ký tự khi kiểm tra nén prompt. ' * 4
                + 'Chính sách ghi chậm (Write-Back) chỉ cập nhật cache và bật bit Dirty trước khi trả về. '
                + 'Dữ liệu trong cache chỉ được đẩy xuống RAM khi khối đệm bị thay thế.'
            ),
        },
        {
            'page_num': 2,
            'text': (
                'Mục 2. Giao thức đồng bộ và điều kiện khóa tuyến tính.\n'
                + 'Ngữ cảnh bổ trợ cho trang hai nhằm giữ nguyên toàn văn đoạn nguồn trong source khi gửi kiểm định. ' * 4
                + 'Độ trễ truy xuất bộ nhớ đệm giảm khi tỷ lệ trúng đệm (hit ratio) tăng cao. '
                + 'Nếu tỷ lệ trượt đệm tăng, băng thông bộ nhớ chính trở thành nút thắt.'
            ),
        },
        {
            'page_num': 3,
            'text': (
                'Mục 3. Kiến trúc phân cấp và quy tắc nhất quán dữ liệu.\n'
                + 'Ngữ cảnh bổ trợ cho trang ba với nhiều câu giải thích chi tiết điều kiện vận hành hệ thống. ' * 4
                + 'Giao thức MESI sử dụng bốn trạng thái để duy trì tính nhất quán giữa các lõi xử lý.'
            ),
        },
    ]
    extracted = PDFExtractionResult(filename='long_notes.txt', filepath=Path('long_notes.txt'))
    extracted.extracted_pages = pages
    extracted.total_pages = len(pages)
    extracted.total_chars = sum(len(p['text']) for p in pages)
    return extracted


def _setup_fake_mlx(monkeypatch, tmp_path, client_cls):
    import app.model_resolver as model_resolver
    import app.token_counter as tkc
    import app.trained_client as tc

    (tmp_path / 'config.json').write_text('{"max_position_embeddings":8192}', encoding='utf-8')
    monkeypatch.setattr(model_resolver.sys, 'platform', 'darwin')
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', tmp_path)
    monkeypatch.setattr(tkc, 'get_exact_token_counter', lambda *args: lambda messages: sum(len(m['content']) // 3 for m in messages))
    monkeypatch.setattr(tc, 'TrainedModelClient', client_cls)


def test_easy_cloze_uses_compact_review_combined_first_turn_and_skips_unnecessary_retry(monkeypatch, tmp_path):
    from app.practice_adapter import SYSTEM_PROMPT as PRACTICE_SYSTEM_PROMPT

    extracted = _long_three_page_source()
    calls = []

    class CombinedCompactClient:
        last_done_reason = 'stop'

        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def available(cls, *args, **kwargs):
            return True

        def chat_complete(self, messages, system_prompt='', **kwargs):
            user_text = messages[0]['content']
            calls.append({'user': user_text, 'system': system_prompt, **kwargs})
            if 'Kiểm định độc lập từng câu điền khuyết' in user_text:
                items = json.JSONDecoder().raw_decode(user_text.split('Dữ liệu cần kiểm định:\n', 1)[1])[0]
                checks = []
                for item in items:
                    assert 'explanation' not in item
                    assert 'evidence_quote' not in item
                    assert len(item['question']) <= 480
                    assert len(item['source']) > 500  # Full page source preserved for condition checks.
                    ans = next(
                        k for k, v in item['choices'].items()
                        if v in ('bit Dirty', 'hit ratio') and k in {'A', 'B', 'C', 'D'}
                    )
                    is_dirty = any(v == 'bit Dirty' for v in item['choices'].values())
                    basis = (
                        'Chính sách ghi chậm (Write-Back) chỉ cập nhật cache và bật bit Dirty trước khi trả về.'
                        if is_dirty
                        else 'Độ trễ truy xuất bộ nhớ đệm giảm khi tỷ lệ trúng đệm (hit ratio) tăng cao.'
                    )
                    checks.append({
                        'index': item['index'],
                        'source_basis': basis,
                        'choice_comparison': f'{ans} matches source directly; remaining three distractors contradict source.',
                        'matching_choices': ans,
                        'supported': True,
                        'qualifiers_preserved': True,
                        'unique_answer': True,
                        'verified_answer': ans,
                    })
                return json.dumps({'checks': checks}, ensure_ascii=False)

            # Combined first turn: returns 2 clean items + 1 extra broken item -> must NOT trigger repair retry.
            return json.dumps({
                'questions': [
                    {
                        'format': 'source_cloze',
                        'evidence_id': 'Q1',
                        'answer': 'bit Dirty',
                        'distractors': ['bit Valid', 'bit Parity', 'bit Lock'],
                    },
                    {
                        'format': 'source_cloze',
                        'evidence_id': 'Q2',
                        'answer': 'hit ratio',
                        'distractors': ['miss penalty', 'clock skew', 'bus latency'],
                    },
                    {
                        'format': 'source_cloze',
                        'evidence_id': 'Q1',
                        'answer': 'cache',
                        'distractors': ['RAM', 'ROM', 'ALU'],
                    },
                ],
                'flashcards': [
                    {'answer': 'Giao thức MESI', 'evidence_id': 'Q3'},
                ],
            }, ensure_ascii=False)

    _setup_fake_mlx(monkeypatch, tmp_path, CombinedCompactClient)
    record = StudyMaterialManager().generate_materials_from_pages(
        extracted, quiz_count=2, flashcard_count=1, difficulty='easy', model='qwen3.5-9b-4bit'
    )

    assert len(calls) == 2  # Exactly 1 combined generation call + 1 compact cloze review call.
    gen_call, rev_call = calls
    assert gen_call['response_schema']['properties']['questions']['maxItems'] == 2
    assert gen_call['response_schema']['properties']['flashcards']['maxItems'] == 1
    assert gen_call['context_prefix_marker'] == '\n<task>\n'
    assert rev_call['system'] == PRACTICE_SYSTEM_PROMPT
    assert rev_call['context_prefix_marker'] == '\nDữ liệu cần kiểm định:\n'
    assert rev_call['max_tokens'] == 440
    assert rev_call['response_schema']['properties']['checks']['maxItems'] == 2
    assert 'input_audit' not in rev_call['response_schema']['properties']['checks']['items']['properties']
    assert record['actual_quiz_count'] == 2
    assert record['actual_flashcard_count'] == 1
    assert record['complete'] is True
    assert record['generation'] == {'quiz': 'model', 'flashcards': 'model'}
    assert all('Căn cứ trực tiếp theo tài liệu trang' in q['explanation'] for q in record['quiz'])


@pytest.mark.parametrize('defect', [
    'wrong_basis',
    'short_basis',
    'ambiguous_matching',
    'wrong_answer',
    'qualifiers_lost',
    'unsupported',
    'tampered_reconstruction',
    'article_collision',
])
def test_compact_cloze_review_fails_closed_on_evidence_answer_ambiguity_and_reconstruction(monkeypatch, defect):
    import app.model_resolver as resolver
    from app.practice import PracticeService

    monkeypatch.setattr(resolver, 'is_mlx_model', lambda _model=None: True)
    source_text = 'The indicator is active only while the closed switch holds contact.'
    question_dict = {
        'format': 'source_cloze',
        'question': 'Chọn cụm điền vào trích đoạn:\nThe indicator is active only while […] holds contact.\nPhần […] là gì?',
        'choices': {
            'A': 'the closed switch',
            'B': 'the open switch',
            'C': 'a broken wire',
            'D': 'no power source',
        },
        'answer': 'A',
        'evidence_quote': source_text,
        'source': {'chunk_id': 1, 'snippet': source_text},
    }
    if defect == 'tampered_reconstruction':
        question_dict['question'] = (
            'Chọn cụm điền vào trích đoạn:\nThe indicator is ALWAYS active when […] holds contact.\nPhần […] là gì?'
        )
    elif defect == 'article_collision':
        question_dict['choices']['B'] = 'closed switch'

    called = []

    def fake_review(prompt, model, max_tokens, timeout=None, response_schema=None):
        called.append(prompt)
        check = {
            'index': 1,
            'source_basis': source_text,
            'choice_comparison': 'Only A matches source; B, C, D fail the closed switch condition.',
            'matching_choices': 'A',
            'supported': True,
            'qualifiers_preserved': True,
            'unique_answer': True,
            'verified_answer': 'A',
        }
        if defect == 'wrong_basis':
            check['source_basis'] = 'The indicator blinks every five seconds automatically.'
        elif defect == 'short_basis':
            check['source_basis'] = 'switch'
        elif defect == 'ambiguous_matching':
            check['matching_choices'] = 'AB'
            check['unique_answer'] = False
            check['verified_answer'] = ''
        elif defect == 'wrong_answer':
            check['matching_choices'] = 'B'
            check['verified_answer'] = 'B'
        elif defect == 'qualifiers_lost':
            check['qualifiers_preserved'] = False
        elif defect == 'unsupported':
            check['supported'] = False
            check['matching_choices'] = ''
            check['unique_answer'] = False
            check['verified_answer'] = ''
        return json.dumps({'checks': [check]})

    service = PracticeService(None, None)
    service._chat_complete = fake_review
    result = service._verify_questions(
        [question_dict], {1: {'safe_text': source_text}}, 'qwen3.5-9b-4bit', difficulty='easy'
    )
    assert result == []
    if defect in ('tampered_reconstruction', 'article_collision'):
        assert called == []
    else:
        assert len(called) == 1


@pytest.mark.parametrize('outcome', ['partial_on_second_batch', 'timeout_on_first_batch', 'runtime_error_after_partial'])
def test_review_timeout_returns_explicit_partial_when_batch_accepted_and_503_when_none_passed(monkeypatch, tmp_path, outcome):
    extracted = _long_three_page_source()
    all_cloze = [
        {'format': 'source_cloze', 'evidence_id': 'Q1', 'answer': 'bit Dirty', 'distractors': ['bit Valid', 'bit Clean', 'bit Zero']},
        {'format': 'source_cloze', 'evidence_id': 'Q1', 'answer': 'Write-Back', 'distractors': ['Write-Through', 'Write-Around', 'No-Write']},
        {'format': 'source_cloze', 'evidence_id': 'Q2', 'answer': 'hit ratio', 'distractors': ['miss rate', 'clock rate', 'bus width']},
        {'format': 'source_cloze', 'evidence_id': 'Q2', 'answer': 'băng thông bộ nhớ chính', 'distractors': ['dung lượng đĩa cứng', 'tần số xung nhịp', 'địa chỉ ảo']},
        {'format': 'source_cloze', 'evidence_id': 'Q3', 'answer': 'Giao thức MESI', 'distractors': ['Giao thức TCP', 'Giao thức HTTP', 'Giao thức USB']},
    ]

    class TimeoutOnBatchClient:
        last_done_reason = 'stop'

        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def available(cls, *args, **kwargs):
            return True

        def chat_complete(self, messages, **kwargs):
            user_text = messages[0]['content']
            if 'Kiểm định độc lập từng câu điền khuyết' in user_text:
                items = json.JSONDecoder().raw_decode(user_text.split('Dữ liệu cần kiểm định:\n', 1)[1])[0]
                if len(items) == 3:
                    if outcome == 'timeout_on_first_batch':
                        raise TimeoutError('Hết thời gian chờ phản hồi từ worker cục bộ.')
                    checks = []
                    for item in items:
                        ans = next(
                            k for k, v in item['choices'].items()
                            if v in {'bit Dirty', 'Write-Back', 'hit ratio'}
                        )
                        checks.append({
                            'index': item['index'],
                            'source_basis': item['source'][:180],
                            'choice_comparison': f'{ans} matches source; remaining three distractors are incorrect.',
                            'matching_choices': ans,
                            'supported': True,
                            'qualifiers_preserved': True,
                            'unique_answer': True,
                            'verified_answer': ans,
                        })
                    return json.dumps({'checks': checks}, ensure_ascii=False)
                if outcome == 'runtime_error_after_partial':
                    raise RuntimeError('Worker cục bộ đã thoát bất ngờ.')
                raise TimeoutError('Hết thời gian chờ phản hồi từ worker cục bộ.')
            if 'flashcards' in user_text.lower():
                return json.dumps({'flashcards': [{'answer': 'lõi xử lý', 'evidence_id': 'Q3'}]}, ensure_ascii=False)
            if 'Không lặp lại' in user_text:
                return json.dumps({'questions': all_cloze[3:]}, ensure_ascii=False)
            return json.dumps({'questions': all_cloze[:3]}, ensure_ascii=False)

    _setup_fake_mlx(monkeypatch, tmp_path, TimeoutOnBatchClient)
    mgr = StudyMaterialManager()
    if outcome == 'partial_on_second_batch':
        record = mgr.generate_materials_from_pages(
            extracted, quiz_count=5, flashcard_count=1, difficulty='easy', model='qwen3.5-9b-4bit'
        )
        assert record['actual_quiz_count'] == 3
        assert record['requested_quiz_count'] == 5
        assert record['complete'] is False
        assert record['generation']['quiz'] == 'model'
        assert '3/5' in record['warning']
        assert 'quá thời gian' in record['warning']
    else:
        with pytest.raises(StudyMaterialError, match='Không hoàn thành được bước kiểm định câu hỏi'):
            mgr.generate_materials_from_pages(
                extracted, quiz_count=5, flashcard_count=1, difficulty='easy', model='qwen3.5-9b-4bit'
            )


@pytest.mark.parametrize('difficulty,use_cloze', [
    ('medium', False),
    ('hard', False),
    ('easy', False),
    ('medium', True),
])
def test_medium_hard_and_general_easy_preserve_full_semantic_review(monkeypatch, tmp_path, difficulty, use_cloze):
    extracted = _long_three_page_source()
    review_prompts = []

    class SemanticClient:
        last_done_reason = 'stop'

        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def available(cls, *args, **kwargs):
            return True

        def chat_complete(self, messages, **kwargs):
            user_text = messages[0]['content']
            if 'Kiểm định độc lập từng câu' in user_text:
                review_prompts.append((user_text, kwargs['response_schema']))
                assert 'Kiểm định độc lập từng câu từ source và question' in user_text
                assert 'điền khuyết từ source' not in user_text
                required_fields = set(kwargs['response_schema']['properties']['checks']['items']['required'])
                assert {'input_audit', 'independent_solution', 'explanation_audit', 'explanation_supported', 'difficulty_matches'} <= required_fields
                return json.dumps({
                    'checks': [{
                        'index': 1,
                        'input_audit': 'Nguồn nêu rõ cơ chế và điều kiện của Write-Back.',
                        'source_basis': 'Chính sách ghi chậm (Write-Back) chỉ cập nhật cache và bật bit Dirty trước khi trả về.',
                        'independent_solution': 'Write-Back chỉ cập nhật cache và bật bit Dirty khi ghi.',
                        'supported': True,
                        'qualifiers_preserved': True,
                        'choice_comparison': 'Chỉ lựa chọn A khớp hoàn toàn với nguồn; B, C, D sai.',
                        'matching_choices': 'A',
                        'unique_answer': True,
                        'verified_answer': 'A',
                        'explanation_audit': 'Giải thích đúng theo đoạn nguồn.',
                        'explanation_supported': True,
                        'difficulty_matches': True,
                    }]
                }, ensure_ascii=False)
            if use_cloze:
                return json.dumps({
                    'questions': [{
                        'format': 'source_cloze',
                        'evidence_id': 'Q1',
                        'answer': 'bit Dirty',
                        'distractors': ['bit Valid', 'bit Parity', 'bit Lock'],
                    }]
                }, ensure_ascii=False)
            return json.dumps({
                'questions': [{
                    'question': 'Trong trường hợp ghi dữ liệu theo chính sách Write-Back, hệ thống xử lý như thế nào?' if difficulty != 'easy' else 'Write-Back là gì?',
                    'choices': {
                        'A': 'Chỉ cập nhật cache và bật bit Dirty',
                        'B': 'Ghi thẳng xuống RAM ngay lập tức',
                        'C': 'Xóa toàn bộ bộ nhớ đệm',
                        'D': 'Ngắt bộ xử lý',
                    },
                    'answer': 'A',
                    'explanation': 'Theo nguồn, Write-Back chỉ cập nhật cache và bật bit Dirty.',
                    'evidence_id': 'Q1',
                    'difficulty': difficulty,
                }]
            }, ensure_ascii=False)

    _setup_fake_mlx(monkeypatch, tmp_path, SemanticClient)
    mgr = StudyMaterialManager()
    if use_cloze and difficulty == 'medium':
        with pytest.raises(StudyMaterialError, match='trung bình'):
            mgr.generate_materials_from_pages(
                extracted, quiz_count=1, flashcard_count=0, difficulty=difficulty, model='qwen3.5-9b-4bit'
            )
    else:
        record = mgr.generate_materials_from_pages(
            extracted, quiz_count=1, flashcard_count=0, difficulty=difficulty, model='qwen3.5-9b-4bit'
        )
        assert record['actual_quiz_count'] == 1
        assert len(review_prompts) == 1


@pytest.mark.parametrize('case', [
    'remote_contradicted_without_audit',
    'remote_compact_witness_missing_explanation_supported',
    'mlx_contradicted_explanation_uses_full_gate',
    'mlx_mismatched_page_explanation_uses_full_gate',
    'mlx_mismatched_quote_explanation_uses_full_gate',
    'mlx_legacy_proposal_normalizes_to_source_derived',
])
def test_cloze_explanation_provenance_and_full_audit_gates(monkeypatch, tmp_path, case):
    import app.model_resolver as resolver
    from app.practice import PracticeService

    original = 'Mẫu dung dịch chỉ được ghi là axit axetic nếu nhiệt độ dưới 20 °C.'
    wrong_explanation = 'Tài liệu says the sample is always hydrochloric acid at every temperature.'
    masked = original.replace('axit axetic', '[…]')

    if case == 'mlx_legacy_proposal_normalizes_to_source_derived':
        extracted = PDFExtractionResult(filename='acid.txt', filepath=Path('acid.txt'))
        extracted.extracted_pages = [{'page_num': 1, 'text': original}]
        extracted.total_pages = 1
        extracted.total_chars = len(original)
        compact_review_called = []

        class LegacyProposalClient:
            last_done_reason = 'stop'

            def __init__(self, *args, **kwargs):
                pass

            @classmethod
            def available(cls, *args, **kwargs):
                return True

            def chat_complete(self, messages, **kwargs):
                user_text = messages[0]['content']
                if 'Kiểm định độc lập từng câu điền khuyết' in user_text:
                    compact_review_called.append(user_text)
                    items = json.JSONDecoder().raw_decode(user_text.split('Dữ liệu cần kiểm định:\n', 1)[1])[0]
                    ans = next(k for k, v in items[0]['choices'].items() if v == 'axit axetic')
                    return json.dumps({
                        'checks': [{
                            'index': 1,
                            'source_basis': original,
                            'choice_comparison': f'{ans} matches source under the < 20 °C condition; others fail.',
                            'matching_choices': ans,
                            'supported': True,
                            'qualifiers_preserved': True,
                            'unique_answer': True,
                            'verified_answer': ans,
                        }]
                    }, ensure_ascii=False)
                if 'flashcards' in user_text.lower():
                    return json.dumps({'flashcards': []})
                return json.dumps({
                    'questions': [{
                        'format': 'source_cloze',
                        'evidence_id': 'Q1',
                        'answer': 'axit axetic',
                        'distractors': ['axit clohidric', 'axit sunfuric', 'axit nitric'],
                        'explanation': wrong_explanation,
                    }]
                }, ensure_ascii=False)

        _setup_fake_mlx(monkeypatch, tmp_path, LegacyProposalClient)
        record = StudyMaterialManager().generate_materials_from_pages(
            extracted, quiz_count=1, flashcard_count=0, difficulty='easy', model='qwen3.5-9b-4bit'
        )
        assert len(compact_review_called) == 1
        assert record['actual_quiz_count'] == 1
        assert record['quiz'][0]['explanation'] == f'Căn cứ trực tiếp theo tài liệu trang 1: {original}'
        assert wrong_explanation not in record['quiz'][0]['explanation']
        return

    is_mlx = case.startswith('mlx_')
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda _model=None: is_mlx)
    if case == 'remote_compact_witness_missing_explanation_supported':
        expl = f'Căn cứ trực tiếp theo tài liệu trang 1: {original}'
    elif case == 'mlx_mismatched_page_explanation_uses_full_gate':
        expl = f'Căn cứ trực tiếp theo tài liệu trang 2: {original}'
    elif case == 'mlx_mismatched_quote_explanation_uses_full_gate':
        expl = f'Căn cứ trực tiếp theo tài liệu trang 1: {original} Bổ sung khẳng định ngoài nguồn.'
    else:
        expl = wrong_explanation

    question_dict = {
        'format': 'source_cloze',
        'question': f'Chọn cụm điền vào trích đoạn:\n{masked}\nPhần […] là gì?',
        'choices': dict(zip('ABCD', ['axit axetic', 'axit clohidric', 'axit sunfuric', 'axit nitric'])),
        'answer': 'A',
        'explanation': expl,
        'evidence_quote': original,
        'page_num': 1,
        'source': {'chunk_id': 1, 'snippet': original},
    }

    seen_prompts = []

    def fake_review(prompt, model, max_tokens, timeout=None, response_schema=None):
        seen_prompts.append((prompt, response_schema))
        if case == 'remote_contradicted_without_audit':
            check = {
                'index': 1,
                'supported': True,
                'qualifiers_preserved': True,
                'choice_comparison': 'A matches; B, C, D do not.',
                'unique_answer': True,
                'verified_answer': 'A',
            }
        elif case == 'remote_compact_witness_missing_explanation_supported':
            check = {
                'index': 1,
                'source_basis': original,
                'choice_comparison': 'A matches; B, C, D do not.',
                'matching_choices': 'A',
                'supported': True,
                'qualifiers_preserved': True,
                'unique_answer': True,
                'verified_answer': 'A',
            }
        else:
            check = {
                'index': 1,
                'input_audit': 'Source establishes the < 20 °C condition.',
                'source_basis': original,
                'independent_solution': 'axit axetic completes the blank under the stated condition.',
                'supported': True,
                'qualifiers_preserved': True,
                'choice_comparison': 'Only A matches; B, C, D name wrong acids.',
                'matching_choices': 'A',
                'unique_answer': True,
                'verified_answer': 'A',
                'explanation_audit': 'Proposed explanation contradicts or misattributes the source.',
                'explanation_supported': False,
                'difficulty_matches': True,
            }
        return json.dumps({'checks': [check]}, ensure_ascii=False)

    service = PracticeService(None, None)
    service._chat_complete = fake_review
    result = service._verify_questions(
        [question_dict],
        {1: {'safe_text': original, 'page_num': 1}},
        'qwen3.5-9b-4bit' if is_mlx else 'test-remote',
        difficulty='easy',
    )
    assert result == []
    assert len(seen_prompts) == 1
    prompt_text, schema = seen_prompts[0]
    assert 'Kiểm định độc lập từng câu điền khuyết' not in prompt_text
    assert 'Kiểm định độc lập từng câu từ source và question' in prompt_text
    required_fields = set(schema['properties']['checks']['items']['required'])
    assert {'explanation_audit', 'explanation_supported'} <= required_fields
