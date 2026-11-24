"""Propose bounded derivative and Bayes-rule exercises from cited source passages."""
from fractions import Fraction
import hashlib
import html
import re
from typing import Any, Optional

from app.math_choices import _polynomial, equivalent_choice_pair
from app.practice_proof import potential_choice_collision


__all__ = [
    "source_bayes_proposals",
    "source_conditional_prob_proposals",
    "source_derivative_proposals",
    "source_hard_multistep_proposals",
]


_BAYES_SCENARIOS = (
    (Fraction(1, 4), Fraction(3, 5), Fraction(3, 10)),
    (Fraction(7, 20), Fraction(3, 5), Fraction(7, 10)),
    (Fraction(1, 10), Fraction(4, 5), Fraction(2, 5)),
    (Fraction(3, 5), Fraction(1, 2), Fraction(2, 5)),
    (Fraction(16, 25), Fraction(3, 4), Fraction(4, 5)),
    (Fraction(3, 25), Fraction(3, 4), Fraction(3, 5)),
)

_UNTRUSTED_TEXT_RE = re.compile(
    r'(?:["“”]|<\||\|>|__|'
    r'\bbỏ\s*qua\b|\bignore\b|\binjection\b|\bbí\s*mật\b|\bsecret\b|'
    r'\bsystem\b|\bhệ\s*thống\b|(?<![.,\d])999(?!\d)|\bthay\s+tất\s+cả\b|\boverride\b)',
    re.IGNORECASE,
)

_EXERCISE_OR_ANSWER_RE = re.compile(
    r'(?:\bbài\s+tập\b|\bbài\s+\d+|\bcâu\s+\d+|\bđáp\s+án\b|\blời\s+giải\b|'
    r'\bcho\s+p\s*\(|\btính\s+p\s*\(|\btìm\s+p\s*\(|'
    r'\bproblem\b|\bexercise\b|\bsolution\b|\banswer\b)',
    re.IGNORECASE,
)

_NEGATION_RE = re.compile(
    r'(?:\bkhông\s+(?:phải|cần|đúng|thỏa|có|thể\s+áp\s+dụng|dùng|áp\s+dụng)\b|'
    r'\bsai\b|\bnot\b|\bnever\b|\bfalse\b|\binvalid\b|\bcannot\b)',
    re.IGNORECASE,
)

_BAYES_COND_FIRST_RE = re.compile(
    r'P\(\s*([A-Z])\s*\|\s*([A-Z])\s*\)\s*=\s*'
    r'(?:P\(\s*([A-Z])\s*\|\s*([A-Z])\s*\)\s*(?:[*·×]\s*)?P\(\s*([A-Z])\s*\)|'
    r'\(\s*P\(\s*([A-Z])\s*\|\s*([A-Z])\s*\)\s*(?:[*·×]\s*)?P\(\s*([A-Z])\s*\)\s*\))'
    r'\s*/\s*P\(\s*([A-Z])\s*\)'
)

_BAYES_MARG_FIRST_RE = re.compile(
    r'P\(\s*([A-Z])\s*\|\s*([A-Z])\s*\)\s*=\s*'
    r'(?:P\(\s*([A-Z])\s*\)\s*(?:[*·×]\s*)?P\(\s*([A-Z])\s*\|\s*([A-Z])\s*\)|'
    r'\(\s*P\(\s*([A-Z])\s*\)\s*(?:[*·×]\s*)?P\(\s*([A-Z])\s*\|\s*([A-Z])\s*\)\s*\))'
    r'\s*/\s*P\(\s*([A-Z])\s*\)'
)

_PROB_ASSIGN_RE = re.compile(r'P\s*\([^)]+\)\s*=')
_AFTER_FORMULA_DISALLOWED_RE = re.compile(r'^(?:[+\-*·×/=<>!^(\[\]{}%)]|\d|P\s*\(|[A-Z]\b)')


def _format_exact_decimal(value: Fraction, max_places: int = 3) -> Optional[str]:
    """Format a Fraction as an exact terminating decimal without float rounding."""
    if value.denominator <= 0:
        return None
    for places in range(max_places + 1):
        scale = 10 ** places
        if (value.numerator * scale) % value.denominator == 0:
            scaled = (value.numerator * scale) // value.denominator
            if places == 0:
                formatted = str(scaled)
            else:
                sign = '-' if scaled < 0 else ''
                digits = str(abs(scaled)).rjust(places + 1, '0')
                int_part = digits[:-places]
                frac_part = digits[-places:].rstrip('0')
                formatted = f'{sign}{int_part}.{frac_part}' if frac_part else f'{sign}{int_part}'
            if Fraction(formatted) == value:
                return formatted
    return None


def _is_feasible_bayes_scenario(p_a: Fraction, p_b_given_a: Fraction, p_b: Fraction) -> bool:
    """Verify full joint probability feasibility for a Bayes scenario via exact rationals."""
    zero, one = Fraction(0), Fraction(1)
    if not (zero < p_a < one and zero < p_b_given_a < one and zero < p_b < one):
        return False
    p_ab = p_b_given_a * p_a
    p_a_not_b = p_a - p_ab
    p_not_a_b = p_b - p_ab
    p_not_a_not_b = one - p_a - p_not_a_b
    joints = (p_ab, p_a_not_b, p_not_a_b, p_not_a_not_b)
    if any(not (zero < cell < one) for cell in joints):
        return False
    if sum(joints) != one:
        return False
    posterior = p_ab / p_b
    return zero < posterior < one and posterior * p_b == p_ab


def _safe_fraction(literal: str) -> Optional[Fraction]:
    try:
        return Fraction(literal.replace(',', '.'))
    except (ValueError, ZeroDivisionError):
        return None


def _has_untrusted_or_conflicting_text(text: str) -> bool:
    return bool(
        _UNTRUSTED_TEXT_RE.search(text)
        or _EXERCISE_OR_ANSWER_RE.search(text)
        or _NEGATION_RE.search(text)
    )


def _match_exact_bayes_formula(text: str) -> Optional[tuple[str, str]]:
    """Match an untampered symbolic Bayes formula P(A|B) = P(B|A)P(A)/P(B) and return (A, B)."""
    if len(_PROB_ASSIGN_RE.findall(text)) != 1:
        return None

    matched_pairs: list[tuple[int, int, str, str]] = []
    for regex, cond_first in ((_BAYES_COND_FIRST_RE, True), (_BAYES_MARG_FIRST_RE, False)):
        for match in regex.finditer(text):
            groups = match.groups()
            lhs_a, lhs_b = groups[0], groups[1]
            if lhs_a == lhs_b:
                continue
            inner = groups[2:5] if groups[2] is not None else groups[5:8]
            denom = groups[8]
            if cond_first:
                c_a, c_b, m_a = inner
                if not (c_a == lhs_b and c_b == lhs_a and m_a == lhs_a and denom == lhs_b):
                    continue
            else:
                m_a, c_a, c_b = inner
                if not (m_a == lhs_a and c_a == lhs_b and c_b == lhs_a and denom == lhs_b):
                    continue
            start, end = match.span()
            if start > 0 and text[start - 1].isalnum():
                continue
            before = text[:start].rstrip()
            if before and (before[-1] in '+-*·×/=<>!^([{%' or before[-1].isdigit()):
                continue
            if end < len(text) and text[end].isalnum():
                continue
            after = text[end:].lstrip()
            if after and _AFTER_FORMULA_DISALLOWED_RE.match(after):
                continue
            matched_pairs.append((start, end, lhs_a, lhs_b))

    if len(matched_pairs) != 1:
        return None
    return matched_pairs[0][2], matched_pairs[0][3]


def _has_explicit_strict_positivity(quote: str, paragraph: str, event_b: str) -> bool:
    """Require explicit P(B) > 0 in quote and reject any contradictory or non-strict bound."""
    esc_b = re.escape(event_b)
    pos_forward = re.compile(rf'(?<![=<>\d.!])P\(\s*{esc_b}\s*\)\s*>\s*0(?![.,]\d|\d)')
    pos_reverse = re.compile(rf'(?<![.,\d])0\s*<\s*P\(\s*{esc_b}\s*\)(?![=<>])')
    if not (pos_forward.search(quote) or pos_reverse.search(quote)):
        return False

    bad_forward = re.compile(rf'P\(\s*{esc_b}\s*\)\s*(?:>=|≥|<=|≤|<|=|!=|≠)\s*0(?![.,]\d|\d)')
    bad_reverse = re.compile(rf'(?<![.,\d])0\s*(?:<=|≤|>=|≥|>|=|!=|≠)\s*P\(\s*{esc_b}\s*\)')
    if bad_forward.search(paragraph) or bad_reverse.search(paragraph):
        return False
    return True


