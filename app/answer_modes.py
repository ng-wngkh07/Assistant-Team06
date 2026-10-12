"""Output format contract validation and retry prompts for answer modes."""

import re
from typing import Optional, Tuple, List


def normalize_mode(answer_mode: Optional[str]) -> str:
    if not answer_mode:
        return "quick"
    mode = str(answer_mode).lower().strip()
    if mode in ("compare", "comparison", "so sánh", "so sánh hai khái niệm"):
        return "compare"
    if mode in ("steps", "step_by_step", "từng bước", "giải thích từng bước"):
        return "steps"
    return "quick"


def parse_markdown_table(text: str) -> Optional[Tuple[List[str], List[List[str]]]]:
    """Parse the first markdown table in text into (header_cells, data_rows)."""
    clean_text = re.sub(
        r"<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>",
        "",
        text,
        flags=re.I | re.S,
    )
    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]

    for i in range(len(lines) - 1):
        line = lines[i]
        next_line = lines[i + 1]

        if "|" not in line or "|" not in next_line:
            continue

        h_cells = [c.strip() for c in line.strip("|").split("|")]
        s_cells = [c.strip() for c in next_line.strip("|").split("|")]

        if len(h_cells) < 3 or len(s_cells) < 3:
            continue

        # Check separator row: all cells must match :?-+:?
        if not all(re.fullmatch(r":?-+:?", sc) is not None for sc in s_cells):
            continue

        data_rows = []
        for r_idx in range(i + 2, len(lines)):
            r_line = lines[r_idx]
            if "|" not in r_line:
                break
            r_cells = [c.strip() for c in r_line.strip("|").split("|")]
            if len(r_cells) < 3:
                break
            data_rows.append(r_cells)

        return h_cells, data_rows

    return None


def validate_compare_mode(
    text: str, compare_concepts: Optional[Tuple[str, str]] = None
) -> bool:
    """Compare mode contract:
    - 3-column Markdown table with header containing criterion and both concept names
    - Separator row
    - >= 1 data row with 3 non-empty cells
    """
    table = parse_markdown_table(text)
    if not table:
        return False
    header_cells, data_rows = table
    if len(header_cells) < 3:
        return False

    if compare_concepts:
        c1, c2 = compare_concepts
        header_str = " ".join(header_cells).casefold()
        if c1.casefold() not in header_str or c2.casefold() not in header_str:
            return False
    else:
        if not header_cells[1] or not header_cells[2]:
            return False

    # Check for >= 1 data row with all 3 cells non-empty
    valid_data_rows = [
        row for row in data_rows
        if len(row) >= 3 and all(len(cell.strip()) > 0 for cell in row[:3])
    ]
    return len(valid_data_rows) >= 1


