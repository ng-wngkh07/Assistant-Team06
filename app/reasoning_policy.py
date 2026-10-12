"""Reasoning and verification policy for study agent admissions (YC195).

Implements application-level processing levels:
- standard: 1 model call, max output 900
- careful: 1 draft call (max 1100) + 1 factual auditor call (max 600)
- auto: careful for comparison/synthesis, standard for definitions
"""

import html
import json
import re
from typing import Optional, Literal, Dict, Any, List, Tuple

ProcessingLevel = Literal["auto", "standard", "careful"]

COMPARISON_SYNTHESIS_PATTERN = re.compile(
    r"\b(?:so\s+sánh|đối\s+chiếu|phân\s+biệt|tổng\s+hợp|mối\s+liên\s+hệ|quan\s+hệ\s+giữa|điểm\s+giống\s+và\s+khác)\b",
    re.IGNORECASE,
)


def resolve_processing_level(
    level: str,
    query: str,
    compare_concepts: Optional[Tuple[str, str]] = None,
) -> Literal["standard", "careful"]:
    """Resolve user-selected or auto processing level deterministically."""
    level_lower = (level or "auto").lower()
    if level_lower == "standard":
        return "standard"
    if level_lower == "careful":
        return "careful"
    # auto: choose careful for comparisons or multi-concept synthesis, standard for definitions
    if compare_concepts is not None:
        return "careful"
    if COMPARISON_SYNTHESIS_PATTERN.search(query):
        return "careful"
    return "standard"


