"""Acceptance boundaries for uploaded study materials; all sources are synthetic."""
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from app.pdf_extractor import PDFExtractionResult, PDFExtractor
from app.study_materials import StudyMaterialManager, StudyMaterialError


TEXT = 'Entropy là độ bất định trung bình của một biến ngẫu nhiên rời rạc hữu hạn.'


def source(text=TEXT, page=2):
    result = PDFExtractionResult(filename='synthetic.txt', filepath=Path('synthetic.txt'))
    result.clean_title = 'Synthetic'
    result.total_pages = page
    result.total_chars = len(text)
    result.extracted_pages = [{'page_num': page, 'text': text}]
    return result


class Responses:
    last_done_reason = 'stop'

    def __init__(self, quiz, cards, review_accepts=True):
        self.responses = iter([json.dumps({'questions': quiz}), json.dumps({'flashcards': cards})])
        self.calls = []
        self.review_accepts = review_accepts

    def check_health(self):
        return True

    def chat_complete(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs['messages'][0]['content']
        if 'Kiểm định độc lập từng câu' in prompt:
            # Simulated reviewer boundary, never evidence of model quality.
            records = json.JSONDecoder().raw_decode(prompt.split('Dữ liệu cần kiểm định:\n',1)[1])[0]
            return json.dumps({'checks':[{'index':item['index'],
                'input_audit': 'Đầy đủ dữ kiện theo định nghĩa trong tài liệu nguồn.' if self.review_accepts else 'Question thiếu dữ kiện để giải.',
                'independent_solution': 'Tra cứu định nghĩa khái niệm trong nguồn để xác định đáp án.' if self.review_accepts else 'Không đủ căn cứ xác định kết quả.',
                'choice_comparison': f"Lựa chọn {item.get('proposed_answer', 'A')} khớp chính xác với kết quả tự giải." if self.review_accepts else 'Không có lựa chọn nào phù hợp.',
                'explanation_audit': 'Giải thích bám sát định nghĩa trong nguồn.' if self.review_accepts else 'Giải thích không có căn cứ trong tài liệu nguồn.',
                'supported':self.review_accepts,'explanation_supported':self.review_accepts,
                'unique_answer':self.review_accepts,'qualifiers_preserved':self.review_accepts,
                'difficulty_matches':self.review_accepts,'verified_answer':(item.get('proposed_answer', 'A') if self.review_accepts else '')}
                for item in records]})
        return next(self.responses)


def question(**changes):
    return {'question': 'Entropy là gì?', 'choices': {'A': 'Độ bất định trung bình',
            'B': 'Tốc độ', 'C': 'Khối lượng', 'D': 'Khoảng cách'},
            'answer': 'A', 'evidence_quote': TEXT, 'page_num': 2, **changes}


def card(**changes):
    return {'front': 'Entropy?', 'back': 'độ bất định trung bình',
            'evidence_quote': TEXT, 'page_num': 2, **changes}


@pytest.mark.parametrize('metadata', [False, True])
def test_image_without_ocr_never_fabricates_page_text(tmp_path, monkeypatch, metadata):
    import app.study_materials as sm
    monkeypatch.setattr(sm, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(PDFExtractor, 'extract_file', staticmethod(lambda p, **kw: source('', 1)))
    monkeypatch.setattr(StudyMaterialManager, '_run_windows_native_ocr', staticmethod(lambda p: ''))
    image = Image.new('RGB', (100, 60), 'white')
    info = PngImagePlugin.PngInfo()
    if metadata:
        info.add_text('Description', TEXT)
    output = io.BytesIO()
    image.save(output, format='PNG', pnginfo=info)
    with pytest.raises(StudyMaterialError, match='văn bản|OCR|đọc'):
        StudyMaterialManager().extract_document('dinh_ly_khong_co_trong_anh.png', output.getvalue())


@pytest.mark.parametrize('bad', [question(evidence_quote='Câu bịa đặt không có trong nguồn.'),
    question(page_num=1), question(page_num='not-a-page'), question(choices={'A': 'x'}),
    question(answer='Z'), question(answer=None), question(page_num=2.5),
    question(choices={'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd', 'E': 'extra'}), None, 'not an object'])
def test_untrusted_quiz_items_cannot_be_published(bad):
    client = Responses([bad], [card()])
    result = StudyMaterialManager(client).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['quiz'] == []
    assert result['complete'] is False and result['warning']
    assert result['generation']['quiz'] == 'unavailable'


@pytest.mark.parametrize('bad', [card(evidence_quote='Không phải nguồn'), card(page_num=1),
                               card(back=''), None])
def test_untrusted_flashcards_cannot_be_published(bad):
    result = StudyMaterialManager(Responses([question()], [bad])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert all(c['page_num'] == 2 and c['evidence_quote'] in TEXT for c in result['flashcards'])
    assert result['generation']['flashcards'] == 'extractive_fallback'


def test_valid_model_items_preserve_content_and_report_provenance():
    client = Responses([question()], [card()])
    result = StudyMaterialManager(client).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['quiz'][0]['question'] == 'Entropy là gì?'
    assert TEXT.replace('độ bất định trung bình', '[…]', 1) in result['flashcards'][0]['front']
    assert result['generation'] == {'quiz': 'model', 'flashcards': 'model'}
    assert result['source_validation'] == 'verbatim_quote'


@pytest.mark.skipif(sys.platform != "darwin", reason="Native Mac MLX deployment")
def test_local_model_uses_existing_mlx_client_even_if_ollama_chat_is_offline(monkeypatch, native_context_metadata):
    import app.trained_client as tc
    calls = []
    class Local:
        last_done_reason = 'stop'
        @staticmethod
        def available(): return True
        def chat_complete(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 3:
                return json.dumps({'checks':[{'index':1,
                    'input_audit': 'Đầy đủ dữ kiện theo định nghĩa trong nguồn.',
                    'independent_solution': 'Tra cứu định nghĩa khái niệm trong nguồn.',
                    'choice_comparison': 'Khớp chính xác với lựa chọn A.',
                    'explanation_audit': 'Giải thích bám sát định nghĩa trong nguồn.',
                    'supported':True,'explanation_supported':True,
                    'unique_answer':True,'qualifiers_preserved':True,'difficulty_matches':True,'verified_answer':'A'}]})
            return json.dumps({'questions': [question()]} if len(calls) == 1 else {'flashcards': [card()]})
    monkeypatch.setattr(tc, 'TrainedModelClient', Local)
    remote = Responses([], [])
    remote.check_health = lambda: False
    result = StudyMaterialManager(remote).generate_materials_from_pages(source(), model='qwen3.5-9b-4bit', difficulty='easy')
    assert len(calls) == 3
    assert remote.calls == []
    assert result['generation'] == {'quiz': 'model', 'flashcards': 'model'}


def test_cached_library_material_respects_new_page_scope(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, 'DB_PATH', tmp_path/'absent.db')
    manager = StudyMaterialManager()
    manager._materials['cached'] = {'filename': 'cached.txt', 'clean_title': 'cached',
        'pages': [{'page_num': 1, 'text': 'Ngoài phạm vi.'}, {'page_num': 2, 'text': TEXT}],
        'quiz': [question(page_num=1)], 'flashcards': [card(page_num=1)]}
    seen = []
    def capture(result, **kwargs):
        seen.extend(result.extracted_pages)
        return {'selected': True}
    monkeypatch.setattr(manager, 'generate_materials_from_pages', capture)
    assert manager.generate_from_library_document(filename='cached.txt', page_from=2, page_to=2) == {'selected': True}
    assert seen == [{'page_num': 2, 'text': TEXT}]


@pytest.mark.parametrize('reason', ['length', None])
def test_incomplete_generation_never_claims_model_provenance(reason):
    client = Responses([question()], [card()])
    client.last_done_reason = reason
    result = StudyMaterialManager(client).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation'] == {'quiz': 'unavailable', 'flashcards': 'extractive_fallback'}


@pytest.mark.parametrize('kind', ['gpu', 'dialogue'])
def test_busy_is_returned_to_caller_instead_of_hidden_fallback(monkeypatch, kind):
    import app.study_materials as sm
    from app.gpu_lock import GPUBusyError
    from app.dialogue import DialogueBusyError
    from contextlib import contextmanager
    error = GPUBusyError if kind == 'gpu' else DialogueBusyError
    class Busy:
        @contextmanager
        def acquire(self, **kwargs):
            raise error('busy')
            yield
        acquire_for_inference = acquire
    monkeypatch.setattr(sm, 'gpu_coordinator' if kind == 'gpu' else 'dialogue_gate', Busy())
    with pytest.raises(error):
        StudyMaterialManager(Responses([question()], [card()])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')


def test_extractive_fallback_keeps_the_full_condition_in_answer_and_quote():
    text = ('Một ánh xạ T giữa hai không gian vector trên cùng một trường được gọi là tuyến tính khi '
            'và chỉ khi T(u + v) = T(u) + T(v) và T(a*u) = a*T(u) với mọi vector u, v và mọi vô hướng a.')
    client = Responses([], [])
    client.check_health = lambda: False
    result = StudyMaterialManager(client).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert result['quiz'] == []  # No generic, unreviewed distractors.
    assert result['flashcards'][0]['back'] == text
    assert result['flashcards'][0]['evidence_quote'] == text


def test_oversized_first_page_cannot_bypass_the_context_budget(monkeypatch):
    import app.token_counter as token_counter
    monkeypatch.setattr(token_counter, 'get_exact_token_counter', lambda: lambda messages: sum(len(m['content']) for m in messages))
    client = Responses([question()], [card()])
    StudyMaterialManager(client).generate_materials_from_pages(source(TEXT * 1000), model='test-remote', difficulty='easy')
    for call in client.calls:
        total = sum(len(m['content']) for m in call['messages'])
        total += len(call.get('system_prompt', ''))
        assert total + 1500 <= 8192


def test_repeated_page_chunks_keep_each_real_quote_admissible():
    result = source()
    result.extracted_pages.append({'page_num': 2, 'text': 'Một đoạn khác trên cùng trang, không chứa câu Entropy.'})
    output = StudyMaterialManager(Responses([question()], [card()])).generate_materials_from_pages(result, model='test-remote', difficulty='easy')
    assert output['generation'] == {'quiz': 'model', 'flashcards': 'model'}
    assert output['quiz'][0]['evidence_quote'] == TEXT


def test_single_oversized_math_sentence_is_never_cut_into_fake_complete_source(monkeypatch):
    import app.token_counter as token_counter
    monkeypatch.setattr(token_counter, 'get_exact_token_counter', lambda: lambda messages: sum(len(m['content']) for m in messages))
    text = 'Đẳng thức được áp dụng với biểu thức ' + 'a + b + ' * 2200 + 'c trong điều kiện mọi biến đều thuộc trường số thực.'
    client = Responses([question()], [card()])
    try:
        result = StudyMaterialManager(client).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    except StudyMaterialError as exc:
        assert str(exc)
    else:
        assert result['generation'] == {'quiz': 'unavailable', 'flashcards': 'extractive_fallback'}
    assert not client.calls


def test_budget_counts_actual_system_and_user_messages_together(monkeypatch):
    import app.token_counter as token_counter
    seen = []
    def count(messages):
        seen.append(messages)
        return sum(len(m['content']) for m in messages)
    monkeypatch.setattr(token_counter, 'get_exact_token_counter', lambda: count)
    client = Responses([question()], [card()])
    StudyMaterialManager(client).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert seen
    assert all([m['role'] for m in messages] == ['system', 'user'] for messages in seen)
    assert all(messages[0]['content'] == client.calls[0]['system_prompt'] for messages in seen)


def test_tokenizer_failure_falls_back_to_conservative_budget(monkeypatch):
    import app.token_counter as token_counter
    def failed(messages):
        raise RuntimeError('tokenizer unavailable')
    monkeypatch.setattr(token_counter, 'get_exact_token_counter', lambda: failed)
    client = Responses([question()], [card()])
    result = StudyMaterialManager(client).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['quiz'] and result['flashcards']


def test_no_tokenizer_budget_accounts_for_utf8_bytes_and_chat_overhead(monkeypatch):
    import app.token_counter as token_counter
    monkeypatch.setattr(token_counter, 'get_exact_token_counter', lambda: None)
    client = Responses([question()], [card()])
    result = StudyMaterialManager(client).generate_materials_from_pages(source('Định nghĩa ' + 'क' * 4000 + '.'), model='test-remote', difficulty='easy')
    for call in client.calls:
        size = len(call['system_prompt'].encode('utf-8')) + sum(len(m['content'].encode('utf-8')) for m in call['messages'])
        assert size + 256 + 1500 <= 8192
    assert not result['quiz'] and result['flashcards']


def test_quiz_with_duplicate_choice_texts_is_not_a_single_answer_question():
    duplicate = question(choices={'A': 'Độ bất định trung bình', 'B': 'Độ bất định trung bình',
                                 'C': 'Tốc độ', 'D': 'Khối lượng'})
    result = StudyMaterialManager(Responses([duplicate], [card()])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'unavailable'


def test_small_context_does_not_force_a_minimum_input_budget(monkeypatch):
    import app.study_materials as sm
    import app.token_counter as token_counter
    monkeypatch.setattr(sm, 'MLX_CONTEXT_TOKENS', 2048)
    monkeypatch.setattr(token_counter, 'get_exact_token_counter', lambda: lambda messages: 1000)
    client = Responses([question()], [card()])
    result = StudyMaterialManager(client).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert not client.calls  # 1000 input + 1500 output cannot fit in 2048.
    assert result['generation'] == {'quiz':'unavailable','flashcards':'extractive_fallback'}


def test_card_without_a_source_substring_answer_is_rejected():
    result = StudyMaterialManager(Responses([question()], [card(front=TEXT, back="An unsupported answer")])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation']['flashcards'] == 'extractive_fallback'


def test_sentence_packing_preserves_original_whitespace_for_verbatim_quotes(monkeypatch):
    import app.token_counter as tc
    monkeypatch.setattr(tc, 'get_exact_token_counter', lambda: lambda messages: sum(len(m['content']) for m in messages))
    quote = TEXT + '\n\n' + 'Entropy có đơn vị bit khi dùng log cơ số hai.'
    text = quote + '\n' + 'Một câu dài ' + 'x' * 12000 + '.'
    result = StudyMaterialManager(Responses([question(evidence_quote=quote)], [card(evidence_quote=quote)])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert result['generation'] == {'quiz': 'model', 'flashcards': 'model'}
    assert result['quiz'][0]['evidence_quote'] == quote


def test_model_can_select_an_admitted_evidence_id_without_retyping_source():
    q = question(); q.pop('page_num'); q.pop('evidence_quote'); q['evidence_id'] = 'Q1'
    f = card(); f.pop('page_num'); f.pop('evidence_quote'); f['evidence_id'] = 'Q1'
    result = StudyMaterialManager(Responses([q], [f])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation'] == {'quiz': 'model', 'flashcards': 'model'}
    for item in result['quiz'] + result['flashcards']:
        assert item['page_num'] == 2 and item['evidence_quote'] == TEXT


@pytest.mark.parametrize('fields', [
    {'evidence_id': 'Q99'},
    {'evidence_id': 'Q1', 'page_num': 1},
    {'evidence_id': 'Q1', 'page_num': 2.0},
    {'evidence_id': 'Q1', 'evidence_quote': 'Câu bịa đặt'},
])
def test_model_evidence_reference_cannot_override_invalid_metadata(fields):
    q = question(**fields); f = card(**fields)
    result = StudyMaterialManager(Responses([q], [f])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation'] == {'quiz': 'unavailable', 'flashcards': 'extractive_fallback'}


def test_model_cannot_return_more_items_than_requested():
    result = StudyMaterialManager(Responses([question(), question()], [card(), card()])).generate_materials_from_pages(source(), quiz_count=1, flashcard_count=1, model='test-remote', difficulty='easy')
    assert len(result['quiz']) == len(result['flashcards']) == 1


def test_question_answer_card_schema_preserves_source_and_self_test():
    f = {'question': 'Entropy là gì?', 'answer': 'độ bất định trung bình', 'evidence_id': 'Q1'}
    result = StudyMaterialManager(Responses([question()], [f])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation']['flashcards'] == 'model'
    assert result['flashcards'][0]['format'] == 'source_cloze'
    assert f['answer'] not in result['flashcards'][0]['front']
    assert result['flashcards'][0]['back'] == f['answer']
    assert result['flashcards'][0]['evidence_quote'] == TEXT


def test_model_math_entities_are_readable_without_changing_canonical_quote():
    text = 'Điều kiện P(B)>0 là bắt buộc để phép chia có nghĩa trong định lý Bayes.'
    q = question(question='Điều kiện mẫu số?', choices={'A': 'P(B)&gt;0', 'B': 'P(B)&lt;0', 'C': 'P(B)=0', 'D': 'Không điều kiện'}, evidence_id='Q1')
    q.pop('page_num'); q.pop('evidence_quote')
    f = {'question': 'Điều kiện mẫu số?', 'answer': 'P(B)&gt;0', 'evidence_id': 'Q1'}
    result = StudyMaterialManager(Responses([q], [f])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert result['quiz'][0]['choices'][0]['text'] == 'P(B)>0'
    assert result['flashcards'][0]['back'] == 'P(B)>0'
    assert all(i['evidence_quote'] == text for i in result['quiz'] + result['flashcards'])


def test_evidence_id_resolves_the_selected_chunk_and_page():
    extracted = source('Đây là trang thứ nhất với một kiến thức khác biệt.', 1)
    extracted.total_pages = 3
    extracted.extracted_pages.append({'page_num': 3, 'text': TEXT})
    q = question(evidence_id='Q2'); q.pop('page_num'); q.pop('evidence_quote')
    f = card(evidence_id='Q2'); f.pop('page_num'); f.pop('evidence_quote')
    result = StudyMaterialManager(Responses([q], [f])).generate_materials_from_pages(extracted, model='test-remote', difficulty='easy')
    assert result['generation'] == {'quiz': 'model', 'flashcards': 'model'}
    assert all(i['page_num'] == 3 and i['evidence_quote'] == TEXT for i in result['quiz'] + result['flashcards'])


def test_model_card_cannot_invert_a_condition_via_its_question():
    text = 'Điều kiện P(B)>0 là bắt buộc để phép chia có nghĩa.'
    f = {'question': 'Điều kiện nào không cần thiết?', 'answer': 'P(B)>0', 'evidence_id': 'Q1'}
    result = StudyMaterialManager(Responses([], [f])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    item = result['flashcards'][0]
    assert item['format'] == 'source_cloze'
    assert item['front'] == 'Điền phần còn thiếu trong trích đoạn:\nĐiều kiện […] là bắt buộc để phép chia có nghĩa.\nPhần […] là gì?'
    assert item['back'] == 'P(B)>0'


def test_model_card_cannot_drop_the_number_of_equal_probability_outcomes():
    text = 'Nếu hai giá trị có xác suất bằng nhau 1/2 thì H(X)=1 bit.'
    f = {'question': 'Entropy khi các giá trị đồng xác suất?', 'answer': '1 bit', 'evidence_id': 'Q1'}
    result = StudyMaterialManager(Responses([], [f])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    item = result['flashcards'][0]
    assert 'Nếu hai giá trị có xác suất bằng nhau 1/2' in item['front']
    assert item['format'] == 'source_cloze'


def test_model_card_answer_outside_its_source_is_rejected():
    result = StudyMaterialManager(Responses([], [card(back='An invented explanation')])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation']['flashcards'] == 'extractive_fallback'


def test_model_choices_repeating_the_answer_with_extra_words_are_not_accepted():
    ambiguous = question(choices={'A': 'Độ bất định trung bình',
        'B': 'Độ bất định trung bình của biến ngẫu nhiên',
        'C': 'Tốc độ', 'D': 'Khối lượng'})
    result = StudyMaterialManager(Responses([ambiguous], [card()])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'unavailable'


def test_quiz_explanation_uses_source_evidence_instead_of_unchecked_model_prose():
    result = StudyMaterialManager(Responses([question(explanation='Invented claim about coin systems.')], [card()], review_accepts=False)).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['quiz'] == []
    assert result['generation']['quiz'] == 'unavailable'


def test_vietnamese_quiz_rejects_language_drift_across_question_and_choices():
    drift = question(question='Entropy của một coin cân bằng có độ不确定量是多少？',
        choices={'A': '1 bit', 'B': '0 bit', 'C': '2 bit', 'D': '不确定'})
    result = StudyMaterialManager(Responses([drift], [card()])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'unavailable'
    assert '不确定' not in str(result['quiz'])


def test_quiz_cloze_keeps_source_conditions_and_ignores_model_question():
    text = 'Nếu đồng xu có hai mặt đồng xác suất thì entropy bằng 1 bit.'
    q = {'format': 'source_cloze', 'answer': '1 bit',
         'distractors': ['0 bit', '2 bit', '3 bit'], 'evidence_id': 'Q1',
         'question': 'Entropy luôn bằng bao nhiêu?'}
    result = StudyMaterialManager(Responses([q], [])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'model'
    item = result['quiz'][0]
    assert item['format'] == 'source_cloze'
    assert text.replace('1 bit', '[…]') in item['question']
    assert 'luôn' not in item['question']
    assert next(c['text'] for c in item['choices'] if c['label'] == item['answer']) == '1 bit'
    assert item['evidence_quote'] == text


@pytest.mark.parametrize('changes', [
    {'answer': '4 bit'}, {'distractors': ['độ bất định trung bình', 'tốc độ', 'khối lượng']},
    {'distractors': ['tốc độ', 'tốc độ', 'khối lượng']},
    {'distractors': ['tốc độ', 'khối lượng']},
    {'distractors': ['tốc độ', 'khối lượng', 3]},
    {'answer': ''}, {'answer': TEXT},
])
def test_quiz_cloze_requires_a_real_span_and_three_distinct_distractors(changes):
    q = {'format': 'source_cloze', 'answer': 'độ bất định trung bình',
         'distractors': ['tốc độ', 'khối lượng', 'khoảng cách'], 'evidence_id': 'Q1', **changes}
    result = StudyMaterialManager(Responses([q], [])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'unavailable'


def test_quiz_cloze_rejects_a_span_with_multiple_occurrences():
    text = 'Entropy được đo bằng bit. Một bit là đơn vị thông tin.'
    q = {'format': 'source_cloze', 'answer': 'bit',
         'distractors': ['joule', 'watt', 'ampe'], 'evidence_id': 'Q1'}
    result = StudyMaterialManager(Responses([q], [])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'unavailable'


def test_quiz_cloze_cannot_leave_its_answer_visible_with_different_case():
    text = 'Điện trở có đơn vị ôm. Định luật Ôm là I = U/R, với R>0.'
    q = {'format': 'source_cloze', 'answer': 'Ôm',
         'distractors': ['Ampere', 'Volt', 'Watt'], 'evidence_id': 'Q1'}
    result = StudyMaterialManager(Responses([q], [])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'unavailable'


def test_duplicate_model_cloze_questions_are_not_published_twice():
    q = {'format': 'source_cloze', 'answer': 'độ bất định trung bình',
         'distractors': ['tốc độ', 'khối lượng', 'khoảng cách'], 'evidence_id': 'Q1'}
    result = StudyMaterialManager(Responses([q, q], [])).generate_materials_from_pages(source(), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'model'
    assert len(result['quiz']) == 1


def test_cloze_math_choices_keep_operators_that_change_the_answer():
    text = 'Điều kiện P(B)>0 là bắt buộc để phép chia có nghĩa.'
    q = {'format': 'source_cloze', 'answer': 'P(B)>0',
         'distractors': ['P(B)<=0', 'P(B)<0', 'P(B)=0'], 'evidence_id': 'Q1'}
    result = StudyMaterialManager(Responses([q], [])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'model'
    assert {c['text'] for c in result['quiz'][0]['choices']} == {'P(B)>0', 'P(B)<=0', 'P(B)<0', 'P(B)=0'}


def test_cloze_cannot_use_a_legacy_quote_to_drop_source_conditions():
    text = 'Nếu đồng xu có hai mặt đồng xác suất, entropy là 1 bit.'
    q = {'format': 'source_cloze', 'answer': '1 bit', 'page_num': 2,
         'evidence_quote': 'entropy là 1 bit.', 'distractors': ['0 bit', '2 bit', '3 bit']}
    result = StudyMaterialManager(Responses([q], [])).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert result['generation']['quiz'] == 'unavailable'


@pytest.mark.parametrize('repaired', [True, False])
def test_repeated_cloze_span_gets_at_most_one_source_bound_repair(repaired):
    text = 'Bayes yêu cầu P(B)>0. P(B) là xác suất của biến cố B.'
    bad = {'format': 'source_cloze', 'answer': 'P(B)', 'evidence_id': 'Q1',
           'distractors': ['P(A)', 'P(A|B)', 'P(B|A)']}
    good = {**bad, 'answer': 'P(B)>0', 'distractors': ['P(B)=0', 'P(B)<0', 'P(B)<=0']}
    client = Responses([], [])
    client.responses = iter([json.dumps({'questions': [bad]}),
                             json.dumps({'questions': [good if repaired else bad]}),
                             json.dumps({'flashcards': []})])
    result = StudyMaterialManager(client).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert len(client.calls) == (4 if repaired else 3)  # Repair, flashcard and accepted-item review.
    assert result['generation']['quiz'] == ('model' if repaired else 'unavailable')
    from html import unescape
    assert text in unescape(client.calls[1]['messages'][0]['content'])


def test_cloze_repair_is_skipped_when_its_prompt_would_exceed_the_budget(monkeypatch):
    import app.study_materials as sm
    import app.token_counter as tc
    monkeypatch.setattr(sm, 'MLX_CONTEXT_TOKENS', 2000)
    monkeypatch.setattr(tc, 'get_exact_token_counter', lambda: lambda messages: 200)
    text = 'Entropy được đo bằng bit. Một bit là đơn vị thông tin.'
    q = {'format': 'source_cloze', 'answer': 'bit',
         'distractors': ['joule', 'watt', 'ampe'], 'evidence_id': 'Q1'}
    client = Responses([q], [])
    result = StudyMaterialManager(client).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert len(client.calls) == 2
    assert result['generation']['quiz'] == 'unavailable'
    assert all(c['num_ctx'] == 2000 for c in client.calls)


@pytest.mark.parametrize('repaired', [True, False])
def test_duplicate_cloze_distractors_get_one_repair_without_publishing_duplicates(repaired):
    text = 'Its entropy is one bit. A certain outcome has zero entropy.'
    bad = {'format': 'source_cloze', 'answer': 'bit', 'evidence_id': 'Q1',
           'distractors': ['byte', 'kilobyte', ' BYTE ']}
    good = {**bad, 'distractors': ['byte', 'joule', 'watt']}
    client = Responses([], [])
    client.responses = iter([json.dumps({'questions': [bad]}),
                             json.dumps({'questions': [good if repaired else bad]}),
                             json.dumps({'flashcards': []})])
    result = StudyMaterialManager(client).generate_materials_from_pages(source(text), model='test-remote', difficulty='easy')
    assert len(client.calls) == (4 if repaired else 3)
    assert result['generation']['quiz'] == ('model' if repaired else 'unavailable')
    if repaired:
        assert len({c['text'].strip().casefold() for c in result['quiz'][0]['choices']}) == 4
    from html import unescape
    assert text in unescape(client.calls[1]['messages'][0]['content'])
