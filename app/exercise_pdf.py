"""Single-responsibility PDF generator for topic-based exercises with a separate answer key at the end.

Uses PyMuPDF (`fitz.Story` / `insert_htmlbox`) for full Vietnamese UTF-8 rendering:
- Part I (`PHẦN I: ĐỀ BÀI LUYỆN TẬP`): Questions, choices (A/B/C/D) or exercise stems, and source document/page references without inline answers.
- Part II (`PHẦN II: ĐÁP ÁN & GIẢI THÍCH ĐỐI CHIẾU CUỐI TRANG`): Separate answer key table, detailed explanations, and verbatim source quotes placed at the end of the PDF for easy self-checking.
"""

from __future__ import annotations

import datetime as dt
import html
import io
from typing import Any, Sequence

import pymupdf as fitz  # PyMuPDF


def _esc(value: Any) -> str:
    return html.escape(str(value or "").strip())


def _normalize_item(raw: dict[str, Any], idx: int) -> dict[str, Any]:
    """Normalize either a PracticeService question dict or an extracted exercise dict."""
    question_text = str(raw.get("question") or raw.get("text") or raw.get("prompt") or "").strip()
    choices_raw = raw.get("choices") or []
    choices: list[dict[str, str]] = []
    if isinstance(choices_raw, dict):
        for label in ("A", "B", "C", "D"):
            if label in choices_raw and str(choices_raw[label]).strip():
                choices.append({"label": label, "text": str(choices_raw[label]).strip()})
    elif isinstance(choices_raw, list):
        for i, item in enumerate(choices_raw):
            if isinstance(item, dict):
                label = str(item.get("label") or chr(65 + i)).strip()
                text = str(item.get("text") or "").strip()
                if text:
                    choices.append({"label": label, "text": text})
            elif str(item).strip():
                choices.append({"label": chr(65 + i), "text": str(item).strip()})

    answer = str(
        raw.get("answer")
        or raw.get("correct_answer")
        or raw.get("solution_summary")
        or ""
    ).strip()
    explanation = str(
        raw.get("explanation")
        or raw.get("solution")
        or raw.get("ai_solution")
        or ""
    ).strip()

    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    source_title = str(
        source.get("title")
        or raw.get("title")
        or raw.get("book_title")
        or raw.get("filename")
        or "Tài liệu học tập"
    ).strip()
    page_num = source.get("page_num") or raw.get("page_num") or 1
    snippet = str(
        source.get("snippet")
        or raw.get("evidence_quote")
        or raw.get("quote")
        or ""
    ).strip()

    correct_choice_text = ""
    if choices and answer in {"A", "B", "C", "D"}:
        for ch in choices:
            if ch["label"].upper() == answer.upper():
                correct_choice_text = ch["text"]
                break

    return {
        "index": idx,
        "question": question_text or f"Bài tập {idx}",
        "choices": choices,
        "answer": answer or "Xem giải thích & trích đoạn nguồn",
        "correct_choice_text": correct_choice_text,
        "explanation": explanation or "Đối chiếu trực tiếp nội dung trích dẫn từ tài liệu nguồn bên dưới.",
        "source_title": source_title,
        "page_num": page_num,
        "snippet": snippet,
    }