def _extract_bayes_rule_evidence(passage: dict[str, Any]) -> Optional[tuple[str, str, str]]:
    """Return bound (evidence_quote, event_a, event_b) when passage states exact Bayes rule and P(B)>0."""
    from app.practice import PracticeService

    raw_text = str(passage.get('text', passage.get('passage', '')))
    if not raw_text.strip():
        return None
    safe_text = passage.get('safe_text')

    def _is_bound_quote(quote: str) -> bool:
        if not 20 <= len(quote) <= 240:
            return False
        if PracticeService._normalized(quote) not in PracticeService._normalized(raw_text):
            return False
        if not PracticeService.extract_supporting_evidence(raw_text, quote):
            return False
        if safe_text is not None and not PracticeService.extract_supporting_evidence(str(safe_text), quote):
            return False
        return True

    bound_quote = passage.get('generation_evidence')
    if bound_quote is not None:
        if not isinstance(bound_quote, str):
            return None
        folded_bound = ' '.join(bound_quote.split())
        enclosing = PracticeService.extract_surrounding_context(raw_text, bound_quote) or folded_bound
        if (_has_untrusted_or_conflicting_text(folded_bound)
                or _has_untrusted_or_conflicting_text(enclosing)
                or not _is_bound_quote(bound_quote)):
            return None
        formula = _match_exact_bayes_formula(folded_bound)
        if formula is None or len(_PROB_ASSIGN_RE.findall(enclosing)) != 1:
            return None
        event_a, event_b = formula
        if not _has_explicit_strict_positivity(folded_bound, enclosing, event_b):
            return None
        return bound_quote, event_a, event_b

    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', raw_text) if p.strip()]
    for para in paragraphs:
        folded_para = ' '.join(para.split())
        if _has_untrusted_or_conflicting_text(folded_para):
            continue
        if len(_PROB_ASSIGN_RE.findall(folded_para)) != 1:
            continue

        candidates: list[str] = []
        if 20 <= len(folded_para) <= 240:
            candidates.append(folded_para)
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', folded_para) if s.strip()]
        for width in (3, 2, 1):
            for idx in range(len(sentences) - width + 1):
                window = ' '.join(sentences[idx:idx + width])
                if 20 <= len(window) <= 240 and window not in candidates:
                    candidates.append(window)

        for quote in candidates:
            if _has_untrusted_or_conflicting_text(quote) or not _is_bound_quote(quote):
                continue
            formula = _match_exact_bayes_formula(quote)
            if formula is None:
                continue
            event_a, event_b = formula
            if _has_explicit_strict_positivity(quote, folded_para, event_b):
                return quote, event_a, event_b
    return None


def _build_bayes_choices(
    p_a: Fraction,
    p_b_given_a: Fraction,
    p_b: Fraction,
    posterior: Fraction,
    quote: str,
    s_pa: str,
    s_pba: str,
    s_pb: str,
) -> Optional[tuple[dict[str, str], str]]:
    """Build 4 distinct rational decimal choices and deterministic answer key."""
    answer_text = _format_exact_decimal(posterior)
    answer_poly = _polynomial(answer_text) if answer_text else None
    if not answer_text or answer_poly is None:
        return None

    numerator = p_b_given_a * p_a
    candidate_rationals = [
        numerator,
        p_a,
        p_b,
        p_b_given_a,
        p_a + p_b - numerator,
        p_b - numerator,
        Fraction(1) - posterior,
        (p_b_given_a * p_b) / p_a,
        p_b_given_a / p_b,
        Fraction(1, 10),
        Fraction(2, 5),
        Fraction(7, 10),
        Fraction(4, 5),
    ]
    distractors: list[str] = []
    used_polys = [answer_poly]
    for cand in candidate_rationals:
        if not (Fraction(0) < cand < Fraction(1)):
            continue
        text = _format_exact_decimal(cand)
        if not text:
            continue
        poly = _polynomial(text)
        if poly is None or any(poly == prev for prev in used_polys):
            continue
        distractors.append(text)
        used_polys.append(poly)
        if len(distractors) == 3:
            break

    if len(distractors) != 3:
        return None

    options = list(distractors)
    digest = hashlib.sha256(f'{quote}\0{s_pa}\0{s_pba}\0{s_pb}'.encode('utf-8')).digest()
    position = digest[0] % 4
    options.insert(position, answer_text)
    choices_dict = dict(zip('ABCD', options))
    choice_items = [{'label': label, 'text': text} for label, text in choices_dict.items()]
    if equivalent_choice_pair(choice_items) is not None or potential_choice_collision(choice_items):
        return None
    return choices_dict, 'ABCD'[position]


def source_bayes_proposals(
    passages: list[dict[str, Any]],
    count: int,
    exclude_questions: Optional[list[str]] = None,
) -> Optional[list[dict[str, Any]]]:
    """Propose at most one proof-backed Bayes-rule medium exercise per distinct event-pair passage."""
    from app.practice import PracticeService

    if count < 1:
        return None
    excluded = {
        PracticeService._normalized(q)
        for q in (exclude_questions or [])
        if isinstance(q, str) and q.strip()
    }
    proposals: list[dict[str, Any]] = []
    seen_event_pairs: set[tuple[str, str]] = set()
    seen_chunks: set[int] = set()

    for passage in passages:
        raw_chunk_id = passage.get('chunk_id')
        if raw_chunk_id is None:
            chunk_id = 1
        else:
            try:
                chunk_id = int(raw_chunk_id)
            except (TypeError, ValueError):
                continue
        if chunk_id in seen_chunks:
            continue

        extracted = _extract_bayes_rule_evidence(passage)
        if extracted is None:
            continue
        quote, event_a, event_b = extracted
        if (event_a, event_b) in seen_event_pairs:
            continue

        raw_text = str(passage.get('text', passage.get('passage', '')))
        source_numbers = {
            frac
            for m in re.finditer(r'(?<![A-Za-z0-9_.])\d+(?:[.,]\d+)?(?![A-Za-z0-9_.])', raw_text)
            for frac in [_safe_fraction(m.group(0))]
            if frac is not None
        }

        chosen: Optional[tuple[Fraction, Fraction, Fraction, Fraction, Fraction]] = None
        for p_a, p_b_given_a, p_b in _BAYES_SCENARIOS:
            if not _is_feasible_bayes_scenario(p_a, p_b_given_a, p_b):
                continue
            numerator = p_b_given_a * p_a
            posterior = numerator / p_b
            if {p_a, p_b_given_a, p_b, numerator, posterior}.isdisjoint(source_numbers):
                chosen = (p_a, p_b_given_a, p_b, numerator, posterior)
                break
        if chosen is None:
            continue

        p_a, p_b_given_a, p_b, numerator, posterior = chosen
        s_pa = _format_exact_decimal(p_a)
        s_pba = _format_exact_decimal(p_b_given_a)
        s_pb = _format_exact_decimal(p_b)
        s_num = _format_exact_decimal(numerator)
        s_ans = _format_exact_decimal(posterior)
        if not all((s_pa, s_pba, s_pb, s_num, s_ans)):
            continue

        built_choices = _build_bayes_choices(
            p_a, p_b_given_a, p_b, posterior, quote, s_pa, s_pba, s_pb
        )
        if built_choices is None:
            continue
        choices_dict, answer_key = built_choices

        question_text = (
            f'Trong cùng một mô hình xác suất, giả sử P({event_a}) = {s_pa}, '
            f'P({event_b}|{event_a}) = {s_pba} và P({event_b}) = {s_pb} > 0. '
            f'Áp dụng công thức trong tài liệu, giá trị P({event_a}|{event_b}) bằng bao nhiêu?'
        )
        if PracticeService._normalized(question_text) in excluded:
            seen_event_pairs.add((event_a, event_b))
            seen_chunks.add(chunk_id)
            continue

        explanation = (
            f'Theo tài liệu, khi P({event_b}) > 0 thì '
            f'P({event_a}|{event_b}) = P({event_b}|{event_a})P({event_a})/P({event_b}). '
            f'Thay dữ kiện giả định của đề (P({event_b}) = {s_pb} > 0): '
            f'P({event_a}|{event_b}) = ({s_pba} × {s_pa}) / {s_pb} = {s_num} / {s_pb} = {s_ans}.'
        )
        seen_event_pairs.add((event_a, event_b))
        seen_chunks.add(chunk_id)
        excluded.add(PracticeService._normalized(question_text))
        proposals.append({
            'source_chunk_id': chunk_id,
            'evidence_quote': quote,
            'question': question_text,
            'explanation': explanation,
            'choices': choices_dict,
            'answer': answer_key,
        })
        if len(proposals) >= count:
            break

    return proposals or None


_COND_PROB_RE = re.compile(
    r"P\(\s*([A-Z])\s*\|\s*([A-Z])\s*\)\s*=\s*"
    r"P\(\s*([A-Z])\s*(?:∩|\\cap|,|\s)?\s*([A-Z])\s*\)\s*/\s*P\(\s*([A-Z])\s*\)"
)
_COMPLEMENT_PROB_RE = re.compile(
    r"P\(\s*(?:([A-Z])['′]|\\bar\{([A-Z])\}|([A-Z])\u0304)\s*\)\s*=\s*1\s*[−-]\s*P\(\s*([A-Z])\s*\)"
)
_COND_PROB_SCENARIOS = (
    (Fraction(3, 25), Fraction(3, 5)),
    (Fraction(9, 50), Fraction(3, 4)),
    (Fraction(7, 25), Fraction(4, 5)),
    (Fraction(9, 40), Fraction(3, 8)),
)
_COMPLEMENT_SCENARIOS = (
    Fraction(3, 20),
    Fraction(7, 25),
    Fraction(9, 25),
    Fraction(13, 20),
    Fraction(18, 25),
)


