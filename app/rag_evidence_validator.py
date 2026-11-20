"""Single-responsibility module for RAG evidence sufficiency validation, numeric literal
verification, and insufficient-evidence user guidance (extracted from `app/rag_agent.py`).
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from app.evaluation_metrics import has_repetition
from app.rag_query_analyzer import COMPARE_SCOPE_SPLIT_RE, DIRECTIVE_STOPWORDS, QUERY_STOPWORDS
from app.searcher import extract_document_scope_tokens


def _normalize_loanword_terms_cf(text: str) -> str:
    """Normalize common academic loanword orthography variants (e.g. vectơ/véctơ/véc tơ <-> vector)."""
    folded = text.casefold()
    return re.sub(r"\bv[eé]c[\s-]*t[oơ]\b", "vector", folded)


def weak_keyword_evidence(query: str, chunks: list[dict]) -> bool:
    """Require substantial lexical coverage for keyword-only retrieval, separating concept from directives."""
    query = re.sub(r"\bphát\s+biểu\b", " ", query, flags=re.I)
    query = re.sub(r"\b(?:vừa|mới|đã)\s+tải\s+lên\b", " ", query, flags=re.I)
    query = re.sub(r"\b(?:ở|về)\s+điểm\s+nào\b|\bđiểm\s+gì\b|\bkhác\s+(?:biệt\s+)?(?:gì|nhau)\b", " ", query, flags=re.I)
    query = re.sub(r"\bkhác\b(?=\s+[\w-]+(?:\s+[\w-]+){0,4}\s*(?:$|[?!.,]|(?:\s+(?:nào|sao)\b)))", " ", query, flags=re.I)
    all_query_words = [w for w in re.findall(r"\w+", query.casefold()) if len(w) >= 2 and w not in QUERY_STOPWORDS]
    if not all_query_words:
        return True

    scope_tokens = set(extract_document_scope_tokens(query))
    concept_terms = {w for w in all_query_words if w not in DIRECTIVE_STOPWORDS and w not in scope_tokens}
    terms = concept_terms if len(concept_terms) >= 1 else set(all_query_words)

    found = set()
    for chunk in chunks:
        c_text = chunk.get("text", "")
        c_norm = re.sub(r"([a-z])([A-Z])", r"\1 \2", c_text)
        c_norm = re.sub(r"(?i)\b(ánh\s*)?(xạ)(tuyến\s*tính)\b", r"\1xạ \3", c_norm)
        c_norm = re.sub(r"(?i)\b(đại\s*)?(số)(tuyến\s*tính)\b", r"\1số \3", c_norm)
        c_words = set(re.findall(r"\w+", c_norm.casefold()))
        found.update(terms.intersection(c_words))

        c_ascii = unicodedata.normalize("NFD", c_norm).encode("ascii", "ignore").decode("utf-8").casefold()
        c_ascii_words = set(re.findall(r"\w+", c_ascii))
        found.update(terms.intersection(c_ascii_words))
        for t in terms:
            t_ascii = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode("utf-8").casefold()
            if t_ascii and t_ascii in c_ascii_words:
                found.add(t)

    return len(found) / len(terms) < 0.75


def has_unsupported_bare_quantity(claim: str, sources: list[str]) -> bool:
    """Reject an otherwise unsupported bare measured value, not general arithmetic."""
    number = r"[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?"
    match = re.fullmatch(
        rf"\s*({number})\s*(?:cm[²³]?|mm[²³]?|km[²³]?|m[²³]?|kg|g|ms|s|mol/L|mol|bit|byte|MB|Mb|%|N|J|K)[.!]?\s*",
        claim,
    )
    if not match:
        return False
    try:
        value = Decimal(match[1].replace(",", "."))
        for source in sources:
            for literal in re.findall(rf"(?<![\w.,])({number})(?![\w.,])", source):
                if value == Decimal(literal.replace(",", ".")):
                    return False
    except InvalidOperation:
        return True
    return True


def extract_numeric_content(text: str) -> tuple[set[Decimal], set[str]]:
    """Extract distinct parsed Decimal values and unparsed/ambiguous numeric tokens."""
    if not text or not isinstance(text, str):
        return set(), set()

    text = text.replace("−", "-")
    unparsed_tokens = set()

    multi_dot_spans = set()
    for m in re.finditer(r"(?<![\w])([+-])?\s*(\d+(?:\.\d+){2,})\b", text):
        multi_dot_spans.add((m.start(), m.end()))
        tok = (m.group(1) or "") + m.group(2)
        unparsed_tokens.add(tok)

    multi_comma_spans = {}
    for m in re.finditer(r"(?<![\w])([+-])?\s*(\d+(?:,\d+){2,})\b", text):
        parts = m.group(2).split(",")
        is_thousands = len(parts[0]) in (1, 2, 3) and all(len(p) == 3 for p in parts[1:])
        multi_comma_spans[(m.start(), m.end())] = (is_thousands, parts)
        if is_thousands:
            tok = (m.group(1) or "") + m.group(2)
            unparsed_tokens.add(tok)

    for m in re.finditer(r"(?<![\w])([+-]?\s*\d+(?:[.,]\d+)?[eE](?![+-]?\d)[+-]?)(?![\w])", text):
        tok = m.group(1).replace(" ", "")
        unparsed_tokens.add(tok)

    pattern = re.compile(
        r"(?<![\w])"
        r"([+-])?\s*"
        r"("
        r"(?:\d+\.\d+|\.\d+|\d+,\d+|\d+)"
        r"(?:[eE][+-]?\d+)?"
        r")"
        r"(?![\w])"
    )

    results = set()

    for match in pattern.finditer(text):
        full_match_start = match.start()
        full_match_end = match.end()
        sign_str = match.group(1)
        num_str = match.group(2)

        if any(s <= full_match_start and full_match_end <= e for s, e in multi_dot_spans):
            continue

        in_multi_comma = False
        for (s, e), (is_thousands, parts) in multi_comma_spans.items():
            if s <= full_match_start and full_match_end <= e:
                in_multi_comma = True
                if not is_thousands:
                    for p in parts:
                        try:
                            results.add(Decimal(p))
                        except InvalidOperation:
                            pass
                break
        if in_multi_comma:
            continue

        if "," in num_str:
            if not re.search(r"[eE]", num_str):
                int_part, frac_part = num_str.split(",", 1)
                if int_part != "0" and re.fullmatch(r"[1-9]\d{0,2}", int_part) and len(frac_part) == 3:
                    tok = (sign_str or "") + num_str
                    unparsed_tokens.add(tok)
                    continue
            num_str = num_str.replace(",", ".")

        has_minus = False
        if sign_str == "-":
            sign_idx = match.start(1)
            pre = text[:sign_idx].rstrip()
            if not pre:
                has_minus = True
            else:
                last_char = pre[-1]
                if last_char in ")]}":
                    has_minus = False
                elif last_char.isdigit():
                    has_minus = False
                elif last_char in "=<>+*/×÷^%~:;,!?([{":
                    has_minus = True
                elif last_char == "-":
                    has_minus = True
                elif (
                    last_char.isalpha()
                    or last_char in "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ"
                ):
                    if sign_idx > 0 and text[sign_idx - 1] not in " \t":
                        has_minus = False
                    else:
                        words = re.findall(r"[\w]+", pre)
                        if words and len(words[-1]) == 1 and words[-1].isascii() and words[-1].isalpha():
                            has_minus = False
                        else:
                            has_minus = True
                else:
                    has_minus = False

        final_str = ("-" if has_minus else "") + num_str
        try:
            results.add(Decimal(final_str))
        except InvalidOperation:
            pass

    return results, unparsed_tokens


def extract_numeric_literals(text: str) -> set[Decimal]:
    """Extract distinct numeric values from text for evidence substantiation."""
    return extract_numeric_content(text)[0]


def extract_unparsed_numeric_tokens(text: str) -> set[str]:
    """Extract distinct unparsed or ambiguous numeric tokens from text."""
    return extract_numeric_content(text)[1]


_VN_UNITS = {
    "một": 1, "mốt": 1, "hai": 2, "ba": 3, "bốn": 4,
    "năm": 5, "lăm": 5, "sáu": 6, "bảy": 7, "bẩy": 7, "tám": 8, "chín": 9,
}
_VN_TENS = {
    "hai mươi": 20, "ba mươi": 30, "bốn mươi": 40, "năm mươi": 50,
    "sáu mươi": 60, "bảy mươi": 70, "tám mươi": 80, "chín mươi": 90,
}
_EN_TEENS_AND_SMALL = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_EN_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_WRITTEN_QUOTE_NUMBER_RE = re.compile(
    r"(?<!\w)("
    r"(?P<skip>một\s+(?:số|vài|ít|chút|đôi|nửa|phần|loạt|mặt|cách|khi|lúc|mình)|không\s+(?:chỉ|những|phải|ai|có|thể|được|bị|hề))"
    r"|(?P<zero_phrase>(?:(?:số|bằng|giá\s+trị|mức|điểm|vectơ|véc\s+tơ|vector|ma\s+trận|phần\s+tử|đa\s+thức|ánh\s+xạ|tiến\s+về|tiến\s+tới|dần\s+tới|hội\s+tụ\s+về|lớn\s+hơn|nhỏ\s+hơn|bé\s+hơn)\s+không|khác\s+không(?=$|[\s.,;:!?)])(?!\s+(?:chỉ|những|có|nêu|được|phải|thể|nói|đề|ghi|cho|làm|cung|hề|cần|chứa|bao|giải|trình|nhắc)\b)))"
    r"|(?P<vn_compound>(?:hai|ba|bốn|năm|sáu|bảy|tám|chín)\s+mươi(?:\s+(?:mốt|hai|ba|bốn|tư|lăm|sáu|bảy|tám|chín))?)"
    r"|(?P<vn_teen>mười(?:\s+(?:một|hai|ba|bốn|lăm|sáu|bảy|tám|chín))?)"
    r"|(?P<en_compound>(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[-\s]+(?:one|two|three|four|five|six|seven|eight|nine))?)"
    r"|(?P<en_small>zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|hundred)"
    r"|(?P<vn_with_year>(?P<vn_pre_year>một|hai|ba|bốn|sáu|bảy|bẩy|tám|chín)\s+năm)"
    r"|(?P<vn_five_unit>năm(?=\s+(?:năm|lần|người|bước|thành\s+phần|loại|trạng\s+thái|điểm|yếu\s+tố|khái\s+niệm|nguyên\s+tắc|nhóm|phần|phương\s+pháp|ngày|tuần|tháng|giờ|phút|giây|tầng|lớp|phần\s+tử|cạnh|đỉnh|mục|lựa\s+chọn)\b))"
    r"|(?P<vn_small>một|hai|ba|bốn|sáu|bảy|bẩy|tám|chín)"
    r")(?!\w)",
    re.IGNORECASE,
)


def extract_quote_written_numbers(text: str) -> set[Decimal]:
    """Extract unambiguous written Vietnamese/English numbers from a cited quote."""
    if not text:
        return set()
    found: set[Decimal] = set()
    for match in _WRITTEN_QUOTE_NUMBER_RE.finditer(text.casefold()):
        if match.group("skip"):
            continue
        if match.group("zero_phrase"):
            found.add(Decimal(0))
        elif match.group("vn_compound"):
            parts = match.group("vn_compound").split()
            base = _VN_TENS.get(f"{parts[0]} {parts[1]}", 0)
            unit = _VN_UNITS.get(parts[2], 0) if len(parts) > 2 else 0
            found.add(Decimal(base + unit))
        elif match.group("vn_teen"):
            parts = match.group("vn_teen").split()
            unit = _VN_UNITS.get(parts[1], 0) if len(parts) > 1 else 0
            found.add(Decimal(10 + unit))
        elif match.group("en_compound"):
            parts = re.split(r"[-\s]+", match.group("en_compound").strip())
            base = _EN_TENS.get(parts[0], 0)
            unit = _EN_TEENS_AND_SMALL.get(parts[1], 0) if len(parts) > 1 else 0
            found.add(Decimal(base + unit))
        elif match.group("en_small"):
            tok = match.group("en_small")
            found.add(Decimal(100 if tok == "hundred" else _EN_TEENS_AND_SMALL[tok]))
        elif match.group("vn_with_year"):
            found.add(Decimal(_VN_UNITS[match.group("vn_pre_year")]))
        elif match.group("vn_five_unit"):
            found.add(Decimal(5))
        elif match.group("vn_small"):
            found.add(Decimal(_VN_UNITS[match.group("vn_small")]))
    return found


def has_relevant_evidence(query: str, chunks: list[dict]) -> bool:
    if not chunks:
        return False
    identifiers = re.findall(r"\b[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+\b|\b\d{7,}\b", query)
    source_text = "\n".join(str(c.get("text", "")) for c in chunks).casefold()
    if any(len(identifier) >= 8 and identifier.casefold() not in source_text for identifier in identifiers):
        return False
    if not weak_keyword_evidence(query, chunks):
        return True
    return max(((chunk.get("semantic_score") or 0) for chunk in chunks), default=0) >= 0.52


def repetitive_answer(answer: str, source_count: int) -> bool:
    return has_repetition(answer) or len(re.findall(r"\[S\d+\]", answer, re.I)) > max(24, source_count * 4)


def is_unanswered_calculation_source(text: str) -> bool:
    """A passage ending with a calculation request supplies no worked answer."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return bool(
        sentences
        and re.match(
            r"^(?:hãy\s+)?(?:tính|tìm|calculate|find|determine)\b",
            sentences[-1],
            re.I,
        )
    )