def validate_steps_mode(text: str) -> bool:
    """Steps mode contract:
    - 2-4 numbered steps sequential starting from 1
    - Accompanied by goal/purpose statement (in preamble or mentioning goal/meaning keywords)
    - Generic prose without steps must be rejected
    """
    clean_text = re.sub(
        r"<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>",
        "",
        text,
        flags=re.I | re.S,
    )
    step_pattern = re.compile(
        r"^\s*(?:[-*•]\s*)?(?:(?:bước|step)\s*)?([1-9]\d*)\s*[\.:\)]\s*(.+)$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(step_pattern.finditer(clean_text))
    if not matches:
        return False

    step_nums = [int(m.group(1)) for m in matches]
    if len(step_nums) < 2 or len(step_nums) > 4:
        return False

    # Check sequential numbering starting at 1
    if step_nums != list(range(1, len(step_nums) + 1)):
        return False

    # Check each step has non-empty content
    if not all(len(m.group(2).strip()) > 0 for m in matches):
        return False

    # Check goal/purpose presence
    preamble = clean_text[:matches[0].start()].strip()
    has_goal = (
        len(preamble) >= 10
        or any(k in clean_text.casefold() for k in ["mục tiêu", "mục đích", "ý nghĩa", "quy trình", "nhằm", "để "])
    )
    return has_goal


def validate_quick_mode(text: str) -> bool:
    """Quick mode contract:
    - 1-4 concise points (bullets or 1 short paragraph)
    - No repetition
    """
    clean_text = re.sub(
        r"<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>",
        "",
        text,
        flags=re.I | re.S,
    ).strip()
    if not clean_text:
        return False

    bullet_pattern = re.compile(r"^\s*[-*•+]\s*(.+)$", re.MULTILINE)
    bullet_matches = list(bullet_pattern.finditer(clean_text))

    if bullet_matches:
        bullet_items = [m.group(1).strip() for m in bullet_matches if m.group(1).strip()]
        if not (1 <= len(bullet_items) <= 4):
            return False
        # Check no repetition
        norm_items = [
            re.sub(r"\[\s*s\d+\s*\]", "", b, flags=re.I).strip().casefold()
            for b in bullet_items
        ]
        if len(norm_items) != len(set(norm_items)):
            return False
        return True

    # No bullets: 1 short paragraph (or up to 2 short paragraphs)
    paragraphs = [p.strip() for p in clean_text.split("\n\n") if p.strip()]
    if len(paragraphs) > 2:
        return False

    # Sentence check: 1 to 5 concise sentences, no repetition
    sentences = [s.strip() for s in re.split(r"[.!?]+", clean_text) if len(s.strip()) > 3]
    if not (1 <= len(sentences) <= 5):
        return False
    norm_sentences = [
        re.sub(r"\[\s*s\d+\s*\]", "", s, flags=re.I).strip().casefold()
        for s in sentences
    ]
    if len(norm_sentences) != len(set(norm_sentences)):
        return False

    return True


def validate_mode_format(
    text: str, answer_mode: str, compare_concepts: Optional[Tuple[str, str]] = None
) -> bool:
    """Validate whether text satisfies the presentation contract of answer_mode."""
    mode = normalize_mode(answer_mode)
    if mode == "compare":
        return validate_compare_mode(text, compare_concepts=compare_concepts)
    elif mode == "steps":
        return validate_steps_mode(text)
    else:
        return validate_quick_mode(text)


def get_mode_retry_prompt(
    answer_mode: str, compare_concepts: Optional[Tuple[str, str]] = None
) -> str:
    """Return specific formatting instruction for retry attempt."""
    mode = normalize_mode(answer_mode)
    if mode == "compare":
        c1, c2 = compare_concepts if compare_concepts else ("Khái niệm 1", "Khái niệm 2")
        return (
            f"Câu trả lời trước chưa đúng định dạng so sánh. BẮT BUỘC trình bày bảng Markdown 3 cột có header:\n"
            f"| Tiêu chí | {c1} | {c2} |\n"
            f"| --- | --- | --- |\n"
            f"kèm ít nhất một dòng dữ liệu đối chiếu đủ 3 ô không rỗng dựa trên nguồn tài liệu. "
            f"Tuyệt đối không chỉ đưa tiêu đề hoặc viết văn xuôi không có bảng đối chiếu."
        )
    elif mode == "steps":
        return (
            "Câu trả lời trước chưa đúng định dạng giải thích từng bước. BẮT BUỘC mở đầu bằng 1 câu nêu mục tiêu/ý nghĩa, "
            "sau đó trình bày tuần tự từ 2 đến 4 bước đánh số (1., 2., ...) có căn cứ từ tài liệu. "
            "Tuyệt đối không dùng văn xuôi chung không có các bước đánh số."
        )
    else:
        return (
            "Câu trả lời trước chưa đúng định dạng tóm tắt nhanh. BẮT BUỘC trình bày từ 1–4 ý ngắn gọn có căn cứ "
            "(dùng gạch đầu dòng hoặc 1 đoạn ngắn nếu chỉ cần 1 ý), không lặp lại ý và không đưa giả định ngoài tài liệu."
        )
