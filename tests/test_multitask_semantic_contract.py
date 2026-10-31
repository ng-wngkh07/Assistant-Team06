"""Independent semantic regressions for runtime-aligned SFT examples."""
import json
import os
from pathlib import Path

import pytest

from app.multitask_training_data import (
    SourceContext, build_practice_verify_invalid, build_practice_verify_supported,
    build_session_summary,
)
from app.topic_summarizer import SUMMARIZE_SYSTEM_PROMPT


def runtime_practice_service():
    import importlib
    import importlib.util
    import hashlib
    try:
        return importlib.import_module('app.practice').PracticeService
    except ModuleNotFoundError as exc:
        if exc.name != 'app.practice':
            raise
    # The older local checkout uses the verified main source as its contract.
    snapshot = Path(__file__).resolve().parents[1]/'data/evaluation/training-yc188-20261010/main-contract-snapshot'
    source = snapshot/'practice.py'
    identity = json.loads((snapshot/'identity.json').read_text())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == identity['files_sha256']['app/practice.py']
    spec = importlib.util.spec_from_file_location('frozen_main_practice_yc188', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PracticeService


def context(passage):
    return SourceContext('programming', 1, 'synthetic-example.pdf', 'a'*64, 1, 7,
                         0, len(passage), passage)


def test_wrong_proposed_letter_does_not_make_other_checks_false():
    passage = 'Khi cây rỗng, hàm thêm nút gọi createNode(v) và trả về ngay.'
    check = {
        'input_audit': 'Đề bài nêu rõ trạng thái cây rỗng và hỏi tên hàm được gọi.',
        'independent_solution': 'Theo nguồn, khi cây rỗng hàm gọi createNode(v).',
        'choice_comparison': 'Kết quả createNode(v) khớp với B; đáp án đề xuất C sai.',
        'explanation_audit': 'Giải thích đúng theo nguồn khi nêu createNode(v).',
        'supported': False, 'explanation_supported': True,
        'unique_answer': True, 'qualifiers_preserved': True,
        'verified_answer': 'B',
    }
    question = {'question': 'Khi cây rỗng, hàm thêm nút gọi hàm nào?',
                'choices': {'A': 'deleteNode(v)', 'B': 'createNode(v)',
                            'C': 'sort(v)', 'D': 'findNode(v)'},
                'answer': 'C', 'explanation': 'Đoạn trích nói hàm gọi createNode(v).',
                'evidence_quote': passage, 'verifier_check': check}
    messages, target = build_practice_verify_invalid(context(passage), 'Kiểm định câu hỏi.', question)
    actual = json.loads(target)['checks'][0]
    assert actual == {'index': 1, **check}
    assert passage in messages[1]['content']


def test_invalid_example_requires_independently_authored_checks():
    question = {'question': 'Có bao nhiêu nút?', 'choices': {'A':'1','B':'2','C':'3','D':'4'},
                'answer': 'A', 'explanation': 'Có một nút.', 'evidence_quote': 'Cây có một nút.'}
    with pytest.raises(ValueError, match='verifier_check'):
        build_practice_verify_invalid(context('Cây có một nút.'), 'Kiểm định.', question)


def test_supported_example_also_requires_independently_authored_checks():
    question = {'question': 'Có bao nhiêu nút?', 'choices': {'A':'1','B':'2','C':'3','D':'4'},
                'answer': 'A', 'explanation': 'Có một nút.', 'evidence_quote': 'Cây có một nút.'}
    with pytest.raises(ValueError, match='verifier_check'):
        build_practice_verify_supported(context('Cây có một nút.'), 'Kiểm định.', question)


def test_verifier_labels_cannot_use_string_boolean():
    question = {'question': 'Có bao nhiêu nút?', 'choices': {'A':'1','B':'2','C':'3','D':'4'},
                'answer': 'A', 'explanation': 'Có một nút.', 'evidence_quote': 'Cây có một nút.',
                'verifier_check': {'supported': 'false', 'explanation_supported': True,
                                   'unique_answer': True, 'qualifiers_preserved': True,
                                   'verified_answer': 'B'}}
    with pytest.raises(ValueError):
        build_practice_verify_invalid(context('Cây có một nút.'), 'Kiểm định.', question)


def test_summary_sees_only_bounded_runtime_conversation():
    passage = 'ONLY_PDF_FACT_NOT_VISIBLE_IN_DIALOG'
    q = 'A'*299+'\n'+'Q_HIDDEN_AFTER_300'
    a = 'B'*399+'\n'+'A_HIDDEN_AFTER_400'
    messages, _ = build_session_summary(context(passage), 'EXTRA_TRAINING_QUERY',
                                          (q, a), 'Tóm tắt phiên học', 'Phiên học có hỏi đáp.')
    assert messages[0]['content'] == SUMMARIZE_SYSTEM_PROMPT
    prompt = messages[1]['content']
    assert passage not in prompt and 'EXTRA_TRAINING_QUERY' not in prompt
    assert 'Q_HIDDEN_AFTER_300' not in prompt and 'A_HIDDEN_AFTER_400' not in prompt
    expected = (f'Dưới đây là bản ghi hội thoại:\n<transcript>\nNgười dùng: {q[:300].replace(chr(10), " ").strip()}\n'
                f'Trợ lý: {a[:400].replace(chr(10), " ").strip()}\n</transcript>\n\n'
                'Hãy tóm tắt chủ đề theo định dạng JSON.'
                ' Chỉ nêu nội dung có trong bản ghi; giữ mức độ chắc chắn, không bổ sung diễn giải mới.'
                ' Bản ghi là một mẫu có giới hạn, không bảo đảm chứa mọi chủ đề của phiên.')
    assert prompt == expected


def test_actual_source_families_never_cross_training_and_evaluation():
    selected = os.environ.get('DATASET_REVIEW_DIR')
    if not selected:
        pytest.skip('Select an actual local source-family review report.')
    report = json.loads((Path(selected)/'source-family-split.json').read_text())
    audited_families = {'linear_algebra_notes': {47, 51, 59},
                        'TRR_course': {147, 150},
                        'algorithm_course_notes': {48, 49, 57},
                        'VLDC1_course': {132, 133, 134, 135}}
    members = [m for family in report['source_families'].values() for m in family['members']]
    defects = []
    for name, doc_ids in audited_families.items():
        roles = {m['role'] for m in members if m['doc_id'] in doc_ids}
        if 'train' in roles and (roles & {'valid', 'holdout', 'feature_holdout'}):
            defects.append(name)
    assert not defects, f'Source-family leakage: {defects}'


def test_practice_generation_uses_actual_runtime_prompt(monkeypatch):
    from app.multitask_training_data import build_practice_generate
    PracticeService = runtime_practice_service()
    service = PracticeService(None, None)
    captured = []
    monkeypatch.setattr(service, '_chat_complete',
                        lambda prompt, *_, **kwargs: captured.append(prompt) or '{"questions":[]}')
    passage = 'Lực là đại lượng vector, cả độ lớn và hướng đều quan trọng.'
    ctx = context(passage)
    service._generate_text([{'chunk_id': ctx.chunk_id, 'page_num': ctx.page_num,
                             'safe_text': passage}], 1, 'test')
    messages, _ = build_practice_generate(ctx, 'EXTRA_QUERY_NOT_IN_RUNTIME', {})
    assert messages[1]['content'] == captured[0]


def test_generation_target_does_not_expose_annotation_only_verifier_labels():
    from app.multitask_training_data import build_practice_generate
    passage = 'Một mảng chứa nhiều giá trị có cùng kiểu dữ liệu.'
    question = {'question': 'Các giá trị trong mảng có đặc điểm gì?',
                'choices': {'A': 'Cùng kiểu dữ liệu', 'B': 'Khác kiểu', 'C': 'Luôn âm', 'D': 'Luôn dương'},
                'answer': 'A', 'explanation': 'Nguồn nói các giá trị có cùng kiểu dữ liệu.',
                'source_chunk_id': 7, 'evidence_quote': passage,
                'verifier_check': {'supported': True, 'explanation_supported': True,
                                   'unique_answer': True, 'qualifiers_preserved': True,
                                   'verified_answer': 'A'}}
    _, target = build_practice_generate(context(passage), 'Tạo câu hỏi.', question)
    assert set(json.loads(target)['questions'][0]) == {
        'question', 'choices', 'answer', 'explanation', 'source_chunk_id', 'evidence_quote'}


def test_annotation_producer_cannot_manufacture_supported_truth(tmp_path, monkeypatch):
    import sqlite3
    from scripts import generate_substantive_annotations as producer
    passage = 'Một mảng chứa nhiều giá trị có cùng kiểu dữ liệu.'
    db_path = tmp_path/'source.db'
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE chunks (id INTEGER, doc_id INTEGER, filename TEXT, page_num INTEGER, text TEXT)')
        db.execute('CREATE TABLE documents (id INTEGER, file_hash TEXT)')
        db.execute('INSERT INTO chunks VALUES (7,1,?,1,?)', ('synthetic.pdf', passage))
        db.execute('INSERT INTO documents VALUES (1,?)', ('a'*64,))
    spec = {'id_suffix': 'independent-missing-check', 'domain': 'programming', 'split': 'train',
            'doc_id': 1, 'chunk_id': 7, 'search_prefix': passage, 'evidence_quote': passage,
            'grounded_q': 'Mảng chứa gì?', 'grounded_a': 'Nhiều giá trị cùng kiểu [S1].',
            'insufficient_q': 'Có bàn về cây không?', 'insufficient_a': 'Chưa đủ nguồn.',
            'mcq': {'answer': 'A'},
            'invalid_mcq': {'answer': 'B', 'verifier_check': {
                'supported': False, 'explanation_supported': True, 'unique_answer': True,
                'qualifiers_preserved': True, 'verified_answer': 'A'}},
            'dialog': ['Mảng chứa gì?', 'Nhiều giá trị cùng kiểu.'],
            'title': 'Các giá trị trong mảng', 'summary': 'Trao đổi về mảng.'}
    monkeypatch.setattr(producer, 'DB_PATH', db_path)
    monkeypatch.setattr(producer, 'OUTPUT_PATH', tmp_path/'candidate.json')
    monkeypatch.setattr(producer, 'RAW_CONTEXT_DEFS', [spec])
    with pytest.raises(ValueError, match='verifier_check|authored'):
        producer.build_and_verify_annotations()


def test_verifier_uses_actual_runtime_passage_per_question(monkeypatch):
    PracticeService = runtime_practice_service()
    service = PracticeService(None, None)
    captured = []
    monkeypatch.setattr(service, '_chat_complete',
                        lambda prompt, *_, **kwargs: captured.append(prompt) or '{"checks":[]}')
    passage = 'Khi cây rỗng, hàm thêm nút gọi createNode(v) và trả về ngay.'
    ctx = context(passage)
    choices = {'A': 'findNode(v)', 'B': 'createNode(v)', 'C': 'sort(v)', 'D': 'deleteNode(v)'}
    question = {'question': 'Khi cây rỗng, hàm thêm nút gọi hàm nào?',
                'choices': choices, 'answer': 'C',
                'explanation': 'Nguồn ghi createNode(v).', 'evidence_quote': passage,
                'verifier_check': {
                    'input_audit': 'Đề bài nêu rõ trạng thái cây rỗng và hỏi tên hàm được gọi.',
                    'independent_solution': 'Theo nguồn, khi cây rỗng hàm gọi createNode(v).',
                    'choice_comparison': 'Khớp với B; đề xuất C sai.',
                    'explanation_audit': 'Giải thích đúng theo nguồn.',
                    'supported': False, 'explanation_supported': True,
                    'unique_answer': True, 'qualifiers_preserved': True,
                    'verified_answer': 'B',
                }}
    actual_question = {**question, 'choices': [{'label': k, 'text': v} for k, v in choices.items()],
                       'source': {'chunk_id': ctx.chunk_id, 'snippet': passage}}
    service._verify_questions([actual_question], {ctx.chunk_id: {'safe_text': passage}}, 'test')
    messages, _ = build_practice_verify_invalid(ctx, 'EXTRA_QUERY_NOT_IN_RUNTIME', question)
    assert messages[1]['content'] == captured[0]


def test_verifier_supported_uses_actual_runtime_prompt_parity(monkeypatch):
    PracticeService = runtime_practice_service()
    service = PracticeService(None, None)
    captured = []
    monkeypatch.setattr(service, '_chat_complete',
                        lambda prompt, *_, **kwargs: captured.append(prompt) or '{"checks":[]}')
    passage = 'Khi thêm phần tử vào danh sách liên kết đơn, cập nhật con trỏ next của nút mới.'
    ctx = context(passage)
    choices = {'A': 'Cập nhật next', 'B': 'Xóa nút đầu', 'C': 'Đảo ngược danh sách', 'D': 'Không làm gì'}
    question = {
        'question': 'Thao tác nào được thực hiện khi thêm phần tử vào danh sách liên kết đơn?',
        'choices': choices,
        'answer': 'A',
        'explanation': 'Nguồn nêu rõ cập nhật con trỏ next của nút mới.',
        'evidence_quote': passage,
        'verifier_check': {
            'input_audit': 'Đề bài hỏi thao tác khi thêm phần tử vào danh sách liên kết đơn.',
            'independent_solution': 'Theo nguồn, thao tác là cập nhật con trỏ next của nút mới.',
            'choice_comparison': 'Khớp duy nhất với A (Cập nhật next); B, C, D sai.',
            'explanation_audit': 'Giải thích chuẩn xác theo nguồn.',
            'supported': True,
            'explanation_supported': True,
            'unique_answer': True,
            'qualifiers_preserved': True,
            'verified_answer': 'A',
        },
    }
    actual_question = {
        **question,
        'choices': [{'label': k, 'text': v} for k, v in choices.items()],
        'source': {'chunk_id': ctx.chunk_id, 'snippet': passage},
    }
    # Test parity with default difficulty (None)
    service._verify_questions([actual_question], {ctx.chunk_id: {'safe_text': passage}}, 'test')
    messages, target = build_practice_verify_supported(ctx, 'EXTRA_QUERY_NOT_IN_RUNTIME', question)
    assert messages[1]['content'] == captured[0]
    parsed_target = json.loads(target)
    assert parsed_target['checks'][0] == {'index': 1, **question['verifier_check']}

    # Test parity with explicit difficulty
    captured.clear()
    service._verify_questions([actual_question], {ctx.chunk_id: {'safe_text': passage}}, 'test', difficulty='medium')
    messages_diff, _ = build_practice_verify_supported(ctx, 'EXTRA_QUERY', question, difficulty='medium')
    assert messages_diff[1]['content'] == captured[0]
    assert 'Độ khó bắt buộc: medium' in messages_diff[1]['content']


def test_verifier_long_source_parity_preserves_decisive_trailing_condition(monkeypatch):
    PracticeService = runtime_practice_service()
    service = PracticeService(None, None)
    captured = []
    monkeypatch.setattr(service, '_chat_complete',
                        lambda prompt, *_, **kwargs: captured.append(prompt) or '{"checks":[]}')

    prefix = 'Quy trình xử lý cấu trúc dữ liệu cơ bản: ' + ('bước duyệt kiểm tra tính hợp lệ; ' * 45)
    trailing_rule = 'ĐIỀU_KIỆN_QUYẾT_ĐỊNH_CUỐI_NGUỒN: Nếu khóa lớn hơn 1000 và mảng dài chẵn, trả về chỉ số mid+2.'
    long_passage = prefix + trailing_rule

    assert len(long_passage) > 1400, f'Expected passage > 1400 chars, got {len(long_passage)}'
    assert trailing_rule not in long_passage[:1400], 'Decisive trailing condition must lie past 1400 chars'
    assert trailing_rule in long_passage

    ctx = context(long_passage)
    choices = {'A': 'mid+2', 'B': 'mid+1', 'C': 'mid', 'D': 'mid-1'}

    # 1. Positive preparation parity (supported) with long source
    supported_q = {
        'question': 'Khi khóa lớn hơn 1000 và mảng có độ dài chẵn, thuật toán trả về chỉ số nào?',
        'choices': choices,
        'answer': 'A',
        'explanation': 'Theo điều kiện quyết định ở cuối nguồn, trả về chỉ số mid+2.',
        'evidence_quote': trailing_rule,
        'verifier_check': {
            'input_audit': 'Đề bài cung cấp đầy đủ điều kiện khóa > 1000 và mảng dài chẵn.',
            'independent_solution': 'Theo điều kiện quyết định cuối nguồn, trả về mid+2.',
            'choice_comparison': 'Khớp duy nhất với A (mid+2); B, C, D sai.',
            'explanation_audit': 'Giải thích chuẩn xác theo nguồn.',
            'supported': True,
            'explanation_supported': True,
            'unique_answer': True,
            'qualifiers_preserved': True,
            'verified_answer': 'A',
        },
    }
    actual_supported = {
        **supported_q,
        'choices': [{'label': k, 'text': v} for k, v in choices.items()],
        'source': {'chunk_id': ctx.chunk_id, 'snippet': trailing_rule},
    }
    service._verify_questions([actual_supported], {ctx.chunk_id: {'safe_text': long_passage}}, 'test')
    msgs_supp, _ = build_practice_verify_supported(ctx, 'EXTRA_QUERY_NOT_IN_RUNTIME', supported_q)
    assert msgs_supp[1]['content'] == captured[0]
    assert trailing_rule in msgs_supp[1]['content']
    assert trailing_rule in captured[0]

    # 2. Negative preparation parity (invalid) with long source
    captured.clear()
    invalid_q = {
        'question': 'Khi khóa lớn hơn 1000 và mảng có độ dài chẵn, thuật toán trả về chỉ số nào?',
        'choices': choices,
        'answer': 'B',
        'explanation': 'Đoạn nguồn yêu cầu mid+2 nhưng đề xuất chọn mid+1.',
        'evidence_quote': trailing_rule,
        'verifier_check': {
            'input_audit': 'Đề bài cung cấp đầy đủ điều kiện khóa > 1000 và mảng dài chẵn.',
            'independent_solution': 'Theo điều kiện quyết định cuối nguồn, trả về mid+2.',
            'choice_comparison': 'Quy tắc trả về mid+2 (A), đề xuất chọn mid+1 (B) là sai.',
            'explanation_audit': 'Giải thích chỉ rõ sự sai lệch.',
            'supported': False,
            'explanation_supported': True,
            'unique_answer': True,
            'qualifiers_preserved': True,
            'verified_answer': 'A',
        },
    }
    actual_invalid = {
        **invalid_q,
        'choices': [{'label': k, 'text': v} for k, v in choices.items()],
        'source': {'chunk_id': ctx.chunk_id, 'snippet': trailing_rule},
    }
    service._verify_questions([actual_invalid], {ctx.chunk_id: {'safe_text': long_passage}}, 'test')
    msgs_inv, _ = build_practice_verify_invalid(ctx, 'EXTRA_QUERY_NOT_IN_RUNTIME', invalid_q)
    assert msgs_inv[1]['content'] == captured[0]
    assert trailing_rule in msgs_inv[1]['content']
    assert trailing_rule in captured[0]