def concept_has_evidence(concept: str, chunks: List[Dict[str, Any]]) -> bool:
    if not chunks:
        return False
    norm_concept = re.sub(r"\s+", " ", concept).strip()
    norm_concept_cf = _normalize_loanword_terms_cf(norm_concept)

    terms_cf = [
        w
        for w in re.findall(r"\w+", norm_concept_cf)
        if (len(w) >= 2 or w.isdigit() or w.isalnum())
        and w not in QUERY_STOPWORDS
        and w not in DIRECTIVE_STOPWORDS
    ]
    if not terms_cf:
        terms_cf = [
            w for w in re.findall(r"\w+", norm_concept_cf) if len(w) >= 2 or w.isdigit() or w.isalnum()
        ]
    if not terms_cf:
        return False

    strict_tokens = [
        w for w in re.findall(r"\w+", norm_concept) if w.isdigit() or (len(w) == 1 and w.isalpha())
    ]

    for chunk in chunks:
        raw_text = str(chunk.get("text", ""))
        c_text = re.sub(r"\s+", " ", raw_text)
        c_text_cf = _normalize_loanword_terms_cf(c_text)

        left_b = r"\b" if re.match(r"^\w", norm_concept) else r""
        right_b = r"\b" if re.search(r"\w$", norm_concept) else r""

        exact_pat = left_b + re.escape(norm_concept) + right_b
        if re.search(exact_pat, c_text):
            return True

        exact_pat_cf = left_b + re.escape(norm_concept_cf) + right_b
        if re.search(exact_pat_cf, c_text_cf):
            if any(len(tok) == 1 and tok.isalpha() for tok in strict_tokens):
                raw_words = set(re.findall(r"\w+", raw_text))
                if all(tok in raw_words for tok in strict_tokens if len(tok) == 1 and tok.isalpha()):
                    return True
                continue
            return True

        c_words_cf = set(re.findall(r"\w+", c_text_cf))
        if all(t in c_words_cf for t in terms_cf):
            if any(len(tok) == 1 and tok.isalpha() for tok in strict_tokens):
                raw_words = set(re.findall(r"\w+", raw_text))
                if all(tok in raw_words for tok in strict_tokens if len(tok) == 1 and tok.isalpha()):
                    return True
                continue
            return True

    return False


