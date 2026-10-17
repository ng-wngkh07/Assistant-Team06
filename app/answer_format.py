"""Keep private reasoning and source markers out of the user-facing answer."""

import re


_THINK = re.compile(r"<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", re.I | re.S)
_FINAL = re.compile(r"<\s*final\s*>(.*?)<\s*/\s*final\s*>", re.I | re.S)
_SOURCE = re.compile(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", re.I)
_BOOK_SOURCE = re.compile(r"\[[^\[\]\n]{2,160},\s*Trang\s+\d+\]", re.I)
_SOURCE_SECTION = re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:nguồn(?: tham khảo)?|trích dẫn|tài liệu tham khảo|sources?|references?)\s*:\s*.*(?:\n(?:\s*[-*].*|\s*\[S\d+\].*)?)*$")
_SOURCE_INTRO = re.compile(r"(?i)\b(?:theo|trong|ở|tại)\s+(?:các\s+)?(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]\s*,?\s*")
_MATH_START = re.compile(r"^[a-z]\s*(?:[\(=+\-*/^<>]|==|!=)")


def _drop_source_intro(match: re.Match) -> str:
    """Remove a source intro; re-capitalize the sentence it started (not math identifiers)."""
    before = match.string[:match.start()].rstrip()
    rest = match.string[match.end():]
    if (not before or before[-1] in ".!?\n") and rest[:1].islower() and not _MATH_START.match(rest):
        return "\x00"  # marker: capitalize next char
    return ""


def clean_answer(text: str) -> str:
    text = _THINK.sub("", text)
    final = _FINAL.search(text)
    if final:
        text = final.group(1)
    text = re.sub(r"(?is)^.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", "", text)
    text = re.sub(r"(?im)^\s*(?:#{1,6}\s*)?(?:câu trả lời(?: cuối cùng)?|final answer)\s*:\s*", "", text)
    text = re.sub(r"(?im)^\s*(?:[-*]\s*)?(?:nêu dữ kiện có nguồn|giải thích suy luận(?: rút ra từ các dữ kiện)?|mức độ chắc chắn|giới hạn(?: hoặc điểm chưa có chứng cứ)?|tóm lại)\s*:\s*", "", text)
    text = _SOURCE_SECTION.sub("", text)
    text = _SOURCE_INTRO.sub(_drop_source_intro, text)
    text = re.sub(r"\x00(.)", lambda m: m.group(1).upper(), text)
    text = re.sub(r"(?i)(?:các\s+)?(?:thông tin|nội dung|dữ liệu)\s+(?:này\s+)?(?:được\s+)?(?:trích dẫn|lấy|thu thập)\s+từ\s*(?:các\s+)?(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]\s*\.?\s*", "", text)
    text = re.sub(r"(?i)(?:các\s+)?(?:thông tin|nội dung|dữ liệu)\s+(?:này\s+)?(?:được\s+)?(?:trích dẫn|lấy|thu thập)\s+từ\s*\.?\s*", "", text)
    text = re.sub(r"(?i)(?:,\s*)?(?:như\s+)?(?:được\s+)?(?:mô tả|trình bày|nêu|ghi|chỉ ra|đề cập)\s+(?:trong|ở|tại)\s*(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", "", text)
    text = _SOURCE.sub("", text)
    text = _BOOK_SOURCE.sub("", text)
    text = re.sub(r"(?i)(?:,\s*)?(?:như\s+)?(?:được\s+)?(?:mô tả|trình bày|nêu|ghi|chỉ ra|đề cập)\s+(?:trong|ở|tại)\s*(?:đoạn|nguồn|tài liệu)?\s*(?=[.!?;]|$)", "", text)
    text = re.sub(r"\bTầm Thân\b", "cái bóng", text, flags=re.I)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if text and re.match(r"^[a-z]\s*(?:[\(=+\-*/^<>]|==|!=)", text):
        return text
    return text[:1].upper() + text[1:] if text else ""


