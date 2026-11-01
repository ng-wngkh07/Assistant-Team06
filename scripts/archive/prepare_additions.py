"""Prepare new-book examples while excluding byte-identical copies of approved books."""

import json
import sqlite3
from pathlib import Path

from app.config import DB_PATH, DEFAULT_CHAT_MODEL
from app.training_data import prepare


def prepare_additions(previous: Path, output: Path, pairs_per_book: int = 2) -> dict:
    old_records = [json.loads(line) for line in (previous / "approved_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    old_names = {r["pair"]["filename"] for r in old_records if not r["pair"].get("cross_book")}
    with sqlite3.connect(DB_PATH) as db:
        docs = db.execute("SELECT filename, file_hash FROM documents WHERE status='indexed' ORDER BY filename").fetchall()
    old_hashes = {digest for name, digest in docs if name in old_names}
    unique_hashes = set(old_hashes)
    selected = []
    duplicates = []
    for name, digest in docs:
        if digest in unique_hashes:
            if name not in old_names:
                duplicates.append(name)
            continue
        unique_hashes.add(digest)
        selected.append(name)
    if not selected:
        raise ValueError("Không có sách mới độc lập để chuẩn bị")
    output.mkdir(parents=True, exist_ok=True)
    (output / "source_selection.json").write_text(json.dumps({
        "previous_dataset": str(previous), "new_files": selected,
        "duplicate_files_excluded": duplicates,
        "source_hashes": {name: digest for name, digest in docs},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return prepare(DB_PATH, output, pairs_per_book=pairs_per_book, model=DEFAULT_CHAT_MODEL, only_files=selected)


if __name__ == "__main__":
    result = prepare_additions(Path("data/training/v2"), Path("data/training/v3_additions"))
    print(json.dumps(result, ensure_ascii=False))