def detect_partial_evidence(query: str, chunks: List[Dict[str, Any]]) -> bool:
    """Conservatively detect a two-sided comparison with lexical support for only one side."""
    if not chunks:
        return False
    if not re.search(r"so sánh|khác nhau|khác gì|điểm khác biệt|phân biệt|\bvs\b|versus", query, re.I):
        return False
    bounded_query = re.split(COMPARE_SCOPE_SPLIT_RE, query, maxsplit=1, flags=re.I)[0]
    parts = re.split(r"\s+(?:và|với|vs|versus)\s+", bounded_query, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return False
    left_text = re.sub(
        r"^\s*(?:hãy\s+)?(?:so\s+sánh|đối\s+chiếu|phân\s+biệt)"
        r"(?:\s+(?:ngắn\s+gọn|nhanh|chi\s+tiết|cụ\s+thể|tóm\s+tắt))?"
        r"(?:\s+(?:(?:sự|những|các)\s+)?(?:điểm\s+)?(?:giống(?:\s+nhau)?(?:\s+và\s+khác(?:\s+nhau)?)?|khác\s+nhau|khác\s+biệt))?"
        r"(?:\s+giữa)?\s+",
        "",
        parts[0],
        flags=re.I,
    )
    right_text = re.split(
        r"\s+(?:khác nhau|khác gì|có gì|hoạt động|thế nào|ra sao)\b", parts[1], maxsplit=1, flags=re.I
    )[0]
    stopwords = QUERY_STOPWORDS | DIRECTIVE_STOPWORDS | {"hệ", "thống", "concept", "khái", "niệm"}
    left = {
        w
        for w in re.findall(r"\w+", _normalize_loanword_terms_cf(left_text))
        if (len(w) >= 2 or w.isdigit()) and w not in stopwords
    }
    right = {
        w
        for w in re.findall(r"\w+", _normalize_loanword_terms_cf(right_text))
        if (len(w) >= 2 or w.isdigit()) and w not in stopwords
    }
    if not left or not right or left == right:
        return False
    shared = left & right
    left_distinct = left - shared or left
    right_distinct = right - shared or right
    source_words = {
        word
        for chunk in chunks
        for word in re.findall(r"\w+", _normalize_loanword_terms_cf(str(chunk.get("text", ""))))
    }
    left_found = bool(left_distinct & source_words)
    right_found = bool(right_distinct & source_words)
    return left_found != right_found


def build_suggested_actions(reason: str, scope: dict) -> List[Dict[str, str]]:
    kind = scope.get("kind")
    actions = []
    if reason == "technical_error":
        actions.append({"action": "retry", "label": "Thử lại câu hỏi", "description": "Lỗi kỹ thuật có thể tạm thời; thử lại cùng phạm vi."})
        actions.append({"action": "select_other_document", "label": "Chọn tài liệu khác", "description": "Kiểm tra bằng một nguồn khác trong thư viện."})
    elif reason in ("source_stale", "source_missing", "source_not_indexed", "source_identity_mismatch"):
        actions.append({"action": "update_index", "label": "Cập nhật lại chỉ mục thư viện", "description": "Đồng bộ lại chỉ mục từ tệp gốc hiện có trong thư mục nguồn trước khi tra cứu."})
        actions.append({"action": "select_other_document", "label": "Chọn tài liệu khác", "description": "Tra cứu từ một tài liệu còn khớp bản gốc trong thư viện."})
    elif reason in ("source_visual_content_unverified", "source_example_inconsistent"):
        actions.append({"action": "select_other_page", "label": "Chọn trang khác", "description": "Tìm và chọn một trang tài liệu khác."})
        actions.append({"action": "select_other_document", "label": "Chọn tài liệu khác", "description": "Tìm một nguồn khác để đối chiếu."})
    elif kind == "page":
        actions.append({
            "action": "expand_to_document",
            "label": "Mở rộng ra toàn tài liệu",
            "description": f"Bỏ giới hạn trang {scope.get('page_num', '')} và tìm kiếm trong tất cả các trang của tài liệu này",
        })
        actions.append({
            "action": "select_other_page",
            "label": "Chọn trang khác",
            "description": "Xem hoặc chọn trang khác trong tài liệu",
        })
        actions.append({
            "action": "rephrase_query",
            "label": "Thử diễn đạt lại câu hỏi",
            "description": "Dùng từ khóa ngắn gọn hoặc thuật ngữ chuyên môn hơn",
        })
    elif reason == "partial_evidence":
        actions.append({
            "action": "focus_single_concept",
            "label": "Tách thành câu hỏi đơn lẻ",
            "description": "Hỏi riêng về khái niệm hoặc phần câu hỏi đã có bằng chứng",
        })
        if kind == "document":
            actions.append({
                "action": "expand_library",
                "label": "Mở rộng ra toàn thư viện",
                "description": "Tìm bằng chứng còn thiếu trong các tài liệu khác đã lập chỉ mục",
            })
        else:
            actions.append({
                "action": "select_other_document",
                "label": "Chọn tài liệu khác",
                "description": "Tìm bằng chứng còn thiếu trong một tài liệu khác của thư viện",
            })
        actions.append({
            "action": "rephrase_query",
            "label": "Thử diễn đạt lại câu hỏi",
            "description": "Bổ sung ngữ cảnh hoặc thay đổi cách hỏi",
        })
    elif kind == "document":
        actions.append({
            "action": "expand_library",
            "label": "Mở rộng ra toàn thư viện",
            "description": "Bỏ giới hạn tài liệu và tìm kiếm trong tất cả tài liệu thư viện",
        })
        actions.append({
            "action": "select_other_document",
            "label": "Chọn tài liệu khác",
            "description": "Chọn tài liệu khác trong thư viện để tra cứu",
        })
        actions.append({
            "action": "rephrase_query",
            "label": "Thử diễn đạt lại câu hỏi",
            "description": "Dùng từ khóa cụ thể hơn",
        })
    else:
        actions.append({
            "action": "rephrase_query",
            "label": "Thử diễn đạt lại câu hỏi",
            "description": "Nêu rõ khái niệm, công thức hoặc tên sách/chương cụ thể",
        })
        actions.append({
            "action": "select_other_document",
            "label": "Chọn tài liệu trong thư viện",
            "description": "Mở danh sách tài liệu để tra cứu theo từng cuốn sách",
        })
    return actions


def insufficient_evidence_message(reason: str, scope: dict) -> str:
    """Describe only retrieval state and scope that the request actually verified."""
    kind = scope.get("kind")
    att_name = scope.get("attachment_filename")
    if kind == "page":
        location = f"trang {scope.get('page_num')} của tài liệu {f'«{att_name}»' if att_name else 'đã chọn'}"
    elif kind == "document" or att_name:
        location = f"tài liệu «{att_name}»" if att_name else "tài liệu đã chọn"
    elif kind == "library":
        location = "thư viện tài liệu đã lập chỉ mục"
    else:
        location = "kết quả tìm kiếm hiện tại"

    if reason == "source_stale":
        return f"Bản gốc của {location} đã thay đổi trên đĩa so với chỉ mục hiện tại nên kết quả cũ không còn được dùng làm căn cứ. Vui lòng cập nhật chỉ mục hoặc chọn tài liệu khác."
    if reason == "source_missing":
        return f"Không tìm thấy tệp gốc khớp với chỉ mục của {location} (tệp có thể đã bị xóa hoặc di chuyển) nên kết quả cũ không thể làm căn cứ. Vui lòng kiểm tra nguồn và cập nhật chỉ mục."
    if reason in ("source_not_indexed", "source_identity_mismatch"):
        return f"Tài liệu trong {location} chưa sẵn sàng hoặc không vượt qua kiểm tra toàn vẹn nguồn nên không thể làm căn cứ trả lời."
    if reason == "source_visual_content_unverified":
        return "Bản chữ của nguồn có thể thiếu hoặc thể hiện sai công thức hay hình minh họa, nên tôi chưa thể trả lời chắc chắn. Vui lòng xem trang tài liệu gốc để kiểm tra hoặc chọn nguồn khác."
    if reason == "source_example_inconsistent":
        return "Ví dụ trong nguồn có danh sách số nguyên tố không khớp với dữ liệu đầu vào. Tôi chưa thể dùng kết quả đó làm đáp án đáng tin cậy; hãy xem trang gốc và kiểm tra lại."
    if reason == "source_signature_difference_unverified":
        return (
            "Đoạn nguồn có khai báo và định nghĩa cùng tên, một tham số truyền giá trị còn tham số kia "
            "là tham chiếu. Chúng có chữ ký khác nhau và có thể là các hàm nạp chồng; hãy đối chiếu "
            "bản gốc và các tệp liên quan trước khi kết luận ảnh hưởng đến biên dịch hoặc liên kết."
        )
    if reason in ("page_empty", "page_scope_too_narrow"):
        return f"Tôi chưa tìm thấy đủ căn cứ trong trang {scope.get('page_num')} của tài liệu đã chọn cho câu hỏi này. Bạn có thể mở rộng tìm kiếm ra toàn tài liệu hoặc chọn một trang khác."
    elif reason == "low_relevance":
        if att_name:
            return f"Tôi chưa tìm thấy thông tin phù hợp trong {location} cho câu hỏi này. Bạn vui lòng đặt câu hỏi liên quan đến nội dung của tài liệu đã tải lên."
        return f"Có kết quả truy xuất trong {location}, nhưng chúng chưa đủ liên quan hoặc căn cứ trực tiếp để trả lời chắc chắn. Bạn có thể thử diễn đạt lại câu hỏi hoặc chọn phạm vi tài liệu khác."
    elif reason == "partial_evidence":
        return f"Tài liệu trong {location} chỉ cung cấp bằng chứng cho một phần câu hỏi (chưa tìm thấy đủ căn cứ để đối chiếu đầy đủ). Bạn có thể hỏi riêng từng khái niệm hoặc mở rộng phạm vi tìm kiếm."
    elif reason in ("retrieval_error", "technical_error"):
        return "Không thể truy cập hoặc xử lý tài liệu do lỗi kỹ thuật. Vui lòng thử lại sau."
    elif reason == "unverified_scope":
        return "Tôi chưa tìm thấy đủ căn cứ trong kết quả hiện tại để trả lời câu hỏi. Bạn có thể thử diễn đạt lại câu hỏi hoặc chọn phạm vi tài liệu khác."
    return f"Tôi chưa tìm thấy đủ căn cứ trong {location} cho câu hỏi này. Bạn có thể thử diễn đạt lại câu hỏi hoặc chọn phạm vi tài liệu khác."


_GENERIC_ASCII_TERMS = frozenset({
    "model", "models", "system", "systems", "method", "methods", "process",
    "processes", "result", "results", "table", "tables", "figure", "figures",
    "example", "examples", "chapter", "chapters", "section", "sections",
    "value", "values", "state", "states", "level", "levels", "factor", "factors",
    "stage", "stages", "phase", "phases", "group", "groups", "class", "classes",
    "object", "objects", "function", "functions", "input", "inputs", "output",
    "outputs", "index", "indices", "score", "scores", "theory", "study", "studies",
    "research", "human", "memory", "brain", "paper", "article", "question",
    "answer", "source", "sources", "claim", "claims", "point", "points", "order",
    "range", "scale", "ratio", "limit", "matrix", "vector", "linear", "space",
    "graph", "array", "string", "number", "numbers", "logic", "rules", "effect",
    "effects", "cause", "causes", "focus", "control", "data", "type", "types",
    "step", "steps", "part", "parts", "case", "cases", "problem", "solution",
    "approach", "concept", "principle", "definition", "property", "element",
    "component", "components", "structure", "feature", "features", "action",
    "return", "print", "public", "private", "static", "void", "boolean", "double",
    "float", "integer", "while", "where", "which", "there", "these", "those",
    "about", "after", "before", "between", "through", "other", "first", "second",
    "third", "using", "based", "given", "within", "without", "because", "therefore",
    "however", "although", "since", "under", "above", "below", "total", "mean",
    "check", "valid", "error", "true", "false", "none", "null", "note", "notes",
    "abstract", "interface", "algorithm", "algorithms", "theorem", "lemma",
})


def _fold_ascii_text(text: str) -> tuple[str, set[str], str]:
    """Normalize ligatures, wrapped hyphens, and whitespace for ASCII term matching."""
    cleaned = unicodedata.normalize("NFKC", text or "")
    cleaned = re.sub(r"([A-Za-z])-\s+([A-Za-z])", r"\1\2", cleaned)
    cleaned = re.sub(r"[-_/]+", " ", cleaned.casefold())
    collapsed = re.sub(r"\s+", " ", cleaned).strip()
    words = set(re.findall(r"[a-z]{2,}", collapsed))
    nospace = re.sub(r"[^a-z0-9]", "", collapsed)
    return collapsed, words, nospace


def _ascii_word_present(word: str, words_set: set[str], nospace_text: str) -> bool:
    if word in words_set:
        return True
    if word.endswith("s") and len(word) >= 5 and word[:-1] in words_set:
        return True
    if word.endswith("es") and len(word) >= 6 and word[:-2] in words_set:
        return True
    return len(word) >= 6 and word in nospace_text


def _ascii_phrase_present(phrase: str, words_set: set[str], nospace_text: str) -> bool:
    _, p_words, p_nospace = _fold_ascii_text(phrase)
    if len(p_nospace) >= 6 and p_nospace in nospace_text:
        return True
    distinctive = [w for w in p_words if len(w) >= 4 and w not in _GENERIC_ASCII_TERMS]
    candidates = distinctive or [w for w in p_words if len(w) >= 3]
    return bool(candidates) and all(_ascii_word_present(w, words_set, nospace_text) for w in candidates)


def _is_predominantly_ascii_source(text: str) -> bool:
    letters = re.findall(r"[^\W\d_]", text or "", re.UNICODE)
    if len(letters) < 80:
        return False
    ascii_letters = sum(1 for ch in letters if ch.isascii() and ch.isalpha())
    return (ascii_letters / len(letters)) >= 0.75


_ROMAN_OR_COMMON_ACRONYMS = {
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
    "AI", "ML", "DL", "NN", "RL", "NLP", "CV", "CS", "IT", "DB", "OS", "OK",
}


_QUERY_ABSTENTION_RE = re.compile(
    r"(?:tài\s+liệu|nguồn|văn\s+bản|đoạn(?:\s+(?:trích|nguồn|văn))?|sách).*?"
    r"(?:không|chưa)\s+(?:có|chứa|đề\s+cập|cung\s+cấp|nêu|ghi\s+nhận|nhắc(?:\s+(?:đến|tới))?"
    r"|nói|mô\s+tả|trình\s+bày|hướng\s+dẫn|giải\s+thích|chứng\s+minh|bàn(?:\s+về)?|đưa\s+ra)"
    r"|^\s*(?:hiện\s+)?(?:không|chưa)\s+có\s+(?:đủ\s+)?thông\s+tin\b",
    re.IGNORECASE,
)


def strip_query_abstention_identifiers(claim_text: str, query: str) -> str:
    """Redact verbatim hyphenated ASCII query identifiers inside explicit abstention claims before numeric checks."""
    if not claim_text or not query or not _QUERY_ABSTENTION_RE.search(claim_text):
        return claim_text
    query_idents = {
        m.group(1)
        for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)\b", query)
    }
    out = claim_text
    for ident in sorted(query_idents, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(ident)}\b", " ", out, flags=re.IGNORECASE)
    return out