def _match_boundary_ok(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1].isalnum():
        return False
    before = text[:start].rstrip()
    if before and (before[-1] in "+-*·×/=<>!^([{% "[:-1] or before[-1].isdigit()):
        return False
    if end < len(text) and text[end].isalnum():
        return False
    after = text[end:].lstrip()
    if after and _AFTER_FORMULA_DISALLOWED_RE.match(after):
        return False
    return True


def _extract_source_numbers(raw_text: str) -> set[Fraction]:
    return {
        frac
        for m in re.finditer(r"(?<![A-Za-z0-9_.])\d+(?:[.,]\d+)?(?![A-Za-z0-9_.])", raw_text)
        for frac in [_safe_fraction(m.group(0))]
        if frac is not None
    }


def _build_distinct_decimal_choices(
    answer_val: Fraction,
    distractor_cands: list[Fraction],
    seed_key: str,
) -> Optional[tuple[dict[str, str], str]]:
    answer_text = _format_exact_decimal(answer_val)
    answer_poly = _polynomial(answer_text) if answer_text else None
    if not answer_text or answer_poly is None:
        return None
    distractors: list[str] = []
    used_polys = [answer_poly]
    for cand in distractor_cands:
        if not (Fraction(0) < cand < Fraction(1)):
            continue
        text = _format_exact_decimal(cand)
        if not text:
            continue
        poly = _polynomial(text)
        if poly is None or any(poly == prev for prev in used_polys):
            continue
        distractors.append(text)
        used_polys.append(poly)
        if len(distractors) == 3:
            break
    if len(distractors) != 3:
        return None
    options = list(distractors)
    digest = hashlib.sha256(seed_key.encode("utf-8")).digest()
    position = digest[0] % 4
    options.insert(position, answer_text)
    choices_dict = dict(zip("ABCD", options))
    choice_items = [{"label": k, "text": v} for k, v in choices_dict.items()]
    if equivalent_choice_pair(choice_items) is not None or potential_choice_collision(choice_items):
        return None
    return choices_dict, "ABCD"[position]


def _iter_safe_quote_windows(passage: dict[str, Any]):
    """Yield (quote, folded_enclosing) pairs that satisfy untrusted-text & quote-binding invariants."""
    from app.practice import PracticeService

    raw_text = str(passage.get("text", passage.get("passage", "")))
    if not raw_text.strip():
        return
    safe_text = passage.get("safe_text")

    def _is_bound(quote: str) -> bool:
        if not 20 <= len(quote) <= 240:
            return False
        if PracticeService._normalized(quote) not in PracticeService._normalized(raw_text):
            return False
        if not PracticeService.extract_supporting_evidence(raw_text, quote):
            return False
        if safe_text is not None and not PracticeService.extract_supporting_evidence(str(safe_text), quote):
            return False
        return True

    for para in (p.strip() for p in re.split(r"\n\s*\n", raw_text) if p.strip()):
        folded_para = " ".join(para.split())
        if _has_untrusted_or_conflicting_text(folded_para):
            continue
        if len(_PROB_ASSIGN_RE.findall(folded_para)) != 1:
            continue
        seen_q: set[str] = set()
        candidates: list[str] = []
        if 20 <= len(folded_para) <= 240:
            candidates.append(folded_para)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", folded_para) if s.strip()]
        for width in (3, 2, 1):
            for idx in range(len(sentences) - width + 1):
                window = " ".join(sentences[idx:idx + width])
                if 20 <= len(window) <= 240 and window not in seen_q:
                    seen_q.add(window)
                    candidates.append(window)
        for quote in candidates:
            if not _has_untrusted_or_conflicting_text(quote) and _is_bound(quote):
                yield quote, folded_para


def source_conditional_prob_proposals(
    passages: list[dict[str, Any]],
    count: int,
    exclude_questions: Optional[list[str]] = None,
) -> Optional[list[dict[str, Any]]]:
    """Propose proof-backed medium exercises for explicit P(A|B) = P(A∩B)/P(B) and complement rules."""
    from app.practice import PracticeService

    if count < 1:
        return None
    excluded = {
        PracticeService._normalized(q)
        for q in (exclude_questions or [])
        if isinstance(q, str) and q.strip()
    }
    proposals: list[dict[str, Any]] = []
    seen_chunks: set[int] = set()
    seen_keys: set[tuple[str, ...]] = set()

    for passage in passages:
        raw_chunk_id = passage.get("chunk_id")
        if raw_chunk_id is None:
            chunk_id = 1
        else:
            try:
                chunk_id = int(raw_chunk_id)
            except (TypeError, ValueError):
                continue
        if chunk_id in seen_chunks:
            continue

        raw_text = str(passage.get("text", passage.get("passage", "")))
        source_numbers = _extract_source_numbers(raw_text)

        for quote, enclosing in _iter_safe_quote_windows(passage):
            # Family 1: Conditional probability P(A|B) = P(A∩B)/P(B) with explicit P(B) > 0
            cond_matches = [
                m for m in _COND_PROB_RE.finditer(quote)
                if _match_boundary_ok(quote, *m.span())
            ]
            if len(cond_matches) == 1:
                m = cond_matches[0]
                lhs_a, lhs_b, j1, j2, denom = m.groups()
                if (
                    lhs_a != lhs_b
                    and denom == lhs_b
                    and {j1, j2} == {lhs_a, lhs_b}
                    and ("|" not in quote[m.start():m.end()].split("=", 1)[1])
                    and _has_explicit_strict_positivity(quote, enclosing, lhs_b)
                    and ("cond", lhs_a, lhs_b) not in seen_keys
                ):
                    for p_ab, p_b in _COND_PROB_SCENARIOS:
                        ans = p_ab / p_b
                        if not {p_ab, p_b, ans}.isdisjoint(source_numbers):
                            continue
                        s_ab = _format_exact_decimal(p_ab)
                        s_b = _format_exact_decimal(p_b)
                        s_ans = _format_exact_decimal(ans)
                        if not all((s_ab, s_b, s_ans)):
                            continue
                        built = _build_distinct_decimal_choices(
                            ans,
                            [p_ab, p_b, p_ab * p_b, Fraction(1) - ans, Fraction(1, 5), Fraction(2, 5)],
                            f"{quote}\0{s_ab}\0{s_b}",
                        )
                        if not built:
                            continue
                        choices_dict, answer_key = built
                        q_text = (
                            f"Trong cùng một không gian mẫu, giả sử P({lhs_a}∩{lhs_b}) = {s_ab} "
                            f"và P({lhs_b}) = {s_b} > 0. Theo công thức trong tài liệu, "
                            f"giá trị P({lhs_a}|{lhs_b}) bằng bao nhiêu?"
                        )
                        if PracticeService._normalized(q_text) in excluded:
                            continue
                        expl = (
                            f"Theo tài liệu, khi P({lhs_b}) > 0 thì "
                            f"P({lhs_a}|{lhs_b}) = P({lhs_a}∩{lhs_b})/P({lhs_b}). "
                            f"Thay dữ kiện giả định của đề (P({lhs_b}) = {s_b} > 0): "
                            f"P({lhs_a}|{lhs_b}) = {s_ab} / {s_b} = {s_ans}."
                        )
                        seen_chunks.add(chunk_id)
                        seen_keys.add(("cond", lhs_a, lhs_b))
                        excluded.add(PracticeService._normalized(q_text))
                        proposals.append({
                            "source_chunk_id": chunk_id,
                            "evidence_quote": quote,
                            "question": q_text,
                            "explanation": expl,
                            "choices": choices_dict,
                            "answer": answer_key,
                        })
                        break
                    if len(proposals) >= count or chunk_id in seen_chunks:
                        break

            # Family 2: Complement rule P(A') = 1 - P(A)
            comp_matches = [
                m for m in _COMPLEMENT_PROB_RE.finditer(quote)
                if _match_boundary_ok(quote, *m.span())
            ]
            if len(comp_matches) == 1:
                m = comp_matches[0]
                lhs_ev = m.group(1) or m.group(2) or m.group(3)
                rhs_ev = m.group(4)
                if lhs_ev == rhs_ev and ("comp", lhs_ev) not in seen_keys:
                    for p_a in _COMPLEMENT_SCENARIOS:
                        ans = Fraction(1) - p_a
                        if ans == p_a or not {p_a, ans}.isdisjoint(source_numbers):
                            continue
                        s_pa = _format_exact_decimal(p_a)
                        s_ans = _format_exact_decimal(ans)
                        if not (s_pa and s_ans):
                            continue
                        built = _build_distinct_decimal_choices(
                            ans,
                            [p_a, p_a / 2, (Fraction(1) + p_a) / 2, Fraction(1, 5), Fraction(2, 5)],
                            f"{quote}\0{s_pa}",
                        )
                        if not built:
                            continue
                        choices_dict, answer_key = built
                        q_text = (
                            f"Giả sử biến cố {lhs_ev} có xác suất P({lhs_ev}) = {s_pa}. "
                            f"Áp dụng quy tắc biến cố đối trong tài liệu, xác suất P({lhs_ev}') bằng bao nhiêu?"
                        )
                        if PracticeService._normalized(q_text) in excluded:
                            continue
                        expl = (
                            f"Theo tài liệu, P({lhs_ev}') = 1 - P({lhs_ev}). "
                            f"Thay P({lhs_ev}) = {s_pa}: P({lhs_ev}') = 1 - {s_pa} = {s_ans}."
                        )
                        seen_chunks.add(chunk_id)
                        seen_keys.add(("comp", lhs_ev))
                        excluded.add(PracticeService._normalized(q_text))
                        proposals.append({
                            "source_chunk_id": chunk_id,
                            "evidence_quote": quote,
                            "question": q_text,
                            "explanation": expl,
                            "choices": choices_dict,
                            "answer": answer_key,
                        })
                        break
                    if len(proposals) >= count or chunk_id in seen_chunks:
                        break
        if len(proposals) >= count:
            break

    return proposals or None