def _build_questions_html(
    *,
    title: str,
    topic: str,
    course: str,
    difficulty: str,
    created_label: str,
    items: list[dict[str, Any]],
) -> str:
    difficulty_map = {"easy": "Dễ", "medium": "Trung bình", "hard": "Khó"}
    diff_label = difficulty_map.get(difficulty.lower(), difficulty or "Trung bình")

    question_blocks: list[str] = []
    for item in items:
        choices_html = ""
        if item["choices"]:
            rows = []
            for ch in item["choices"]:
                rows.append(
                    f'<div class="choice-item"><b>{_esc(ch["label"])}.</b> {_esc(ch["text"])}</div>'
                )
            choices_html = f'<div class="choices-box">{"".join(rows)}</div>'

        question_blocks.append(
            f"""
            <div class="question-card">
                <div class="q-header">
                    <span class="q-num">Câu {item["index"]}.</span>
                    <span class="q-source">[Nguồn: {_esc(item["source_title"])} · Trang {_esc(item["page_num"])}]</span>
                </div>
                <div class="q-stem">{_esc(item["question"])}</div>
                {choices_html}
            </div>
            """
        )

    course_meta = f"<span><b>Môn học:</b> {_esc(course)}</span> · " if course else ""
    return f"""
    <html>
    <head>
        <style>
            body {{
                font-family: sans-serif;
                font-size: 10.5pt;
                line-height: 1.45;
                color: #111111;
            }}
            .brand-bar {{
                border-bottom: 1.5pt solid #111111;
                padding-bottom: 6pt;
                margin-bottom: 10pt;
            }}
            .brand-title {{
                font-size: 14pt;
                font-weight: bold;
                letter-spacing: 0.5pt;
                color: #000000;
            }}
            .brand-sub {{
                font-size: 8.5pt;
                color: #444444;
            }}
            .doc-title {{
                font-size: 13pt;
                font-weight: bold;
                margin-top: 6pt;
                margin-bottom: 4pt;
                color: #000000;
            }}
            .meta-line {{
                font-size: 9pt;
                color: #333333;
                margin-bottom: 12pt;
            }}
            .section-heading {{
                font-size: 11pt;
                font-weight: bold;
                background-color: #f3f4f6;
                padding: 5pt 8pt;
                margin-bottom: 10pt;
                border-left: 3pt solid #111111;
            }}
            .question-card {{
                margin-bottom: 11pt;
                padding-bottom: 8pt;
                border-bottom: 0.5pt solid #d4d4d8;
            }}
            .q-header {{
                margin-bottom: 3pt;
            }}
            .q-num {{
                font-weight: bold;
                font-size: 10.5pt;
            }}
            .q-source {{
                font-size: 8.5pt;
                color: #52525b;
                margin-left: 6pt;
            }}
            .q-stem {{
                margin-bottom: 5pt;
            }}
            .choices-box {{
                margin-left: 10pt;
            }}
            .choice-item {{
                margin-bottom: 2.5pt;
            }}
            .footer-note {{
                margin-top: 12pt;
                font-size: 8.5pt;
                color: #52525b;
                font-style: italic;
            }}
        </style>
    </head>
    <body>
        <div class="brand-bar">
            <div class="brand-title">ARYM</div>
            <div class="brand-sub">Adaptive Revision Yields Mastery · Phiếu bài tập luyện tập có trích dẫn nguồn</div>
        </div>
        <div class="doc-title">{_esc(title)}</div>
        <div class="meta-line">
            {course_meta}<span><b>Chủ đề:</b> {_esc(topic)}</span> ·
            <span><b>Độ khó:</b> {_esc(diff_label)}</span> ·
            <span><b>Số câu:</b> {len(items)}</span> ·
            <span><b>Ngày tạo:</b> {_esc(created_label)}</span>
        </div>
        <div class="section-heading">PHẦN I — ĐỀ BÀI LUYỆN TẬP (Chưa kèm đáp án để tự làm)</div>
        {"".join(question_blocks)}
        <div class="footer-note">
            * Lưu ý: Bảng đáp án và giải thích chi tiết kèm trích dẫn nguyên văn được đặt riêng ở trang cuối của tài liệu này để thuận tiện đối chiếu sau khi làm bài.
        </div>
    </body>
    </html>
    """


