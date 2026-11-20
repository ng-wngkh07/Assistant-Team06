"""Tests for corpus scope admission gates (producer and fine-tuning consumer).

Validates that:
1. Producer blocks chunks from excluded pages even if native text is present.
2. Fine-tune preflight rejects datasets referencing excluded pages in train/valid splits or nested sources.
3. Fine-tune preflight rejects malformed or unverified page provenance.
4. Clean datasets without excluded pages pass preflight scope verification.
5. Scope helpers correctly reflect the user-approved YC-188 exclusion manifest.
"""

import hashlib
import json
import pytest
from pathlib import Path

from app.corpus_scope import (
    get_validated_scope,
    get_excluded_pages,
    is_page_excluded,
    validate_page_exclusion_manifest,
    verify_dataset_scope,
)
from app.training_data import is_chunk_allowed_in_training


def test_scope_helper_loads_real_yc188_exclusions():
    """Verify that the real project workspace loads exactly the 13 approved YC-188 exclusions."""
    policy = Path(__file__).resolve().parents[1] / "data/runtime/corpus_completion_policy.json"
    if not policy.exists():
        pytest.skip("Private YC-188 policy/source manifest is available only on the corpus host")
    scope = get_validated_scope()
    assert scope["has_exclusions"] is True  # nosec B101
    assert scope["decision_id"] == "YC-188"  # nosec B101
    assert scope["approved_count"] == 13  # nosec B101
    assert len(scope["page_set"]) == 13  # nosec B101
    assert ("cslt/slides/chapter10.pdf", 2) in scope["page_set"]  # nosec B101
    assert ("cslt/slides/chapter10.pdf", 46) in scope["page_set"]  # nosec B101
    assert ("cslt/slides/chapter09-pointer.pdf", 42) in scope["page_set"]  # nosec B101
    assert ("cslt/slides/chapter05-numbered.pdf", 66) in scope["page_set"]  # nosec B101
    assert ("Toàn Thư Tâm Lý Học - Motofumi Fukahori & Phương Hoa (dịch).pdf", 5) in scope["page_set"]  # nosec B101


def test_producer_blocks_native_chunks_from_excluded_pages(tmp_path, monkeypatch):
    """Producer must block any chunk from an excluded page even if native text is present."""
    src = tmp_path / "src"
    src.mkdir()
    doc_file = src / "test_doc.txt"
    doc_content = "This is a substantive native text passage that would normally be accepted by the producer."
    doc_file.write_text(doc_content, encoding="utf-8")
    doc_hash = hashlib.sha256(doc_content.encode("utf-8")).hexdigest()

    doc_meta = {
        "id": 1,
        "filename": "test_doc.txt",
        "clean_title": "Test Doc",
        "is_scanned": 0,
        "file_hash": doc_hash,
        "filepath": str(doc_file),
    }

    # When page is NOT excluded: chunk allowed
    monkeypatch.setattr("app.corpus_scope.get_excluded_pages", lambda root=None: set())
    assert is_chunk_allowed_in_training(doc_meta, 1, doc_content) is True  # nosec B101

    # When page IS excluded: chunk must be blocked immediately
    monkeypatch.setattr("app.corpus_scope.get_excluded_pages", lambda root=None: {("test_doc.txt", 1)})
    assert is_chunk_allowed_in_training(doc_meta, 1, doc_content) is False  # nosec B101


