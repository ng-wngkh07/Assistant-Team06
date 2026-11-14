import pytest

from app.math_choices import equivalent_choice_pair


def pair(left, right):
    return equivalent_choice_pair([{'label': 'A', 'text': left}, {'label': 'B', 'text': right}])


@pytest.mark.parametrize('left,right', [
    ('(1/4)x^3', '4(1/16)x^3'),
    ('(1/4)x^3', '(1/16) * 4x^3'),
    ('2x(5 − 2x) + x^2 · (−2)', '−6x^2 + 10x'),
    ('2x(5 − 2x) − 2x^2', '−6x² + 10x'),
    ('1/2', '0.5'),
    ('x*(y+z)', 'x*y+x*z'),
])
def test_exact_polynomial_equivalence(left, right):
    assert pair(left, right) == ('A', 'B')


@pytest.mark.parametrize('left,right', [
    ('(1/4)x^3', '(1/16)x^3'),
    ('−6x^2 + 10x', '10x − 4x^2'),
    ('x/x', '1'),
    ('1/x', 'x'),
    ('0K', '0J'),
    ('0m', '0s'),
    ('sin x', 'cos x'),
    ('2e+3', '2*e+3'),
    ('0^0', '1'),
    ('x^0', '1'),
    ('x^100000000', '0'),
    ('__import__("os")', '0'),
    ('Không xác định', '0'),
])
def test_distinct_or_unsupported_expressions_are_not_claimed_equivalent(left, right):
    assert pair(left, right) is None


def test_practice_rejects_proven_equivalent_choices_before_model_review():
    from app.practice import PracticeService
    quote = 'For this exercise, the expression F is (1/4)x^3.'
    question = {'question': 'Which expression equals F?', 'answer': 'A',
                'choices': [{'label': 'A', 'text': '(1/4)x^3'},
                            {'label': 'B', 'text': '4(1/16)x^3'},
                            {'label': 'C', 'text': 'x^2'}, {'label': 'D', 'text': '0'}],
                'explanation': 'The source defines F.',
                'source': {'chunk_id': 1, 'snippet': quote}}
    service = PracticeService(None, None)
    service._chat_complete = lambda *args, **kwargs: pytest.fail('Ambiguous choices must not reach model acceptance')
    assert service._verify_questions([question], {1: {'safe_text': quote}}, 'test') == []


def test_generated_distractors_are_distinct_without_changing_proposed_key():
    from app.math_choices import distinct_polynomial_choices
    choices = [{'label': 'A', 'text': '2x(5-2x)-2x^2'},
               {'label': 'B', 'text': '-6x^2+10x'},
               {'label': 'C', 'text': '10x-4x^2'},
               {'label': 'D', 'text': '-6x^2+10x+1'}]
    result = distinct_polynomial_choices(choices, 'B')
    assert result[1] == choices[1]
    assert result[2:] == choices[2:]
    assert equivalent_choice_pair(result) is None
    assert choices[0]['text'] == '2x(5-2x)-2x^2'


def test_distractor_repair_leaves_units_and_unsupported_expressions_for_rejection():
    from app.math_choices import distinct_polynomial_choices
    choices = [{'label': 'A', 'text': '0J'}, {'label': 'B', 'text': '0K'},
               {'label': 'C', 'text': 'x'}, {'label': 'D', 'text': '1*x'}]
    assert distinct_polynomial_choices(choices, 'C') == choices


def test_unestablished_exponent_words_preserves_the_actual_unestablished_token():
    from app.math_choices import unestablished_exponent_words
    source = 'x^r = e^(ln(x^r)) = e^(r ln x)'
    explanation = 'e^(r is x) * r/x and e^(r ln x)'
    assert unestablished_exponent_words(source, 'Find x^5 for x > 0.', explanation) == [
        {'expression': 'e^(r is x)', 'word': 'is'}]
    assert unestablished_exponent_words(source, '', 'e^(r ln x)') == []
    assert unestablished_exponent_words('For e^(alpha), alpha is defined.', '', 'e^(alpha)') == []


def test_unestablished_exponent_notation_fails_closed_before_model_review():
    from app.practice import PracticeService
    quote = 'For x > 0, x^r = e^(r ln x) and its derivative is r*x^(r-1).'
    question = {'question': 'For x > 0, what is the derivative of x^5?', 'answer': 'A',
                'choices': {'A': '5x^4', 'B': '5x^5', 'C': 'x^4', 'D': '5x^6'},
                'explanation': 'The derivative uses e^(r is x) * r/x.',
                'source': {'chunk_id': 1, 'snippet': quote}}
    service = PracticeService(None, None)
    service._chat_complete = lambda *args, **kwargs: pytest.fail('Unestablished notation must not reach acceptance')
    assert service._verify_questions([question], {1: {'safe_text': quote}}, 'test') == []
