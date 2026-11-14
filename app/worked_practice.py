"""Propose bounded derivative exercises from complete cited worked solutions."""
import re

from app.math_choices import _polynomial


def source_derivative_proposals(passages, count):
    proposals = []
    for passage in passages:
        quote = passage.get('generation_evidence', '')
        match = re.fullmatch(r"\d+[.)]\s+f\(x\)\s*=\s*(.+?)\s+Solution:\s*(.+)", quote)
        if not match:
            continue
        expression, solution = match.groups()
        if re.fullmatch(r'the derivative is 0 since f is constant\.?', solution, re.I):
            result = '0'
            explanation = "Lời giải nêu f là hàm hằng nên f'(x) = 0."
        else:
            solved = re.fullmatch(r"using the (power|product) rule,\s*f['′]\(x\)\s*=\s*(.+)", solution, re.I)
            if not solved:
                continue
            steps = solved[2].rstrip('.')
            result = steps.rsplit('=', 1)[-1].strip()
            method = 'lũy thừa' if solved[1].lower() == 'power' else 'tích'
            explanation = f"Theo lời giải dùng quy tắc {method}: f'(x) = {steps}."
        value = _polynomial(result)
        if value is None:
            continue
        answers = [result]
        parsed = [value]
        for candidate in [f'-({result})', f'({result}) + 1', f'({result}) - 1', f'({result}) + 2']:
            polynomial = _polynomial(candidate)
            if polynomial is not None and polynomial not in parsed:
                answers.append(candidate)
                parsed.append(polynomial)
            if len(answers) == 4:
                break
        if len(answers) != 4:
            continue
        position = len(proposals) % 4
        answers.insert(position, answers.pop(0))
        proposals.append(dict(source_chunk_id=int(passage['chunk_id']), evidence_quote=quote,
                              question=f"Trong ví dụ đã giải, với f(x) = {expression}, đạo hàm f'(x) bằng bao nhiêu?",
                              explanation=explanation, choices=dict(zip('ABCD', answers)), answer='ABCD'[position]))
        if len(proposals) == count:
            return proposals
    return None