def find_ungrounded_technical_terms(
    claim_text: str,
    cited_sources: List[str],
    query: str,
    chat_history: List[Dict[str, Any]] | None = None,
) -> List[str]:
    """Detect ASCII technical terms copied from prior turns, query subjects absent from cited sources, or fabricated in English sources."""
    if not claim_text or not cited_sources:
        return []
    combined_source = " ".join(s for s in cited_sources if s)
    if not combined_source.strip():
        return []

    _, source_words, source_nospace = _fold_ascii_text(combined_source)
    _, query_words, query_nospace = _fold_ascii_text(query or "")
    _, claim_words, claim_nospace = _fold_ascii_text(claim_text)

    flagged: list[str] = []
    seen_flagged: set[str] = set()

    def _record(term: str) -> None:
        key = term.casefold()
        if key not in seen_flagged:
            seen_flagged.add(key)
            flagged.append(term)

    if query and not _QUERY_ABSTENTION_RE.search(claim_text):
        # Named ASCII acronyms / hyphenated identifiers explicitly asked by the query and asserted in the claim
        # must exist in the cited source (unless excluded common translated acronyms).
        for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+|[A-Z][A-Z0-9]{1,9})\b", query):
            ident = m.group(1)
            if ident.upper() in _ROMAN_OR_COMMON_ACRONYMS:
                continue
            if "-" in ident:
                if _ascii_phrase_present(ident, claim_words, claim_nospace) or re.search(rf"\b{re.escape(ident)}\b", claim_text, re.I):
                    if not (
                        _ascii_phrase_present(ident, source_words, source_nospace)
                        or re.search(rf"\b{re.escape(ident)}\b", combined_source, re.I)
                    ):
                        _record(ident)
            elif re.search(rf"\b{re.escape(ident)}\b", claim_text):
                if not re.search(rf"\b{re.escape(ident)}\b", combined_source, re.I):
                    _record(ident)

    if chat_history:
        history_text = " ".join(
            str(m.get("content", ""))
            for m in chat_history
            if isinstance(m, dict) and isinstance(m.get("content"), str)
        )
        if history_text.strip():
            # Multi-word ASCII technical phrases shared between history and claim.
            for match in re.finditer(r"\b([A-Za-z][A-Za-z\-]{2,}(?:\s+[A-Za-z][A-Za-z\-]{2,}){1,3})\b", history_text):
                phrase = match.group(1)
                p_tokens = [w.casefold() for w in re.findall(r"[A-Za-z]{2,}", phrase)]
                if not any(len(w) >= 5 and w not in _GENERIC_ASCII_TERMS for w in p_tokens):
                    continue
                if not _ascii_phrase_present(phrase, claim_words, claim_nospace):
                    continue
                if _ascii_phrase_present(phrase, query_words, query_nospace):
                    continue
                if not _ascii_phrase_present(phrase, source_words, source_nospace):
                    _record(phrase)

            # Single long distinctive ASCII tokens or mid-text proper nouns shared between history and claim.
            history_tokens = set(re.findall(r"\b[A-Za-z]{4,}\b", history_text))
            claim_tokens_lower = set(re.findall(r"\b[a-z]{4,}\b", claim_text.casefold()))
            for tok in history_tokens:
                low = tok.casefold()
                if low in _GENERIC_ASCII_TERMS:
                    continue
                is_long_tech = len(low) >= 8
                is_proper = tok[0].isupper() and tok[1:].islower() and len(tok) >= 5
                if not (is_long_tech or is_proper):
                    continue
                if low not in claim_tokens_lower:
                    continue
                if _ascii_word_present(low, query_words, query_nospace):
                    continue
                if not _ascii_word_present(low, source_words, source_nospace):
                    _record(tok)

    if _is_predominantly_ascii_source(combined_source):
        for match in re.finditer(r"\(\s*([A-Za-z][A-Za-z0-9\-\s]{4,60})\s*\)", claim_text):
            phrase = match.group(1).strip()
            words = [w.casefold() for w in re.findall(r"[A-Za-z]{3,}", phrase)]
            distinctive = [w for w in words if len(w) >= 4 and w not in _GENERIC_ASCII_TERMS]
            if len(distinctive) < 2 or sum(len(w) for w in distinctive) < 10:
                continue
            if _ascii_phrase_present(phrase, query_words, query_nospace):
                continue
            if not _ascii_phrase_present(phrase, source_words, source_nospace):
                _record(phrase)

    return flagged


