"""Office source preview rendering for PPTX slides and DOCX sections.

Provides honest original presentation previews:
- PPTX: Headless local LibreOffice PDF conversion followed by PyMuPDF page rendering,
  preserving original slide layout, diagrams, and typography without synthetic text PNG approximations.
- DOCX: Logical reading sections are preserved as structured text and download links,
  without forging fake page images or mapping logical sections to physical PDF pages.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

import defusedxml.ElementTree as ET

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / "office_preview_cache"
_CONVERT_LOCK = threading.Lock()

MAX_PPTX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_ZIP_ENTRIES = 5000
MAX_SINGLE_ENTRY_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_UNCOMPRESSED_TOTAL_SIZE = 200 * 1024 * 1024  # 200 MB
MAX_COMPRESSION_RATIO = 100.0


def preflight_pptx_for_preview(file_path: Path, *, static_source: bool = False) -> Tuple[bool, str]:
    """Validate OOXML PPTX structure and security bounds before conversion.

    Fails closed for:
    - Non-existent, empty, or oversized files
    - Corrupt, encrypted, or unsafe ZIP archives (path traversal, zip bomb)
    - Active payloads (VBA macros, ActiveX controls, embedded OLE binaries)
    - Malformed XML, external DTDs, or XML entity expansions (via defusedxml)
    - External relationship targets or out-of-package references
    - Missing core OOXML presentation structures
    """
    if not file_path or not file_path.is_file():
        return False, "File does not exist or is not a regular file"

    try:
        st_size = file_path.stat().st_size
    except OSError as exc:
        return False, f"Cannot stat file: {exc}"

    if st_size == 0:
        return False, "File is empty"
    if st_size > MAX_PPTX_FILE_SIZE:
        return False, f"File size {st_size} exceeds maximum allowed {MAX_PPTX_FILE_SIZE}"

    if not zipfile.is_zipfile(file_path):
        return False, "File is not a valid ZIP / OOXML archive"

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            infolist = zf.infolist()
            if len({item.filename for item in infolist}) != len(infolist):
                return False, "Duplicate ZIP entries"
            if len(infolist) > MAX_ZIP_ENTRIES:
                return False, f"ZIP entry count {len(infolist)} exceeds maximum {MAX_ZIP_ENTRIES}"

            total_uncompressed = 0
            for zinfo in infolist:
                # 1. Encryption check
                if zinfo.flag_bits & 0x1 or (getattr(zinfo, "is_encrypted", None) and zinfo.is_encrypted()):
                    return False, f"Encrypted ZIP entry detected: {zinfo.filename}"

                # 2. Path traversal / Zip Slip
                fname = zinfo.filename.replace("\\", "/")
                if fname.startswith("/") or fname.startswith("\\"):
                    return False, f"Absolute path in ZIP entry: {zinfo.filename}"
                if ":" in fname:
                    return False, f"Drive letter or illegal colon in ZIP entry: {zinfo.filename}"
                parts = fname.split("/")
                if ".." in parts:
                    return False, f"Path traversal component in ZIP entry: {zinfo.filename}"

                # 3. Size and compression ratio checks
                if zinfo.file_size > MAX_SINGLE_ENTRY_SIZE:
                    return False, f"ZIP entry {zinfo.filename} uncompressed size {zinfo.file_size} exceeds {MAX_SINGLE_ENTRY_SIZE}"
                total_uncompressed += zinfo.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_TOTAL_SIZE:
                    return False, f"Total uncompressed ZIP size exceeds {MAX_UNCOMPRESSED_TOTAL_SIZE}"

                if zinfo.compress_size > 0 and zinfo.file_size > 1024 * 1024:
                    ratio = zinfo.file_size / zinfo.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        return False, f"Suspicious compression ratio {ratio:.1f} for entry {zinfo.filename}"

                # 4. Active payloads in filenames
                lower_name = fname.lower()
                if (
                    "vbaproject" in lower_name
                    or "vbadata" in lower_name
                    or lower_name.endswith(".vba")
                    or (lower_name.endswith(".bin") and not (static_source and lower_name.startswith("ppt/embeddings/")))
                    or "activex" in lower_name
                    or (not static_source and "oleobject" in lower_name)
                    or (not static_source and lower_name.startswith("ppt/embeddings/"))
                    or lower_name.startswith("ppt/activex/")
                    or "/macros/" in lower_name
                    or lower_name.startswith("macros/")
                ):
                    return False, f"Active payload or binary entry detected: {zinfo.filename}"

            namelist_set = set(zf.namelist())

            # 5. Missing core OOXML presentation parts
            if "[Content_Types].xml" not in namelist_set:
                return False, "Missing [Content_Types].xml in OOXML package"
            if "ppt/presentation.xml" not in namelist_set:
                return False, "Missing ppt/presentation.xml in OOXML package"

            # 6. Parse and check all XML and .rels parts with defusedxml
            for name in namelist_set:
                lower = name.lower()
                if lower.endswith(".xml") or lower.endswith(".rels"):
                    try:
                        raw_bytes = zf.read(name)
                        root = ET.fromstring(raw_bytes, forbid_dtd=True, forbid_entities=True)
                    except Exception as exc:
                        return False, f"Disallowed or malformed XML in {name}: {exc}"

                    # Inspect [Content_Types].xml for disallowed active MIME types
                    if name == "[Content_Types].xml":
                        for elem in root.iter():
                            content_type = (elem.get("ContentType") or "").lower()
                            if any(
                                dis in content_type
                                for dis in ("macro", "vba", "activex", "executable", "macroenabled", *(() if static_source else ("oleobject",)))
                            ):
                                return False, f"Disallowed active content type in {name}: {content_type}"

                    # Inspect .rels files for external relationships or unsafe targets
                    if lower.endswith(".rels"):
                        rels_dir = posixpath.dirname(name.replace("\\", "/"))
                        # Base dir of the part whose relationships are defined
                        base_dir = posixpath.dirname(rels_dir) if rels_dir.endswith("_rels") else rels_dir

                        for elem in root.iter():
                            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                            if tag == "Relationship":
                                target_mode = (elem.get("TargetMode") or "").strip().lower()
                                if target_mode == "external":
                                    return False, f"External relationship mode in {name}: {elem.attrib}"

                                target = elem.get("Target") or ""
                                if not target:
                                    continue
                                target_stripped = target.strip()
                                # Any URI scheme (http:, https:, file:, ftp:, javascript:, data:, etc.)
                                if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target_stripped):
                                    return False, f"External URI scheme in relationship target: {target}"
                                if target_stripped.startswith("//") or target_stripped.startswith("\\\\"):
                                    return False, f"Network/UNC path in relationship target: {target}"
                                if target_stripped.startswith("/") or target_stripped.startswith("\\"):
                                    return False, f"Absolute path in relationship target: {target}"

                                # Check path normalization
                                norm_target = posixpath.normpath(posixpath.join(base_dir, target_stripped.replace("\\", "/")))
                                if norm_target.startswith("..") or norm_target == "..":
                                    return False, f"Relationship target escapes package: {target}"
                                if base_dir.startswith("ppt") and not norm_target.startswith("ppt/"):
                                    return False, f"Relationship target escapes ppt directory: {target}"

    except zipfile.BadZipFile as exc:
        return False, f"Corrupted ZIP archive: {exc}"
    except Exception as exc:
        return False, f"Failed OOXML preflight inspection: {exc}"

    return True, "Safe"


def write_hardened_profile(user_dir: Path) -> None:
    """Write hardened profile to prevent macro execution and external updates.

    Uses recognized LibreOffice profile settings. External relationships are
    rejected separately by OOXML preflight, before document conversion.
    """
    xcu_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        '  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">\n'
        '    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>\n'
        '  </item>\n'
        '  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">\n'
        '    <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>\n'
        '  </item>\n'
        '</oor:items>\n'
    )
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "registrymodifications.xcu").write_text(xcu_content, encoding="utf-8")


def find_soffice_executable() -> Optional[Path]:
    """Discover LibreOffice / soffice executable without hard-coded user-specific paths."""
    # 1. Explicit environment variable overrides
    for env_var in ("SOFFICE_PATH", "LIBREOFFICE_PATH"):
        env_val = os.getenv(env_var)
        if env_val:
            cand = Path(env_val).expanduser().resolve()
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand

    # 2. PATH resolution
    for name in ("soffice", "libreoffice"):
        which_path = shutil.which(name)
        if which_path:
            cand = Path(which_path).resolve()
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand

    # 3. Standard platform vendor locations
    candidates: List[Path] = []
    if os.name == "nt":
        candidates = [
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
        ]
    elif os.uname().sysname == "Darwin":
        candidates = [
            Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
            Path("/Applications/LibreOfficeDev.app/Contents/MacOS/soffice"),
            Path("/opt/homebrew/bin/soffice"),
            Path("/usr/local/bin/soffice"),
        ]
    else:
        candidates = [
            Path("/usr/bin/soffice"),
            Path("/usr/local/bin/soffice"),
            Path("/usr/lib/libreoffice/program/soffice"),
        ]

    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand

    return None


def is_soffice_available() -> bool:
    """Check if a working LibreOffice headless renderer is discoverable."""
    return find_soffice_executable() is not None


def get_renderer_identity(soffice_path: Path) -> str:
    """Return renderer identity string (version/hash) for deterministic cache keying."""
    try:
        res = subprocess.run(
            [str(soffice_path), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass

    try:
        st = soffice_path.stat()
        return f"soffice-{st.st_size}-{int(st.st_mtime)}"
    except Exception:
        return "soffice-unknown"


def compute_preview_cache_key(file_path: Path, renderer_id: str) -> str:
    """Compute deterministic cache key bound to source content hash and renderer identity."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    source_sha256 = hasher.hexdigest()
    combined = f"{source_sha256}:{renderer_id}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def _validate_cache_path(cache_dir: Path, filename: str) -> Path:
    """Ensure cache filename does not escape the cache directory."""
    if not re.fullmatch(r"[0-9a-f]{64}\.(pdf|png|json)", filename):
        raise ValueError(f"Tên tệp cache không hợp lệ: {filename}")
    target = (cache_dir / filename).resolve()
    if not target.is_relative_to(cache_dir.resolve()):
        raise PermissionError(f"Đường dẫn cache thoát khỏi CACHE_DIR: {target}")
    return target


