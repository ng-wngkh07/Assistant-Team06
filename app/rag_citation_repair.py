"""Single-responsibility module for RAG citation tag parsing and echo/unanswered
response detection (extracted from `app/rag_agent.py`).
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple


def parse_citations(text: str, available_map: Dict[str, dict]) -> Tuple[List[str], List[str]]:
    """Parse [S1], [S2]... tags and separate valid IDs from invalid/out-of-range IDs."""
    normalized = re.sub(
        r"\[\s*S(\d+)\s*[,;]\s*S(\d+)\s*\]",
        lambda match: f"[S{match.group(1)}] [S{match.group(2)}]",
        text,
        flags=re.IGNORECASE,
    )
    raw_refs = re.findall(r"\[\s*S([^\]]*)\]", normalized, flags=re.IGNORECASE)
    ref_ids = [value.strip() for value in raw_refs]
    valid_nums = [value for value in ref_ids if re.fullmatch(r"\d+", value)]
    inv_ids = [value for value in ref_ids if not re.fullmatch(r"\d+", value)]
    inv_ids.extend(number for number in valid_nums if f"S{int(number)}" not in available_map)
    val_ids = list(
        dict.fromkeys(f"S{int(number)}" for number in valid_nums if f"S{int(number)}" in available_map)
    )
    return val_ids, inv_ids


def clean_for_echo_comparison(text: str) -> str:
    """Normalize text for checking whether the LLM merely echoed the user's question."""
    t = re.sub(r"^\s*\[(.*)\]\s*$", r"\1", text)
    t = re.sub(r"(?i)^\s*(?:câu hỏi|đề bài|user_query|query)\s*:\s*", "", t)
    t = re.sub(r"[?.,!;:\"'“”«»\[\]\(\)\{\}]", " ", t)
    return " ".join(t.casefold().split())
