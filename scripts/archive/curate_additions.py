"""Build a reviewed subset of synthetic additions without changing the draft."""

import json
import shutil
from pathlib import Path

from app.training_data import basic_record_quality, write_training_files


def curate(draft: Path, review_path: Path, output: Path) -> dict:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (draft / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    chosen = []
    for index, changes in review.items():
        original = records[int(index)]
        record = json.loads(json.dumps(original, ensure_ascii=False))
        record["items"] = [{"question": changes["question"], "answer": changes["answer"]}]
        record["translations"].update(changes.get("translations", {}))
        record["teacher_model"] = "human-reviewed"
        if not basic_record_quality(record):
            raise ValueError(f"Bản ghi {index} không đạt kiểm tra cơ bản")
        chosen.append(record)
    expected = set(json.loads((draft / "source_selection.json").read_text(encoding="utf-8"))["new_files"])
    covered = {record["pair"]["filename"] for record in chosen}
    if expected != covered:
        raise ValueError(f"Sách thiếu: {sorted(expected - covered)}; sách thừa: {sorted(covered - expected)}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in chosen), encoding="utf-8"
    )
    shutil.copyfile(draft / "source_selection.json", output / "source_selection.json")
    summary = write_training_files(chosen, output, "human-reviewed")
    summary.update({"reviewed_from": str(draft), "review_file": str(review_path), "audit_complete": False})
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(curate(
        Path("data/training/v3_additions"), Path("data/training/v3_review.json"),
        Path("data/training/v3_curated"),
    ), ensure_ascii=False))