_VIETNAMESE_CALCULUS_SPECS = (
    (
        "tangent_line",
        re.compile(r"Về hình học,\s*f[′']\(a\)\s*là hệ số góc tiếp tuyến\s*y\s*=\s*f\(a\)\s*\+\s*f[′']\(a\)\(x\s*[−-]\s*a\)\."),
        "Theo công thức tiếp tuyến trong tài liệu, nếu f(2) = 5 và f'(2) = 3 thì giá trị y trên tiếp tuyến tại a = 2 ứng với x = 4 bằng bao nhiêu?",
        "Theo tài liệu, y = f(a) + f′(a)(x − a). Thay a = 2, f(2) = 5, f′(2) = 3 và x = 4: y = 5 + 3×(4 − 2) = 11.",
        ["11", "17", "13", "-1"],
    ),
    (
        "product_rule",
        re.compile(r"Ba quy tắc quyết định:\s*\(uv\)[′']\s*=\s*u[′']v\s*\+\s*uv[′'];\s*\(u/v\)[′']\s*=\s*\(u[′']v\s*[−-]\s*uv[′']\)/v²;\s*\(f∘g\)[′']\s*=\s*\(f[′']∘g\)g[′']"),
        "Theo quy tắc đạo hàm tích trong tài liệu, tại điểm x₀ có u = 2, u' = 5, v = 3 và v' = 4 (u, v khả vi tại x₀), giá trị (uv)'(x₀) bằng bao nhiêu?",
        "Theo tài liệu, (uv)′ = u′v + uv′. Thay u = 2, u′ = 5, v = 3, v′ = 4: (uv)′(x₀) = 5×3 + 2×4 = 23.",
        ["23", "20", "7", "26"],
    ),
    (
        "implicit_derivative",
        re.compile(
            r"Đề\.\s*Cho x²\+xy\+y²=7\.\s*Tìm y[′']\.\s*"
            r"1\.\s*Lấy đạo hàm hai vế theo x:\s*2x\s*\+\s*\(x\s*y[′']\s*\+\s*y\)\s*\+\s*2y\s*y[′']\s*=\s*0\.\s*"
            r"2\.\s*Gom các hạng chứa y[′']:\s*\(x\+2y\)y[′']\s*=\s*[−-]\(2x\+y\)\.\s*"
            r"Kết quả\.\s*y[′']\s*=\s*[−-]\(2x\+y\)/\(x\+2y\),\s*với x\+2y≠0\."
        ),
        "Theo lời giải đạo hàm ẩn của phương trình x² + xy + y² = 7 trong tài liệu, tại điểm (x, y) = (3, -2) (thỏa x + 2y ≠ 0), giá trị y' bằng bao nhiêu?",
        "Theo tài liệu, y′ = −(2x + y)/(x + 2y) với x + 2y ≠ 0. Tại (x, y) = (3, −2) thỏa 3² + 3(−2) + (−2)² = 7 và x + 2y = −1 ≠ 0, ta có y′ = −(2×3 − 2)/(−1) = 4.",
        ["4", "-4", "8", "-8"],
    ),
    (
        "mvt_rule",
        re.compile(r"Nếu f liên tục trên \[a,b\] và khả vi trên \(a,b\),\s*tồn tại c\s*∈\s*\(a,\s*b\)\s*sao cho f[′']\(c\)=\[f\(b\)[−-]f\(a\)\]/\(b[−-]a\)\."),
        "Theo Định lý giá trị trung bình trong tài liệu, cho f liên tục trên [1, 5], khả vi trên (1, 5) với f(1) = 2 và f(5) = 14. Giá trị f'(c) tại điểm c ∈ (1, 5) mà định lý đảm bảo tồn tại bằng bao nhiêu?",
        "Theo Định lý giá trị trung bình trong tài liệu, vì f liên tục trên [1, 5] và khả vi trên (1, 5), tồn tại c ∈ (1, 5) sao cho f′(c) = [f(5) − f(1)]/(5 − 1) = (14 − 2)/(5 − 1) = 3.",
        ["3", "4", "-3", "12"],
    ),
)


