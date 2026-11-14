"""Bounded exact equivalence checks for elementary polynomial quiz choices."""

import ast
from fractions import Fraction
import re


def _polynomial(text):
    if not isinstance(text, str) or len(text) > 200:
        return None
    text = text.translate(str.maketrans({'−': '-', '–': '-', '×': '*', '·': '*'}))
    superscripts = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')
    text = re.sub(r'([a-zA-Z0-9)])([⁰¹²³⁴⁵⁶⁷⁸⁹]+)',
                  lambda m: m[1] + '**' + m[2].translate(superscripts), text)
    text = re.sub(r'\s+', '', text).replace('^', '**')
    if not text or re.search(r'[^a-zA-Z0-9.+*/()\-]', text):
        return None
    if re.search(r'[a-zA-Z]{2}|\d[eE][+-]?\d', text):
        return None
    text = re.sub(r'(?<=[0-9)])(?=[a-zA-Z(])|(?<=[a-zA-Z])(?=\()', '*', text)
    try:
        root = ast.parse(text, mode='eval')
        if sum(1 for _ in ast.walk(root)) > 100:
            return None

        def bounded(value):
            result = {key: coefficient for key, coefficient in value.items() if coefficient}
            if (len(result) > 100 or any(sum(power for _, power in key) > 12
                    or coefficient.numerator.bit_length() > 256
                    or coefficient.denominator.bit_length() > 256
                    for key, coefficient in result.items())):
                raise ValueError('Polynomial limit')
            return result

        def add(left, right, sign=1):
            result = dict(left)
            for key, coefficient in right.items():
                result[key] = result.get(key, Fraction(0)) + sign * coefficient
            return bounded(result)

        def multiply(left, right):
            result = {}
            for lhs, a in left.items():
                for rhs, b in right.items():
                    powers = dict(lhs)
                    for variable, power in rhs:
                        powers[variable] = powers.get(variable, 0) + power
                    key = tuple(sorted(powers.items()))
                    result[key] = result.get(key, Fraction(0)) + a * b
            return bounded(result)

        def visit(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                literal = ast.get_source_segment(text, node)
                if not re.fullmatch(r'[0-9]+(?:\.[0-9]*)?|\.[0-9]+', literal):
                    raise ValueError('Numeric literal')
                return bounded({(): Fraction(literal)})
            if isinstance(node, ast.Name) and node.id in ('x', 'y', 'z'):
                return {((node.id, 1),): Fraction(1)}
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return multiply({(): Fraction(-1 if isinstance(node.op, ast.USub) else 1)}, visit(node.operand))
            if not isinstance(node, ast.BinOp):
                raise ValueError('Unsupported expression')
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return add(left, right)
            if isinstance(node.op, ast.Sub):
                return add(left, right, -1)
            if isinstance(node.op, ast.Mult):
                return multiply(left, right)
            if isinstance(node.op, ast.Div) and set(right) == {()}:
                return bounded({key: coefficient / right[()] for key, coefficient in left.items()})
            if isinstance(node.op, ast.Pow) and set(right) <= {()}:
                power = right.get((), Fraction(0))
                if power.denominator != 1 or not 0 <= power <= 12:
                    raise ValueError('Exponent limit')
                if power == 0 and (set(left) != {()} or not left[()]):
                    raise ValueError('Zero exponent domain')
                result = {(): Fraction(1)}
                for _ in range(int(power)):
                    result = multiply(result, left)
                return result
            raise ValueError('Unsupported operation')

        return visit(root.body)
    except (ValueError, TypeError, SyntaxError, OverflowError, ZeroDivisionError, RecursionError):
        return None


def equivalent_choice_pair(choices):
    """Return a proven equivalent pair; unknown syntax is left to source review."""
    parsed = []
    for choice in choices:
        polynomial = _polynomial(choice['text'])
        if polynomial is not None:
            for label, previous in parsed:
                if previous == polynomial:
                    return label, choice['label']
            parsed.append((choice['label'], polynomial))
    return None


def distinct_polynomial_choices(choices, answer):
    """Repair proven duplicate generated distractors; the key still needs source review."""
    if len(choices) != 4 or answer not in {choice['label'] for choice in choices}:
        return choices
    polynomials = [_polynomial(choice['text']) for choice in choices]
    if any(value is None for value in polynomials):
        return choices
    order = sorted(range(4), key=lambda index: choices[index]['label'] != answer)
    kept = []
    duplicates = []
    for index in order:
        if any(polynomials[index] == polynomials[previous] for previous in kept):
            duplicates.append(index)
        else:
            kept.append(index)
    if not duplicates:
        return choices
    result = [dict(choice) for choice in choices]
    used = list(polynomials)
    for index in duplicates:
        for offset in range(1, 6):
            text = f"({choices[index]['text']}) + {offset}"
            polynomial = _polynomial(text)
            if polynomial is not None and all(polynomial != value for value in used):
                result[index]['text'] = text
                used.append(polynomial)
                break
        else:
            return choices
    return result


def unestablished_exponent_words(source, question, explanation):
    """Report literal unfamiliar words in exponent operands, without a math verdict."""
    def spans(text):
        result = []
        for match in re.finditer(r'[A-Za-z0-9_]+\s*\^\s*\(', text):
            start = text.index('(', match.start(), match.end())
            depth = 1
            for end in range(start + 1, min(len(text), start + 240)):
                depth += (text[end] == '(') - (text[end] == ')')
                if depth == 0:
                    result.append(text[match.start():end + 1])
                    break
        return result
    known = {word for expression in spans(source) + spans(question)
             for word in re.findall(r'[A-Za-z]{2,}', expression)}
    observations = []
    for expression in spans(explanation):
        for word in re.findall(r'[A-Za-z]{2,}', expression):
            record = {'expression': expression, 'word': word}
            if word not in known and record not in observations:
                observations.append(record)
    return observations
