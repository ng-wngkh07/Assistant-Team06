"""Single-responsibility module for study material file storage, safe path resolution,
and page-chunk text merging (extracted from `app/study_materials.py`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
MIN_EXTRACT_CHARS = 40
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown",
    ".png", ".jpg", ".jpeg", ".webp",
}


class StudyMaterialError(ValueError):
    """Raised when a document cannot produce valid study materials."""
    pass


class StudyMaterialIndexError(RuntimeError):
    """Raised when indexing or persisting an uploaded material fails."""
    pass


def resolve_safe_source_file(
    filename: Optional[str],
    search_dirs: Optional[List[Path]] = None,
) -> Optional[Path]:
    """Resolve exact originals first; ambiguous basenames never pick an arbitrary file."""
    if not filename:
        return None
    from app.config import BASE_DIR, SRC_DIR

    if search_dirs is None:
        search_dirs = [SRC_DIR, BASE_DIR / "docs", BASE_DIR / "tests" / "fixtures" / "documents"]
    raw_path = Path(filename.replace("\\", "/"))
    if ".." in raw_path.parts or raw_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None
    roots = {folder.resolve() for folder in search_dirs if folder and folder.is_dir()}
    if raw_path.is_absolute():
        candidate = raw_path.resolve()
        return candidate if candidate.is_file() and any(candidate.is_relative_to(root) for root in roots) else None
    variants = [raw_path]
    if raw_path.parts and raw_path.parts[0].lower() == "src":
        variants.append(Path(*raw_path.parts[1:]))
    exact = set()
    for root in roots:
        for variant in variants:
            candidate = (root / variant).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                exact.add(candidate)
    if exact and len(raw_path.parts) > 1:
        return next(iter(exact)) if len(exact) == 1 else None
    if len(raw_path.parts) != 1:
        return None
    matches = set(exact)
    for root in roots:
        for path in root.rglob(raw_path.name):
            candidate = path.resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                matches.add(candidate)
                if len(matches) > 1:
                    return None
    return next(iter(matches)) if matches else None


def merge_chunk_texts(chunks: List[Any]) -> str:
    """Merge adjacent chunks into page text using provenance offsets when available."""
    if not chunks:
        return ""

    def _get_info(c: Any):
        if isinstance(c, dict):
            return str(c.get("text", "")).strip(), c.get("char_start"), c.get("char_end")
        return str(c).strip(), None, None

    first_txt, prev_start, prev_end = _get_info(chunks[0])
    merged = first_txt
    has_provenance = prev_start is not None and prev_end is not None

    for c in chunks[1:]:
        txt, c_start, c_end = _get_info(c)
        if not txt:
            continue
        if has_provenance and c_start is not None and c_end is not None and prev_end is not None:
            if c_start < prev_end:
                overlap_chars = max(0, prev_end - c_start)
                if overlap_chars < len(txt):
                    merged += txt[overlap_chars:]
            else:
                merged += "\n\n" + txt
            prev_end = max(prev_end, c_end)
        else:
            if not merged.endswith(txt):
                merged += "\n\n" + txt
    return merged