def _source_identity(source: Path) -> Tuple[str, int]:
    """Inspect the source container without executing its embedded objects."""
    safe, reason = preflight_pptx_for_preview(source, static_source=True)
    if not safe or source.suffix.lower() != ".pptx":
        raise ValueError(f"Unsupported preview source: {reason}")
    with zipfile.ZipFile(source) as archive:
        root = ET.fromstring(archive.read("ppt/presentation.xml"), forbid_dtd=True, forbid_entities=True)
        ns = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
        slides = root.findall(f"{ns}sldIdLst/{ns}sldId")
        relationships = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"), forbid_dtd=True, forbid_entities=True)
        targets = {item.get("Id"): item.get("Target", "") for item in relationships}
        relation_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        if not slides or len(slides) > MAX_ZIP_ENTRIES:
            raise ValueError("Missing physical slides")
        for slide in slides:
            target = targets.get(slide.get(relation_key), "")
            name = posixpath.normpath(posixpath.join("ppt", target))
            if not target or name not in archive.namelist() or not name.startswith("ppt/slides/"):
                raise ValueError("Missing physical slide relationship")
    return hashlib.sha256(source.read_bytes()).hexdigest(), len(slides)


def _validate_static_pdf(path: Path, expected_sha: str, page_count: int) -> None:
    import pymupdf

    if not path.is_file() or not 0 < path.stat().st_size <= MAX_PPTX_FILE_SIZE:
        raise ValueError("Invalid static preview size")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
        raise ValueError("Static preview digest mismatch")
    with pymupdf.open(path) as document:
        if not document.is_pdf or document.needs_pass or document.is_encrypted or len(document) != page_count:
            raise ValueError("Invalid static PDF or physical slide count")


