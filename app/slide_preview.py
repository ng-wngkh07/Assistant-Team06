"""Single-responsibility preview renderer for PPTX slides and DOCX sections.

Renders `.pptx` slides and `.docx` document pages/sections into clean PNG preview
images using `pymupdf.Story` so non-PDF office documents can be previewed in the
browser without external LibreOffice dependencies.
"""

from __future__ import annotations

import html
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple


def extract_pptx_slide_texts(file_path: Path) -> List[Tuple[str, List[str]]]:
    """Extract (title, bullet_lines) for each slide in a .pptx file via PDFExtractor."""
    from app.pdf_extractor import PDFExtractor

    res = PDFExtractor.extract_pptx(file_path)
    slides_data: List[Tuple[str, List[str]]] = []
    for idx, page in enumerate(res.extracted_pages, 1):
        raw_lines = [ln.strip() for ln in (page.get("text") or "").splitlines() if ln.strip()]
        if raw_lines:
            title_text = raw_lines[0]
            body_lines = raw_lines[1:] if len(raw_lines) > 1 else [raw_lines[0]]
        else:
            title_text = f"Slide {idx}"
            body_lines = []
        slides_data.append((title_text, body_lines))
    return slides_data


def extract_docx_page_texts(file_path: Path, paragraphs_per_page: int = 12) -> List[Tuple[str, List[str]]]:
    """Extract logical preview pages from a .docx file via PDFExtractor."""
    from app.pdf_extractor import PDFExtractor

    res = PDFExtractor.extract_docx(file_path)
    pages: List[Tuple[str, List[str]]] = []
    for idx, page in enumerate(res.extracted_pages, 1):
        lines = [ln.strip() for ln in (page.get("text") or "").splitlines() if ln.strip()]
        pages.append((f"{file_path.name} — Trang {idx}", lines or ["(Tài liệu trống)"]))
    if not pages:
        return [(file_path.name, ["(Tài liệu trống)"])]
    return pages


def render_office_page_png(file_path: Path, page_num: int = 1, dpi: int = 150) -> Optional[bytes]:
    """Render 1-indexed slide/page of a .pptx or .docx file to PNG bytes."""
    import pymupdf

    if not file_path or not file_path.is_file() or page_num < 1:
        return None
    ext = file_path.suffix.lower()
    if ext == ".pptx":
        pages = extract_pptx_slide_texts(file_path)
        width, height = 960, 540
    elif ext == ".docx":
        pages = extract_docx_page_texts(file_path)
        width, height = 794, 1123
    else:
        return None

    if page_num > len(pages):
        return None

    title, lines = pages[page_num - 1]
    escaped_title = html.escape(title)
    escaped_items = "".join(f"<li>{html.escape(line)}</li>" for line in lines[:25])
    if not escaped_items:
        escaped_items = "<li><em>(Không có văn bản trên trang này)</em></li>"

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    color: #0f172a;
    margin: 0;
    padding: 0;
}}
.header {{
    background-color: #eff6ff;
    border-bottom: 2px solid #2563eb;
    padding: 12pt 16pt;
    margin-bottom: 12pt;
}}
.meta {{
    font-size: 9pt;
    color: #475569;
    margin-bottom: 4pt;
}}
h1 {{
    font-size: 16pt;
    color: #1e3a8a;
    margin: 0;
}}
ul {{
    margin: 8pt 0 0 18pt;
    padding: 0;
}}
li {{
    font-size: 11pt;
    line-height: 1.45;
    margin-bottom: 6pt;
}}
</style>
</head>
<body>
<div class="header">
    <div class="meta">{html.escape(file_path.name)} — Trang/Slide {page_num}/{len(pages)}</div>
    <h1>{escaped_title}</h1>
</div>
<ul>{escaped_items}</ul>
</body>
</html>"""

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp_pdf:
        story = pymupdf.Story(html=html_doc)
        writer = pymupdf.DocumentWriter(tmp_pdf.name)
        mediabox = pymupdf.Rect(0, 0, width, height)
        where = pymupdf.Rect(28, 28, width - 28, height - 28)
        dev = writer.begin_page(mediabox)
        story.place(where)
        story.draw(dev)
        writer.end_page()
        writer.close()
        with pymupdf.open(tmp_pdf.name) as rendered_pdf:
            pix = rendered_pdf[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            return pix.tobytes("png")
