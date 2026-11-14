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
