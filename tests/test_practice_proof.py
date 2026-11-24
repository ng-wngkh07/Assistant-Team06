import json
import pytest

from app.practice import PracticeService


@pytest.mark.parametrize('defect', [None, 'borrowed_context', 'multiple_matches', 'missing_basis'])
def test_native_admission_requires_bound_evidence_and_consistent_unique_match(monkeypatch, defect):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    source = 'The indicator is active only while the switch remains closed.'
    question = dict(question='When is the indicator active?',
                    choices=dict(A='While the switch is closed', B='Always', C='Never', D='For exactly one hour'),
                    answer='A', explanation=source, evidence_quote=source, source=dict(chunk_id=1))
    check = dict(index=1, source_basis=source, input_audit='Nguồn nêu điều kiện đóng công tắc.',
                 independent_solution='Đèn hoạt động trong khi công tắc đóng.', matching_choices='A',
                 choice_comparison='Chỉ A đúng theo điều kiện nguồn.', explanation_audit='Giải thích giữ nguyên điều kiện.',
                 supported=True, explanation_supported=True, unique_answer=True,
                 qualifiers_preserved=True, verified_answer='A')
    if defect == 'borrowed_context':
        check['source_basis'] = 'The indicator turns off after ten seconds.'
    elif defect == 'multiple_matches':
        check['matching_choices'] = 'AB'
    elif defect == 'missing_basis':
        del check['source_basis']
    service = PracticeService(None, None)
    service._chat_complete = lambda *args, **kwargs: json.dumps(dict(checks=[check]))
    actual = service._verify_questions([question], {1: dict(safe_text=source)}, 'native-test')
    assert actual == ([question] if defect is None else [])


def test_near_identical_article_variants_cannot_be_published_as_unique():
    quote = 'Every component has a particular responsibility.'
    q = dict(question='What responsibility does each component have?',
             choices=dict(A='A particular responsibility.', B='Particular responsibility.', C='None.', D='All system duties.'),
             answer='A', explanation='The source assigns a particular responsibility.',
             source=dict(chunk_id=1, snippet=quote), evidence_quote=quote)
    service = PracticeService(None, None)
    service._chat_complete = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Potential duplicate must be excluded before model acceptance'))
    assert service._verify_questions([q], {1: dict(safe_text=quote)}, 'test') == []


@pytest.mark.parametrize('reject_first,reserve_timeout,expected', [
    (False, False, 3), (True, False, 3), (True, True, 2),
])
def test_native_three_reviews_share_batch_and_only_review_needed_reserve(
        monkeypatch, reject_first, reserve_timeout, expected):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    source = 'The selected value follows the stated condition in this source.'
    questions = [dict(question=f'Which value is selected for case {i}?',
                      choices=dict(A=f'Selected value {i}', B=f'Other value {i}',
                                   C=f'Absent value {i}', D=f'Reversed value {i}'),
                      answer='A', explanation=source, evidence_quote=source,
                      source=dict(chunk_id=1)) for i in range(1, 5)]
    calls = []
    service = PracticeService(None, None)

    def review(prompt, model, max_tokens, timeout=None, response_schema=None):
        items = json.loads(prompt.split('Dữ liệu cần kiểm định:\n')[1])
        calls.append((len(items), timeout, max_tokens))
        if reserve_timeout and items[0]['question'] == questions[3]['question']:
            raise TimeoutError('Reserve exhausted remaining deadline')
        checks = [dict(index=item['index'], source_basis=source,
                       input_audit='Source and question suffice.',
                       independent_solution='The selected value follows the condition.',
                       choice_comparison='A matches; B,C,D do not match the result.',
                       explanation_audit='The explanation follows the source.',
                       supported=True, unique_answer=True, matching_choices='A',
                       qualifiers_preserved=True, verified_answer='A',
                       explanation_supported=not (reject_first and item['question'] == questions[0]['question']))
                  for item in items]
        return json.dumps(dict(checks=checks))

    service._chat_complete = review
    result = service._verify_questions(questions, {1: dict(safe_text=source)},
                                       'native-test', timeout=120, stop_after=3)
    assert len(result) == expected
    assert [n for n, _, _ in calls] == ([3, 1] if reject_first else [3])
    assert calls[0][2] == 1500
    assert all(0 < seconds <= 120 for _, seconds, _ in calls)
    if reject_first:
        assert questions[0] not in result
        assert calls[1][1] <= calls[0][1]


