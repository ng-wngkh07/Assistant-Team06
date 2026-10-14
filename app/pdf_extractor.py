import os
import re
import hashlib
import shutil
import zipfile
import posixpath
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional

import pymupdf

from app.config import DATA_DIR, MIN_TEXT_PER_PAGE
from app.text_cleaner import normalize_vietnamese_text
from app.vision_ocr import VisionOCRManager, BACKEND_TESSERACT
from app.source_quality import PIPELINE_VERSION, inspect_page_quality


class PDFExtractionResult:
    """Extraction result representing a document file (PDF, DOCX, TXT, MD, Image)."""

    def __init__(self, filename: str, filepath: Path, doc_type: str = "pdf", unit_name: str = "trang"):
        self.filename = filename  # Relative path or basename
        self.filepath = filepath
        self.doc_type = doc_type
        self.unit_name = unit_name
        self.clean_title = self._clean_title(filename)
        self.total_pages = 0
        self.extracted_pages: List[Dict[str, Any]] = []
        self.failed_pages: List[int] = []
        self.is_scanned: bool = False
        self.total_chars: int = 0
        self.file_size_bytes: int = 0
        self.file_hash: str = ""
        self.error_message: Optional[str] = None
        self.ocr_pages: int = 0
        self.handwriting_suspected: bool = False
        self.needs_review: bool = False
        self.pipeline_version: str = PIPELINE_VERSION
        self.quality_warnings: List[str] = []
        self.raw_capture: Dict[str, Any] = {}
        self.source_quality: Optional[Dict[str, Any]] = None

    @staticmethod
    def _clean_title(filename: str) -> str:
        """Derive a friendly readable title from filename, removing extensions and cleaning newlines."""
        name = Path(filename).stem
        name = name.replace("\n", " ").replace("\r", " ")
        name = name.replace("_", " ")
        name = re.sub(r"\s+", " ", name).strip()
        return name