def _build_answer_key_html(
    *,
    topic: str,
    course: str,
    items: list[dict[str, Any]],
) -> str:
    quick_cells = [
        f"<span><b>Câu {it['index']}:</b> {_esc(it['answer'])}</span>"
        for it in items
    ]
    quick_summary = " &nbsp;|&nbsp; ".join(quick_cells)

    detail_blocks: list[str] = []
    for item in items:
        ans_display = _esc(item["answer"])
        if item["correct_choice_text"]:
            ans_display = f"{ans_display} — {_esc(item['correct_choice_text'])}"
        quote_html = ""
        if item["snippet"]:
            quote_html = (
                f'<div class="ans-quote"><b>Trích đoạn nguồn ({_esc(item["source_title"])}, '
                f'trang {_esc(item["page_num"])}):</b> “{_esc(item["snippet"])}”</div>'
            )
        detail_blocks.append(
            f"""
            <div class="ans-card">
                <div class="ans-line">
                    <b>Câu {item["index"]} — Đáp án đúng: {ans_display}</b>
                    <span class="ans-src">[{_esc(item["source_title"])} · Trang {_esc(item["page_num"])}]</span>
                </div>
                <div class="ans-expl"><b>Giải thích:</b> {_esc(item["explanation"])}</div>
                {quote_html}
            </div>
            """
        )

    course_str = f" ({_esc(course)})" if course else ""
    return f"""
    <html>
    <head>
        <style>
            body {{
                font-family: sans-serif;
                font-size: 10pt;
                line-height: 1.45;
                color: #111111;
            }}
            .answer-banner {{
                border-top: 2pt solid #000000;
                border-bottom: 1pt solid #000000;
                padding: 6pt 0;
                margin-bottom: 10pt;
            }}
            .answer-title {{
                font-size: 12.5pt;
                font-weight: bold;
                color: #000000;
            }}
            .answer-sub {{
                font-size: 8.5pt;
                color: #444444;
            }}
            .quick-grid {{
                background-color: #f4f4f5;
                border: 0.8pt solid #a1a1aa;
                padding: 7pt 10pt;
                margin-bottom: 12pt;
                font-size: 9.5pt;
            }}
            .ans-card {{
                margin-bottom: 10pt;
                padding-bottom: 7pt;
                border-bottom: 0.5pt solid #d4d4d8;
            }}
            .ans-line {{
                margin-bottom: 3pt;
            }}
            .ans-src {{
                font-size: 8.5pt;
                color: #52525b;
                margin-left: 6pt;
            }}
            .ans-expl {{
                margin-bottom: 3pt;
            }}
            .ans-quote {{
                font-size: 8.8pt;
                color: #3f3f46;
                font-style: italic;
                margin-left: 8pt;
            }}
        </style>
    </head>
    <body>
        <div class="answer-banner">
            <div class="answer-title">PHẦN II — BẢNG ĐÁP ÁN &amp; GIẢI THÍCH ĐỐI CHIẾU CUỐI TRANG</div>
            <div class="answer-sub">ARYM · Adaptive Revision Yields Mastery — Chủ đề: {_esc(topic)}{course_str}</div>
        </div>
        <div class="quick-grid">
            <b>Bảng tra nhanh đáp án:</b><br/>
            {quick_summary}
        </div>
        {"".join(detail_blocks)}
    </body>
    </html>
    """


def _render_html_story_to_doc(doc: fitz.Document, html_content: str) -> None:
    """Render an HTML document onto an A4 page using page.insert_htmlbox."""
    mediabox = fitz.paper_rect("a4")
    margin = 36  # 0.5 inch margin
    where = fitz.Rect(margin, margin, mediabox.width - margin, mediabox.height - margin - 24)
    page = doc.new_page(width=mediabox.width, height=mediabox.height)
    page.insert_htmlbox(where, html_content, scale_low=0.55)


def generate_exercise_worksheet_pdf(
    *,
    topic: str,
    questions: Sequence[dict[str, Any]],
    course: str = "",
    difficulty: str = "medium",
    title: str = "",
) -> bytes:
    """Generate a PDF binary containing topic exercises and a separate answer key on the final page(s)."""
    normalized = [
        _normalize_item(dict(item), idx)
        for idx, item in enumerate(questions, start=1)
        if isinstance(item, dict)
    ]
    if not normalized:
        raise ValueError("Không có câu hỏi hoặc bài tập nào để xuất PDF.")

    clean_topic = (topic or "Chủ đề ôn tập").strip()
    clean_course = (course or "").strip()
    doc_title = (title or f"Bài tập theo chủ đề: {clean_topic}").strip()
    now_label = dt.datetime.now().strftime("%d/%m/%Y %H:%M")

    questions_html = _build_questions_html(
        title=doc_title,
        topic=clean_topic,
        course=clean_course,
        difficulty=difficulty,
        created_label=now_label,
        items=normalized,
    )
    answer_key_html = _build_answer_key_html(
        topic=clean_topic,
        course=clean_course,
        items=normalized,
    )

    doc = fitz.open()
    try:
        # Part I: Questions pages
        _render_html_story_to_doc(doc, questions_html)
        # Part II: Separate Answer Key & Explanations page(s) at the end of the PDF
        _render_html_story_to_doc(doc, answer_key_html)

        # Stamp page numbers in footer
        total_pages = len(doc)
        for page_index, page in enumerate(doc, start=1):
            footer_rect = fitz.Rect(36, page.rect.height - 26, page.rect.width - 36, page.rect.height - 10)
            section_tag = "Đáp án đối chiếu cuối trang" if page_index == total_pages else "Đề bài luyện tập"
            page.insert_htmlbox(
                footer_rect,
                f'<div style="font-family: sans-serif; font-size: 8pt; color: #52525b;">'
                f'ARYM · Adaptive Revision Yields Mastery — {_esc(section_tag)} · Trang {page_index}/{total_pages}'
                f'</div>',
            )

        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    finally:
        doc.close()
