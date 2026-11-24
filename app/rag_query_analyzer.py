"""Single-responsibility module for RAG query stopword normalization, comparison intent
extraction, broad query detection, and answer-mode instructions (extracted from `app/rag_agent.py`).
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple


def _normalize_stopword(word: str) -> str:
    text = unicodedata.normalize("NFC", str(word or "")).casefold()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.replace("đ", "d").replace("_", " ")).strip()


_BASE_QUERY_STOPWORDS = {
    "của", "và", "trong", "theo", "như", "thế", "nào", "được", "là", "cho",
    "về", "giữa", "gì", "tại", "sao", "bao", "nhiêu", "the", "and", "what",
    "how", "such", "with", "có", "hay", "không", "ở", "này", "đó", "kia",
    "is", "are", "was", "were", "be", "do", "does", "did", "can", "could",
    "of", "in", "on", "to", "for", "from", "into", "about", "between",
    "this", "that", "these", "those", "which", "when", "where", "why", "who",
    "an", "at", "by", "as", "or", "it", "its", "vs", "versus",
}
QUERY_STOPWORDS = _BASE_QUERY_STOPWORDS | {_normalize_stopword(w) for w in _BASE_QUERY_STOPWORDS if _normalize_stopword(w)}

_BASE_DIRECTIVE_STOPWORDS = {
    "nêu", "hãy", "cho", "biết", "tìm", "trình", "bày", "giải", "thích",
    "liệt", "kê", "mô", "tả", "hai", "ba", "bốn", "năm", "các", "những",
    "một", "mấy", "tài", "liệu", "chất", "sách", "chương", "cuốn", "trang",
    "từng", "bước", "thật", "rõ", "ràng", "tóm", "tắt", "ngắn", "gọn",
    "chi", "tiết", "cụ", "thể", "so", "sánh", "đối", "chiếu", "phân", "biệt",
    "thao", "tác", "tên", "gọi",
    "explain", "define", "describe", "compare", "contrast", "difference",
    "differences", "distinguish", "list", "summarize", "summary", "overview",
    "give", "meaning", "definition", "concept", "concepts", "example",
    "examples", "please", "tell", "show", "state", "step", "steps",
    "briefly", "short", "detail", "details", "detailed",
}
DIRECTIVE_STOPWORDS = _BASE_DIRECTIVE_STOPWORDS | {
    _normalize_stopword(w) for w in _BASE_DIRECTIVE_STOPWORDS if _normalize_stopword(w)
}


def is_broad_query(query: str) -> bool:
    """Detect whether a query seeks an overview, multi-theory comparison, or broad synthesis."""
    q = query.casefold()
    broad_indicators = [
        "tổng quan", "tóm tắt", "tổng hợp",
        "các lý thuyết", "những lý thuyết",
        "các nguyên lý", "những nguyên lý",
        "các đặc điểm", "những đặc điểm",
        "các phương pháp", "những phương pháp",
        "so sánh", "phân biệt", "phân loại",
        "các yếu tố", "những yếu tố", "các kiểu", "những kiểu", "các giai đoạn",
        "ảnh hưởng như thế nào đến", "tác động qua lại",
        "mối liên hệ giữa", "mối quan hệ giữa", "tương quan giữa",
        "khác biệt giữa", "khác nhau giữa", "sự khác nhau giữa",
        "khác nhau thế nào", "khác nhau như thế nào", "giống và khác",
        "overview", "compare",
        "theories", "khác gì với", "điểm khác biệt", "nguyên lý và", "vai trò của",
        "và ứng dụng", "những cơ chế", "những sai lầm",
    ]
    return any(ind in q for ind in broad_indicators)


def extract_query_facets(query: str, search_query: str) -> List[str]:
    """Generate multi-aspect search queries for broad synthesis questions."""
    facets = [search_query]
    q = query.casefold()
    if any(k in q for k in ["lý thuyết trò chơi", "game theory"]):
        facets.extend([
            "lý thuyết trò chơi khái niệm nền tảng tác nhân chiến lược",
            "phân loại trò chơi tĩnh trò chơi động đa bước",
            "thế lưỡng nan của tù nhân hợp tác và ví dụ",
            "giới hạn tác nhân lý trí tâm lý học con người",
        ])
    elif any(k in q for k in ["liên tưởng", "hậu quả", "điều kiện hóa", "củng cố", "trừng phạt"]):
        facets.extend([
            "điều kiện hóa cổ điển liên tưởng kích thích phản xạ",
            "điều kiện hóa thao tác skinner hậu quả hành vi",
            "phân biệt củng cố dương tính và củng cố âm tính",
            "trừng phạt dương tính và trừng phạt âm tính ví dụ",
        ])
    elif "so sánh" in q or "khác gì" in q or "điểm khác biệt" in q:
        parts = [
            p.strip()
            for p in re.split(r"so sánh|khác gì|điểm khác biệt|với|và|giữa", query, flags=re.I)
            if len(p.strip()) > 3
        ]
        for p in parts[:3]:
            facets.append(f"{p} đặc điểm nguyên lý")
    else:
        facets.extend([
            f"{search_query} khái niệm định nghĩa nền tảng",
            f"{search_query} cơ chế phân loại so sánh",
            f"{search_query} ứng dụng ví dụ giới hạn",
        ])
    return facets


MODE_ALIASES = {
    "quick": "quick",
    "summary": "quick",
    "tóm tắt": "quick",
    "tóm tắt nhanh": "quick",
    "steps": "steps",
    "step_by_step": "steps",
    "từng bước": "steps",
    "giải thích từng bước": "steps",
    "compare": "compare",
    "comparison": "compare",
    "so sánh": "compare",
    "so sánh hai khái niệm": "compare",
}


def normalize_answer_mode(mode: Optional[str]) -> str:
    if not mode:
        return "quick"
    return MODE_ALIASES.get(str(mode).lower().strip(), "quick")


def answer_mode_instruction(
    answer_mode: str, compare_concepts: Optional[Tuple[str, str]] = None
) -> str:
    """Return prompt guidance; default QA returns full conditions without forcing bullet/step counts."""
    if compare_concepts:
        c1, c2 = compare_concepts
        return (
            f"Nhiệm vụ so sánh: Đối chiếu và so sánh các điểm tương đồng, khác biệt giữa «{c1}» và «{c2}» "
            "dựa trên các đoạn tài liệu được cung cấp. Nêu rõ tiêu chí đối chiếu và giới hạn nếu nguồn chỉ hỗ trợ một phần."
        )
    return (
        "Yêu cầu trả lời: Trình bày câu trả lời đầy đủ, trực tiếp bám sát các đoạn tài liệu được cung cấp trong <context>; "
        "giữ nguyên mọi điều kiện áp dụng, giả thiết và mức độ chắc chắn của nguồn; không suy diễn hay đưa kiến thức ngoài nguồn."
    )


COMPARE_SCOPE_SPLIT_RE = (
    r"\b(?:trong\s+cuốn|trong\s+sách|trong\s+tài\s+liệu|theo\s+tài\s+liệu|theo\s+sách|"
    r"của\s+tài\s+liệu|trên\s+tài\s+liệu|trong\s+giáo\s+trình|theo\s+giáo\s+trình|"
    r"trong\s+bài\s+giảng|theo\s+bài\s+giảng|trong\s+slide|theo\s+slide)\b"
)


def _strip_parenthetical_gloss(concept: str) -> str:
    """Drop trailing/inline parenthetical glosses (4+ chars after space) while preserving short markers or f(x)."""
    stripped = re.sub(r"\s+\([^()]{4,}\)", "", concept).strip()
    return stripped if len(stripped) >= 2 else concept


def extract_compare_concepts(query: str, is_legacy_compare: bool = False) -> Optional[Tuple[str, str]]:
    """Extract two distinct concepts for compare mode, or None if not an explicit compare intent."""
    if not is_legacy_compare and not re.search(
        r"\b(?:so\s+sánh|đối\s+chiếu|phân\s+biệt|khác\s+nhau|khác\s+gì|điểm\s+khác|"
        r"compare|contrast|distinguish|differences?\s+between|versus|\bvs\.?\b)\b",
        query,
        flags=re.I,
    ):
        return None

    q = query.strip()
    q = re.split(COMPARE_SCOPE_SPLIT_RE, q, maxsplit=1, flags=re.I)[0].strip()
    # Strip trailing pitfall/purpose/condition/criteria clauses before binary splitting
    q = re.sub(
        r"\s+(?:(?:để|nhằm)\s+tránh|tránh\s+ngộ\s+nhận|tránh\s+nhầm|tránh\s+đánh\s+đồng|(?:cùng|và|kèm)\s+bẫy|"
        r"(?:bằng|dựa\s+trên|theo)\s+(?:định\s+nghĩa|tiêu\s+chí|phương\s+pháp|cách|nguyên\s+lý)|"
        r"by\s+definition(?:\s+of)?|"
        r"(?:and\s+)?(?:avoid\s+(?:confusing|conflating|mixing\s+up)|to\s+avoid\s+(?:confusing|confusion)))\b.*$",
        "",
        q,
        flags=re.I,
    ).strip()
    # Strip trailing parenthetical caveat at sentence end before suffix stripping
    q = re.sub(r"\s*\([^()]{8,}\)\s*([?!.]*)$", r"\1", q).strip()
    if ";" in q:
        return None
    if re.search(r"\s+(?:với|vs\.?)\s+bẫy\b", q, flags=re.I):
        return None
    has_distinguish_verb = bool(re.search(r"\b(?:distinguish|differentiate|phân\s+biệt)\b", q, flags=re.I))
    prefix_pattern = (
        r"^(?:hãy\s+|vui\s+lòng\s+|please\s+)?"
        r"(?:(?:trình\s+bày|giải\s+thích|nêu|mô\s+tả|định\s+nghĩa|cho\s+biết|phân\s+tích|explain|what\s+(?:is|are)(?:\s+the)?)\s+(?:(?:và|and)\s+)?)?"
        r"(?:(?:so\s+sánh|đối\s+chiếu|phân\s+biệt|compare|contrast|distinguish|differentiate|(?:the\s+)?differences?)"
        r"(?:\s+(?:ngắn\s+gọn|nhanh|chi\s+tiết|cụ\s+thể|tóm\s+tắt))?"
        r"(?:\s+(?:(?:sự|những|các)\s+)?(?:điểm\s+)?(?:giống(?:\s+nhau)?(?:\s+và\s+khác(?:\s+nhau)?)?|khác\s+nhau|khác\s+biệt))?"
        r"|giải\s+thích\s+(?:sự|điểm)\s+khác\s+(?:nhau|biệt)"
        r"|(?:sự|điểm)\s+khác\s+(?:nhau|biệt))\s*(?:(?:giữa|between)\s+)?"
    )
    q_cleaned = re.sub(prefix_pattern, "", q, flags=re.I).strip()
    if q_cleaned.lower().startswith("giữa "):
        q_cleaned = q_cleaned[5:].strip()
    elif q_cleaned.lower().startswith("between "):
        q_cleaned = q_cleaned[8:].strip()
    else:
        # Handle "So sánh <aspect> giữa <C1> và <C2>" or "Compare <aspect> between <C1> and <C2>"
        between_match = re.search(r"\s+(?:giữa|between)\s+(.+)$", q_cleaned, flags=re.I)
        if between_match and re.search(r"\s+(?:và|với|and|vs\.?|versus)\s+", between_match.group(1), flags=re.I):
            q_cleaned = between_match.group(1).strip()
    q_cleaned = re.sub(r"^(?:khái\s+niệm|the\s+concepts?\s+of)\s+", "", q_cleaned, flags=re.I).strip()

    suffix_pattern = (
        r"(?:\s+trong\s+[^?.,;]{1,35})?(?:\s+có\s+(?:vai\s+trò|chức\s+năng|tác\s+dụng))?"
        r"\s+(?:(?:giống(?:\s+nhau)?\s+và\s+)?khác\s+nhau"
        r"(?:\s+(?:thế\s+nào|như\s+thế\s+nào|ra\s+sao|gì|nào))?"
        r"(?:\s+(?:ở|về)(?:\s+(?:điểm|chỗ|mặt|cách))?(?:\s+\S+){0,8})?"
        r"|khác\s+gì(?:\s+nhau)?|có\s+gì\s+khác\s+nhau|như\s+thế\s+nào|ra\s+sao|là\s+gì"
        r"|differ|different)\??$"
    )
    q_cleaned = re.sub(suffix_pattern, "", q_cleaned, flags=re.I).strip()
    q_cleaned = re.sub(
        r"\s+(?:khi\s+(?:truyền|áp\s+dụng|sử\s+dụng|dùng)|when\s+(?:passing|using|applying))\b.*$",
        "",
        q_cleaned,
        flags=re.I,
    ).strip()

    split_patterns = [
        r"\s+và\s+",
        r"\s+với\s+",
        r"\s+vs\.?\s+",
        r"\s+versus\s+",
        r"\s+khác\s+gì\s+",
        r"\s+khác\s+nhau\s+với\s+",
        r"\s+and\s+",
        r"\s+with\s+",
        *([r"\s+from\s+"] if has_distinguish_verb else []),
    ]
    _nested_compare_re = re.compile(
        r"(?:\s+(?:với|vs\.?|versus)\s+|\b(?:so\s+sánh|phân\s+biệt|compare|contrast|distinguish)\b)",
        flags=re.I,
    )
    for pat in split_patterns:
        parts = re.split(pat, q_cleaned, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            c1 = re.sub(r"[?,.]+$", "", parts[0].strip()).strip()
            c2 = re.sub(r"[?,.]+$", "", parts[1].strip()).strip()
            c1 = _strip_parenthetical_gloss(c1)
            c2 = _strip_parenthetical_gloss(c2)
            # 3+ item comparisons or compound sub-comparisons are broad syntheses, not binary 2-concept splits
            if "," in c1 or "," in c2 or _nested_compare_re.search(c1) or _nested_compare_re.search(c2):
                return None
            if c1 and c2 and (c1.casefold() != c2.casefold() or c1 != c2):
                return (c1, c2)
    return None
