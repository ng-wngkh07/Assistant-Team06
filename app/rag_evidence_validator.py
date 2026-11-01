"""Single-responsibility module for RAG evidence sufficiency validation, numeric literal
verification, and insufficient-evidence user guidance (extracted from `app/rag_agent.py`).
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from app.evaluation_metrics import has_repetition
from app.rag_query_analyzer import DIRECTIVE_STOPWORDS, QUERY_STOPWORDS
from app.searcher import extract_document_scope_tokens


def weak_keyword_evidence(query: str, chunks: list[dict]) -> bool:
    """Require substantial lexical coverage for keyword-only retrieval, separating concept from directives."""
    query = re.sub(r"\bphát\s+biểu\b", " ", query, flags=re.I)
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


def has_relevant_evidence(query: str, chunks: list[dict]) -> bool:
    if not chunks:
        return False
    identifiers = re.findall(r"\b[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+\b|\b\d{7,}\b", query)
    source_text = "\n".join(str(c.get("text", "")) for c in chunks).casefold()
    if any(len(identifier) >= 8 and identifier.casefold() not in source_text for identifier in identifiers):
        return False
    if not weak_keyword_evidence(query, chunks):
        return True
    return max((chunk.get("semantic_score", 0) for chunk in chunks), default=0) >= 0.52


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
    norm_concept_cf = norm_concept.casefold()

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
        c_text_cf = c_text.casefold()

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
    bounded_query = re.split(
        r"\b(?:trong cuốn|trong sách|theo tài liệu|theo sách)\b", query, maxsplit=1, flags=re.I
    )[0]
    parts = re.split(r"\s+(?:và|với|vs|versus)\s+", bounded_query, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return False
    left_text = re.sub(r"^\s*so sánh\s+", "", parts[0], flags=re.I)
    right_text = re.split(
        r"\s+(?:khác nhau|khác gì|có gì|hoạt động|thế nào|ra sao)\b", parts[1], maxsplit=1, flags=re.I
    )[0]
    stopwords = QUERY_STOPWORDS | DIRECTIVE_STOPWORDS | {"hệ", "thống", "concept", "khái", "niệm"}
    left = {
        w
        for w in re.findall(r"\w+", left_text.casefold())
        if (len(w) >= 2 or w.isdigit()) and w not in stopwords
    }
    right = {
        w
        for w in re.findall(r"\w+", right_text.casefold())
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
        for word in re.findall(r"\w+", str(chunk.get("text", "")).casefold())
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