def _escape_data_content(text: str) -> str:
    """Broad HTML/XML escaping of &, <, > so untrusted data cannot break XML delimiters."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_context_data(text: str) -> str:
    """Escape data inside context so untrusted source data cannot create raw markup tags."""
    if not text:
        return ""
    clean = re.sub(r"(?is)^\s*<\s*context\s*>", "", text)
    clean = re.sub(r"(?is)<\s*/\s*context\s*>\s*$", "", clean)
    clean = clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return clean.strip()


def normalize_statement(text: str) -> str:
    """Controlled statement normalization for exact identity comparison.

    Preserves: word order, operators, numerals, decimals, quantifiers, exact casing.
    Normalizes: citation markers, markdown list/style markup, whitespace, outer punctuation.
    """
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", text)
    # Strip markdown code spans and stray backticks
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("`", "")
    # Strip markdown bold formatting
    text = re.sub(r"\*{3}([^*]+)\*{3}", r"\1", text)
    text = re.sub(r"\*{2}([^*]+)\*{2}", r"\1", text)
    text = re.sub(r"_{2}([^_]+)_{2}", r"\1", text)
    # Strip markdown italic with word/space boundaries to avoid math operators or intraword underscores
    text = re.sub(r"(?<!\w)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)", r"\1", text)
    text = re.sub(r"[\s\u00a0\u2000-\u200b]+", " ", text).strip()
    text = text.strip("\"'“”‘’«».,;:!? ")
    return text


def extract_draft_claims(draft: str) -> List[str]:
    """Extract individual statements/claims from draft answer without dropping short formulas or math."""
    if not draft:
        return []
    clean = re.sub(r"(?is)<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", "", draft)
    clean = re.sub(r"(?is)^.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", "", clean)
    final_match = re.search(r"<\s*final\s*>(.*?)<\s*/\s*final\s*>", clean, re.DOTALL | re.IGNORECASE)
    if final_match:
        clean = final_match.group(1)
    clean = re.sub(r"(?im)^\s*(?:#{1,6}\s*)?(?:nguồn(?: tham khảo)?|trích dẫn|tài liệu tham khảo|sources?|references?)\s*:\s*.*$", "", clean)
    clean = re.sub(r"(?im)^\s*(?:#{1,6}\s*)?(?:câu trả lời(?: cuối cùng)?|final answer)\s*:\s*", "", clean)
    clean = re.sub(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", "", clean, flags=re.IGNORECASE)

    lines = [l.strip() for l in clean.splitlines() if l.strip()]
    claims = []
    for line in lines:
        if line.startswith("|") and line.endswith("|") and set(line.replace("|", "").strip()) <= {"-", ":"}:
            continue
        parts = re.split(r"(?<=[^0-9]\.)\s+|(?<=[0-9]\.)(?!\d)\s+|[!?]+(?:\s+|$)", line)
        for p in parts:
            p = p.strip()
            if p and any(c.isalnum() or c in "+-=<>/*^√∑∫π≠≤≥" for c in p):
                claims.append(p)
    return claims


def check_claim_coverage(draft: str, verified_claims: List[Dict[str, Any]]) -> bool:
    """Exact normalized identity coverage check without word-overlap thresholds or substring matching."""
    if not draft:
        return True
    draft_statements = extract_draft_claims(draft)
    if not draft_statements:
        return False

    supported_claim_norms = {
        normalize_statement(c.get("statement", ""))
        for c in verified_claims
        if c.get("verdict") == "supported"
    }

    for stmt in draft_statements:
        norm_s = normalize_statement(stmt)
        if not norm_s:
            continue
        if norm_s in supported_claim_norms:
            continue
        # Check sub-clauses for compound statements
        clauses = re.split(r"(?:,\s*|\s+)(?:và|đồng\s+thời|nhưng|hoặc)\s+", stmt)
        norm_clauses = [normalize_statement(c) for c in clauses if normalize_statement(c)]
        if len(norm_clauses) > 1 and all(nc in supported_claim_norms for nc in norm_clauses):
            continue
        return False
    return True


def build_auditor_messages(
    query: str,
    draft_answer: str,
    context_block: str,
) -> List[Dict[str, str]]:
    """Build auditor prompt messages (single user message) to verify draft answer against admitted context only."""
    draft_claims = extract_draft_claims(draft_answer)
    claims_list_text = ""
    if draft_claims:
        claims_list_text = "\n\nNhận định cần kiểm tra (copy nguyên văn từng nhận định vào \"statement\"):\n" + "\n".join(
            f"{idx}. {_escape_data_content(c)}" for idx, c in enumerate(draft_claims, 1)
        )

    auditor_instruction = (
        "Đối chiếu <draft_answer> với tài liệu trong <context> để kiểm tra tính xác thực:\n"
        "1. Không dùng kiến thức ngoài <context>. Rà soát từng nhận định theo mã nguồn [S#].\n"
        "2. evidence_quote phải là chuỗi con liên tục lấy từ tài liệu nguồn tương ứng.\n"
        "3. Xuất DUY NHẤT định dạng JSON hợp lệ, không thêm văn bản ngoài JSON:\n"
        "{\n"
        '  "claims": [\n'
        '    {\n'
        '      "statement": "nội dung nhận định (copy nguyên văn)",\n'
        '      "source_id": "S1",\n'
        '      "evidence_quote": "trích đoạn liên tục từ tài liệu S1",\n'
        '      "verdict": "supported"\n'
        '    }\n'
        '  ],\n'
        '  "overall_verdict": "supported"\n'
        "}\n"
        'verdict gồm: "supported" (được nguồn hỗ trợ đầy đủ), "unsupported" (không có trong nguồn hoặc suy diễn sai), "uncertain" (nghi ngờ/thiếu cơ sở).'
        f"{claims_list_text}"
    )
    esc_context = _escape_context_data(context_block)
    esc_query = _escape_data_content(query)
    esc_draft = _escape_data_content(draft_answer)

    user_content = (
        f"<context>\n{esc_context}\n</context>\n\n"
        f"<user_query>\n{esc_query}\n</user_query>\n\n"
        f"<draft_answer>\n{esc_draft}\n</draft_answer>\n\n"
        f"{auditor_instruction}"
    )
    return [
        {"role": "user", "content": user_content},
    ]


def audit_draft_answer(
    auditor_output: str,
    admitted_source_ids: set,
    admitted_sources_text: Dict[str, str],
    draft: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Parse auditor JSON verdict strictly and verify claim coverage and source constraints.

    Returns: (check_status, verified_claims)
    check_status is one of: "verified", "partially_verified", "unverified".
    """
    cleaned = auditor_output.strip()
    if cleaned.startswith("```"):
        if not cleaned.endswith("```") or len(cleaned) < 6:
            return "unverified", []
        cleaned = cleaned[:-3].strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:].strip()
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except Exception:
        # Strict JSON only: do not use regex to carve JSON out of mixed text
        return "unverified", []

    if not isinstance(data, dict):
        return "unverified", []

    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        return "unverified", []

    overall_verdict = data.get("overall_verdict")
    if not isinstance(overall_verdict, str):
        return "unverified", []
    overall_verdict = overall_verdict.strip().lower()
    if overall_verdict not in {"supported", "unsupported", "uncertain"}:
        return "unverified", []

    verified_claims = []
    has_unsupported = False
    has_uncertain = False

    for c in claims:
        if not isinstance(c, dict):
            return "unverified", []
        stmt = c.get("statement")
        if not isinstance(stmt, str) or not stmt.strip():
            return "unverified", []
        src_id = c.get("source_id")
        if not isinstance(src_id, str) or not src_id.strip():
            return "unverified", []
        quote = c.get("evidence_quote")
        if not isinstance(quote, str) or not quote.strip():
            return "unverified", []
        verdict_val = c.get("verdict")
        if not isinstance(verdict_val, str):
            return "unverified", []
        verdict = verdict_val.strip().lower()
        if verdict not in {"supported", "unsupported", "uncertain"}:
            return "unverified", []

        # 1. source_id must belong to admitted sources
        if src_id not in admitted_source_ids:
            verdict = "unsupported"
            has_unsupported = True

        # 2. evidence_quote must be continuous substring of the cited source text
        source_text = admitted_sources_text.get(src_id, "")
        if (
            quote in source_text
            or html.unescape(quote) in source_text
            or html.unescape(quote) in html.unescape(source_text)
            or html.escape(quote) in source_text
        ):
            pass  # Verified quote
        else:
            if verdict == "supported":
                verdict = "uncertain"
                has_uncertain = True

        if verdict == "unsupported":
            has_unsupported = True
        elif verdict == "uncertain":
            has_uncertain = True

        verified_claims.append({
            "statement": stmt,
            "source_id": src_id,
            "evidence_quote": quote,
            "verdict": verdict,
        })

    # 3. Check exact normalized claim coverage against draft answer
    if draft:
        if not check_claim_coverage(draft, verified_claims):
            has_unsupported = True

    if has_unsupported or overall_verdict == "unsupported":
        return "unverified", verified_claims
    if has_uncertain or overall_verdict != "supported":
        return "partially_verified", verified_claims
    return "verified", verified_claims
