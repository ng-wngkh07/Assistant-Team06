import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Canonical RAG pipeline version fingerprint
PIPELINE_VERSION = "rag-pipeline-yc205-v2"

# Patterns for code syntax classification
_CODE_KEYWORDS = re.compile(
    r"(?m)(?:^|\s)(?:#include\s*<|class\s+\w+|struct\s+\w+|namespace\s+\w+|"
    r"public:|private:|protected:|using\s+namespace|int\s+main|def\s+\w+\(|"
    r"import\s+\w+|void\s+\w+\(|float\s+\w+|string\s+\w+|return\s+[^;]+;)"
)

# Mathematical formula and symbol patterns
_MATH_PATTERNS = re.compile(
    r"(?:\\(?:lim|frac|int|sum|partial|sqrt|vec|Delta|alpha|beta|gamma|theta)|"
    r"[∑∫∂√→↦≤≥≠∈∉⊂∪∩∆∇αβγδεθλμπστφψω]|"
    r"\b(?:lim|f\(x\)|f'\(x\)|dy/dx|dx/dt|ds/dt|dr/dt)\b)"
)

# Heading patterns
_HEADING_PATTERNS = re.compile(
    r"(?i)^(?:[0-9]+(?:\.[0-9]+)*\s+[A-ZÀ-Ỹ]|chương\s+[0-9]+|bài\s+[0-9]+|"
    r"định\s+nghĩa|ví\s+dụ|figure\s+[0-9]|hình\s+[0-9])"
)


def check_pua_characters(text: str) -> List[str]:
    """Identify Unicode Private Use Area (PUA) characters."""
    if not text:
        return []
    pua = []
    for c in text:
        cp = ord(c)
        if (0xE000 <= cp <= 0xF8FF) or (0xF0000 <= cp <= 0xFFFFD) or (0x100000 <= cp <= 0x10FFFD):
            pua.append(f"U+{cp:04X}")
    return pua


def classify_block(bbox: Tuple[float, float, float, float], text: str, block_type_num: int = 0) -> str:
    """Classify an extracted PDF block into typed category: code, formula, heading, table, image, or prose."""
    if block_type_num == 1:
        return "image"
    if not text or not text.strip():
        return "empty"

    stripped = text.strip()
    if _CODE_KEYWORDS.search(stripped):
        return "code"

    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if len(lines) == 1 and len(stripped) <= 120 and not stripped.endswith(".") and _HEADING_PATTERNS.match(stripped):
        return "heading"

    if "\t" in stripped or (stripped.count("|") >= 2 and len(lines) >= 2):
        return "table"

    if _MATH_PATTERNS.search(stripped):
        return "formula"

    return "prose"


