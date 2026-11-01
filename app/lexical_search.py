"""Single-responsibility module for lexical query analysis, FTS5 query construction,
and heading/catalogue heuristic checks (extracted from `app/searcher.py`).
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple


STOPWORDS = {
    "là", "gì", "thế", "nào", "sao", "ra", "như", "được", "có", "của", "trong", "ở", "về",
    "cho", "với", "theo", "và", "hoặc", "hay", "những", "các", "một", "này", "đó", "kia",
    "bởi", "tại", "khi", "để", "thì", "mà", "nếu", "bị", "do", "từ", "hãy", "nêu", "biết",
}


def clean_fts_query(query: str) -> str:
    """Sanitize query string for FTS5 syntax, avoiding syntax errors with special chars."""
    cleaned = re.sub(r'["\'\*\^\:\(\)\[\]\{\}\+\-\~]', " ", query)
    words = [w.strip() for w in cleaned.split() if w.strip()]
    if not words:
        return ""
    meaningful = [w for w in words if w.lower() not in STOPWORDS]
    target_words = meaningful if meaningful else words
    terms = [f'"{w}"*' for w in target_words]
    w_lower = [w.lower() for w in words]
    if "xạ" in w_lower and "tuyến" in w_lower:
        terms.extend(['"xạtuyến"*', '"sốtuyến"*'])
    return " OR ".join(terms)


def extract_document_scope_tokens(query: str) -> List[str]:
    """Extract document/chapter identifiers from queries specifying a target document."""
    patterns = [
        r"(?:trong\s+(?:tài\s+liệu|sách|giáo\s+trình|bài\s+giảng|chương))\s+([^\?\.\,\!]+)",
        r"(?:tài\s+liệu|sách|giáo\s+trình|bài\s+giảng|chương)\s+([0-9]+[^\?\.\,\!]*)",
    ]
    for pat in patterns:
        m = re.search(pat, query, re.I)
        if m:
            scope = m.group(1).strip()
            words = [
                w for w in re.findall(r"\w+", scope.lower())
                if w not in ("trong", "tài", "liệu", "sách", "giáo", "trình", "bài", "giảng")
            ]
            if words:
                return words
    return []


def extract_differential_operands(text: str) -> List[Tuple[str, str, int, Optional[str]]]:
    """Extract differential operands: (v_num, v_den, order, coef)."""
    pat = re.compile(
        r"(?:(?<=\s)|(?<=[(\[+\-*/=:<>])|^)([0-9]+|[a-zA-Zα-ωΑ-Ω])?\s*"
        r"d(?:\^?(\d+)|([¹²³⁴⁵⁶⁷⁸⁹]))?\s*([A-Za-z])\s*/\s*"
        r"d(?:\^?(\d+)|([¹²³⁴⁵⁶⁷⁸⁹]))?\s*([A-Za-z])(?:\^?(\d+)|([¹²³⁴⁵⁶⁷⁸⁹]))?(?![\w])"
    )
    sups = {"¹": 1, "²": 2, "³": 3, "⁴": 4, "⁵": 5, "⁶": 6, "⁷": 7, "⁸": 8, "⁹": 9}
    res: List[Tuple[str, str, int, Optional[str]]] = []
    for m in pat.finditer(text):
        coef, o1_dig, o1_sup, v_num, o2_dig, o2_sup, v_den, o3_dig, o3_sup = m.groups()
        ord_val = 1
        for val in (o1_dig, o1_sup, o2_dig, o2_sup, o3_dig, o3_sup):
            if not val:
                continue
            if val in sups:
                ord_val = sups[val]
                break
            if val.isascii() and val.isdigit():
                ord_val = int(val)
                break
        tup = (v_num, v_den, ord_val, coef)
        if tup not in res:
            res.append(tup)
    return res


def extract_query_concepts(query: str) -> List[str]:
    """Extract candidate concept phrases from query, handling intent frames, ellipsis, and comparisons."""
    concepts: List[str] = []

    m_ellipsis = re.search(
        r"([^\d\W]+(?:\s+[^\d\W]+)*)\s+(\d+)\s*(?:vs\.?|và|với|hoặc|-)\s*(\d+)",
        query,
        re.UNICODE,
    )
    if m_ellipsis:
        base = m_ellipsis.group(1).strip()
        num1 = m_ellipsis.group(2).strip()
        num2 = m_ellipsis.group(3).strip()
        p1 = f"{base} {num1}"
        p2 = f"{base} {num2}"
        if p1 not in concepts:
            concepts.append(p1)
        if p2 not in concepts:
            concepts.append(p2)

    words = re.findall(r"\w+", query, re.UNICODE)
    for index, word in enumerate(words):
        if not word.isdigit() or index < 2:
            continue
        prefix = words[index - 2 : index]
        if all(part.isalpha() for part in prefix):
            phrase = " ".join((*prefix, word))
            if phrase not in concepts:
                concepts.append(phrase)

    q = query.strip()
    q = re.split(r"\b(?:trong\s+(?:tài\s+liệu|sách|giáo\s+trình|bài\s+giảng|chương))\b", q, flags=re.I)[0].strip()
    q = re.sub(r"^(?:ở|trong|tại)\s+(?:phần|mục|chương|bài|chủ\s+đề)\s+[^,;:\n]+[,;:\n]\s*", "", q, flags=re.I).strip()

    compare_match = re.search(
        r"^(?:hãy\s+)?(?:so\s+sánh|đối\s+chiếu|phân\s+biệt|giải\s+thích\s+sự\s+khác\s+nhau|sự\s+khác\s+biệt|điểm\s+khác\s+biệt)\s+(?:giữa\s+)?(.*?)(?:\s+(?:khác\s+nhau|khác\s+gì|như\s+thế\s+nào|ra\s+sao)\??$|$)",
        q,
        flags=re.I,
    )
    if compare_match:
        target = compare_match.group(1).strip()
        for sep in [r"\s+và\s+", r"\s+với\s+", r"\s+vs\.?\s+", r"\s+versus\s+"]:
            parts = re.split(sep, target, maxsplit=1, flags=re.I)
            if len(parts) == 2:
                c1, c2 = parts[0].strip(), parts[1].strip()
                c1 = re.sub(r"[?,.]+$", "", c1).strip()
                c2 = re.sub(r"[?,.]+$", "", c2).strip()
                if c1 and c1 not in concepts:
                    concepts.append(c1)
                if c2 and c2 not in concepts:
                    concepts.append(c2)

    lead_pat = (
        r"^(?:hãy\s+)?(?:cho\s+biết|giải\s+thích|trình\s+bày|nêu|tìm\s+hiểu(?:\s+về)?|"
        r"định\s+nghĩa(?:\s+(?:về|của))?|khái\s+niệm(?:\s+(?:về|của))?|tính\s+chất(?:\s+(?:về|của))?|"
        r"đặc\s+điểm(?:\s+(?:về|của))?|nguyên\s+lý(?:\s+(?:về|của))?|ý\s+nghĩa(?:\s+(?:về|của))?|"
        r"nội\s+dung(?:\s+(?:về|của))?|quy\s+luật(?:\s+(?:về|của))?|quy\s+tắc(?:\s+(?:về|của))?|"
        r"công\s+thức(?:\s+(?:tính|của))?|cách\s+(?:tính|áp\s+dụng|sử\s+dụng)?|"
        r"lý\s+thuyết(?:\s+(?:về|của))?|học\s+thuyết(?:\s+(?:về|của))?|kiến\s+thức(?:\s+(?:về|của))?|"
        r"cơ\s+sở\s+lý\s+thuyết(?:\s+(?:về|của))?|tổng\s+quan(?:\s+(?:về|của))?)\s+"
    )
    q_clean = re.sub(lead_pat, "", q, flags=re.I).strip()
    q_clean = re.sub(
        r"^(?:(?:hãy|nêu|trình bày)\s+)*(?:\d+|hai|ba|bốn|năm|sáu)\s+"
        r"(?:tính\s+chất|đặc\s+điểm|nguyên\s+tắc|thành\s+phần)"
        r"(?:\s+(?:cơ\s+bản|cốt\s+lõi|chính|quan\s+trọng))?\s+của\s+",
        "",
        q_clean,
        flags=re.I,
    )

    trail_pat = (
        r"\s+(?:cho\s+biết|hãy\s+cho\s+biết|xác\s+định)\s+.*$|"
        r"\s+(?:là\s+gì|như\s+thế\s+nào|thế\s+nào|ra\s+sao|là\s+sao|nghĩa\s+là\s+gì|có\s+nghĩa\s+là\s+gì|"
        r"được\s+hiểu\s+như\s+thế\s+nào|được\s+định\s+nghĩa\s+(?:như\s+thế\s+nào|là\s+gì)|"
        r"được\s+tính\s+như\s+thế\s+nào|khác\s+nhau\s+(?:như\s+thế\s+nào|thế\s+nào|ra\s+sao)|"
        r"khác\s+gì(?:\s+nhau)?)[\?\.\!\s]*$"
    )
    q_clean = re.sub(trail_pat, "", q_clean, flags=re.I).strip(" ?.!,:")
    q_clean = re.sub(r"^(?:của|về)\s+", "", q_clean, flags=re.I).strip()

    if q_clean and len(q_clean) >= 2 and not q_clean.lower().startswith("so sánh"):
        if q_clean not in concepts:
            concepts.append(q_clean)

    if re.search(r"lập trình hướng đối tượng|\boop\b", q_clean, re.I):
        for alias in ("lập trình hướng đối tượng", "OOP"):
            if alias not in concepts:
                concepts.append(alias)

    for v_num, v_den, ord_val, _coef in extract_differential_operands(query):
        if ord_val == 2:
            d_concept = f"d²{v_num}/d{v_den}²"
        elif ord_val == 3:
            d_concept = f"d³{v_num}/d{v_den}³"
        else:
            d_concept = f"d{v_num}/d{v_den}"
        if d_concept not in concepts:
            concepts.append(d_concept)

    return concepts


def is_heading_or_toc_chunk(text: str) -> bool:
    """Detect if chunk is predominantly a table of contents, outline, or heading slide."""
    t = text.strip()
    if not t:
        return True
    tl = t.lower()
    lines = [line.strip() for line in t.split("\n") if line.strip()]
    if not lines:
        return True

    leader_pat = re.compile(r"((?:\.\s*){4,}|…{2,}|_{4,}|-{4,})\s*\d*\s*$")
    dot_leader_lines = sum(1 for line in lines if leader_pat.search(line))
    if dot_leader_lines >= 2:
        prose_lines = sum(1 for line in lines if len(line) > 80 and not leader_pat.search(line))
        if prose_lines < 2:
            return True

    has_toc_header = any(tl.startswith(prefix) for prefix in ("mục lục", "table of contents")) or (
        tl.startswith("nội dung") and any(w in tl for w in ("chương trình", "mục lục", "chi tiết", "bài giảng", "khóa học"))
    )
    if has_toc_header:
        substantive = [l for l in lines if len(l) > 60 and not re.match(r"^(\d+[\.\)]|[A-ZĐ\.\-\s]{3,})", l)]
        if not substantive or len(" ".join(substantive)) < 150:
            return True

    numbered_lines = sum(1 for line in lines if re.match(r"^\d+[\.\)]\s*", line))
    if len(lines) >= 3 and numbered_lines >= len(lines) * 0.6:
        has_substantive_math = any(c in t for c in "=><≈≠≤≥∫∑√") or bool(
            re.search(r"\b(?:d\s*[A-Za-z]\s*/\s*d?\s*[A-Za-z]|f′|lim_)\b", t)
        )
        if not has_substantive_math:
            long_prose = sum(1 for line in lines if len(line) > 80 and not leader_pat.search(line))
            if long_prose == 0:
                return True

    if len(lines) <= 6 and len(t) < 250:
        section_lines = sum(1 for line in lines if re.match(r"^(\d+(\.\d+)*\.?|[A-ZĐ\d\.\s\-]{3,30}$)", line))
        if section_lines > 0 and section_lines >= len(lines) - 1:
            return True

    return False


def check_local_definition_and_catalog(text: str, concepts: List[str]) -> Tuple[bool, bool, bool]:
    """Distinguish locally attached explanation/definition from remote catalogue cues."""
    t = text.strip()
    if not t:
        return False, False, False
    t_lower = t.lower()
    lines = [line.strip() for line in t.split("\n") if line.strip()]

    cat_header_patterns = [
        r"danh\s+sách\s+(?:bài\s+học|khóa\s+học|video|tài\s+nguyên|mục\s+lục)",
        r"(?:kế\s+hoạch|lịch\s+trình|lịch\s+học|tiến\s+trình)\s+(?:học\s+tập|giảng\s+dạy|chi\s+tiết)?",
        r"(?:video|bài\s+tập)\s+và\s+(?:lịch\s+học|tài\s+liệu)",
        r"tài\s+liệu\s+bổ\s+sung\s+và\s+lịch",
        r"playlist",
        r"slide\s+bài\s+giảng",
    ]
    has_cat_header = any(re.search(pat, t_lower) for pat in cat_header_patterns)
    schedule_lines = sum(
        1 for line in lines
        if re.match(r"^(?:tuần|buổi|tiết|bài|chương|lecture|week|session)\s+\d+[:\.\-]", line.lower())
    )
    is_catalogue = has_cat_header or (schedule_lines >= 2)

    target_concepts = [c.strip().lower() for c in concepts if c and c.strip()] if concepts else []

    def_cues = (
        "định nghĩa", "khái niệm", "được định nghĩa là", "được định nghĩa", "là giới hạn",
        "công thức", "nguyên lý", "ý nghĩa", "bản chất", "tính chất", "đặc điểm", "quy luật",
        "lý thuyết", "học thuyết", "hai điều kiện", "f(u + v)", "f(αu)", "f′(", "lim_",
        "definition", "meaning",
    )
    explanation_cues = (
        "giải thích", "điều kiện áp dụng", "thành phần", "nội dung", "mô tả",
        "được hiểu là", "tức là", "cụ thể", "chứng minh", "áp dụng cho",
        "cung cấp kiến thức", "biểu diễn", "nêu ý nghĩa", "nêu rõ",
    )

    locally_attached = False
    has_explanation = False

    if not target_concepts:
        if any(c in t_lower for c in ("được định nghĩa là", "định nghĩa:", "khái niệm:")):
            locally_attached = True
            has_explanation = any(c in t_lower for c in explanation_cues) or len(t) > 120
        return locally_attached, has_explanation, is_catalogue

    for concept in target_concepts:
        c_words = [w for w in re.findall(r"\w+", concept) if w not in STOPWORDS]
        if not c_words:
            continue

        for idx, line in enumerate(lines):
            line_lower = line.lower()
            has_concept_in_line = (concept in line_lower) or (
                len(c_words) >= 2 and all(w in line_lower for w in c_words)
            )
            if not has_concept_in_line:
                continue

            is_schedule_line = bool(re.match(r"^(?:tuần|buổi|tiết|lecture|week)\s+\d+", line_lower))

            if any(cue in line_lower for cue in def_cues):
                locally_attached = True
                surrounding = " ".join(lines[idx : idx + 3]).lower()
                if any(ec in surrounding for ec in explanation_cues) or len(surrounding) > 100:
                    has_explanation = True
                break

            if not is_schedule_line:
                for next_idx in (idx + 1, idx + 2):
                    if next_idx < len(lines):
                        next_line_lower = lines[next_idx].lower()
                        if re.match(r"^(?:tuần|buổi|tiết|lecture|week)\s+\d+", next_line_lower):
                            break
                        if any(cue in next_line_lower for cue in def_cues):
                            locally_attached = True
                            body_lines = [lines[j] for j in range(next_idx, min(len(lines), next_idx + 4))]
                            body_text = " ".join(body_lines).lower()
                            if any(ec in body_text for ec in explanation_cues) or len(body_text) > 80:
                                has_explanation = True
                            break
            if locally_attached:
                break

            if not is_schedule_line:
                pos = t_lower.find(concept)
                while pos != -1:
                    window_start = max(0, pos - 120)
                    window_end = min(len(t_lower), pos + len(concept) + 160)
                    window = t_lower[window_start:window_end]
                    if not re.search(r"(?:tuần|buổi|tiết)\s+\d+", window):
                        if any(cue in window for cue in def_cues):
                            locally_attached = True
                            if any(ec in window for ec in explanation_cues) or len(t) > 120:
                                has_explanation = True
                            break
                    pos = t_lower.find(concept, pos + 1)
            if locally_attached:
                break

    return locally_attached, has_explanation, is_catalogue