def _clean_claim_text(text: str) -> str:
    claim = _SOURCE_INTRO.sub("", text)
    claim = re.sub(r"(?i)(?:các\s+)?(?:thông tin|nội dung|dữ liệu)\s+(?:này\s+)?(?:được\s+)?(?:trích dẫn|lấy|thu thập)\s+từ\s*(?:các\s+)?(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]\s*\.?\s*", "", claim)
    claim = re.sub(r"(?i)(?:,\s*)?(?:như\s+)?(?:được\s+)?(?:mô tả|trình bày|nêu|ghi|chỉ ra|đề cập)\s+(?:trong|ở|tại)\s*(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", "", claim)
    claim = _SOURCE.sub("", claim)
    claim = _BOOK_SOURCE.sub("", claim)
    claim = re.sub(r"(?i)(?:các\s+)?(?:thông tin|nội dung|dữ liệu)\s+(?:này\s+)?(?:được\s+)?(?:trích dẫn|lấy|thu thập)\s+từ\s*\.?\s*", "", claim)
    claim = re.sub(r"(?i)(?:,\s*)?(?:như\s+)?(?:được\s+)?(?:mô tả|trình bày|nêu|ghi|chỉ ra|đề cập)\s+(?:trong|ở|tại)\s*(?:đoạn|nguồn|tài liệu)?\s*(?=[.!?;]|$)", "", claim)
    claim = re.sub(r"^\s*(?:[-*•]|\d+[.)]|[a-zA-Z][.)])\s*", "", claim)
    # A bare intro left after marker removal ("Trong đoạn ,") needs a source noun;
    # ordinary sentences such as "Trong trường hợp..." must be kept.
    claim = re.sub(r"(?i)^\s*(?:theo|trong|ở|tại|căn cứ vào|dựa vào)\s+(?:các\s+)?(?:đoạn|nguồn|tài liệu)\s*(?:,\s*|$)", "", claim)
    claim = re.sub(r"(?i)^\s*(?:theo|trong|ở|tại|căn cứ vào|dựa vào)\s*$", "", claim)
    claim = re.sub(r"\s+([,.;:!?])", r"\1", claim)
    claim = re.sub(r"[ \t]{2,}", " ", claim)
    claim = claim.strip(" \t\r\n-*•;,")
    return claim


def _is_substantive(text: str) -> bool:
    cleaned = _clean_claim_text(text)
    if not cleaned:
        return False
    if re.fullmatch(r"^(?:[-*•]|\d+[.)]|[a-zA-Z][.)])$", cleaned):
        return False
    if re.fullmatch(r"^(?:theo|trong|ở|tại|căn cứ vào|dựa vào|nguồn|đoạn|tài liệu|chỉ dẫn trong nguồn)$", cleaned, re.I):
        return False
    words = re.findall(r"\w+", cleaned, re.UNICODE)
    meaningful = [w for w in words if w.casefold() not in {"trong", "đoạn", "theo", "nguồn", "ở", "tại", "tài", "liệu", "là", "và", "của"}]
    has_math = any(c in "=<>+*/^√∑∫π≠≤≥()" for c in cleaned)
    return bool(meaningful or has_math)


def extract_citation_claims(raw_text: str, valid_source_ids: set[str] | list[str] | None = None) -> dict[str, list[str]]:
    """Extract model-attributed statement claims near each visible [S#] marker.

    Claims are derived ONLY from the visible final answer (ignoring private
    reasoning/think/analysis tags and source/reference sections).
    Grouped citations like [S1, S2] map the claim to all specified valid IDs.
    Returns a dict mapping source_id (e.g. 'S1') to a list of clean claim strings.
    """
    if not raw_text or not isinstance(raw_text, str):
        return {}

    # 1. Isolate visible text: remove private reasoning and trailing references
    text = _THINK.sub("", raw_text)
    final = _FINAL.search(text)
    if final:
        text = final.group(1)
    text = re.sub(r"(?is)^.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", "", text)
    text = _SOURCE_SECTION.sub("", text)
    text = re.sub(r"(?im)^\s*(?:#{1,6}\s*)?(?:câu trả lời(?: cuối cùng)?|final answer)\s*:\s*", "", text)

    val_set = set(valid_source_ids) if valid_source_ids is not None else None
    claims_by_id: dict[str, list[str]] = {}

    # 2. Split into paragraphs, sentences, and sub-clauses
    paragraphs = re.split(r"\n+", text)
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        sentences = re.split(r"(?<=[^0-9]\.)\s+(?!\[\s*s\d)|[!?]+\s+(?!\[\s*s\d)", p, flags=re.I)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            sub_clauses = re.split(
                r"(?<=\])\s*(?:;\s*|,\s*(?=[^\[\]]*\[\s*s\d+)|(?:,\s*)?(?:và|hoặc|nhưng|còn|đồng thời|trong khi)\s+)",
                sent,
                flags=re.I,
            )
            for clause in sub_clauses:
                clause_str = clause.strip()
                if not clause_str:
                    continue
                matches = list(_SOURCE.finditer(clause_str))
                if not matches:
                    continue
                for idx, m in enumerate(matches):
                    matched_ids = []
                    for num in re.findall(r"\d+", m.group(0)):
                        s_id = f"S{int(num)}"
                        if val_set is None or s_id in val_set:
                            if s_id not in matched_ids:
                                matched_ids.append(s_id)
                    if not matched_ids:
                        continue

                    prev_end = matches[idx - 1].end() if idx > 0 else 0
                    next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(clause_str)
                    before_text = clause_str[prev_end : m.start()]
                    after_text = clause_str[m.end() : next_start]

                    if _is_substantive(before_text):
                        chosen = before_text
                    elif _is_substantive(after_text):
                        chosen = after_text
                    else:
                        chosen = clause_str

                    claim = _clean_claim_text(chosen)
                    if not claim or not _is_substantive(claim):
                        continue
                    for s_id in matched_ids:
                        claims_by_id.setdefault(s_id, [])
                        if claim not in claims_by_id[s_id]:
                            claims_by_id[s_id].append(claim)

    return claims_by_id