def test_proof_backed_medium_builders_cover_chains_numeric_bounds_and_stoichiometry():
    from app.practice_proof import (
        build_proof_backed_medium_questions,
        verify_proof_backed_medium_question,
    )
    from app.question_validator import evaluate_question_difficulty

    passages = [
        {
            'page_num': 1,
            'text': (
                'Thang độ dẫn nhiệt tương đối: Kim cương > Bạc > Đồng > Vàng > Nhôm.\n'
                'Phản ứng nhiệt phân: 2KClO3 -> 2KCl + 3O2.\n'
                'Phản ứng xà phòng hóa trieste giả định:\n'
                'X3Y + 3KOH → 3XK + Y(OH)3'
            ),
        },
        {
            'page_num': 2,
            'text': (
                'Điều kiện kết hợp: Phần tử phải có từ 3 liên kết chéo trở lên (mạng đa tầng).\n'
                'Quy tắc phát sáng: Chuỗi có từ 4 mắt xích trở lên (mạch dài) phát huỳnh quang ổn định.'
            ),
        },
        {
            'page_num': 3,
            'text': (
                'Công thức dãy đồng đẳng giả định: CₙH₂ₙ₋₂O₂ (n ≥ 3).'
            ),
        },
    ]

    built3 = build_proof_backed_medium_questions(passages, count=3, context_passages=passages)
    assert len(built3) == 3  # nosec B101
    assert {q['page_num'] for q in built3} == {1, 2, 3}  # nosec B101

    built = build_proof_backed_medium_questions(passages, count=5, context_passages=passages)
    assert len(built) == 5  # nosec B101
    assert len({q['evidence_quote'] for q in built}) == 5  # nosec B101
    assert {q['page_num'] for q in built} == {1, 2, 3}  # nosec B101

    page_map = {p['page_num']: p['text'] for p in passages}
    for q in built:
        assert evaluate_question_difficulty(q) == 'medium'  # nosec B101
        assert 20 <= len(q['evidence_quote']) <= 240  # nosec B101
        assert q['evidence_quote'] in page_map[q['page_num']]  # nosec B101
        assert verify_proof_backed_medium_question(q, page_map[q['page_num']], context_passages=passages) is True  # nosec B101

    # Verify necessary condition vs direct eligibility distinction
    necessary_q = next(q for q in built if '3 liên kết chéo' in q['evidence_quote'])
    assert 'điều kiện cần' in necessary_q['choices'][necessary_q['answer']].casefold()  # nosec B101
    assert 'chưa khẳng định đủ điều kiện' in necessary_q['choices'][necessary_q['answer']].casefold()  # nosec B101
    assert 'Mẫu II và Mẫu III' in necessary_q['choices'][necessary_q['answer']]  # nosec B101
    direct_q = next(q for q in built if '4 mắt xích' in q['evidence_quote'])
    assert 'phát huỳnh quang ổn định' in direct_q['choices'][direct_q['answer']]  # nosec B101

    # Verify ordered chain ranks newly named scenario labels only (no original item names in choices)
    chain_q = next(q for q in built if 'Thang độ dẫn nhiệt' in q['evidence_quote'])
    assert 'Mẫu X' in chain_q['question'] and 'Mẫu Y' in chain_q['question'] and 'Mẫu Z' in chain_q['question']  # nosec B101
    assert chain_q['choices'][chain_q['answer']] == 'Mẫu Z > Mẫu X > Mẫu Y'  # nosec B101
    for choice_text in chain_q['choices'].values():
        assert all(name not in choice_text for name in ('Kim cương', 'Bạc', 'Đồng', 'Vàng', 'Nhôm'))  # nosec B101

    # Verify stoichiometry computation
    stoich_q = next(q for q in built if '3KOH' in q['evidence_quote'])
    assert '0,6 mol KOH' in stoich_q['choices'][stoich_q['answer']]  # nosec B101
    assert '0,6 mol XK' in stoich_q['choices'][stoich_q['answer']]  # nosec B101

    # Verify parametric domain bound
    formula_q = next(q for q in built if 'n ≥ 3' in q['evidence_quote'])
    assert 'C₄H₆O₂' in formula_q['choices'][formula_q['answer']]  # nosec B101
    assert 'n ≥ 3' in formula_q['choices'][formula_q['answer']]  # nosec B101

    # Verify Bayes conditional probability family on extended_probability.md
    from pathlib import Path
    ext_prob = (Path(__file__).resolve().parent / 'fixtures' / 'documents' / 'extended_probability.md').read_text(encoding='utf-8')
    prob_built = build_proof_backed_medium_questions([{'page_num': 4, 'text': ext_prob}], count=1)
    assert len(prob_built) == 1  # nosec B101
    assert verify_proof_backed_medium_question(prob_built[0], ext_prob) is True  # nosec B101
    assert 'P(A|B)=P(B|A)P(A)/P(B)' in prob_built[0]['evidence_quote']  # nosec B101


