"""Shared synthetic review fixture helpers for knowledge pack tests."""
import hashlib
import json
from pathlib import Path
import sqlite3

from app.chunker import TextChunker
from app.indexer import pack_vector
from app.source_page_reviews import reviewed_page_text, validate_page_review


# Minimal 1x1 valid PNG bytes
DUMMY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02"
    b"\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)


def create_synthetic_review(
    target_dir: Path,
    filename: str,
    page: int,
    source_sha256: str,
    transcript: str = "Encapsulation keeps state behind a public interface.\n",
    *,
    kind: str = "printed",
    completeness: str = "complete",
    unresolved: list = None,
    image_reviewed: bool = True,
    reviewer: str = "synthetic-reviewer",
    editorial: str = "",
    visual_description: str = "",
) -> tuple[Path, dict]:
    """Create a fully valid synthetic page review matching validate_page_review."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{Path(filename).stem}_p{page}"
    image_path = target_dir / f"{stem}.png"
    image_path.write_bytes(DUMMY_PNG_BYTES)
    image_sha256 = hashlib.sha256(DUMMY_PNG_BYTES).hexdigest()

    raw_data = {
        "source_relpath": filename,
        "page_num": page,
        "source_hash": source_sha256,
        "image_hash": image_sha256,
    }
    raw_path = target_dir / f"{stem}-raw.json"
    raw_bytes = json.dumps(raw_data, indent=2).encode("utf-8")
    raw_path.write_bytes(raw_bytes)
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    review = {
        "filename": filename,
        "page": page,
        "source_sha256": source_sha256,
        "image_path": str(image_path),
        "image_sha256": image_sha256,
        "raw_capture_path": str(raw_path),
        "raw_capture_sha256": raw_sha256,
        "reviewed_at": "2026-10-27T00:00:00Z",
        "reviewer": reviewer,
        "kind": kind,
        "image_reviewed": image_reviewed,
        "completeness": completeness,
        "transcript": transcript if kind not in ("blank", "visual") else "",
        "unresolved": unresolved or [],
        "editorial": editorial,
    }
    if kind == "visual":
        review["visual_description"] = visual_description or "Visual diagram description"
        review["visible_text_status"] = "none"
        review["transcript"] = ""

    review_file = target_dir / f"{stem}-review.json"
    review_file.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
    return review_file, review


def create_synthetic_review_manifest(
    manifest_path: Path,
    reviews: list[dict],
) -> Path:
    """Create a review manifest matching existing publisher review-results format."""
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_entries = []
    for r in reviews:
        if "review_path" in r:
            manifest_entries.append(r)
        else:
            manifest_entries.append(r)
    data = {
        "status": "STAGED_NOT_APPLIED",
        "serving_database_changed": False,
        "training_authorized": False,
        "reviews": manifest_entries,
    }
    manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def setup_reviewed_chunk_in_db(
    db: sqlite3.Connection,
    doc_id: int,
    filename: str,
    clean_title: str,
    page: int,
    review: dict,
    identity: str,
):
    """Align SQLite chunks and vector provenance with the reviewed page text."""
    expected_text = reviewed_page_text(dict(review, editorial=""))
    db.execute("DELETE FROM chunks WHERE doc_id=? AND page_num=?", (doc_id, page))
    if review["kind"] == "blank" or not expected_text.strip():
        return []

    chunks = TextChunker.chunk_page(
        book_title=clean_title,
        filename=filename,
        page_num=page,
        page_text=expected_text,
    )
    blob = pack_vector([1.0] + [0.0] * 1023)
    blob_hash = hashlib.sha256(blob).hexdigest()
    for c in chunks:
        cur = db.execute(
            """INSERT INTO chunks(doc_id, book_title, filename, page_num, chunk_index, text, embedding)
               VALUES(?,?,?,?,?,?,?)""",
            (doc_id, clean_title, filename, page, c["chunk_index"], c["text"], blob),
        )
        cid = cur.lastrowid
        text_sha = hashlib.sha256(c["text"].encode("utf-8")).hexdigest()
        db.execute(
            """INSERT OR REPLACE INTO corpus_vector_provenance(chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at)
               VALUES(?,?,?,?,1024,1,CURRENT_TIMESTAMP)""",
            (cid, text_sha, identity, blob_hash),
        )
    return chunks
