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


def test_five_questions_seven_cards_keep_requested_counts_and_hard_review():
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


def test_short_evidence_quote_cannot_hide_source_exceptions_from_reviewer():
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
            assert records[0]['source'] == RULES
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