def inspect_page_quality(page, raw_text: Optional[str] = None, page_num: int = 1) -> Dict[str, Any]:
    """
    Inspect quality of an extracted PDF page using native PyMuPDF structures.
    Preserves raw positional blocks, flags PUA glyphs, legacy encodings,
    and unrepresented diagrams or formula regions.
    """
    if raw_text is None:
        raw_text = page.get_text() if hasattr(page, "get_text") else ""
    warnings: List[str] = []
    needs_review = False

    # 1. PUA detection
    pua_chars = check_pua_characters(raw_text)
    if pua_chars:
        warnings.append(f"pua_characters_detected: {len(pua_chars)} chars ({', '.join(pua_chars[:5])})")
        needs_review = True

    # 2. Legacy font detection
    from app.text_cleaner import is_likely_tcvn3, is_likely_vni
    legacy_encoding = None
    if is_likely_tcvn3(raw_text):
        legacy_encoding = "tcvn3"
        warnings.append("tcvn3_legacy_encoding_detected")
        needs_review = True
    elif is_likely_vni(raw_text):
        legacy_encoding = "vni"
        warnings.append("vni_legacy_encoding_detected")
        needs_review = True

    # 3. Vector drawings and raster images inspection
    drawing_count = 0
    image_count = 0
    try:
        if hasattr(page, "get_drawings"):
            drawing_count = len(page.get_drawings())
        if hasattr(page, "get_images"):
            image_count = len(page.get_images())
    except Exception as e:
        logger.debug(f"Could not inspect drawings/images for page {page_num}: {e}")

    if drawing_count >= 5:
        warnings.append(f"vector_drawings_detected: {drawing_count} drawings")
    if image_count >= 1:
        warnings.append(f"embedded_images_detected: {image_count} images")
        needs_review = True

    # 4. Typed positional blocks extraction
    blocks_data: List[Dict[str, Any]] = []
    has_code = False
    has_formula = False
    has_table = False

    try:
        raw_blocks = page.get_text("blocks") if hasattr(page, "get_text") else []
        for idx, b in enumerate(raw_blocks):
            # PyMuPDF block tuple: (x0, y0, x1, y1, text, block_no, block_type)
            if len(b) >= 7:
                bbox = (round(b[0], 2), round(b[1], 2), round(b[2], 2), round(b[3], 2))
                b_text = b[4]
                b_type_num = b[6]
            elif len(b) >= 5:
                bbox = (round(b[0], 2), round(b[1], 2), round(b[2], 2), round(b[3], 2))
                b_text = b[4]
                b_type_num = 0
            else:
                continue

            category = classify_block(bbox, b_text, b_type_num)
            if category == "code":
                has_code = True
            elif category == "formula":
                has_formula = True
            elif category == "table":
                has_table = True

            blocks_data.append({
                "block_id": f"p{page_num}_b{idx}",
                "bbox": list(bbox),
                "type": category,
                "text": b_text
            })
    except Exception as e:
        logger.debug(f"Could not extract blocks for page {page_num}: {e}")

    # 5. Formula / diagram unrepresented heuristic
    # A page with text and formulas that also has vector drawings or raster images may have
    # visual equation components or diagrams not fully captured in the plain text stream.
    if len(raw_text.strip()) > 50:
        if (has_formula or _MATH_PATTERNS.search(raw_text)) and (drawing_count >= 3 or image_count >= 1):
            warnings.append("unrepresented_formula_or_diagram_suspected")
            needs_review = True
        elif image_count >= 1 and re.search(r"(?i)\b(figure|hình|sơ đồ|biểu đồ)\b", raw_text):
            warnings.append("embedded_figure_diagram_requires_review")
            needs_review = True

    return {
        "page_num": page_num,
        "needs_review": needs_review,
        "warnings": warnings,
        "indicators": {
            "pua_count": len(pua_chars),
            "pua_samples": pua_chars[:5],
            "legacy_encoding": legacy_encoding,
            "drawing_count": drawing_count,
            "image_count": image_count,
            "has_code": has_code,
            "has_formula": has_formula,
            "has_table": has_table
        },
        "blocks": blocks_data,
        "raw_text": raw_text,
        "has_text": len(raw_text.strip()) > 0,
        "raw_char_count": len(raw_text),
    }


def get_sidecar_dir(data_dir: Path) -> Path:
    """Return directory path for persisted source quality sidecars."""
    sidecar_dir = data_dir / "source_quality"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    return sidecar_dir


def save_source_quality_sidecar(data_dir: Path, file_hash: str, quality_data: Dict[str, Any]) -> Path:
    """Persist source quality inspection sidecar JSON file bound by source file_hash."""
    sidecar_dir = get_sidecar_dir(data_dir)
    target_file = sidecar_dir / f"{file_hash}.json"
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(quality_data, f, ensure_ascii=False, indent=2)
    return target_file


def load_source_quality_sidecar(data_dir: Path, file_hash: str) -> Optional[Dict[str, Any]]:
    """Load persisted source quality inspection sidecar JSON file if it exists."""
    sidecar_file = get_sidecar_dir(data_dir) / f"{file_hash}.json"
    if sidecar_file.exists():
        try:
            return json.loads(sidecar_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Could not read quality sidecar {sidecar_file}: {e}")
    return None
