"""Single-responsibility module for quiz/flashcard JSON parsing, difficulty evaluation,
and distractor validation (extracted from `app/study_materials.py`).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Optional


def evaluate_question_difficulty(q: Dict[str, Any]) -> str:
    """Evaluate actual content difficulty of a question: 'easy', 'medium', or 'hard'."""
    if not isinstance(q, dict):
        return "easy"
    if q.get("fallback") or q.get("is_fallback"):
        return "easy"

    is_cloze = q.get("format") == "source_cloze"
    q_text = str(q.get("question", "")).lower()
    expl = str(q.get("explanation", "")).lower()

    if is_cloze:
        return "easy"

    hard_markers = [
        "ngoại trừ", "trường hợp nào sau đây không", "không áp dụng", "không đúng trong trường hợp",
        "hệ quả nếu", "suy luận", "phân tích", "tình huống", "điều kiện cần và đủ", "nguyên nhân tại sao không",
        "nếu thay đổi", "phân biệt giữa",
    ]
    medium_markers = [
        "điều kiện", "khi nào", "trong trường hợp", "phân biệt", "áp dụng", "vai trò chính",
        "khác nhau", "ảnh hưởng như thế nào", "tại sao", "như thế nào nếu", "mục đích", "nguyên tắc",
    ]
    easy_markers = [
        "là gì", "được định nghĩa", "nhận định nào sau đây là chính xác", "khái niệm", "tên gọi",
        "gồm có", "có nghĩa là", "theo tài liệu, nhận định", "được gọi là",
    ]

    model_diff = str(q.get("difficulty", "")).strip().lower()
    if model_diff == "hard":
        if any(m in q_text for m in hard_markers) or any(
            m in expl for m in ["ngoại lệ", "trừ trường hợp", "không áp dụng", "suy ra từ"]
        ):
            return "hard"
        if any(m in q_text for m in easy_markers):
            return "easy"
        return "medium"

    if any(m in q_text for m in hard_markers):
        return "hard"
    if any(m in q_text for m in medium_markers):
        return "medium"
    if any(m in q_text for m in easy_markers):
        return "easy"

    if model_diff in ("easy", "medium", "hard"):
        return model_diff

    return "medium" if len(expl) > 120 else "easy"


def parse_json_from_response(raw_text: str) -> Optional[dict]:
    """Safely extract and parse JSON object from LLM response text."""
    if not raw_text or not raw_text.strip():
        return None
    raw_text = re.sub(
        r"(?is)<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>",
        "",
        raw_text,
    )
    raw_text = re.sub(r"(?is)^.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", "", raw_text).strip()
    if not raw_text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"(\{.*\})", raw_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    try:
        return json.loads(raw_text.strip())
    except Exception:
        return None


def _zero_condition_distractors_are_disjoint(answer: str, distractors: List[str]) -> bool:
    """Reject same-zero comparisons that can also be true under the answer."""
    regions = {
        "<": {-1},
        "<=": {-1, 0},
        "=": {0},
        ">=": {0, 1},
        ">": {1},
        "!=": {-1, 1},
    }

    def parse(text: str):
        text = re.sub(r"\s+", "", html.unescape(text))
        text = text.replace("≤", "<=").replace("≥", ">=").replace("≠", "!=")
        match = re.fullmatch(r"([A-Za-z]\w*(?:\([^()\n]+\))?)(<=|>=|!=|=|<|>)0(?:\.0+)?", text)
        return (match[1], regions[match[2]]) if match else None

    correct = parse(answer)
    if correct is None:
        return True
    for distractor in distractors:
        other = parse(distractor)
        if other is None or other[0] != correct[0] or other[1] & correct[1]:
            return False
    return True


def _information_unit_distractors_match(answer: str, distractors: List[str]) -> bool:
    """Keep bit/byte alternatives concrete; generic categories are not values."""
    if not re.search(r"\b(?:bits?|bytes?)$", answer.strip(), re.I):
        return True
    quantity = r"(?:[0-9]+(?:[.,][0-9]+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
    pattern = rf"(?:{quantity}\s+)?(?:bits?|bytes?|kilobytes?|joules?|watts?)"
    return all(re.fullmatch(pattern, value.strip(), re.I) is not None for value in distractors)
