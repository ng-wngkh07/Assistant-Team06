"""Narrow checks for locally recognizable source inconsistencies and ambiguities."""

from collections import Counter
import math
import re
import unicodedata


_PRIME_EXTRACTION = re.compile(
    r"\b(?:extract|filter|select|lọc|trích(?:\s+xuất)?)\b[^\n]{0,80}"
    r"\b(?:prime\s+numbers?|số\s+nguyên\s+tố)\b",
    re.IGNORECASE,
)
_TEXT_FILE_LABEL = re.compile(r"(?im)^[ \t]*([^\s/\\]{1,80}\.txt)[ \t]*\r?$")
_INTEGER_LINE = re.compile(r"[ \t\r\n]*([+-]?\d+(?:[ \t]+[+-]?\d+)*)[ \t]*(?:\r?\n|$)")
_SIMPLE_FUNCTION = re.compile(
    r"(?m)^[ \t]*(void|bool|char|short|int|long|float|double)\s+"
    r"([A-Za-z_]\w*)\s*\(\s*(bool|char|short|int|long|float|double)\b"
    r"\s*(&?)\s*(?:[A-Za-z_]\w*)?\s*\)\s*([;{])"
)


def differing_value_reference_signatures(text: str, query: str) -> bool:
    """Recognize a narrow declaration/definition comparison in one passage.

    A value parameter and a reference parameter form distinct C++ signatures;
    they may be intentional overloads. This is not a compiler or a general
    proof that a source is incorrect, and unsupported syntax is left unchecked.
    """
    question = unicodedata.normalize("NFC", query or "").casefold()
    asks_declaration = any(word in question for word in ("khai báo", "declaration", "prototype"))
    asks_definition = any(word in question for word in ("định nghĩa", "definition"))
    if not text or not (asks_declaration and asks_definition):
        return False

    signatures = {}
    for match in _SIMPLE_FUNCTION.finditer(text):
        return_type, name, parameter_type, reference, ending = match.groups()
        if not re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", question):
            continue
        key = (return_type, name, parameter_type)
        declarations, definitions = signatures.setdefault(key, (set(), set()))
        (declarations if ending == ";" else definitions).add(bool(reference))

    return any(
        declarations and definitions and declarations.isdisjoint(definitions)
        for declarations, definitions in signatures.values()
    )


def inconsistent_prime_extraction_example(text: str) -> bool:
    """Check only unambiguous, two-file, integer-list prime examples.

    An unsupported layout is left unchecked; False does not certify the
    source. Bounds keep this check small for untrusted document text.
    """
    if not text or not _PRIME_EXTRACTION.search(text):
        return False
    labels = list(_TEXT_FILE_LABEL.finditer(text))
    if len(labels) != 2 or labels[0].group(1).casefold() == labels[1].group(1).casefold():
        return False
    # Do not infer input/output roles solely from the order of the examples.
    introduction = text[:labels[0].start()]
    roles = ((r"(?:read|đọc)", labels[0]), (r"(?:write|ghi)", labels[1]))
    if any(
        not re.search(rf"\b{verb}\b[^\n]{{0,160}}{re.escape(label.group(1))}\b", introduction, re.I)
        for verb, label in roles
    ):
        return False
    lists = []
    for index, label in enumerate(labels):
        end = labels[index + 1].start() if index + 1 < len(labels) else len(text)
        match = _INTEGER_LINE.match(text[label.end():end])
        if not match:
            return False
        values = match.group(1).split()
        if len(values) > 200 or any(len(value.lstrip("+-")) > 10 for value in values):
            return False
        numbers = [int(value) for value in values]
        if any(abs(value) > 1_000_000_000 for value in numbers):
            return False
        lists.append(numbers)

    def is_prime(value: int) -> bool:
        if value < 2:
            return False
        return all(value % divisor for divisor in range(2, math.isqrt(value) + 1))

    return Counter(value for value in lists[0] if is_prime(value)) != Counter(lists[1])
