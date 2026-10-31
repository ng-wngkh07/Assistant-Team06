"""Combine independently reviewed training sets without changing either source."""

import hashlib
import json
import sqlite3
from pathlib import Path

from app.config import DB_PATH
from app.training_data import basic_record_quality, write_training_files
from app.text_cleaner import sanitize_for_prompt_context


def _approved_records(directory: Path) -> tuple[list[dict], str]:
    approval = json.loads((directory / "approval.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(
        (directory / "train.jsonl").read_bytes() + (directory / "valid.jsonl").read_bytes()
    ).hexdigest()
    if approval.get("dataset_sha256") != digest:
        raise ValueError(f"Bộ dữ liệu {directory} không khớp approval.json")
    records = [json.loads(line) for line in (directory / "approved_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    if any(not basic_record_quality(record) for record in records):
        raise ValueError(f"Bộ dữ liệu {directory} có bản ghi không đạt kiểm tra cơ bản")
    return records, digest


def _source_is_current(db: sqlite3.Connection, pair: dict) -> bool:
    for source in pair["sources"]:
        filename = source.get("filename", pair["filename"])
        rows = db.execute(
            "SELECT text FROM chunks WHERE filename=? AND page_num=?",
            (filename, source["page"]),
        ).fetchall()
        if not any(sanitize_for_prompt_context(row[0][:650]) == source["text"] for row in rows):
            return False
    return True


def merge(previous: Path, additions: Path, destination: Path, db_path: Path = DB_PATH) -> dict:
    old_records, old_digest = _approved_records(previous)
    new_records, new_digest = _approved_records(additions)
    new_summary = json.loads((additions / "summary.json").read_text(encoding="utf-8"))
    if new_summary.get("audit_complete") is not True:
        raise ValueError("Chưa kiểm tra xong tất cả bản ghi bổ sung")
    if not new_records:
        raise ValueError("Không có bản ghi bổ sung nào đạt kiểm tra")
    selection_path = additions / "source_selection.json"
    if selection_path.exists():
        selected = set(json.loads(selection_path.read_text(encoding="utf-8"))["new_files"])
        covered = {record["pair"]["filename"] for record in new_records
                   if not record["pair"].get("cross_book") and record["items"]}
        missing = selected - covered
        if missing:
            raise ValueError("Sách mới chưa có ví dụ suy luận được duyệt: " + ", ".join(sorted(missing)))
    records = []
    seen = set()
    with sqlite3.connect(db_path) as db:
        for record in old_records + new_records:
            pair = record["pair"]
            if not _source_is_current(db, pair):
                raise ValueError(f'Đoạn nguồn không còn khớp chỉ mục: {pair["filename"]}')
            signature = hashlib.sha256(json.dumps(
                [(source.get("filename", pair["filename"]), source["page"], source["text"])
                 for source in pair["sources"]], ensure_ascii=False, sort_keys=True
            ).encode()).hexdigest()
            if signature in seen:
                continue
            seen.add(signature)
            records.append(record)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "approved_manifest.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8"
    )
    teachers = sorted({str(record.get("teacher_model", "unknown")) for record in records})
    summary = write_training_files(records, destination, ", ".join(teachers) or "unknown")
    summary.update({
        "previous_records": len(old_records), "addition_records": len(new_records),
        "merged_records": len(records), "previous_dataset_sha256": old_digest,
        "additions_dataset_sha256": new_digest, "additions_audit_complete": True,
    })
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = hashlib.sha256((destination / "train.jsonl").read_bytes() + (destination / "valid.jsonl").read_bytes()).hexdigest()
    (destination / "approval.json").write_text(json.dumps({
        "dataset_sha256": digest, "method": "merge-of-approved-datasets",
        "previous_dataset_sha256": old_digest, "additions_dataset_sha256": new_digest,
        "source_verified_against_index": True, "merged_records": len(records),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