def test_proof_backed_medium_verifier_fails_closed_on_invalid_or_ambiguous_mutations():
    from app.practice_proof import (
        build_proof_backed_medium_questions,
        verify_proof_backed_medium_question,
    )

    ambiguous_passages = [
        {
            'page_num': 1,
            'text': (
                'Phản ứng thuận nghịch: A + 2B ⇌ 2C + D\n'
                'Quy tắc chưa chắc chắn: Mẫu có thể có từ 2 nút trở lên.\n'
                'Bảng tóm tắt | Cột A > Cột B > Cột C | Ghi chú\n'
                'Số học thuần: 5 > 3 > 1\n'
                'Bất đẳng thức hỗn hợp: A > B < C\n'
                'Trật tự tính base: Amine béo > NH₃ > Amine thơm.\n'
                'Ví dụ 1: Cho hàm số f(x) = x^3. Giải: Áp dụng quy tắc lũy thừa ta có đạo hàm f\'(x) = 3x^2.'
            ),
        }
    ]
    assert build_proof_backed_medium_questions(ambiguous_passages, count=5) == []  # nosec B101

    valid_source = (
        'Trật tự độ bền: Hợp chất A > Hợp chất B > Hợp chất C > Hợp chất D > Hợp chất E.\n'
        'Điều kiện tối thiểu: Cấu trúc phải có từ 2 vòng liên hợp trở lên (dạng vòng bền).'
    )
    built = build_proof_backed_medium_questions([{'page_num': 1, 'text': valid_source}], count=2)
    assert len(built) == 2  # nosec B101

    chain_q = built[0]
    assert verify_proof_backed_medium_question(chain_q, valid_source) is True  # nosec B101

    # Tampered answer key
    wrong_key = 'B' if chain_q['answer'] != 'B' else 'C'
    assert verify_proof_backed_medium_question({**chain_q, 'answer': wrong_key}, valid_source) is False  # nosec B101

    # Unbound citation
    assert verify_proof_backed_medium_question({**chain_q, 'evidence_quote': 'Trật tự độ bền: X > Y > Z.'}, valid_source) is False  # nosec B101

    # Easy recall marker smuggled into question stem
    assert verify_proof_backed_medium_question({**chain_q, 'question': 'Hợp chất A là gì?'}, valid_source) is False  # nosec B101

    # Choice collision / duplicate text
    dup_choices = dict(chain_q['choices'])
    other_label = next(k for k in 'ABCD' if k != chain_q['answer'])
    dup_choices[other_label] = dup_choices[chain_q['answer']]
    assert verify_proof_backed_medium_question({**chain_q, 'choices': dup_choices}, valid_source) is False  # nosec B101

    # Missing target page in context_passages fails closed
    assert verify_proof_backed_medium_question(chain_q, valid_source, context_passages=[{'page_num': 2, 'text': valid_source}]) is False  # nosec B101


def test_proof_suppresses_corrected_and_contradictory_rules_across_page_and_neighbors():
    from app.practice_proof import (
        build_proof_backed_medium_questions,
        verify_proof_backed_medium_question,
    )

    clean_rank = 'Thang độ dẫn nhiệt: Kim cương > Bạc > Đồng > Vàng > Nhôm.'
    clean_built = build_proof_backed_medium_questions([{'page_num': 1, 'text': clean_rank}], count=1)
    assert len(clean_built) == 1  # nosec B101
    clean_q = clean_built[0]

    # 1. Conflicting correction on the same page suppresses both inconsistent rules
    same_page_conflict = (
        f'{clean_rank}\n'
        'Đính chính trong cùng điều kiện: Bạc > Kim cương > Đồng > Vàng > Nhôm.'
    )
    assert build_proof_backed_medium_questions([{'page_num': 1, 'text': same_page_conflict}], count=3) == []  # nosec B101
    assert verify_proof_backed_medium_question(clean_q, same_page_conflict) is False  # nosec B101
    assert verify_proof_backed_medium_question(clean_q, clean_rank, context_passages=[{'page_num': 1, 'text': same_page_conflict}]) is False  # nosec B101

    # 2. Neighboring supplied page containing correction or reversed rank suppresses proof
    neighbor_passages = [
        {'page_num': 1, 'text': clean_rank},
        {'page_num': 2, 'text': 'Đính chính trang trước: Bạc > Kim cương > Đồng > Vàng > Nhôm.'},
    ]
    assert build_proof_backed_medium_questions(neighbor_passages[:1], count=1, context_passages=neighbor_passages) == []  # nosec B101
    assert build_proof_backed_medium_questions(neighbor_passages, count=3, context_passages=neighbor_passages) == []  # nosec B101
    assert verify_proof_backed_medium_question(clean_q, clean_rank, context_passages=neighbor_passages) is False  # nosec B101