def _vietnamese_calculus_rule_proposals(
    passages: list[dict[str, Any]],
    count: int,
    exclude_questions: Optional[list[str]] = None,
    used_rules: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Build deterministic calculus proposals strictly bounded to verbatim doc=44 rule spans."""
    from app.practice import PracticeService

    if count < 1:
        return []
    excluded = {
        PracticeService._normalized(q)
        for q in (exclude_questions or [])
        if isinstance(q, str) and q.strip()
    }
    seen_rules = set(used_rules or ())
    proposals: list[dict[str, Any]] = []
    for passage in passages:
        raw_chunk_id = passage.get("chunk_id")
        if raw_chunk_id is None:
            continue
        try:
            chunk_id = int(raw_chunk_id)
        except (TypeError, ValueError):
            continue
        passage_text = html.unescape(
            str(
                passage.get("safe_text")
                or passage.get("generation_evidence")
                or passage.get("text")
                or passage.get("passage")
                or ""
            )
        )
        if not passage_text or _UNTRUSTED_TEXT_RE.search(passage_text):
            continue
        for rule_id, pattern, question_text, explanation, ordered_answers in _VIETNAMESE_CALCULUS_SPECS:
            if rule_id in seen_rules:
                continue
            if PracticeService._normalized(question_text) in excluded:
                seen_rules.add(rule_id)
                continue
            match = pattern.search(passage_text)
            if not match:
                continue
            quote = match.group(0).strip()
            if _UNTRUSTED_TEXT_RE.search(quote):
                continue
            answers = list(ordered_answers)
            position = len(proposals) % 4
            answers.insert(position, answers.pop(0))
            choices_dict = dict(zip("ABCD", answers))
            choice_items = [{"label": k, "text": v} for k, v in choices_dict.items()]
            if potential_choice_collision(choice_items) or equivalent_choice_pair(choice_items):
                continue
            seen_rules.add(rule_id)
            excluded.add(PracticeService._normalized(question_text))
            proposals.append({
                "source_chunk_id": chunk_id,
                "evidence_quote": quote,
                "question": question_text,
                "explanation": explanation,
                "choices": choices_dict,
                "answer": "ABCD"[position],
            })
            if len(proposals) == count:
                return proposals
    return proposals


def source_derivative_proposals(
    passages,
    count,
    exclude_questions: Optional[list[str]] = None,
):
    if count < 1 or count > 3:
        return None
    from app.practice import PracticeService

    excluded = {
        PracticeService._normalized(q)
        for q in (exclude_questions or [])
        if isinstance(q, str) and q.strip()
    }
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
        question_text = f"Trong ví dụ đã giải, với f(x) = {expression}, đạo hàm f'(x) bằng bao nhiêu?"
        if PracticeService._normalized(question_text) in excluded:
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
        excluded.add(PracticeService._normalized(question_text))
        proposals.append(dict(source_chunk_id=int(passage['chunk_id']), evidence_quote=quote,
                              question=question_text,
                              explanation=explanation, choices=dict(zip('ABCD', answers)), answer='ABCD'[position]))
        if len(proposals) == count:
            return proposals
    if len(proposals) < count:
        proposals.extend(
            _vietnamese_calculus_rule_proposals(
                passages,
                count - len(proposals),
                exclude_questions=list(excluded),
            )
        )
    return proposals if len(proposals) == count else None


_HARD_MULTISTEP_SPECS = (
    # Calculus & Derivatives
    (
        "hard_leibniz_find_n_then_eval_at_one",
        re.compile(
            r"Đề\.\s*Tính y\^\(n\) với y=x²e\^x\.\s*"
            r"1\.\s*Áp dụng Leibniz cho u=x²,\s*v=e\^x\.\s*Chỉ u,\s*u′,\s*u″ khác 0\.\s*"
            r"2\.\s*y\^\(n\)=C_n\^0x²e\^x\+C_n\^1\(2x\)e\^x\+C_n\^2\(2\)e\^x\.\s*"
            r"Kết quả\.\s*y\^\(n\)=e\^x\[x²\+2nx\+n\(n−1\)\]\."
        ),
        (
            "Áp dụng công thức y^(n) = e^x[x² + 2nx + n(n − 1)] trong đoạn trích cho bậc nguyên giả định "
            "n > 1 thỏa y^(n)(0) = 6 (với e^0 = 1). Tìm bậc n ở Bước 1, rồi thay bậc n vừa tìm để tính giá trị "
            "y^(n)(1) tại x = 1 theo e ở Bước 2?"
        ),
        (
            "Bước 1: Thay x = 0 (biết e^0 = 1) và giả thiết y^(n)(0) = 6 của câu hỏi vào công thức "
            "y^(n) = e^x[x² + 2nx + n(n − 1)] trong đoạn trích được n(n − 1) = 6; với n > 1 nguyên suy ra n = 3. "
            "Bước 2: Thay n = 3 và x = 1 vào công thức đó được y^(3)(1) = e^1[1² + 2×3×1 + 3×(3 − 1)] = 13e."
        ),
        [
            "n = 3 và y^(3)(1) = 13e",
            "n = 3 và y^(3)(1) = 7e",
            "n = 2 và y^(2)(1) = 7e",
            "n = 4 và y^(4)(1) = 21e",
        ],
    ),
    (
        "hard_power_series_dalembert_and_endpoints_2step",
        re.compile(
            r"1\.\s*Áp dụng tiêu chuẩn d'Alembert cho trị tuyệt đối được \|x−1\|³/27<\s*1 ⇒ \|x - 1\| < 3\.\s*Tâm a=1,\s*R=3\.\s*"
            r"2\.\s*Đầu phải x=4:\s*số hạng thành 2/n ⇒ phân kỳ\.\s*"
            r"3\.\s*Đầu trái x=−2:\s*số hạng thành 2\(-1\)ⁿ/n ⇒ hội tụ theo Leibniz\.\s*"
            r"Kết quả\.\s*Miền hội tụ \[−2,4\)\."
        ),
        (
            "Trong bài toán tìm miền hội tụ ở đoạn trích, từ bất đẳng thức tiêu chuẩn d'Alembert |x − 1|³/27 < 1 "
            "ở Bước 1 xác định tâm a, bán kính R và hai đầu mút x = a − R, x = a + R; sau đó ở Bước 2 xét tính "
            "hội tụ/phân kỳ tại từng đầu mút (2/n và 2(−1)ⁿ/n theo Leibniz) để kết luận miền hội tụ. Kết quả hai bước nào đúng?"
        ),
        (
            "Bước 1: Từ tiêu chuẩn d'Alembert |x − 1|³/27 < 1 suy ra |x − 1| < 3 nên tâm a = 1, bán kính R = 3 "
            "và hai đầu mút là x = −2 và x = 4. "
            "Bước 2: Tại đầu phải x = 4 số hạng là 2/n nên phân kỳ, còn tại đầu trái x = −2 số hạng là 2(−1)ⁿ/n hội tụ "
            "theo Leibniz, suy ra miền hội tụ [−2, 4)."
        ),
        [
            "Tâm a = 1, R = 3; đầu phải x = 4 (2/n) phân kỳ và đầu trái x = −2 hội tụ theo Leibniz nên miền hội tụ là [−2, 4)",
            "Tâm a = 1, R = 3; đầu phải x = 4 hội tụ theo Leibniz và đầu trái x = −2 phân kỳ nên miền hội tụ là (−2, 4]",
            "Tâm a = 1, R = 27; cả hai đầu mút x = −2 và x = 4 đều hội tụ theo Leibniz nên miền hội tụ là [−2, 4]",
            "Tâm a = −1, R = 3; cả hai đầu mút x = −2 và x = 4 đều phân kỳ nên miền hội tụ là (−2, 4)",
        ],
    ),
    (
        "hard_vcb_product_quotient_vs_sum_diff_2step",
        re.compile(
            r"Trong tích hoặc\s+thương,\s*có thể thay một VCB bằng VCB tương đương;\s*"
            r"trong tổng/hiệu không được thay máy móc vì có thể làm mất\s+triệt tiêu bậc thấp\.\s*"
            r"Khi u→0\s+Tương đương cần nhớ\s+Bậc tiếp theo hay dùng\s+sin u;\s*tan u\s+sin u~u;\s*tan u~u"
        ),
        (
            "Khi u → 0, xét hai biểu thức giả định: thương P(u) = (6·sin u·tan u) / u² ở Bước 1 và hiệu D(u) = sin u − tan u ở Bước 2. "
            "Kết hợp quy tắc thay vô cùng bé tương đương trong tích/thương, quy tắc trong tổng/hiệu và bảng tương đương "
            "(sin u ~ u, tan u ~ u) của đoạn trích, kết luận nào đúng?"
        ),
        (
            "Bước 1: Với thương chứa tích P(u) = (6·sin u·tan u)/u², đoạn trích cho phép thay sin u ~ u và tan u ~ u "
            "nên P(u) tương đương (6·u·u)/u² = 6 (giới hạn bằng 6). "
            "Bước 2: Với hiệu D(u) = sin u − tan u, quy tắc trong tổng/hiệu quy định không được thay máy móc "
            "sin u ~ u và tan u ~ u vì có thể làm mất triệt tiêu bậc thấp."
        ),
        [
            "Ở P(u) thay được sin u ~ u, tan u ~ u để rút gọn (6u²)/u² = 6; ở D(u) là hiệu nên không được thay máy móc vì có thể làm mất triệt tiêu bậc thấp",
            "Ở cả P(u) và D(u) đều thay máy móc được sin u ~ u, tan u ~ u để kết luận P(u) → 6 và D(u) = 0",
            "Ở P(u) không được thay tương đương vì có phép chia cho u², chỉ được thay tương đương trong hiệu D(u)",
            "Ở P(u) thay sin u ~ u, tan u ~ u được giới hạn bằng 1; ở D(u) thay tương đương được kết quả bằng 2u",
        ],
    ),
    # Linear Algebra
    (
        "hard_row_ops_and_scalar_det",
        re.compile(
            r"Định lý\.\s*Cho A,A′ ∈ M_n\(ℝ\)\.\s*Khi đó\s*"
            r"\(i\) Nếu A −\[d_i↔d_j;i≠j\]→ A′ thì \|A′\|=−\|A\|;\s*"
            r"\(ii\) Nếu A −\[αd_i\]→ A′ thì \|A′\|=α\|A\| hay \|A\|=\(1/α\)\|A′\|;\s*"
            r"\(iii\) Nếu A −\[d_i\+βd_j;i≠j\]→ A′ thì \|A′\|=\|A\|\.\s*"
            r"Hệ quả\.\s*Cho A ∈ M_n\(ℝ\)\.\s*Khi đó,\s*với mọi α ∈ ℝ,\s*ta có \|αA\|=α\^n\|A\|\."
        ),
        (
            "Cho ma trận vuông A ∈ M₃(ℝ) (cấp n = 3) có |A| = 2. Thực hiện phép đổi hai dòng khác nhau "
            "d₁ ↔ d₂ trên A để được A′, rồi tính định thức |2A′| (với α = 2). Kết quả |2A′| bằng bao nhiêu?"
        ),
        (
            "Bước 1: Theo mục (i), đổi hai dòng khác nhau cho |A′| = −|A| = −2. "
            "Bước 2: Theo Hệ quả |αA| = α^n|A| với n = 3 và α = 2, ta có |2A′| = 2³×|A′| = 8×(−2) = −16."
        ),
        ["-16", "-4", "16", "-8"],
    ),
    (
        "hard_invertible_matrix_2x2_corollary_2step",
        re.compile(
            r"Hệ quả\.\s*Ma trận A=\[\[a,b\],\[c,d\]\] khả nghịch khi và chỉ khi ad−bc≠0\.\s*"
            r"Khi đó A\^\(−1\)=\(1/\(ad−bc\)\)\[\[d,−b\],\[−c,a\]\]\."
        ),
        (
            "Cho ma trận vuông giả định A = [[3, 1], [4, 2]]. Áp dụng Hệ quả trong đoạn trích qua hai bước "
            "kiểm tra điều kiện ad − bc ≠ 0 rồi lập ma trận nghịch đảo, A^(−1) bằng biểu thức nào?"
        ),
        (
            "Bước 1: Với ma trận giả định A = [[3, 1], [4, 2]] của câu hỏi (a = 3, b = 1, c = 4, d = 2), "
            "tính ad − bc = 3×2 − 1×4 = 2 ≠ 0 nên theo Hệ quả, A khả nghịch. "
            "Bước 2: Thay vào A^(−1) = (1/(ad−bc))[[d,−b],[−c,a]] được A^(−1) = (1/2)[[2, −1], [−4, 3]]."
        ),
        [
            "(1/2)[[2, −1], [−4, 3]]",
            "(1/2)[[3, −1], [−4, 2]]",
            "(1/2)[[2, 1], [4, 3]]",
            "Ma trận A không khả nghịch vì ad − bc = 0",
        ],
    ),
    (
        "hard_invertible_adjugate_matrix_2step",
        re.compile(
            r"Định lý\.\s*Ma trận vuông A khả nghịch khi và chỉ khi \|A\|≠0\.\s*"
            r"Hơn nữa,\s*A\^\(−1\)=\(1/\|A\|\)adj\(A\)\."
        ),
        (
            "Cho ma trận vuông giả định A cấp 2 có |A| = 5 và adj(A) = [[10, −15], [5, 20]]. "
            "Áp dụng Định lý trong đoạn trích, kết luận về tính khả nghịch và ma trận A^(−1) là gì?"
        ),
        (
            "Bước 1: Vì giả thiết câu hỏi cho |A| = 5 ≠ 0 nên theo Định lý, ma trận vuông A khả nghịch. "
            "Bước 2: Thay |A| = 5 và adj(A) giả định vào công thức A^(−1) = (1/|A|)adj(A) nhân mỗi phần tử với 1/5 được [[2, −3], [1, 4]]."
        ),
        [
            "A khả nghịch và A^(−1) = [[2, −3], [1, 4]]",
            "A khả nghịch và A^(−1) = [[50, −75], [25, 100]]",
            "A khả nghịch và A^(−1) = [[10, −15], [5, 20]]",
            "A không khả nghịch vì |A| = 5 ≠ 0",
        ],
    ),
    # Logic & Discrete Math
    (
        "hard_recurrence_homog_distinct_roots_find_c2_then_x2",
        re.compile(
            r"• Với k=2,\s*phương trình đặc trưng trở thành a₀λ²\+a₁λ\+a₂=0\.\s*"
            r"• Nếu phương trình đặc trưng có hai nghiệm thực phân biệt λ₁,λ₂ thì hệ thức đệ quy có nghiệm tổng quát là xₙ=C₁·λ₁ⁿ\+C₂·λ₂ⁿ\."
        ),
        (
            "Cho hệ thức đệ quy bậc k = 2 có các hệ số giả định a₀ = 1, a₁ = −5, a₂ = 6 (phương trình đặc trưng "
            "λ² − 5λ + 6 = 0), biết C₁ = 1 và điều kiện ban đầu x₀ = 3 tại n = 0 (với 2⁰ = 3⁰ = 1). "
            "Tìm hằng số C₂ ở Bước 1 rồi tính giá trị x₂ tại n = 2 ở Bước 2?"
        ),
        (
            "Bước 1: Từ hai hệ số giả định cho λ² − 5λ + 6 = (λ − 2)(λ − 3) = 0 (λ₁ = 2, λ₂ = 3), công thức "
            "xₙ = C₁·λ₁ⁿ + C₂·λ₂ⁿ trong đoạn trích kết hợp dữ kiện câu hỏi x₀ = 3, C₁ = 1 cho 1×1 + C₂×1 = 3 ⇒ C₂ = 2. "
            "Bước 2: Thay C₁ = 1, C₂ = 2 (vừa tìm ở Bước 1) và n = 2 được x₂ = 1×2² + 2×3² = 4 + 18 = 22."
        ),
        ["C₂ = 2 và x₂ = 22", "C₂ = 3 và x₂ = 31", "C₂ = 2 và x₂ = 17", "C₂ = 1 và x₂ = 13"],
    ),
    (
        "hard_nonhomog_double_root_find_B_then_x2",
        re.compile(
            r"Phương trình đặc trưng của \(2\) là λ²−6λ\+9=0\.\s*\(\*\)\s*"
            r"Phương trình \(\*\) có nghiệm kép là λ₀=3\.\s*"
            r"• Nếu fₙ=3ⁿ thì \(1\) có nghiệm riêng dạng xₙ\^\(P\)=n²3ⁿA\.\s*"
            r"• Nếu fₙ=3ⁿ\(2n\+1\) thì xₙ\^\(P\)=n²3ⁿ\(An\+B\)\."
        ),
        (
            "Trong ví dụ về phương trình (*) có nghiệm kép λ₀ = 3 với vế phải giả định fₙ = 3ⁿ(2n + 1), "
            "biết hằng số giả định A = 2 và giá trị tại n = 1 là x₁^(P) = 15. "
            "Áp dụng dạng nghiệm riêng xₙ^(P) = n²3ⁿ(An + B) trong đoạn trích để giải tìm B ở Bước 1, rồi tính x₂^(P) tại n = 2 ở Bước 2?"
        ),
        (
            "Bước 1: Với fₙ = 3ⁿ(2n + 1), đoạn trích cho xₙ^(P) = n²3ⁿ(An + B); thay các dữ kiện giả định của câu hỏi "
            "n = 1, A = 2, x₁^(P) = 15 được 1²×3¹×(2 + B) = 15 ⇒ 2 + B = 5 ⇒ B = 3. "
            "Bước 2: Thay A = 2, B = 3 (vừa tìm ở Bước 1) và n = 2 được x₂^(P) = 2²×3²×(2×2 + 3) = 4×9×7 = 252."
        ),
        [
            "B = 3 và x₂^(P) = 252",
            "B = 3 và x₂^(P) = 126",
            "B = 5 và x₂^(P) = 324",
            "B = 2 và x₂^(P) = 216",
        ],
    ),
    (
        "hard_congruence_sym_trans_remainder_2step",
        re.compile(
            r"Cho n là số nguyên dương\.\s*Hai số nguyên a và b được gọi là đồng dư với nhau theo modulo n,\s*"
            r"nếu a và b chia n có cùng số dư\.\s*Ký hiệu a≡b \(mod n\)\.\s*Tính chất\s*"
            r"i\. Với mọi số nguyên a,\s*ta có a≡a \(mod n\);\s*"
            r"ii\. Nếu a≡b \(mod n\) thì b≡a \(mod n\);\s*"
            r"iii\. Nếu a≡b \(mod n\) và b≡c \(mod n\),\s*thì a≡c \(mod n\)\."
        ),
        (
            "Cho modulo giả định n = 5 và các số nguyên a = 17, b, c sao cho a và b chia 5 có cùng số dư, "
            "đồng thời c ≡ b (mod 5). Dùng các tính chất đồng dư trong đoạn trích để suy ra quan hệ đồng dư "
            "giữa a và c ở Bước 1, rồi xác định số dư khi chia c cho 5 ở Bước 2?"
        ),
        (
            "Bước 1: Từ định nghĩa có a ≡ b (mod 5); kết hợp c ≡ b (mod 5) qua tính chất (ii) được b ≡ c (mod 5), "
            "rồi qua tính chất (iii) suy ra a ≡ c (mod 5). "
            "Bước 2: Từ a ≡ c (mod 5) (tìm được ở Bước 1) và định nghĩa đồng dư trong đoạn trích, c chia 5 có cùng số dư với "
            "a = 17 (của câu hỏi), tức là số dư bằng 2."
        ),
        [
            "Suy ra a ≡ c (mod 5) theo (ii)–(iii) và c chia 5 dư 2",
            "Suy ra a ≡ c (mod 5) theo (ii)–(iii) và c chia 5 dư 3",
            "Chỉ áp dụng tính chất (i) để kết luận c chia 5 dư 0",
            "Suy ra b ≡ c (mod 5) theo (ii) và c chia 5 dư 4",
        ],
    ),
    # Mechanics
    (
        "hard_viscous_drag_2step",
        re.compile(
            r"Lực ma sát ướt \(nhớt\):\s*"
            r"• Xuất hiện khi vật rắn chuyển động trong chất lỏng hoặc chất khí\.\s*"
            r"• Ngược chiều chiều chuyển động\.\s*"
            r"• Tỉ lệ với tiết diện cản và tỉ lệ bậc nhất với vận tốc \(hoặc bậc 2,\s*nếu vận tốc lớn\)\.\s*"
            r"F_c = kSv;\s*F_c = kSv²\."
        ),
        (
            "Một vật chuyển động trong chất lỏng với vận tốc lớn (tuân theo công thức bậc hai), hệ số cản k và tiết diện S giữ nguyên. "
            "Nếu vận tốc vật tăng gấp 3 lần theo cùng chiều chuyển động, hướng và độ lớn lực ma sát nhớt F_c thay đổi ra sao?"
        ),
        (
            "Bước 1: Vì vật chuyển động với vận tốc lớn nên đoạn trích áp dụng quy tắc bậc hai F_c = kSv² (không dùng bậc nhất F_c = kSv), và lực luôn ngược chiều chuyển động. "
            "Bước 2: Giữ nguyên k, S và thay v′ = 3v vào F_c = kSv² thì độ lớn tăng 3² = 9 lần và vẫn ngược chiều chuyển động."
        ),
        [
            "Vẫn ngược chiều chuyển động và có độ lớn tăng gấp 9 lần",
            "Vẫn ngược chiều chuyển động và có độ lớn tăng gấp 3 lần",
            "Hướng cùng chiều chuyển động và có độ lớn tăng gấp 9 lần",
            "Không đổi độ lớn vì lực ma sát nhớt chỉ phụ thuộc tiết diện S",
        ],
    ),
    (
        "hard_centripetal_force_find_m_then_new_force",
        re.compile(
            r"□ Lực trong chuyển động cong\s+vector a = vector a_t \+ vector a_n\s+"
            r"⇔ m·vector a = m·vector a_t \+ m·vector a_n\s+⇔ vector F = vector F_t \+ vector F_n\s+"
            r"vector F_t:\s*Lực tiếp tuyến;\s*vector F_n:\s*Lực pháp tuyến\.\s+F_n = F_ht = ma_ht = mv²/r\."
        ),
        (
            "Áp dụng công thức lực pháp tuyến (lực hướng tâm) F_n = mv²/r trong chuyển động cong từ đoạn trích "
            "(bỏ qua đơn vị) cho vật có r = 4, v = 2 chịu lực F_n = 3 (giả định). Tìm khối lượng m của vật ở Bước 1, "
            "rồi dùng khối lượng m vừa tìm để tính lực pháp tuyến F_n′ khi vật chuyển động với v′ = 6 trên quỹ đạo r′ = 2 ở Bước 2?"
        ),
        (
            "Bước 1: Từ công thức F_n = mv²/r trong đoạn trích và dữ kiện giả định của câu hỏi (F_n = 3, r = 4, v = 2), "
            "giải ra m = F_n·r/v² = 3×4/2² = 3. "
            "Bước 2: Dùng m = 3 vừa tìm ở Bước 1 với giá trị giả định v′ = 6, r′ = 2 được F_n′ = 3×6²/2 = 54."
        ),
        ["m = 3 và F_n′ = 54", "m = 3 và F_n′ = 18", "m = 6 và F_n′ = 108", "m = 2 và F_n′ = 36"],
    ),
    (
        "hard_hooke_states_find_fspr_then_state_d",
        re.compile(
            r"vector F_đh = −k·vector Δℓ = −k·vector x\.\s*"
            r"Hình bốn trạng thái lò xo gắn tường trái trên mặt ngang,\s*trục x sang phải,\s*đường x=0 nét đứt đứng:\s*"
            r"a\) chưa biến dạng;\s*b\) kéo dãn x sang phải,\s*F_spr sang trái;\s*"
            r"c\) kéo dãn 2x sang phải,\s*2F_spr sang trái;\s*d\) nén \(−x\) sang trái,\s*F_spr sang phải\."
        ),
        (
            "Quan sát bốn trạng thái lò xo trong đoạn trích: giả sử ở trạng thái c) kéo dãn 2x sang phải, "
            "lực đàn hồi đo được có độ lớn 2F_spr = 30 N. Tìm độ lớn F_spr (ở trạng thái b) ở Bước 1, "
            "rồi dùng giá trị đó xác định độ lớn và chiều lực đàn hồi ở trạng thái d) nén (−x) sang trái ở Bước 2?"
        ),
        (
            "Bước 1: Theo đoạn trích, trạng thái c) có độ lớn 2F_spr; với giả thiết câu hỏi 2F_spr = 30 N suy ra F_spr = 15 N "
            "(độ lớn ở trạng thái b). "
            "Bước 2: Thay F_spr = 15 N vừa tìm vào trạng thái d) nén (−x) sang trái trong đoạn trích được lực có độ lớn 15 N hướng sang phải."
        ),
        [
            "F_spr = 15 N và ở trạng thái d) lực có độ lớn 15 N hướng sang phải",
            "F_spr = 15 N và ở trạng thái d) lực có độ lớn 15 N hướng sang trái",
            "F_spr = 30 N và ở trạng thái d) lực có độ lớn 30 N hướng sang phải",
            "F_spr = 60 N và ở trạng thái d) lực có độ lớn 60 N hướng sang trái",
        ],
    ),
    # Data Structures & Algorithms
    (
        "hard_stack_evaluate_two_parens_2step",
        re.compile(
            r'while \(!in\.eof\(\)\) \{\s+in >> tok;\s+if \(tok == "\("\);\s+'
            r'else if \(tok == "\+" \|\| tok == "\*"\) ops\.push\(tok\);\s+'
            r'else if \(tok == "\)"\) \{\s+string op = ops\.top\(\); ops\.pop\(\);\s+'
            r'double val2 = vals\.top\(\); vals\.pop\(\);\s+double val1 = vals\.top\(\); vals\.pop\(\);\s+'
            r'if \(op == "\+"\) vals\.push\(val1 \+ val2\);\s+else if \(op == "\*"\) vals\.push\(val1 \* val2\);\s+\}\s+'
            r'else vals\.push\(stod\(tok\)\);\s+\}'
        ),
        (
            "Theo hàm evaluate ở đoạn trích, giả sử trước các token còn lại, ngăn xếp ops đang chứa đáy là [\"*\"] "
            "và vals đang chứa [4]. Sau đó luồng in đọc lần lượt năm token giả định: \"2\", \"+\", \"3\", \")\", \")\". "
            "Giá trị được đẩy vào vals ở dấu \")\" thứ nhất (Bước 1) và kết quả trên đỉnh vals sau dấu \")\" thứ hai (Bước 2) là gì?"
        ),
        (
            "Bước 1: Đọc \"2\", \"+\", \"3\" làm ops thành [\"*\", \"+\"] và vals thành [4, 2, 3]; gặp dấu \")\" thứ nhất, "
            "lấy op = \"+\", val2 = 3, val1 = 2 rồi đẩy val1 + val2 = 5 vào vals (vals thành [4, 5]). "
            "Bước 2: Gặp dấu \")\" thứ hai, lấy op = \"*\", val2 = 5 (từ Bước 1), val1 = 4 rồi đẩy val1 * val2 = 20 vào vals."
        ),
        [
            "Dấu ')' thứ nhất đẩy 5; dấu ')' thứ hai cho đỉnh vals bằng 20",
            "Dấu ')' thứ nhất đẩy 6; dấu ')' thứ hai cho đỉnh vals bằng 24",
            "Dấu ')' thứ nhất đẩy 8; dấu ')' thứ hai cho đỉnh vals bằng 11",
            "Dấu ')' thứ nhất đẩy 5; dấu ')' thứ hai cho đỉnh vals bằng 9",
        ],
    ),
    (
        "hard_ordered_list_empty_then_end_2step",
        re.compile(
            r"(?i)Create a new node\.\s*Store data in the new node\.\s*"
            r"if there are no nodes in the list\s+Make the new node the first node\.\s*"
            r"else\s+Find the first node whose value is greater than or equal to the new\s+value,\s*"
            r"or the end of the list \(whichever is first\)\.\s*"
            r"Insert the new node before the found node,\s*or at the end of the list\s+if no such node was found\.\s*end if\."
        ),
        (
            "Bắt đầu từ một danh sách liên kết rỗng và thực hiện tuần tự thuật toán trong đoạn trích cho "
            "hai lần chèn giả định liên tiếp: lần 1 chèn giá trị 25, lần 2 chèn giá trị 40. "
            "Diễn biến nhánh thực thi và danh sách thu được sau hai bước là gì?"
        ),
        (
            "Bước 1: Với danh sách rỗng ban đầu và giá trị giả định 25 của câu hỏi, lần 1 vào nhánh if chưa có nút "
            "nên đặt 25 làm nút đầu tiên. "
            "Bước 2: Lần 2 chèn giá trị giả định 40 vào nhánh else, duyệt qua 25 không gặp nút nào >= 40 nên "
            "chạm cuối danh sách và chèn 40 ở cuối, thu được 25, 40."
        ),
        [
            "Lần 1 vào nhánh if đặt 25 làm nút đầu; lần 2 vào nhánh else không thấy nút >= 40 nên chèn 40 ở cuối, được 25, 40",
            "Lần 1 vào nhánh if đặt 25 làm nút đầu; lần 2 vào nhánh else chèn 40 vào trước nút 25, được 40, 25",
            "Cả hai lần đều vào nhánh if làm nút đầu nên danh sách cuối chỉ còn nút 40",
            "Lần 1 vào nhánh else đặt 25 ở cuối; lần 2 dừng vì không có nút >= 40 nên bỏ qua nút 40",
        ],
    ),
    (
        "hard_ordered_list_empty_end_middle_3cases",
        re.compile(
            r"Inserting a Node in a Linked List\s+"
            r"Create a new node\.\s*Store data in the new node\.\s*"
            r"If there are no nodes in the list\s+Make the new node the first node\.\s*"
            r"Else\s+Find the first node whose value is greater than or equal to the new value,\s*"
            r"or the end of the list \(whichever is first\)\.\s*"
            r"Insert the new node before the found node,\s*or at the end of the list if no such node was found\.\s*End If\."
        ),
        (
            "Xuất phát từ danh sách liên kết rỗng và gọi thuật toán chèn nút trong đoạn trích lần lượt cho ba giá trị "
            "20 (bước rỗng), rồi 40, rồi 30. Nhánh If/Else tương ứng ở ba lần chèn và thứ tự nút cuối cùng là gì?"
        ),
        (
            "Bước 1: Danh sách rỗng nên khi chèn 20 thuật toán vào nhánh If đặt 20 làm nút đầu ([20]). "
            "Bước 2: Khi chèn 40 rồi 30 đều vào nhánh Else: chèn 40 không gặp nút >= 40 nên đặt ở cuối ([20, 40]); "
            "chèn 30 gặp nút đầu tiên >= 30 là 40 nên chèn trước 40, tạo danh sách [20, 30, 40]."
        ),
        [
            "Chèn 20 theo If làm nút đầu; chèn 40 theo Else ở cuối vì không có nút >= 40; chèn 30 theo Else trước nút 40 được [20, 30, 40]",
            "Cả ba lần đều vào nhánh Else và chèn vào cuối danh sách tạo thành [20, 40, 30]",
            "Chèn 20 theo If làm nút đầu; chèn 40 theo Else trước 20; chèn 30 theo Else trước 40 được [30, 40, 20]",
            "Chèn 20 theo If làm nút đầu; chèn 40 và 30 đều theo nhánh If ghi đè nút đầu nên chỉ còn [30]",
        ],
    ),
    # Object-Oriented Programming
    (
        "hard_virtual_dtor_2step",
        re.compile(
            r"p có kiểu khai báo NhanVien\* nhưng object thật là NhanVienVanPhong\.\s*"
            r"Destructor ảo cho phép chuỗi\s+hủy chạy đúng từ class con rồi về class cha\.\s*"
            r"Nếu base destructor không virtual,\s*phần tài nguyên riêng của\s+object con có thể không được dọn đúng\."
        ),
        (
            "Khi con trỏ p khai báo kiểu NhanVien* đang trỏ tới đối tượng thật là NhanVienVanPhong và gọi delete p, "
            "thứ tự gọi destructor và hệ quả dọn tài nguyên khác nhau thế nào giữa trường hợp có và không có virtual ở base destructor?"
        ),
        (
            "Bước 1: Dù p khai báo là NhanVien*, đối tượng thật là NhanVienVanPhong nên hành vi hủy phụ thuộc vào base destructor có virtual hay không. "
            "Bước 2: Nếu virtual, chuỗi hủy chạy từ lớp con NhanVienVanPhong rồi về lớp cha NhanVien; nếu không virtual, phần tài nguyên riêng của object con có thể không được dọn đúng."
        ),
        [
            "Có virtual thì hủy từ NhanVienVanPhong rồi về NhanVien; không virtual thì tài nguyên riêng của object con có thể không được dọn đúng",
            "Có hay không có virtual thì chuỗi hủy đều chỉ chạy destructor của lớp cha NhanVien vì p có kiểu khai báo NhanVien*",
            "Không virtual sẽ hủy từ NhanVienVanPhong trước rồi mới gọi tới lớp cha nhưng tốn thêm bộ nhớ",
            "Có virtual chỉ hủy phần lớp cha NhanVien và bỏ qua hoàn toàn phần tài nguyên của NhanVienVanPhong",
        ],
    ),
    (
        "hard_folder_them_and_tinh_dung_luong_2step",
        re.compile(
            r"void Folder::Them\(ThanhPhan\* p\) \{\s+if \(p != nullptr\)\s+dsCon\.push_back\(p\);\s+\}\s+"
            r"int Folder::TinhDungLuong\(\) \{\s+int tong = 0;\s+int n = dsCon\.size\(\);\s+"
            r"for \(int i = 0; i < n; i\+\+\)\s+tong = tong \+ dsCon\[i\]->TinhDungLuong\(\);\s+return tong;\s+\}"
        ),
        (
            "Trên một đối tượng Folder ban đầu có dsCon rỗng ở đoạn trích, gọi liên tiếp ba lệnh giả định "
            "Them(p1), Them(nullptr), Them(p2) với p1 != nullptr có p1->TinhDungLuong() = 12 và p2 != nullptr có p2->TinhDungLuong() = 18. "
            "Xác định kích thước n = dsCon.size() sau khi gọi Them ở Bước 1, rồi tính kết quả Folder::TinhDungLuong() ở Bước 2?"
        ),
        (
            "Bước 1: Trong Folder::Them, điều kiện if (p != nullptr) chỉ đẩy p1 và p2 vào dsCon và bỏ qua nullptr, "
            "nên n = dsCon.size() = 2. "
            "Bước 2: Với n = 2 tìm ở Bước 1, vòng lặp Folder::TinhDungLuong cộng dồn tong = 0 + 12 + 18 = 30."
        ),
        [
            "n = 2 và TinhDungLuong() trả về 30",
            "n = 3 và TinhDungLuong() trả về 30",
            "n = 2 và TinhDungLuong() trả về 18",
            "n = 1 và TinhDungLuong() trả về 12",
        ],
    ),
    (
        "hard_copy_assign_zero_vs_loop_sum_2step",
        re.compile(
            r"if \(kichThuoc == 0\) \{\s+duLieu = nullptr;\s+return \*this;\s+\}\s+"
            r"duLieu = new int\[kichThuoc\];\s+for \(int i = 0; i < kichThuoc; i\+\+\) \{\s+"
            r"duLieu\[i\] = other\.duLieu\[i\];\s+\}\s+return \*this;"
        ),
        (
            "Theo đoạn mã sao chép mảng trong đoạn trích, xét hai trường hợp giả định: ở Bước 1 với kichThuoc = 0 "
            "thì con trỏ duLieu nhận giá trị gì trước khi return *this; ở Bước 2 với kichThuoc = 3 và mảng nguồn "
            "other.duLieu gồm các phần tử [5, 8, 11] (chỉ số 0, 1, 2) thì sau vòng lặp for tổng duLieu[1] + duLieu[2] bằng bao nhiêu?"
        ),
        (
            "Bước 1: Khi kichThuoc == 0, nhánh if gán duLieu = nullptr và thực hiện return *this ngay mà không chạy cấp phát new hay vòng lặp. "
            "Bước 2: Khi kichThuoc = 3 (khác 0), chương trình cấp phát new int[3] và vòng lặp for (i = 0..2) chép "
            "duLieu[1] = 8, duLieu[2] = 11 từ other.duLieu của câu hỏi, suy ra duLieu[1] + duLieu[2] = 8 + 11 = 19."
        ),
        [
            "Bước 1 gán duLieu = nullptr; Bước 2 cho duLieu[1] + duLieu[2] = 19",
            "Bước 1 cấp phát new int[0]; Bước 2 cho duLieu[1] + duLieu[2] = 13",
            "Bước 1 gán duLieu = nullptr; Bước 2 cho duLieu[1] + duLieu[2] = 16",
            "Bước 1 giữ nguyên duLieu cũ; Bước 2 chỉ chép duLieu[0] = 5 nên tổng bằng 5",
        ],
    ),
)


_HARD_UNTRUSTED_PASSAGE_RE = re.compile(
    r"(?:<\|[^<>\n]{0,40}\|>|\bbỏ\s*qua\b|\bignore\b|\binjection\b|\bbí\s*mật\b|\bsecret\b|\boverride\b|\bthay\s+tất\s+cả\b|(?<![.,\d])999(?!\d))",
    re.IGNORECASE,
)


def source_hard_multistep_proposals(
    passages: list[dict[str, Any]],
    count: int,
    exclude_questions: Optional[list[str]] = None,
    exclude_quotes: Optional[set[str]] = None,
) -> Optional[list[dict[str, Any]]]:
    """Propose source-bound 2-step reasoning candidates for independent hard-mode verification."""
    from app.practice import PracticeService

    if count < 1:
        return None
    excluded = {
        PracticeService._normalized(q)
        for q in (exclude_questions or [])
        if isinstance(q, str) and q.strip()
    }
    excluded_quotes = {
        str(q).strip().casefold()
        for q in (exclude_quotes or ())
        if isinstance(q, str) and q.strip()
    }
    eligible_passages: list[tuple[int, str]] = []
    for passage in passages:
        raw_chunk_id = passage.get("chunk_id")
        if raw_chunk_id is None:
            continue
        try:
            chunk_id = int(raw_chunk_id)
        except (TypeError, ValueError):
            continue
        passage_text = html.unescape(
            str(
                passage.get("safe_text")
                or passage.get("generation_evidence")
                or passage.get("text")
                or passage.get("passage")
                or ""
            )
        )
        if not passage_text or _HARD_UNTRUSTED_PASSAGE_RE.search(passage_text):
            continue
        eligible_passages.append((chunk_id, passage_text))

    proposals: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    seen_chunks: set[int] = set()
    for allow_same_chunk in (False, True):
        for rule_id, pattern, question_text, explanation, ordered_answers in _HARD_MULTISTEP_SPECS:
            if rule_id in seen_rules:
                continue
            if PracticeService._normalized(question_text) in excluded:
                seen_rules.add(rule_id)
                continue
            for chunk_id, passage_text in eligible_passages:
                if not allow_same_chunk and chunk_id in seen_chunks:
                    continue
                match = pattern.search(passage_text)
                if not match:
                    continue
                quote = match.group(0).strip()
                if not (20 <= len(quote) <= 420) or quote.casefold() in excluded_quotes:
                    continue
                if _HARD_UNTRUSTED_PASSAGE_RE.search(quote):
                    continue
                if not PracticeService.extract_supporting_evidence(passage_text, quote):
                    continue
                answers = list(ordered_answers)
                position = hashlib.sha256(f"{rule_id}\0{quote}".encode("utf-8")).digest()[0] % 4
                answers.insert(position, answers.pop(0))
                choices_dict = dict(zip("ABCD", answers))
                choice_items = [{"label": k, "text": v} for k, v in choices_dict.items()]
                if potential_choice_collision(choice_items) or equivalent_choice_pair(choice_items):
                    continue
                seen_rules.add(rule_id)
                seen_chunks.add(chunk_id)
                excluded.add(PracticeService._normalized(question_text))
                proposals.append({
                    "source_chunk_id": chunk_id,
                    "evidence_quote": quote,
                    "question": question_text,
                    "explanation": explanation,
                    "choices": choices_dict,
                    "answer": "ABCD"[position],
                })
                if len(proposals) == count:
                    return proposals
                break
    return proposals or None
