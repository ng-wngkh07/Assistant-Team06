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
}
QUERY_STOPWORDS = _BASE_QUERY_STOPWORDS | {_normalize_stopword(w) for w in _BASE_QUERY_STOPWORDS if _normalize_stopword(w)}

_BASE_DIRECTIVE_STOPWORDS = {
    "nêu", "hãy", "cho", "biết", "tìm", "trình", "bày", "giải", "thích",
    "liệt", "kê", "mô", "tả", "hai", "ba", "bốn", "năm", "các", "những",
    "một", "mấy", "tài", "liệu", "chất", "sách", "chương", "cuốn", "trang",
    "từng", "bước", "thật", "rõ", "ràng", "tóm", "tắt", "ngắn", "gọn",
    "chi", "tiết", "cụ", "thể", "so", "sánh", "đối", "chiếu", "phân", "biệt",
}
DIRECTIVE_STOPWORDS = _BASE_DIRECTIVE_STOPWORDS | {
    _normalize_stopword(w) for w in _BASE_DIRECTIVE_STOPWORDS if _normalize_stopword(w)
}


def is_broad_query(query: str) -> bool:
    """Detect whether a query seeks an overview, multi-theory comparison, or broad synthesis."""
    q = query.casefold()
    broad_indicators = [
        "tổng quan", "các lý thuyết", "những lý thuyết", "so sánh", "phân loại",
        "các yếu tố", "những yếu tố", "các kiểu", "những kiểu", "các giai đoạn",
        "ảnh hưởng như thế nào đến", "mối liên hệ giữa", "overview", "compare",
        "theories", "khác gì với", "điểm khác biệt", "nguyên lý và", "vai trò của",
        "và ứng dụng", "những cơ chế", "những sai lầm",
    ]
    if any(ind in q for ind in broad_indicators):
        return True
    words = [w for w in re.findall(r"\w+", q) if len(w) >= 2]
    return len(words) >= 10 and (" và " in q or " giữa " in q)


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


def extract_compare_concepts(query: str, is_legacy_compare: bool = False) -> Optional[Tuple[str, str]]:
    """Extract two distinct concepts for compare mode, or None if not an explicit compare intent."""
    if not is_legacy_compare and not re.search(
        r"\b(?:so\s+sánh|đối\s+chiếu|phân\s+biệt|khác\s+nhau|khác\s+gì|điểm\s+khác|versus|\bvs\.?\b)\b",
        query,
        flags=re.I,
    ):
        return None

    q = query.strip()
    q = re.split(r"\b(?:trong cuốn|trong sách|theo tài liệu|theo sách)\b", q, maxsplit=1, flags=re.I)[0].strip()
    prefix_pattern = r"^(?:hãy\s+)?(?:so\s+sánh|đối\s+chiếu|phân\s+biệt|giải\s+thích\s+sự\s+khác\s+nhau|sự\s+khác\s+biệt|điểm\s+khác\s+biệt)\s+(?:giữa\s+)?"
    q_cleaned = re.sub(prefix_pattern, "", q, flags=re.I).strip()
    if q_cleaned.lower().startswith("giữa "):
        q_cleaned = q_cleaned[5:].strip()

    suffix_pattern = r"\s+(?:khác\s+nhau(?:\s+thế\s+nào|\s+như\s+thế\s+nào|\s+ra\s+sao)?|khác\s+gì(?:\s+nhau)?|có\s+gì\s+khác\s+nhau|như\s+thế\s+nào|ra\s+sao)\??$"
    q_cleaned = re.sub(suffix_pattern, "", q_cleaned, flags=re.I).strip()

    split_patterns = [
        r"\s+và\s+",
        r"\s+với\s+",
        r"\s+vs\.?\s+",
        r"\s+versus\s+",
        r"\s+khác\s+gì\s+",
        r"\s+khác\s+nhau\s+với\s+",
    ]
    for pat in split_patterns:
        parts = re.split(pat, q_cleaned, maxsplit=1, flags=re.I)
        if len(parts) == 2:
            c1 = parts[0].strip()
            c2 = parts[1].strip()
            c1 = re.sub(r"[?,.]+$", "", c1).strip()
            c2 = re.sub(r"[?,.]+$", "", c2).strip()
            if c1 and c2 and (c1.casefold() != c2.casefold() or c1 != c2):
                return (c1, c2)
    return None