def register_precomputed_pptx_preview(source: Path, pdf: Path, *, renderer_id: str) -> Path:
    """Explicit local import of a static rendition; never executes Office.

    The caller supplies a rendition produced from this exact original. Hashes
    bind the pair; they establish integrity, not visual or content acceptance.
    No public endpoint imports assets and no source or retrieval data is changed.
    """
    if not isinstance(renderer_id, str) or not renderer_id.strip() or len(renderer_id) > 500:
        raise ValueError("Renderer provenance is required")
    source_sha, pages = _source_identity(source)
    if not pdf.is_file() or pdf.stat().st_size > MAX_PPTX_FILE_SIZE:
        raise ValueError("Invalid static preview size")
    payload = pdf.read_bytes()
    pdf_sha = hashlib.sha256(payload).hexdigest()
    _validate_static_pdf(pdf, pdf_sha, pages)
    manifest = {"format": "study-agent-office-preview-v1", "source_sha256": source_sha,
                "pdf_sha256": pdf_sha, "page_count": pages, "renderer_id": renderer_id.strip()}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _CONVERT_LOCK:
        target = _validate_cache_path(CACHE_DIR, f"{pdf_sha}.pdf")
        receipt = _validate_cache_path(CACHE_DIR, f"{source_sha}.json")
        # Both replacements are atomic; publish the binding only after the PDF.
        with tempfile.NamedTemporaryFile(dir=CACHE_DIR, delete=False) as temp:
            temporary_pdf = Path(temp.name)
            temp.write(payload)
        try:
            temporary_pdf.replace(target)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE_DIR, delete=False) as temp:
                temporary_receipt = Path(temp.name)
                json.dump(manifest, temp, sort_keys=True)
            try:
                temporary_receipt.replace(receipt)
            finally:
                temporary_receipt.unlink(missing_ok=True)
        finally:
            temporary_pdf.unlink(missing_ok=True)
    return target