def test_proof_suppresses_negated_and_explicitly_wrong_rules():
    from app.practice_proof import (
        build_proof_backed_medium_questions,
        verify_proof_backed_medium_question,
    )

    clean_chain = 'Trật tự độ bền: Hợp chất A > Hợp chất B > Hợp chất C > Hợp chất D > Hợp chất E.'
    clean_q = build_proof_backed_medium_questions([{'page_num': 1, 'text': clean_chain}], count=1)[0]

    # 1. Preceding wrong-rule directive on same page
    negated_same_page = f'Đừng áp dụng quy tắc sai sau:\n{clean_chain}'
    assert build_proof_backed_medium_questions([{'page_num': 1, 'text': negated_same_page}], count=3) == []  # nosec B101
    assert verify_proof_backed_medium_question(clean_q, negated_same_page) is False  # nosec B101

    # 2. Preceding wrong-rule directive on previous supplied page boundary
    cross_page_negated = [
        {'page_num': 1, 'text': 'Lưu ý tránh nhầm lẫn, đừng áp dụng quy tắc sai sau:'},
        {'page_num': 2, 'text': clean_chain},
    ]
    assert build_proof_backed_medium_questions(cross_page_negated, count=3, context_passages=cross_page_negated) == []  # nosec B101
    q_p2 = {**clean_q, 'page_num': 2, 'explanation': clean_q['explanation'].replace('trang 1:', 'trang 2:')}
    assert verify_proof_backed_medium_question(q_p2, clean_chain, context_passages=cross_page_negated) is False  # nosec B101


def test_proof_suppresses_exceptions_and_reaction_caveats_including_adjacent_pages():
    from app.practice_proof import (
        build_proof_backed_medium_questions,
        verify_proof_backed_medium_question,
    )

    clean_rxn = 'X3Y + 3KOH → 3XK + Y(OH)3'
    clean_q = build_proof_backed_medium_questions([{'page_num': 1, 'text': clean_rxn}], count=1)[0]

    # 1. Trailing reaction caveat on the same page
    caveat_same_page = f'{clean_rxn}\nLưu ý: phản ứng trên chỉ xảy ra hoàn toàn khi đun nóng kéo dài.'
    assert build_proof_backed_medium_questions([{'page_num': 1, 'text': caveat_same_page}], count=3) == []  # nosec B101
    assert verify_proof_backed_medium_question(clean_q, caveat_same_page) is False  # nosec B101

    # 2. Same-line exception on a rank rule
    exception_rank = 'Dẫn điện tốt nhất: Ag > Cu > Au > Al > Fe (ngoại trừ trạng thái siêu dẫn).'
    assert build_proof_backed_medium_questions([{'page_num': 1, 'text': exception_rank}], count=3) == []  # nosec B101

    # 3. Adjacent supplied page exception referencing the reaction
    adjacent_exception = [
        {'page_num': 1, 'text': clean_rxn},
        {'page_num': 2, 'text': 'Ngoại lệ của phản ứng X3Y + 3KOH: khi có hơi nước, hiệu suất giảm mạnh.'},
    ]
    assert build_proof_backed_medium_questions(adjacent_exception[:1], count=1, context_passages=adjacent_exception) == []  # nosec B101
    assert verify_proof_backed_medium_question(clean_q, clean_rxn, context_passages=adjacent_exception) is False  # nosec B101

    # 4. Control clean multi-page source still succeeds and verifies
    clean_control = [
        {'page_num': 1, 'text': clean_rxn},
        {'page_num': 2, 'text': 'Chú ý: Không nên dùng nước dập đám cháy kim loại kiềm.'},
        {'page_num': 3, 'text': 'Dẫn điện tốt nhất: Ag > Cu > Au > Al > Fe'},
    ]
    control_built = build_proof_backed_medium_questions(clean_control, count=2, context_passages=clean_control)
    assert len(control_built) == 2  # nosec B101
    pmap = {p['page_num']: p['text'] for p in clean_control}
    assert all(verify_proof_backed_medium_question(q, pmap[q['page_num']], context_passages=clean_control) for q in control_built)  # nosec B101