_VI_DIACRITIC_CHARS = (
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
    "ùúủũụưừứửữựỳýỷỹỵđ"
    "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ"
    "ÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ"
)
_OCR_GLUED_VI_NUM_RE = re.compile(
    rf"[A-Za-z{_VI_DIACRITIC_CHARS}]*[{_VI_DIACRITIC_CHARS}](\d+(?:[.,]\d+)?)(?=[^0-9A-Za-z_]|$)"
)
_OCR_GLUED_REL_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])[a-z]{2,}(\d+(?:[.,]\d+)?)(?=[∈∉≤≥≠])"
)


def extract_quote_ocr_glued_numbers(text: str) -> set[Decimal]:
    """Extract digits glued onto preceding words in OCR/PDF quotes without touching algebra variables."""
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKC", text)
    found: set[Decimal] = set()
    for pattern in (_OCR_GLUED_VI_NUM_RE, _OCR_GLUED_REL_NUM_RE):
        for match in pattern.finditer(normalized):
            raw_num = match.group(1).replace(",", ".")
            try:
                value = Decimal(raw_num)
            except (InvalidOperation, ValueError):
                value = None
            if value is not None:
                found.add(value)
    return found


_SUB_SUP_TRANS = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")
_UNICODE_SUB_SUP_RE = re.compile(r"[₀-₉]+|[⁰¹²³⁴-⁹]+")
_ASCII_SUB_SUP_RE = re.compile(r"(?<=[A-Za-zÀ-ỹℝℕℤℚℂ])(?:_\{?(\d+)\}?|\^\{?(\d+)\}?)")


