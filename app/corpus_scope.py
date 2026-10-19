"""Approved corpus scope and page exclusion validation.

Fails closed on any invalid, tampered, unauthorized, wildcard, or out-of-bounds exclusion manifest.
Blocks excluded pages from both training dataset producers and fine-tuning consumers.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


def validate_page_exclusion_manifest(
    root: Path,
    manifest_entry: dict,
    source_files: Optional[Dict[str, Path]] = None,
) -> dict:
    """Validate a page exclusion manifest referenced by corpus completion policy.

    Fails closed by raising ValueError on any validation failure.
    """
    if not isinstance(manifest_entry, dict):
        raise ValueError("page_exclusion_manifest must be a dictionary")

    path_str = manifest_entry.get("path")
    expected_sha256 = manifest_entry.get("sha256")
    if not path_str or not isinstance(path_str, str):
        raise ValueError("Missing or invalid manifest path in policy")
    if not expected_sha256 or not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError("Missing or invalid manifest sha256 in policy")

    root_resolved = root.resolve()
    rel_path = Path(path_str)

    raw_target = (root / rel_path) if not rel_path.is_absolute() else rel_path
    p_cur = raw_target
    while p_cur != root_resolved and p_cur != p_cur.parent:
        if p_cur.is_symlink():
            raise ValueError(f"Manifest path or ancestor must not be a symlink: {p_cur}")
        p_cur = p_cur.parent

    # Prevent path traversal outside root
    try:
        if rel_path.is_absolute():
            target_path = rel_path.resolve()
        else:
            target_path = (root / rel_path).resolve()
        target_path.relative_to(root_resolved)
    except (ValueError, RuntimeError):
        raise ValueError(f"Manifest path outside project root: {path_str}")

    if not target_path.is_file():
        raise ValueError(f"Manifest file not found: {target_path}")

    actual_bytes = target_path.read_bytes()
    actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(f"Manifest sha256 mismatch: {actual_sha256} != {expected_sha256}")

    try:
        manifest = json.loads(actual_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Malformed manifest JSON: {exc}")

    if not isinstance(manifest, dict):
        raise ValueError("Manifest root must be a JSON object")

    if manifest.get("schema") != "approved-page-exclusions-v1":
        raise ValueError(f"Unsupported manifest schema: {manifest.get('schema')}")

    decision_id = manifest.get("decision_id")
    if not decision_id or not isinstance(decision_id, str):
        raise ValueError("Missing decision_id in exclusion manifest")

    if manifest.get("approved_by") != "user":
        raise ValueError(f"Unauthorized approval: approved_by must be 'user', got {manifest.get('approved_by')}")

    approved_at = manifest.get("approved_at")
    if not approved_at or not isinstance(approved_at, str) or not approved_at.strip():
        raise ValueError("Missing or empty approved_at in exclusion manifest")

    approval = manifest.get("approval")
    if not approval or not isinstance(approval, str) or not approval.startswith("EXCLUDE_"):
        raise ValueError(f"Invalid approval declaration: {approval}")

    approved_count = manifest.get("approved_count")
    if not isinstance(approved_count, int) or approved_count <= 0:
        raise ValueError("approved_count must be a positive integer")

    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != approved_count:
        raise ValueError(f"pages count mismatch: {len(pages) if isinstance(pages, list) else None} != {approved_count}")

    seen_pages: Set[Tuple[str, int]] = set()
    validated_pages: List[dict] = []
    src_dir = (root / "src").resolve()

    for p in pages:
        if not isinstance(p, dict):
            raise ValueError("Each page item in manifest must be a dictionary")

        reason = p.get("reason")
        if not reason or not isinstance(reason, list) or len(reason) == 0:
            raise ValueError("Missing reason for page exclusion")
        if not all(isinstance(r, str) and r.strip() for r in reason):
            raise ValueError("Invalid reason item in page exclusion")

        filename = p.get("filename")
        if not filename or not isinstance(filename, str):
            raise ValueError(f"Invalid filename: {filename}")
        if filename.startswith((".", "/", "\\")) or "/../" in filename or "\\..\\" in filename:
            raise ValueError(f"Non-canonical source filename: {filename}")
        if any(c in filename for c in ("*", "?", "[", "]")):
            raise ValueError(f"Wildcard filename not permitted: {filename}")

        if source_files is not None and filename not in source_files:
            raise ValueError(f"Excluded source file '{filename}' is not a canonical discovered document")

        src_path = (root / "src" / filename).resolve()
        try:
            src_path.relative_to(src_dir)
        except (ValueError, RuntimeError):
            raise ValueError(f"Source file path outside src directory: {filename}")

        if not src_path.is_file():
            raise ValueError(f"Source file does not exist: {filename}")

        actual_src_hash = hashlib.sha256(src_path.read_bytes()).hexdigest()
        if p.get("source_sha256") != actual_src_hash:
            raise ValueError(f"Source sha256 mismatch for {filename}: {p.get('source_sha256')} != {actual_src_hash}")

        page_num = p.get("page")
        if not isinstance(page_num, int) or page_num <= 0:
            raise ValueError(f"Page must be a positive integer, got: {page_num}")

        # Check physical page bound
        if src_path.suffix.lower() == ".pdf":
            try:
                import pymupdf
                with pymupdf.open(src_path) as pdf_doc:
                    total_physical = len(pdf_doc)
            except Exception:
                import fitz
                with fitz.open(src_path) as pdf_doc:
                    total_physical = len(pdf_doc)
        else:
            from app.pdf_extractor import PDFExtractor
            total_physical = PDFExtractor.extract_file(src_path).total_pages

        if page_num > total_physical:
            raise ValueError(f"Page {page_num} out of range for {filename} (max physical pages {total_physical})")

        key = (filename, page_num)
        if key in seen_pages:
            raise ValueError(f"Duplicate page exclusion entry: {key}")
        seen_pages.add(key)

        img_path_str = p.get("image_path")
        if not img_path_str or not isinstance(img_path_str, str):
            raise ValueError(f"Missing image_path for {key}")

        img_path = Path(img_path_str)
        raw_img = (root / img_path) if not img_path.is_absolute() else img_path
        p_img_cur = raw_img
        while p_img_cur != root_resolved and p_img_cur != p_img_cur.parent:
            if p_img_cur.is_symlink():
                raise ValueError(f"Image path or ancestor must not be a symlink: {p_img_cur}")
            p_img_cur = p_img_cur.parent

        try:
            if img_path.is_absolute():
                resolved_img = img_path.resolve()
            else:
                resolved_img = (root / img_path).resolve()
            resolved_img.relative_to(root_resolved)
        except (ValueError, RuntimeError):
            raise ValueError(f"Image path outside project root: {img_path_str}")

        if not resolved_img.is_file():
            raise ValueError(f"Image file not found: {resolved_img}")

        actual_img_hash = hashlib.sha256(resolved_img.read_bytes()).hexdigest()
        if p.get("image_sha256") != actual_img_hash:
            raise ValueError(f"Image sha256 mismatch for {key}: {p.get('image_sha256')} != {actual_img_hash}")

        validated_pages.append({
            "filename": filename,
            "page": page_num,
            "source_sha256": actual_src_hash,
            "image_path": str(resolved_img),
            "image_sha256": actual_img_hash,
            "reason": p.get("reason", []),
        })

    return {
        "decision_id": decision_id,
        "approved_by": manifest["approved_by"],
        "approved_at": manifest.get("approved_at"),
        "approval": approval,
        "approved_count": approved_count,
        "pages": validated_pages,
        "page_set": seen_pages,
        "manifest_path": str(target_path),
        "manifest_sha256": actual_sha256,
    }


def get_validated_scope(root: Optional[Path] = None) -> dict:
    """Retrieve and validate current scope from policy.

    Returns dict with scope info. If no manifest is configured, returns empty exclusion set.
    """
    if root is None:
        from app.config import BASE_DIR
        root = BASE_DIR

    policy_path = root / "data/runtime/corpus_completion_policy.json"
    if not policy_path.exists():
        return {
            "decision_id": None,
            "page_set": set(),
            "pages": [],
            "has_exclusions": False,
        }

    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Corrupt corpus completion policy: {exc}")

    if not isinstance(policy, dict):
        raise ValueError("Corpus completion policy root must be a JSON object")

    manifest_entry = policy.get("page_exclusion_manifest")
    if not manifest_entry:
        return {
            "decision_id": None,
            "page_set": set(),
            "pages": [],
            "has_exclusions": False,
        }

    scope_info = validate_page_exclusion_manifest(root, manifest_entry)
    scope_info["has_exclusions"] = True
    return scope_info


def get_excluded_pages(root: Optional[Path] = None) -> Set[Tuple[str, int]]:
    """Return set of (filename, page_num) tuples excluded by valid policy manifest."""
    scope = get_validated_scope(root)
    return scope["page_set"]


def is_page_excluded(filename: str, page_num: int, root: Optional[Path] = None) -> bool:
    """Check if a specific page is excluded from training scope."""
    return (filename, page_num) in get_excluded_pages(root)


def validate_canonical_filename(filename: str, root: Optional[Path] = None) -> str:
    """Validate canonical source filename (no aliases, traversal, or wildcards) and existence if root supplied."""
    if not isinstance(filename, str):
        raise ValueError(f"Invalid filename type: {type(filename)}")
    clean = filename.strip()
    if not clean or clean.startswith((".", "/", "\\")) or "/../" in clean or "\\..\\" in clean:
        raise ValueError(f"Non-canonical or alias source filename: {filename}")
    norm = Path(clean).as_posix()
    if norm != clean or norm.startswith((".", "/")):
        raise ValueError(f"Non-canonical source filename: {filename}")
    if any(c in clean for c in ("*", "?", "[", "]")):
        raise ValueError(f"Wildcard filename not permitted: {filename}")

    if root is not None:
        src_dir = (root / "src").resolve()
        target = (src_dir / clean).resolve()
        try:
            target.relative_to(src_dir)
        except (ValueError, RuntimeError):
            raise ValueError(f"Source file path outside src directory: {filename}")
        if not target.is_file():
            raise ValueError(f"Unknown or nonexistent source file: {filename}")
    return clean


def validate_page_number(page_val, filename: str, root: Optional[Path] = None) -> int:
    """Validate that page_val is a positive physical integer bound."""
    if isinstance(page_val, bool):
        raise ValueError(f"Page number cannot be a boolean: {page_val}")
    if isinstance(page_val, float):
        raise ValueError(f"Page number cannot be a fraction/float: {page_val}")
    if isinstance(page_val, str) and "." in page_val:
        raise ValueError(f"Page number cannot be a fraction: {page_val}")
    try:
        page_int = int(page_val)
    except (ValueError, TypeError):
        raise ValueError(f"Malformed page number: {page_val}")

    if page_int <= 0:
        raise ValueError(f"Page number must be a positive integer, got: {page_int}")

    if root is not None:
        src_path = root / "src" / filename
        if src_path.is_file():
            total_pages = None
            if src_path.suffix.lower() == ".pdf":
                try:
                    import pymupdf
                    with pymupdf.open(src_path) as pdf:
                        total_pages = len(pdf)
                except Exception:
                    try:
                        import fitz
                        with fitz.open(src_path) as pdf:
                            total_pages = len(pdf)
                    except Exception:
                        total_pages = None
            else:
                try:
                    from app.pdf_extractor import PDFExtractor
                    ext_res = PDFExtractor.extract_file(src_path)
                    if ext_res.error_message:
                        total_pages = None
                    else:
                        total_pages = ext_res.total_pages
                except Exception:
                    total_pages = None

            if total_pages is None or total_pages <= 0:
                raise ValueError(f"Không thể xác định số trang vật lý của tệp nguồn: {filename}")
            if page_int > total_pages:
                raise ValueError(f"Page {page_int} out of range for {filename} (max physical pages: {total_pages})")

    return page_int


def extract_and_validate_provenance(
    item: dict,
    root: Optional[Path] = None,
    excluded_pages: Optional[Set[Tuple[str, int]]] = None,
    allow_empty: bool = False,
) -> List[Tuple[str, int]]:
    """Recursively extract and strictly validate all source/page references from a record.

    Fails closed on half-metadata, non-canonical filenames, unknown files,
    invalid page bounds, or references to excluded pages.
    """
    found: List[Tuple[str, int]] = []

    # Check top-level provenance
    has_fn = ("source_file" in item and item["source_file"] is not None) or ("filename" in item and item["filename"] is not None)
    has_pg = ("page" in item and item["page"] is not None) or ("page_num" in item and item["page_num"] is not None)

    if has_fn or has_pg:
        if not (has_fn and has_pg):
            raise ValueError(f"Half-metadata in record: has_fn={has_fn}, has_pg={has_pg}")
        raw_fn = item.get("source_file") or item.get("filename")
        raw_pg = item.get("page") if item.get("page") is not None else item.get("page_num")
        canon_fn = validate_canonical_filename(raw_fn, root=root)
        valid_pg = validate_page_number(raw_pg, canon_fn, root=root)
        if excluded_pages and (canon_fn, valid_pg) in excluded_pages:
            raise ValueError(
                f"CHẶN HUẤN LUYỆN: bản ghi chứa trang bị loại khỏi phạm vi: {canon_fn} trang {valid_pg}"
            )
        found.append((canon_fn, valid_pg))

    # Check nested sources
    if "sources" in item:
        sources = item["sources"]
        if not isinstance(sources, list) or len(sources) == 0:
            raise ValueError("Malformed sources: must be a non-empty list")
        for s in sources:
            if not isinstance(s, dict):
                raise ValueError("Malformed source item: must be a dict")
            s_fn = s.get("source_file") or s.get("filename") or (item.get("source_file") or item.get("filename"))
            s_pg = s.get("page") if s.get("page") is not None else s.get("page_num")
            if s_fn is None or s_pg is None:
                raise ValueError("Malformed source item: missing filename or page")
            canon_fn = validate_canonical_filename(s_fn, root=root)
            valid_pg = validate_page_number(s_pg, canon_fn, root=root)
            if excluded_pages and (canon_fn, valid_pg) in excluded_pages:
                raise ValueError(
                    f"CHẶN HUẤN LUYỆN: bản ghi chứa nguồn bị loại khỏi phạm vi: {canon_fn} trang {valid_pg}"
                )
            found.append((canon_fn, valid_pg))

    # Check pair layout
    if "pair" in item:
        pair = item["pair"]
        if not isinstance(pair, dict):
            raise ValueError("Malformed pair: must be a dict")
        pair_fn = pair.get("filename") or pair.get("source_file")
        pair_sources = pair.get("sources")
        if not isinstance(pair_sources, list) or len(pair_sources) == 0:
            raise ValueError("Malformed pair sources: must be a non-empty list")
        for s in pair_sources:
            if not isinstance(s, dict):
                raise ValueError("Malformed pair source item: must be a dict")
            s_fn = s.get("source_file") or s.get("filename") or pair_fn
            s_pg = s.get("page") if s.get("page") is not None else s.get("page_num")
            if s_fn is None or s_pg is None:
                raise ValueError("Malformed pair source item: missing filename or page")
            canon_fn = validate_canonical_filename(s_fn, root=root)
            valid_pg = validate_page_number(s_pg, canon_fn, root=root)
            if excluded_pages and (canon_fn, valid_pg) in excluded_pages:
                raise ValueError(
                    f"CHẶN HUẤN LUYỆN: bản ghi chứa nguồn bị loại khỏi phạm vi: {canon_fn} trang {valid_pg}"
                )
            found.append((canon_fn, valid_pg))

    if not found and not allow_empty:
        raise ValueError("Missing source page provenance in record")

    return found


def verify_dataset_scope(
    data_dir: Path,
    excluded_pages: Optional[Set[Tuple[str, int]]] = None,
    root: Optional[Path] = None,
) -> None:
    """Preflight check: verify that a dataset does not reference any excluded pages.

    Fails closed on malformed provenance, unknown provenance, or references to excluded pages.
    """
    if excluded_pages is None:
        excluded_pages = get_excluded_pages(root)

    if not excluded_pages:
        return

    # Scan split files first
    split_lines_count = 0
    for split_filename in ("train.jsonl", "valid.jsonl"):
        split_path = data_dir / split_filename
        if not split_path.is_file():
            continue
        for line_num, line in enumerate(split_path.read_text(encoding="utf-8").splitlines(), 1):
            line_str = line.strip()
            if not line_str:
                continue
            split_lines_count += 1
            try:
                item = json.loads(line_str)
            except Exception as exc:
                raise ValueError(f"Malformed JSON at line {line_num} in {split_filename}: {exc}")
            if not isinstance(item, dict):
                raise ValueError(f"Item at line {line_num} in {split_filename} must be a JSON object")

            extract_and_validate_provenance(
                item,
                root=root,
                excluded_pages=excluded_pages,
                allow_empty=True,
            )

    # Check approved_manifest.jsonl
    manifest_file = data_dir / "approved_manifest.jsonl"
    if split_lines_count > 0 and (not manifest_file.is_file() or not manifest_file.read_text(encoding="utf-8").strip()):
        raise ValueError(f"Do not allow an empty/missing manifest to admit nonempty messages dataset in {data_dir.name}")

    if manifest_file.is_file():
        lines = [line.strip() for line in manifest_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        for line_num, line_str in enumerate(lines, 1):
            try:
                record = json.loads(line_str)
            except Exception as exc:
                raise ValueError(f"Malformed manifest JSON at line {line_num} in {manifest_file.name}: {exc}")

            if not isinstance(record, dict):
                raise ValueError(f"Malformed record at line {line_num} in {manifest_file.name}")

            extract_and_validate_provenance(
                record,
                root=root,
                excluded_pages=excluded_pages,
                allow_empty=False,
            )