def test_proof_strict_necessity_never_implies_sufficiency_for_lower_bound_rules():
    from app.practice_proof import build_proof_backed_medium_questions

    passages = [
        {
            'page_num': 1,
            'text': 'Điều kiện tham gia: Mẫu có từ 3 liên kết chéo trở lên mới bền mạng.',
        }
    ]
    built = build_proof_backed_medium_questions(passages, count=1)
    assert len(built) == 1  # nosec B101
    q = built[0]
    correct_choice = q['choices'][q['answer']].casefold()
    assert 'điều kiện cần' in correct_choice  # nosec B101
    assert 'chưa khẳng định đủ điều kiện' in correct_choice  # nosec B101
    assert any('chắc chắn thỏa mãn đầy đủ mọi điều kiện' in c.casefold() for k, c in q['choices'].items() if k != q['answer'])  # nosec B101


def test_proof_backed_medium_questions_respects_exclude_questions_and_quotes():
    from app.practice_proof import build_proof_backed_medium_questions

    passages = [
        {
            'page_num': 1,
            'text': 'X3Y + 3KOH → 3XK + Y(OH)3',
        },
        {
            'page_num': 2,
            'text': 'Dẫn điện tốt nhất: Ag > Cu > Au > Al > Fe',
        },
        {
            'page_num': 3,
            'text': 'Điều kiện tham gia: Mẫu có từ 3 liên kết chéo trở lên mới bền mạng.',
        },
    ]
    batch1 = build_proof_backed_medium_questions(passages, count=1, context_passages=passages)
    assert len(batch1) == 1  # nosec B101
    batch2 = build_proof_backed_medium_questions(
        passages,
        count=2,
        context_passages=passages,
        exclude_questions=[batch1[0]['question']],
        exclude_quotes={batch1[0]['evidence_quote']},
    )
    assert len(batch2) == 2  # nosec B101
    assert all(item['question'] != batch1[0]['question'] for item in batch2)  # nosec B101
    assert all(item['evidence_quote'] != batch1[0]['evidence_quote'] for item in batch2)  # nosec B101


def test_native_grounded_review_prompt_moves_array_reference_to_json_item_only_when_difficulty_set():
    from app.practice_proof import native_grounded_review_prompt

    item_a = {
        'index': 1,
        'source': 'Mảng kết quả áp dụng chỉ số bắt đầu từ 0 theo chuẩn ngôn ngữ.',
        'question': 'Cho mảng a = [10, 20, 30], giá trị a[1] bằng bao nhiêu?',
        'choices': {'A': '10', 'B': '20', 'C': '30', 'D': '0'},
        'explanation': 'Tại index 1 có giá trị 20.',
        'evidence_quote': 'Mảng kết quả áp dụng chỉ số bắt đầu từ 0 theo chuẩn ngôn ngữ.',
    }
    item_b = {
        'index': 1,
        'source': 'Mảng kết quả áp dụng chỉ số bắt đầu từ 0 theo chuẩn ngôn ngữ.',
        'question': 'Cho mảng a = [5, 9], giá trị a[0] bằng bao nhiêu?',
        'choices': {'A': '5', 'B': '9', 'C': '0', 'D': '1'},
        'explanation': 'Tại index 0 có giá trị 5.',
        'evidence_quote': 'Mảng kết quả áp dụng chỉ số bắt đầu từ 0 theo chuẩn ngôn ngữ.',
    }

    pinned = native_grounded_review_prompt(item_a, difficulty=None)
    assert 'Question array transcription, index=1:' in pinned.split('Dữ liệu cần kiểm định:\n', 1)[0]

    hard_a = native_grounded_review_prompt(item_a, difficulty='hard')
    hard_b = native_grounded_review_prompt(item_b, difficulty='hard')
    prefix_a, body_a = hard_a.split('Dữ liệu cần kiểm định:\n', 1)
    prefix_b, _body_b = hard_b.split('Dữ liệu cần kiểm định:\n', 1)
    assert prefix_a == prefix_b
    assert 'Question array transcription' not in prefix_a
    parsed_a = json.loads(body_a)
    assert 'array_index_reference' in parsed_a[0]