def precomputed_pptx_preview(source: Path) -> Optional[Path]:
    """Resolve only an intact, source-bound local static rendition."""
    try:
        source_sha, pages = _source_identity(source)
        receipt = _validate_cache_path(CACHE_DIR, f"{source_sha}.json")
        if not receipt.is_file() or receipt.stat().st_size > 8192:
            return None
        manifest = json.loads(receipt.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or set(manifest) != {
                "format", "source_sha256", "pdf_sha256", "page_count", "renderer_id"}
                or manifest["format"] != "study-agent-office-preview-v1"
                or manifest["source_sha256"] != source_sha
                or type(manifest["page_count"]) is not int or manifest["page_count"] != pages
                or not isinstance(manifest["renderer_id"], str)
                or not 0 < len(manifest["renderer_id"].strip()) <= 500
                or not isinstance(manifest["pdf_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", manifest["pdf_sha256"])):
            return None
        pdf = _validate_cache_path(CACHE_DIR, f"{manifest['pdf_sha256']}.pdf")
        _validate_static_pdf(pdf, manifest["pdf_sha256"], pages)
        return pdf
    except Exception:
        return None


def is_pptx_preview_available(source: Path) -> bool:
    return precomputed_pptx_preview(source) is not None or (
        is_soffice_available() and preflight_pptx_for_preview(source)[0]
    )


def convert_pptx_to_pdf_cached(pptx_path: Path) -> Optional[Path]:
    """Convert PPTX to PDF using local LibreOffice headless with per-hash caching and security preflight."""
    if not pptx_path or not pptx_path.is_file():
        return None

    prepared = precomputed_pptx_preview(pptx_path)
    if prepared is not None:
        return prepared

    # Step 1: OOXML Security Preflight (fail closed before any subprocess invocation)
    is_safe, reason = preflight_pptx_for_preview(pptx_path)
    if not is_safe:
        logger.warning("OOXML preflight rejected PPTX %s: %s", pptx_path, reason)
        return None

    soffice = find_soffice_executable()
    if not soffice:
        return None

    renderer_id = get_renderer_identity(soffice)
    cache_key = compute_preview_cache_key(pptx_path, renderer_id)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target_pdf = _validate_cache_path(CACHE_DIR, f"{cache_key}.pdf")

    # Fast path: check valid existing cached PDF
    if target_pdf.is_file() and target_pdf.stat().st_size > 0:
        return target_pdf

    with _CONVERT_LOCK:
        # Re-check under lock in case another thread converted it
        if target_pdf.is_file() and target_pdf.stat().st_size > 0:
            return target_pdf

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            user_install_dir = tmp_dir / "user_install"
            user_dir = user_install_dir / "user"
            out_dir = tmp_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            # Disable macros using recognized isolated-profile settings.
            write_hardened_profile(user_dir)

            user_install_uri = user_install_dir.resolve().as_uri()

            cmd = [
                str(soffice),
                "--headless",
                "--invisible",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={user_install_uri}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir.resolve()),
                str(pptx_path.resolve()),
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=60,
                )
                if proc.returncode != 0:
                    logger.warning("LibreOffice conversion exited with code %d: %s", proc.returncode, proc.stderr)
                    return None
            except subprocess.TimeoutExpired:
                logger.warning("LibreOffice conversion timed out for %s", pptx_path)
                return None
            except Exception as exc:
                logger.warning("LibreOffice conversion execution error: %s", exc)
                return None

            converted_candidates = list(out_dir.glob("*.pdf"))
            if not converted_candidates or converted_candidates[0].stat().st_size == 0:
                logger.warning("No converted PDF output found for %s", pptx_path)
                return None

            converted = converted_candidates[0]
            # Write to cache atomically using temp file on the same directory
            temp_target = CACHE_DIR / f"{cache_key}.{os.getpid()}.{time.time_ns()}.tmp"
            shutil.copyfile(converted, temp_target)
            temp_target.replace(target_pdf)
            return target_pdf