def test_fine_tune_rejects_excluded_page_in_train_split(tmp_path):
    """Fine-tune preflight must reject approved_manifest.jsonl referencing an excluded page in train split."""
    data_dir = tmp_path / "data_v_test"
    data_dir.mkdir()

    record = {
        "id": "item_001",
        "split": "train",
        "source_file": "cslt/slides/chapter10.pdf",
        "page": 2,
        "source_file_sha256": "980e7cd40a5ed0933f4f439b1dd3cb29ed0b9c984c3e40d287394719a586d09b",
        "query": "Sample question?",
        "reference": "Sample answer.",
    }
    (data_dir / "approved_manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    excluded = {("cslt/slides/chapter10.pdf", 2)}
    with pytest.raises(ValueError, match="CHẶN HUẤN LUYỆN.*chứa trang bị loại"):
        verify_dataset_scope(data_dir, excluded_pages=excluded)


def test_fine_tune_rejects_excluded_page_in_valid_split(tmp_path):
    """Fine-tune preflight must reject approved_manifest.jsonl referencing an excluded page in valid split."""
    data_dir = tmp_path / "data_v_test"
    data_dir.mkdir()

    record = {
        "id": "item_valid_001",
        "split": "valid",
        "source_file": "cslt/slides/chapter09-pointer.pdf",
        "page": 42,
        "source_file_sha256": "f70869a736bcbae971c1fef900d59a96a5cbe5741032e1d1fb64441bb84754b5",
        "query": "Sample question?",
        "reference": "Sample answer.",
    }
    (data_dir / "approved_manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    excluded = {("cslt/slides/chapter09-pointer.pdf", 42)}
    with pytest.raises(ValueError, match="CHẶN HUẤN LUYỆN.*chứa trang bị loại"):
        verify_dataset_scope(data_dir, excluded_pages=excluded)


def test_fine_tune_rejects_nested_sources_referencing_excluded_page(tmp_path):
    """Fine-tune preflight must reject nested sources referencing an excluded page."""
    data_dir = tmp_path / "data_v_test"
    data_dir.mkdir()

    record = {
        "id": "item_cross_001",
        "split": "train",
        "sources": [
            {"source_file": "valid_doc.pdf", "page": 10},
            {"source_file": "cslt/slides/chapter10.pdf", "page": 46},
        ],
        "query": "Sample cross question?",
        "reference": "Sample answer.",
    }
    (data_dir / "approved_manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    excluded = {("cslt/slides/chapter10.pdf", 46)}
    with pytest.raises(ValueError, match="CHẶN HUẤN LUYỆN.*chứa nguồn bị loại"):
        verify_dataset_scope(data_dir, excluded_pages=excluded)


def test_fine_tune_rejects_malformed_provenance(tmp_path):
    """Fine-tune preflight must fail closed on malformed page provenance."""
    data_dir = tmp_path / "data_v_test"
    data_dir.mkdir()

    record = {
        "id": "item_bad",
        "split": "train",
        "source_file": "some_doc.pdf",
        "page": "not_an_int",
    }
    (data_dir / "approved_manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    excluded = {("cslt/slides/chapter10.pdf", 2)}
    with pytest.raises(ValueError, match="Malformed page number"):
        verify_dataset_scope(data_dir, excluded_pages=excluded)


def test_fine_tune_accepts_clean_in_scope_dataset(tmp_path):
    """Datasets containing only valid in-scope sources pass scope verification without error."""
    data_dir = tmp_path / "data_v_test"
    data_dir.mkdir()

    record = {
        "id": "item_clean",
        "split": "train",
        "source_file": "phaitraidungsai.pdf",
        "page": 20,
        "query": "Valid query?",
        "reference": "Valid answer.",
    }
    (data_dir / "approved_manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    excluded = {("cslt/slides/chapter10.pdf", 2)}
    # Should not raise
    verify_dataset_scope(data_dir, excluded_pages=excluded)


def test_producer_accepts_pre_validated_excluded_pages_context(tmp_path):
    """Producer accepts explicit pre-validated excluded_pages context for batch builder efficiency."""
    src = tmp_path / "src"
    src.mkdir()
    doc_file = src / "test_doc.txt"
    doc_content = "Substantive native text for testing producer admission context."
    doc_file.write_text(doc_content, encoding="utf-8")
    doc_hash = hashlib.sha256(doc_content.encode("utf-8")).hexdigest()

    doc_meta = {
        "id": 1,
        "filename": "test_doc.txt",
        "clean_title": "Test Doc",
        "is_scanned": 0,
        "file_hash": doc_hash,
        "filepath": str(doc_file),
    }

    # Pass pre-validated set directly: blocked without disk policy re-read
    assert is_chunk_allowed_in_training(doc_meta, 1, doc_content, excluded_pages={("test_doc.txt", 1)}) is False  # nosec B101
    # When not in set: admitted
    assert is_chunk_allowed_in_training(doc_meta, 1, doc_content, excluded_pages=set()) is True  # nosec B101


def test_audit_corpus_safe_error_handling_when_db_corrupt(tmp_path):
    """Audit corpus returns safe scope counters without KeyError when database is corrupt/missing tables."""
    from app.corpus_readiness import audit_corpus
    src = tmp_path / "src"
    src.mkdir()
    (src / "sample.txt").write_text("Sample content", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    # Empty corrupt database
    (data / "knowledge_base.db").write_bytes(b"SQLite format 3\x00corrupt")
    result = audit_corpus(tmp_path)
    assert result["complete"] is False  # nosec B101
    assert result["total_pages"] == 0  # nosec B101
    assert result["in_scope_pages"] == 0  # nosec B101
    assert result["excluded_page_count"] == 0  # nosec B101
    assert result["complete_all_sources"] is False  # nosec B101
    assert any(f.get("kind") == "audit_error" for f in result.get("failures", []))  # nosec B101


def test_exclusion_manifest_rejects_ancestor_symlink(tmp_path):
    """Manifest path reject if any ancestor directory is a symlink."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "sample.txt").write_text("Sample content", encoding="utf-8")
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    manifest_file = real_data / "manifest.json"
    manifest_file.write_text("{}", encoding="utf-8")

    link_data = tmp_path / "link_data"
    link_data.symlink_to(real_data)

    entry = {
        "path": "link_data/manifest.json",
        "sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
    }
    with pytest.raises(ValueError, match="symlink"):
        validate_page_exclusion_manifest(tmp_path, entry)



def test_producer_type_annotations_are_resolvable():
    """Resolve annotations even on Python versions that defer their evaluation."""
    from typing import get_type_hints
    hints = get_type_hints(is_chunk_allowed_in_training)
    assert hints["return"] is bool  # nosec B101


def test_exclusion_manifest_relocates_legacy_image_across_nfc_nfd_project_name(tmp_path):
    """Legacy image path in NFD must relocate cleanly when CLI root uses NFC."""
    import unicodedata

    project_root = tmp_path / unicodedata.normalize("NFC", "agent học tập")
    src = project_root / "src"
    src.mkdir(parents=True)
    note = src / "note.txt"
    note.write_text("Excluded page test content.", encoding="utf-8")

    img = project_root / "data/evaluation/corpus-review/img.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"png-fixture")

    legacy_nfd_project = unicodedata.normalize("NFD", "agent học tập")
    legacy_image_path = f"/legacy/location/{legacy_nfd_project}/data/evaluation/corpus-review/img.png"
    manifest = {
        "schema": "approved-page-exclusions-v1",
        "decision_id": "YC-188",
        "approved_by": "user",
        "approved_at": "2026-10-10T18:40:15+07:00",
        "approval": "EXCLUDE_TEST_PAGE",
        "approved_count": 1,
        "pages": [{
            "filename": "note.txt",
            "page": 1,
            "source_sha256": hashlib.sha256(note.read_bytes()).hexdigest(),
            "image_path": legacy_image_path,
            "image_sha256": hashlib.sha256(img.read_bytes()).hexdigest(),
            "reason": ["Legacy relocation test across NFD/NFC project folder names."],
        }],
    }
    manifest_file = project_root / "data/runtime/exclusions.json"
    manifest_file.parent.mkdir(parents=True)
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    scope = validate_page_exclusion_manifest(
        project_root,
        {"path": "data/runtime/exclusions.json", "sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest()},
        {"note.txt": note},
    )
    assert scope["page_set"] == {("note.txt", 1)}  # nosec B101
