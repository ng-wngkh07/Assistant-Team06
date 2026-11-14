"""Source-derived proposals retain full native review; these are synthetic fixtures."""
import json

from app.math_choices import equivalent_choice_pair
from app.practice import PracticeService


SOURCE = """1. f(x) = 17
Solution: the derivative is 0 since f is constant.
2. f(x) = x^3
Solution: using the power rule, f'(x) = 3x^2
3. f(x) = x^2(4 - x)
Solution: using the product rule, f'(x) = 2x(4 - x) + x^2(-1) = 8x - 3x^2.
"""


def test_native_three_derivatives_reuse_complete_source_solutions_before_review(monkeypatch):
    import app.model_resolver as resolver
    monkeypatch.setattr(resolver, 'is_mlx_model', lambda model: True)
    service = PracticeService(None, None)
    monkeypatch.setattr(service, '_chat_complete', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unnecessary model generation')))
    result = json.loads(service._generate_text([dict(chunk_id=91, text=SOURCE, safe_text=SOURCE)], 3, 'native', timeout=180))
    rows = result['questions']
    assert len(rows) == 3
    assert [row['choices'][row['answer']] for row in rows] == ['0', '3x^2', '8x - 3x^2']
    for row in rows:
        assert row['source_chunk_id'] == 91
        assert PracticeService.extract_supporting_evidence(SOURCE, row['evidence_quote'])
        assert equivalent_choice_pair([dict(label=k, text=v) for k, v in row['choices'].items()]) is None
        assert len(set(row['choices'].values())) == 4
    assert "2x(4 - x) + x^2(-1) = 8x - 3x^2" in rows[2]['explanation']


def test_source_proposals_do_not_replace_missing_or_unparseable_answers():
    from app.worked_practice import source_derivative_proposals
    passages = [dict(chunk_id=1, generation_evidence="1. f(x) = x^3 Solution: find it yourself.")]
    assert source_derivative_proposals(passages, 1) is None
    assert source_derivative_proposals(passages, 3) is None
    passages[0]['generation_evidence'] = "1. f(x) = x^3 Solution: using the power rule, f'(x) = __import__('os')"
    assert source_derivative_proposals(passages, 1) is None


def test_grounded_input_check_does_not_require_recomputing_explicit_source_results():
    from app.practice import build_grounded_review_prompt
    prompt = build_grounded_review_prompt([])
    assert 'đã nêu trực tiếp kết quả cần hỏi' in prompt
    assert 'Khi thiếu dữ kiện' in prompt
    assert 'không phải kết luận mọi lựa chọn sai' in prompt