def render_office_page_png(file_path: Path, page_num: int = 1, dpi: int = 150) -> Optional[bytes]:
    """Render 1-indexed slide of a .pptx presentation to PNG bytes.

    For PPTX: validates OOXML preflight, converts via local LibreOffice headless to PDF, and renders via PyMuPDF.
    For DOCX: returns None to avoid forging physical page images for logical reading sections.
    """
    import pymupdf

    if not file_path or not file_path.is_file() or page_num < 1:
        return None

    ext = file_path.suffix.lower()
    if ext != ".pptx":
        # Do not synthesize fake page images for DOCX or other formats
        return None

    pdf_path = convert_pptx_to_pdf_cached(file_path)
    if not pdf_path or not pdf_path.is_file():
        return None

    try:
        with pymupdf.open(pdf_path) as pdf_doc:
            if page_num > len(pdf_doc):
                return None
            page = pdf_doc[page_num - 1]
            width, height = page.rect.width, page.rect.height
            if width <= 0 or height <= 0:
                return None
            scale = min(1400 / width, 2200 / height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
            return pix.tobytes("png")
    except Exception as exc:
        logger.warning("PyMuPDF rendering failed for converted PDF %s: %s", pdf_path, exc)
        return None


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
        pages.append((f"{file_path.name} — Phần {idx}", lines or ["(Tài liệu trống)"]))
    if not pages:
        return [(file_path.name, ["(Tài liệu trống)"])]
    return pages