def extract_quote_sub_sup_numbers(text: str) -> set[Decimal]:
    """Extract integer index/exponent digits from Unicode/TeX-style subscripts and superscripts in a cited quote."""
    if not text:
        return set()
    found: set[Decimal] = set()
    for match in _UNICODE_SUB_SUP_RE.finditer(text):
        try:
            found.add(Decimal(match.group(0).translate(_SUB_SUP_TRANS)))
        except InvalidOperation:
            pass
    for match in _ASCII_SUB_SUP_RE.finditer(text):
        digits = match.group(1) or match.group(2)
        if digits:
            try:
                found.add(Decimal(digits))
            except InvalidOperation:
                pass
    return found


def extract_quote_supported_numbers(text: str) -> tuple[set[Decimal], set[str]]:
    """Return numeric values and unparsed numeric tokens supported by a cited quote."""
    nums, unparsed = extract_numeric_content(text)
    supported = (
        nums
        | extract_quote_written_numbers(text)
        | extract_quote_ocr_glued_numbers(text)
        | extract_quote_sub_sup_numbers(text)
    )
    return supported, unparsed


def _format_decimal_label(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _analyze_numeric_mismatch(
    source_id: str,
    claim_numbers: set[Decimal],
    quote_numbers: set[Decimal],
    claim_unparsed: set[str],
    quote_unparsed: set[str],
    raw_sources_map: dict[str, str],
    *,
    use_source_lines: bool,
    query: str = "",
    source_meta: dict[str, tuple[Any, Any]] | None = None,
) -> tuple[list[str], list[int], list[str], list[str], list[str], Any, list[str]]:
    """Classify missing claim numbers against lines of the selected source, peer chunks, and query."""
    missing_nums = sorted(claim_numbers - quote_numbers)
    missing_unparsed = sorted(claim_unparsed - quote_unparsed)
    missing_labels = [_format_decimal_label(v) for v in missing_nums] + missing_unparsed
    missing_set = set(missing_nums)
    missing_unp_set = set(missing_unparsed)

    same_source_lines: list[int] = []
    if use_source_lines and source_id in raw_sources_map:
        for line_no, line in enumerate(raw_sources_map[source_id].split("\n"), 1):
            l_nums, l_unp = extract_quote_supported_numbers(line)
            if (missing_set and (missing_set & l_nums)) or (missing_unp_set and (missing_unp_set & l_unp)):
                same_source_lines.append(line_no)

    peer_sources: list[str] = []
    complete_peers: list[str] = []
    for sid in sorted(
        (k for k in raw_sources_map if k != source_id),
        key=lambda k: (0, int(k[1:])) if k.startswith("S") and k[1:].isdigit() else (1, k),
    ):
        s_nums, s_unp = extract_quote_supported_numbers(raw_sources_map[sid])
        if (missing_set and (missing_set & s_nums)) or (missing_unp_set and (missing_unp_set & s_unp)):
            peer_sources.append(sid)
            if (not claim_numbers or claim_numbers.issubset(s_nums)) and (
                not claim_unparsed or claim_unparsed.issubset(s_unp)
            ):
                complete_peers.append(sid)

    src_meta = source_meta.get(source_id) if source_meta else None
    source_page = src_meta[1] if src_meta and len(src_meta) >= 2 else None
    same_page_peers: list[str] = []
    if src_meta and source_page is not None and source_meta is not None:
        same_page_complete = [sid for sid in complete_peers if source_meta.get(sid) == src_meta]
        same_page_partial = [sid for sid in peer_sources if source_meta.get(sid) == src_meta]
        same_page_peers = same_page_complete or same_page_partial

    q_nums, q_unp = extract_numeric_content(query) if query else (set(), set())
    query_missing_labels = [_format_decimal_label(v) for v in sorted(missing_set & q_nums)] + sorted(
        missing_unp_set & q_unp
    )
    return (
        missing_labels,
        same_source_lines,
        peer_sources,
        complete_peers,
        same_page_peers,
        source_page,
        query_missing_labels,
    )


def format_numeric_mismatch_feedback(
    source_id: str,
    claim_numbers: set[Decimal],
    quote_numbers: set[Decimal],
    claim_unparsed: set[str],
    quote_unparsed: set[str],
    raw_sources_map: dict[str, str],
    *,
    use_source_lines: bool,
    query: str = "",
    source_meta: dict[str, tuple[Any, Any]] | None = None,
) -> str:
    """Build actionable repair feedback identifying missing numbers, matching lines, or peer sources."""
    (
        missing_labels,
        same_source_lines,
        peer_sources,
        complete_peers,
        same_page_peers,
        source_page,
        query_missing_labels,
    ) = _analyze_numeric_mismatch(
        source_id,
        claim_numbers,
        quote_numbers,
        claim_unparsed,
        quote_unparsed,
        raw_sources_map,
        use_source_lines=use_source_lines,
        query=query,
        source_meta=source_meta,
    )
    from_query = bool(query_missing_labels)
    query_hint = (
        f"KHÔNG lặp lại câu dẫn/bối cảnh mở đầu từ câu hỏi chứa số ({', '.join(query_missing_labels)}); "
        if query_missing_labels
        else ""
    )

    missing_summary = f" (Thiếu số: {', '.join(missing_labels)})" if missing_labels else ""
    parts = [f"{source_id}: text có số không nằm trong đoạn đã chọn{missing_summary}."]
    if same_source_lines:
        line_refs = ", ".join(f"L{n}" for n in same_source_lines[:4])
        parts.append(f"Trong {source_id}, số bị thiếu nằm ở {line_refs}; mở rộng evidence_lines bao gồm dòng đó hoặc bỏ số ngoài đoạn đã chọn.")
    else:
        if same_page_peers and source_page is not None:
            same_page_refs = ", ".join(same_page_peers[:3])
            parts.append(
                f"CHÚ Ý CÙNG TRANG (tr.{source_page}): {source_id} chỉ là MỘT đoạn bị cắt trên trang này; "
                f"đoạn cùng trang {same_page_refs} mới chứa số bị thiếu — đổi source_ids sang {same_page_refs} nếu trích nội dung đó."
            )
        if complete_peers:
            complete_refs = ", ".join(complete_peers[:3])
            if from_query:
                q_list = ", ".join(query_missing_labels)
                parts.append(
                    f"Số {q_list} chỉ có trong câu hỏi hoặc nguồn {complete_refs} và KHÔNG có trong {source_id}; "
                    f"{query_hint}KHÔNG chép lại số từ câu hỏi vào text của {source_id}; "
                    f"BỎ hoàn toàn mọi chữ số ({q_list}) khỏi claim gắn với {source_id} (chỉ nêu nội dung/định nghĩa thực sự có trong {source_id}) "
                    f"hoặc dùng nguồn chứa đủ các số ({complete_refs})."
                )
            else:
                parts.append(
                    f"Số bị thiếu không có trong {source_id} mà nằm ở nguồn chứa đủ các số ({complete_refs}); "
                    f"dùng {complete_refs} hoặc tách riêng từng claim cho mỗi nguồn, không gộp số từ nhiều nguồn vào cùng một claim."
                )
        elif peer_sources:
            peer_refs = ", ".join(peer_sources[:3])
            if from_query:
                q_list = ", ".join(query_missing_labels)
                parts.append(
                    f"Số bị thiếu không có trong {source_id} mà nằm ở {peer_refs} (hoặc đến từ câu hỏi); "
                    f"{query_hint}KHÔNG chép lại số từ câu hỏi vào text nếu {source_id} không chứa chúng; "
                    f"BỎ hoàn toàn mọi chữ số ({q_list}) khỏi claim gắn với {source_id} (hoặc tách riêng từng claim cho {peer_refs})."
                )
            else:
                parts.append(f"Số bị thiếu không có trong {source_id} mà nằm ở {peer_refs}; tách riêng từng claim cho mỗi nguồn, không gộp số từ nhiều nguồn vào cùng một claim.")
        elif from_query:
            q_list = ", ".join(query_missing_labels)
            parts.append(
                f"Số {q_list} đến từ câu hỏi và KHÔNG có trong {source_id}; "
                f"{query_hint}Không chép dữ kiện từ câu hỏi vào text nếu đoạn nguồn đã chọn không chứa chúng; "
                f"KHÔNG chép số từ câu hỏi; BỎ hoàn toàn mọi chữ số ({q_list}) khỏi claim gắn với {source_id} và bắt đầu thẳng bằng dữ kiện thực sự có trong {source_id}."
            )
        else:
            parts.append(
                "Không thêm số thứ tự hoặc giá trị ngoài nguồn; chọn đoạn chứng minh đủ từng ý. "
                "Không chép dữ kiện từ câu hỏi vào text nếu đoạn nguồn đã chọn không chứa chúng; "
                "trả lời trực tiếp bằng phép tính và kết quả có trong nguồn lời giải."
            )
    return " ".join(parts)


def format_numeric_ban_hint(
    source_id: str,
    claim_numbers: set[Decimal],
    quote_numbers: set[Decimal],
    claim_unparsed: set[str],
    quote_unparsed: set[str],
    raw_sources_map: dict[str, str],
    *,
    use_source_lines: bool,
    query: str = "",
    source_meta: dict[str, tuple[Any, Any]] | None = None,
) -> str:
    """Return a compact one-line numeric ban reminder placed at the end of repair instructions."""
    (
        missing_labels,
        same_source_lines,
        peer_sources,
        complete_peers,
        same_page_peers,
        source_page,
        _,
    ) = _analyze_numeric_mismatch(
        source_id,
        claim_numbers,
        quote_numbers,
        claim_unparsed,
        quote_unparsed,
        raw_sources_map,
        use_source_lines=use_source_lines,
        query=query,
        source_meta=source_meta,
    )
    if not missing_labels:
        return ""
    nums_str = ", ".join(missing_labels[:5])
    if same_source_lines:
        line_refs = ", ".join(f"L{n}" for n in same_source_lines[:4])
        return f"{source_id}: thiếu ({nums_str}) ở dòng đã chọn — mở rộng evidence_lines tới {line_refs} hoặc BỎ ({nums_str})."
    ordered_peers = list(dict.fromkeys([*same_page_peers, *complete_peers])) or peer_sources
    peer_hint = f" -> dùng {', '.join(ordered_peers[:3])}" if ordered_peers else ""
    page_hint = f" (tr.{source_page})" if source_page is not None else ""
    return f"{source_id}{page_hint} KHÔNG có ({nums_str}){peer_hint} hoặc BỎ ({nums_str}) khỏi text."
