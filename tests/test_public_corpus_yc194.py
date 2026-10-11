"""Integrity and coverage of independently authored public functional fixtures."""
import hashlib
import json
from pathlib import Path
import fitz

ROOT = Path(__file__).resolve().parent / "fixtures"


def test_manifest_documents_have_declared_hash_pages_and_topic_coverage():
    manifest = json.loads((ROOT / "public_corpus_yc194.json").read_text())
    assert manifest["training_allowed"] is False and manifest["quality_holdout"] is False
    assert len(manifest["topics"]) == 9 and len(manifest["documents"]) == 27
    topics = {topic["id"] for topic in manifest["topics"]}
    kinds = {}
    total_bytes, total_pages = 0, 0
    for item in manifest["documents"]:
        assert item["topic"] in topics
        assert item["origin"] == "project_authored_synthetic" and item["training_allowed"] is False
        path = ROOT / "documents" / item["path"]
        assert path.parent == ROOT / "documents"
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert len(data) == item["bytes"]
        total_bytes += len(data)
        kinds.setdefault(item["topic"], set()).add(item["kind"])
        if path.suffix == ".pdf":
            with fitz.open(path) as doc:
                assert len(doc) == item["pdf_pages"]
                total_pages += len(doc)
                assert all(len(page.get_text().strip()) > 80 for page in doc)
                if item["kind"] == "exercise_with_answer":
                    assert "Bài 1" in doc[1].get_text().replace("\u00a0", " ")
                    assert "Đáp án Bài 1" in doc[2].get_text().replace("\u00a0", " ")
        else:
            assert "Bài 2" in data.decode("utf-8")
    assert total_pages == 36 and total_bytes < 1_000_000
    assert all(value == {"theory", "exercise_with_answer", "exercise_without_answer"} for value in kinds.values())