class PDFExtractor:
    """Multi-format document extractor supporting PDF, DOCX, TXT, MD, and images."""

    SUPPORTED_EXTENSIONS = {
        ".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown",
        ".png", ".jpg", ".jpeg"
    }

    @staticmethod
    def ocr_available() -> bool:
        return VisionOCRManager.ocr_available(BACKEND_TESSERACT)

    @staticmethod
    def calculate_file_hash(filepath: Path) -> str:
        """Calculate SHA256 of file for incremental change detection."""
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def is_safe_path(filepath: Path, base_dir: Path) -> bool:
        """Reject symlinks that escape the base source directory."""
        try:
            resolved_base = base_dir.resolve()
            resolved_path = filepath.resolve()
            return resolved_path.is_relative_to(resolved_base)
        except Exception:
            return False

    @staticmethod
    def discover_source_files(src_dir: Path) -> Dict[str, Any]:
        """Recursively scan src_dir for all supported document files, checking symlink safety."""
        report = {
            "supported": [],
            "unsupported": [],
            "symlink_violations": [],
            "total_discovered": 0,
        }
        if not src_dir.exists():
            return report

        resolved_src = src_dir.resolve()
        for root, dirs, files in os.walk(src_dir):
            # Exclude hidden directories
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for file in sorted(files):
                if file.startswith("."):
                    continue
                path = Path(root) / file
                # Check symlink escape
                if not PDFExtractor.is_safe_path(path, resolved_src):
                    report["symlink_violations"].append(str(path))
                    continue

                ext = path.suffix.lower()
                rel_path = str(path.relative_to(src_dir))
                if ext in PDFExtractor.SUPPORTED_EXTENSIONS:
                    report["supported"].append(path)
                else:
                    report["unsupported"].append({"path": rel_path, "ext": ext})

        report["total_discovered"] = len(report["supported"])
        return report

    @classmethod
    def _ocr_page(cls, page, file_hash: str, page_num: int, *args, **kwargs) -> Any:
        """Recognize one page locally and cache it for resumable OCR."""
        source_relpath = kwargs.get("source_relpath") or (args[0] if args else "") or file_hash
        is_handwriting = kwargs.get("is_handwriting", False)
        ocr_mgr = VisionOCRManager()
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
        img_bytes = pix.tobytes("png")
        result = ocr_mgr.ocr_image_bytes(
            image_bytes=img_bytes,
            source_hash=file_hash,
            source_relpath=source_relpath,
            page_num=page_num,
            is_handwriting_suspected=is_handwriting,
        )
        return result

    @classmethod
    def extract_pdf(cls, filepath: Path, ocr: bool = False, force_ocr: bool = False, rel_path: Optional[str] = None, max_pages: int = 0) -> PDFExtractionResult:
        """Extract text from a single PDF page by page while preserving raw capture and typed positional blocks."""
        from app.config import SRC_DIR
        try:
            if rel_path:
                derived_rel = rel_path
            elif filepath.is_relative_to(SRC_DIR):
                derived_rel = str(filepath.relative_to(SRC_DIR))
            else:
                derived_rel = filepath.name
        except Exception:
            derived_rel = rel_path or filepath.name

        result = PDFExtractionResult(filename=derived_rel, filepath=filepath, doc_type="pdf", unit_name="trang")
        if (ocr or force_ocr) and not cls.ocr_available():
            result.error_message = "OCR cần Tesseract hoặc VLM cục bộ trên máy."
            return result

        try:
            result.file_size_bytes = filepath.stat().st_size
            result.file_hash = cls.calculate_file_hash(filepath)
        except Exception as e:
            result.error_message = f"Không thể đọc file hoặc tính hash: {e}"
            return result

        # Handwriting heuristic on filename and verified samples
        low_name = filepath.name.lower()
        if any(h in low_name for h in ("viet tay", "viettay", "viet_tay", "sắp xếp", "sap xep")):
            result.handwriting_suspected = True
            result.needs_review = True

        try:
            doc = pymupdf.open(str(filepath))
            total_doc_pages = len(doc)
            result.total_pages = total_doc_pages
            limit_pages = min(total_doc_pages, max_pages) if max_pages > 0 else total_doc_pages
            pages_with_significant_text = 0

            for page_num in range(1, limit_pages + 1):
                try:
                    page = doc[page_num - 1]
                    native_raw_text = page.get_text() or ""

                    # Extract typed positional blocks and assess quality indicators
                    quality_report = inspect_page_quality(page, native_raw_text, page_num)
                    page_warnings = list(quality_report.get("warnings", []))
                    page_needs_review = quality_report.get("needs_review", False)
                    page_blocks = quality_report.get("blocks", [])

                    # OCR fallback only if native text is minimal or force_ocr is explicitly requested.
                    # Complete native text is NEVER silently discarded or replaced with OCR text.
                    native_char_count = len(native_raw_text.strip())
                    ocr_run = False
                    ocr_text = ""
                    ocr_capture = None
                    if force_ocr or (ocr and native_char_count < MIN_TEXT_PER_PAGE):
                        ocr_run = True
                        try:
                            ocr_info = cls._ocr_page(
                                page, result.file_hash, page_num,
                                source_relpath=result.filename,
                                is_handwriting=result.handwriting_suspected,
                            )
                        except TypeError:
                            ocr_info = cls._ocr_page(page, result.file_hash, page_num)

                        if isinstance(ocr_info, dict):
                            ocr_text = ocr_info.get("text", "")
                            if ocr_info.get("handwriting_suspected"):
                                result.handwriting_suspected = True
                            if ocr_info.get("needs_review"):
                                page_needs_review = True
                            ocr_capture = {
                                "ocr_text": ocr_text,
                                "char_count": len(ocr_text),
                                "has_text": len(ocr_text.strip()) > 0,
                                "needs_review": bool(ocr_info.get("needs_review")),
                            }
                        else:
                            ocr_text = str(ocr_info or "")
                            ocr_capture = {
                                "ocr_text": ocr_text,
                                "char_count": len(ocr_text),
                                "has_text": len(ocr_text.strip()) > 0,
                                "needs_review": False,
                            }

                        result.ocr_pages += 1

                    page_source_text = ocr_text if (ocr_run and (force_ocr or native_char_count < MIN_TEXT_PER_PAGE)) else native_raw_text
                    cleaned_text = normalize_vietnamese_text(page_source_text)
                    char_count = len(cleaned_text)
                    has_text = char_count >= MIN_TEXT_PER_PAGE

                    if page_needs_review:
                        result.needs_review = True
                    for w in page_warnings:
                        if w not in result.quality_warnings:
                            result.quality_warnings.append(w)

                    native_capture_data = {
                        "raw_text": native_raw_text,
                        "char_count": len(native_raw_text),
                        "has_text": len(native_raw_text.strip()) > 0
                    }

                    page_entry = {
                        "page_num": page_num,
                        "text": cleaned_text,
                        "char_count": char_count,
                        "has_text": has_text,
                        "unit_type": "trang",
                        "raw_text": native_raw_text,
                        "raw_capture": native_capture_data,
                        "ocr_text": ocr_text if ocr_run else None,
                        "ocr_capture": ocr_capture,
                        "blocks": page_blocks,
                        "quality_warnings": page_warnings,
                        "needs_review": page_needs_review,
                        "indicators": quality_report.get("indicators", {})
                    }

                    if has_text:
                        pages_with_significant_text += 1
                        result.total_chars += char_count

                    result.extracted_pages.append(page_entry)
                except Exception as pe:
                    result.failed_pages.append(page_num)
                    result.needs_review = True
                    result.extracted_pages.append({
                        "page_num": page_num,
                        "text": "",
                        "raw_text": "",
                        "raw_capture": {"raw_text": "", "char_count": 0, "has_text": False},
                        "ocr_text": None,
                        "ocr_capture": None,
                        "blocks": [],
                        "quality_warnings": [f"extraction_error: {pe}"],
                        "needs_review": True,
                        "indicators": {},
                        "char_count": 0,
                        "has_text": False,
                        "unit_type": "trang",
                        "error": str(pe)
                    })

            doc.close()
            if result.failed_pages:
                result.needs_review = True
                if not result.error_message:
                    result.error_message = f"Lỗi trích xuất tại các trang: {result.failed_pages}"

            # Record overall raw capture and source quality metadata
            result.raw_capture = {
                "file_hash": result.file_hash,
                "total_pages": result.total_pages,
                "pipeline_version": result.pipeline_version,
                "pages_summary": [
                    {
                        "page_num": p["page_num"],
                        "raw_char_count": len(p.get("raw_text", "")),
                        "has_text": p["has_text"],
                        "needs_review": p.get("needs_review", False),
                        "warnings": p.get("quality_warnings", [])
                    }
                    for p in result.extracted_pages
                ]
            }
            result.source_quality = {
                "pipeline_version": result.pipeline_version,
                "file_hash": result.file_hash,
                "needs_review": result.needs_review,
                "quality_warnings": sorted(list(set(result.quality_warnings))),
                "total_pages": result.total_pages
            }

            if result.total_pages > 0:
                text_ratio = pages_with_significant_text / result.total_pages
                if pages_with_significant_text == 0 or text_ratio < 0.05:
                    result.is_scanned = True
        except Exception as e:
            result.error_message = f"Lỗi mở/đọc PDF: {e}"

        return result

    @classmethod
    def extract_docx(cls, filepath: Path) -> PDFExtractionResult:
        """Extract text from DOCX file using built-in zipfile and XML parser."""
        result = PDFExtractionResult(filename=filepath.name, filepath=filepath, doc_type="docx", unit_name="đoạn")
        try:
            result.file_size_bytes = filepath.stat().st_size
            result.file_hash = cls.calculate_file_hash(filepath)
            with zipfile.ZipFile(filepath) as z:
                xml_content = z.read("word/document.xml")
            root = ET.fromstring(xml_content)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paras = []
            for p in root.iterfind(".//w:p", ns):
                texts = [node.text for node in p.iterfind(".//w:t", ns) if node.text]
                if texts:
                    clean_para = normalize_vietnamese_text("".join(texts))
                    if clean_para.strip():
                        paras.append(clean_para.strip())

            result.total_pages = len(paras)
            for idx, para in enumerate(paras, 1):
                result.total_chars += len(para)
                result.extracted_pages.append({
                    "page_num": idx,
                    "text": para,
                    "raw_text": para,
                    "raw_capture": {"raw_text": para, "char_count": len(para), "has_text": True},
                    "blocks": [{"block_id": f"p{idx}_b0", "bbox": [0, 0, 0, 0], "type": "prose", "text": para}],
                    "quality_warnings": [],
                    "needs_review": False,
                    "char_count": len(para),
                    "has_text": True,
                    "unit_type": "đoạn"
                })
        except Exception as e:
            result.error_message = f"Lỗi đọc tài liệu DOCX: {e}"
        return result

    @classmethod
    def extract_pptx(cls, filepath: Path) -> PDFExtractionResult:
        """Read slides in presentation order, including table text and speaker notes."""
        result = PDFExtractionResult(filepath.name, filepath, doc_type="pptx", unit_name="slide")
        ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
              "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

        def paragraphs(xml):
            root = ET.fromstring(xml)
            return [''.join(n.text or '' for n in p.iterfind('.//a:t', ns))
                    for p in root.iter('{'+ns['a']+'}p')]

        def target(base, relation):
            if relation.get('TargetMode') == 'External':
                raise ValueError('External slide relationship is not a document source')
            value = posixpath.normpath(posixpath.join(base, relation['Target']))
            if not value.startswith('ppt/'):
                raise ValueError('Slide relationship escapes ppt directory')
            return value

        try:
            result.file_size_bytes = filepath.stat().st_size
            result.file_hash = cls.calculate_file_hash(filepath)
            with zipfile.ZipFile(filepath) as z:
                presentation = ET.fromstring(z.read('ppt/presentation.xml'))
                rels = {x.attrib['Id']: x.attrib for x in ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))}
                slides = presentation.findall('p:sldIdLst/p:sldId', ns)
                result.total_pages = len(slides)
                for page, slide in enumerate(slides, 1):
                    path = target('ppt', rels[slide.attrib['{'+rns+'}id']])
                    text = '\n'.join(p for p in paragraphs(z.read(path)) if p.strip())
                    relpath = posixpath.join(posixpath.dirname(path), '_rels', posixpath.basename(path)+'.rels')
                    if relpath in z.namelist():
                        for rel in ET.fromstring(z.read(relpath)):
                            if rel.attrib.get('Type', '').endswith('/notesSlide'):
                                notes = '\n'.join(p for p in paragraphs(z.read(target(posixpath.dirname(path), rel.attrib))) if p.strip())
                                if notes:
                                    text += '\n\n[GHI CHÚ TRANG CHIẾU]\n' + notes
                    has_text = bool(text.strip())
                    if not has_text:
                        result.failed_pages.append(page)
                        result.needs_review = True
                    result.total_chars += len(text)
                    result.extracted_pages.append(dict(
                        page_num=page, text=text, raw_text=text,
                        raw_capture={"raw_text": text, "char_count": len(text), "has_text": has_text},
                        blocks=[{"block_id": f"p{page}_b0", "bbox": [0, 0, 0, 0], "type": "prose", "text": text}],
                        quality_warnings=[] if has_text else ["image_only_slide_requires_review"],
                        needs_review=not has_text,
                        char_count=len(text), has_text=has_text, unit_type='slide'
                    ))
                result.is_scanned = not any(p['has_text'] for p in result.extracted_pages)
        except Exception as e:
            result.error_message = f'Lỗi đọc tài liệu PPTX: {e}'
        return result

    @classmethod
    def extract_text(cls, filepath: Path) -> PDFExtractionResult:
        """Extract text from TXT or Markdown file, splitting into paragraphs."""
        doc_type = "md" if filepath.suffix.lower() in (".md", ".markdown") else "txt"
        result = PDFExtractionResult(filename=filepath.name, filepath=filepath, doc_type=doc_type, unit_name="đoạn")
        try:
            result.file_size_bytes = filepath.stat().st_size
            result.file_hash = cls.calculate_file_hash(filepath)
            try:
                content = filepath.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = filepath.read_text(encoding="latin-1")

            paras = [normalize_vietnamese_text(p).strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
            result.total_pages = len(paras)
            for idx, para in enumerate(paras, 1):
                result.total_chars += len(para)
                result.extracted_pages.append({
                    "page_num": idx,
                    "text": para,
                    "raw_text": para,
                    "raw_capture": {"raw_text": para, "char_count": len(para), "has_text": True},
                    "blocks": [{"block_id": f"p{idx}_b0", "bbox": [0, 0, 0, 0], "type": "prose", "text": para}],
                    "quality_warnings": [],
                    "needs_review": False,
                    "char_count": len(para),
                    "has_text": True,
                    "unit_type": "đoạn"
                })
        except Exception as e:
            result.error_message = f"Lỗi đọc tệp văn bản: {e}"
        return result

    @classmethod
    def extract_image(cls, filepath: Path, ocr: bool = True) -> PDFExtractionResult:
        """Extract text from image file using PyMuPDF and OCR."""
        result = PDFExtractionResult(filename=filepath.name, filepath=filepath, doc_type="image", unit_name="hình ảnh")
        try:
            result.file_size_bytes = filepath.stat().st_size
            result.file_hash = cls.calculate_file_hash(filepath)
            result.total_pages = 1

            if ocr and cls.ocr_available():
                ocr_mgr = VisionOCRManager()
                raw_bytes = filepath.read_bytes()
                ocr_res = ocr_mgr.ocr_image_bytes(
                    image_bytes=raw_bytes,
                    source_hash=result.file_hash,
                    source_relpath=filepath.name,
                    page_num=1,
                    is_handwriting_suspected=result.handwriting_suspected,
                )
                text = ocr_res.get("text", "")
                result.needs_review = bool(ocr_res.get("needs_review", True) or not ocr_res.get("is_verified", False))
                result.quality_warnings = ["image_ocr_requires_review"] if result.needs_review else []
                result.ocr_pages = 1
                result.total_chars = len(text)
                result.extracted_pages.append({
                    "page_num": 1,
                    "text": text,
                    "raw_text": text,
                    "raw_capture": {"raw_text": text, "char_count": len(text), "has_text": len(text) > 0},
                    "blocks": [{"block_id": "p1_b0", "bbox": [0, 0, 0, 0], "type": "image", "text": text}],
                    "quality_warnings": result.quality_warnings,
                    "needs_review": result.needs_review,
                    "char_count": len(text),
                    "has_text": len(text) > 0,
                    "unit_type": "hình ảnh"
                })
            else:
                result.is_scanned = True
                result.needs_review = True
                result.quality_warnings = ["unscanned_image_requires_ocr"]
                result.extracted_pages.append({
                    "page_num": 1,
                    "text": "",
                    "raw_text": "",
                    "raw_capture": {"raw_text": "", "char_count": 0, "has_text": False},
                    "blocks": [],
                    "quality_warnings": ["unscanned_image_requires_ocr"],
                    "needs_review": True,
                    "char_count": 0,
                    "has_text": False,
                    "unit_type": "hình ảnh"
                })
        except Exception as e:
            result.error_message = f"Lỗi xử lý tệp ảnh: {e}"
        return result

    @classmethod
    def extract_file(cls, filepath: Path, ocr: bool = False, force_ocr: bool = False, rel_path: Optional[str] = None, max_pages: int = 0) -> PDFExtractionResult:
        """Dispatch extraction based on file extension."""
        ext = filepath.suffix.lower()
        if ext == ".pdf":
            return cls.extract_pdf(filepath, ocr=ocr, force_ocr=force_ocr, rel_path=rel_path, max_pages=max_pages)
        elif ext == ".docx":
            return cls.extract_docx(filepath)
        elif ext == ".pptx":
            return cls.extract_pptx(filepath)
        elif ext in (".txt", ".md", ".markdown"):
            return cls.extract_text(filepath)
        elif ext in (".png", ".jpg", ".jpeg"):
            return cls.extract_image(filepath, ocr=ocr or force_ocr)
        else:
            res = PDFExtractionResult(filename=rel_path or filepath.name, filepath=filepath, doc_type="unsupported")
            res.error_message = f"Định dạng không được hỗ trợ: {ext}"
            return res

    @classmethod
    def scan_directory(cls, dir_path: Path) -> List[PDFExtractionResult]:
        """Scan all supported files in directory recursively and return extraction results."""
        if not dir_path.exists():
            return []

        discovery = cls.discover_source_files(dir_path)
        results = []
        for p in discovery["supported"]:
            results.append(cls.extract_file(p))
        return results


# Backward compatibility alias
DocumentExtractor = PDFExtractor
