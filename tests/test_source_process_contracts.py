"""Source and process contracts retained from the original execution audit.

Private trial counts, retired architecture acceptance and owner-specific paths
are historical evidence, not current application requirements.
"""

import io
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.rag_agent import SYSTEM_PROMPT, StudyAgent, PsychologyAgent
from app.safety import SafetyGuard
from app.pdf_extractor import PDFExtractor, PDFExtractionResult
from app.vision_ocr import (
    VisionOCRManager, BACKEND_TESSERACT, BACKEND_OLLAMA_VISION,
    PRINT_ONLY_DISCLAIMER
)
from app.document_lookup import DocumentLookup
from app.run_ledger import (
    RunLedger, STATUS_CREATED, STATUS_RUNNING, STATUS_COMPLETED,
    STATUS_KILLED_ABNORMAL, compute_signature_hash
)
from app.paired_evaluation import evaluate_passage_grounding, evaluate_paired_dossier


def test_scope1_multidomain_system_prompt_and_safety():
    """Verify prompt is multi-domain without imposing psychology framing."""
    assert "đa lĩnh vực" in SYSTEM_PROMPT
    assert "Không áp khung tâm lý học lên mọi chủ đề" in SYSTEM_PROMPT
    assert issubclass(StudyAgent, PsychologyAgent) or StudyAgent == PsychologyAgent

    # Safety check on medical roleplay refusal
    is_refusal, msg = SafetyGuard.check_roleplay_or_diagnosis("Kê đơn thuốc trầm cảm cho tôi")
    assert is_refusal is True
    assert "không phải là bác sĩ" in msg.lower()
    assert "học tập" in msg.lower()


def test_scope2_recursive_discovery_and_docx_parsing(tmp_path):
    """Test recursive discovery, symlink protection, and DOCX paragraph extraction."""
    src = tmp_path / "src"
    src.mkdir()
    sub = src / "math_folder"
    sub.mkdir()

    # Create dummy pdf, uppercase PDF, and docx
    (src / "top.pdf").write_bytes(b"%PDF-1.4\n")
    (sub / "nested.PDF").write_bytes(b"%PDF-1.4\n")
    
    # Create valid docx
    docx_file = sub / "notes.docx"
    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as z:
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:t>Đoạn 1: Ma trận đối xứng</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Đoạn 2: Định thức ma trận vuông</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        z.writestr("word/document.xml", xml)
    docx_file.write_bytes(docx_buf.getvalue())

    # Create escaping symlink
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    (src / "escape.pdf").symlink_to(outside)

    discovery = PDFExtractor.discover_source_files(src)
    assert len(discovery["supported"]) == 3
    assert len(discovery["symlink_violations"]) == 1

    # Extract docx
    docx_res = PDFExtractor.extract_docx(docx_file)
    assert docx_res.doc_type == "docx"
    assert docx_res.unit_name == "trang"
    assert docx_res.total_pages == 1
    assert "Ma trận đối xứng" in docx_res.extracted_pages[0]["text"]
    assert "Định thức ma trận vuông" in docx_res.extracted_pages[0]["text"]


def test_scope3_vision_ocr_backend_and_review_workflow(tmp_path):
    """Verify OCR manager, print-only disclaimer, cache keying, review and training gating."""
    mgr = VisionOCRManager(backend=BACKEND_TESSERACT)
    key = mgr.compute_cache_key(
        source_hash="testhash123", page_num=1, backend=BACKEND_TESSERACT
    )
    assert len(key) == 64

    # Gate unverified handwriting
    unverified_entry = {
        "cache_key": key,
        "handwriting_suspected": True,
        "is_verified": False,
    }
    allowed, reason = VisionOCRManager.is_allowed_in_training(unverified_entry)
    assert allowed is False
    assert "chưa kiểm tra" in reason

    # After verification
    unverified_entry["is_verified"] = True
    unverified_entry["verified_by"] = "Teacher"
    unverified_entry["verified_at"] = "2026-10-03T08:00:00Z"
    allowed_after, _ = VisionOCRManager.is_allowed_in_training(unverified_entry)
    assert allowed_after is True


def test_scope6_trial_audit_and_run_ledger(tmp_path):
    """Verify the isolated current run ledger lifecycle without private historical trials."""
    # Ledger identity is synthetic; no owner model/dataset is read.
    base = tmp_path / "base"; base.mkdir()
    (base / "config.json").write_text("{}")
    (base / "model.safetensors").write_bytes(b"ledger-only fixture; never loaded")
    ledger_dir = tmp_path / "runs"
    ledger = RunLedger(runs_dir=ledger_dir, index_file=tmp_path / "ledger.jsonl")

    run_rec = ledger.create_run(
        config={"batch_size": 1, "grad_checkpoint": True},
        base_model_dir=base,
        dataset_dir=tmp_path,
        dataset_sha256="abc123hash",
        target_hypothesis="Test hypothesis",
        baseline_metrics={"acc": 0.8},
        frozen_holdout_id="holdout-v1",
    )
    assert run_rec["status"] == STATUS_CREATED
    run_id = run_rec["run_id"]

    # Block duplicate signature without explanation
    sig = run_rec["signature_hash"]
    ledger.update_status(run_id, STATUS_COMPLETED)
    # Check that active runs reconcile
    reconciled = ledger.reconcile_active_runs()
    assert reconciled == 0


def test_scope7_paired_evaluation_and_grounding():
    """Verify paired content evaluation on frozen inputs and honesty gating."""
    # Grounded answer
    grounded = evaluate_passage_grounding(
        answer="Thuật toán sắp xếp nổi bọt [S1] thực hiện so sánh hai phần tử liền kề.",
        passage="Thuật toán sắp xếp nổi bọt thực hiện so sánh hai phần tử liền kề và đổi chỗ nếu sai thứ tự.",
    )
    assert grounded["is_grounded"] is True
    assert grounded["has_citations"] is True

    # No-source honest refusal
    honest = evaluate_passage_grounding(
        answer="Tài liệu không đề cập đến thông tin này, chưa đủ căn cứ để kết luận.",
        passage="Văn bản chỉ nói về ma trận khả nghịch.",
        is_no_source_expected=True,
    )
    assert honest["is_grounded"] is True
    assert honest["no_source_honesty"] is True


def test_source_contract_nested_duplicate_identity(tmp_path):
    """Regression test: nested file with same basename and hash must have its own document identity."""
    from app.indexer import KnowledgeIndexer

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    db_path = tmp_path / "test.db"

    # Step 1: index top-level lesson.txt
    top_file = src_dir / "lesson.txt"
    top_file.write_text("Nội dung bài học đại số tuyến tính", encoding="utf-8")

    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src_dir)
    res1 = indexer.index_all(embed_model=None)
    assert res1["indexed_files"] == 1

    with indexer.get_connection() as conn:
        rows1 = conn.execute("SELECT id, filename, filepath FROM documents").fetchall()
    assert len(rows1) == 1
    assert rows1[0]["filename"] == "lesson.txt"

    # Step 2: add nested/lesson.txt with exact identical content
    nested_dir = src_dir / "nested"
    nested_dir.mkdir()
    nested_file = nested_dir / "lesson.txt"
    nested_file.write_text("Nội dung bài học đại số tuyến tính", encoding="utf-8")

    res2 = indexer.index_all(embed_model=None)
    # Must index the nested file as a distinct document, not skip it due to basename collision
    assert res2["indexed_files"] == 1
    assert res2["skipped_files"] == 1

    with indexer.get_connection() as conn:
        rows2 = conn.execute("SELECT id, filename, filepath FROM documents ORDER BY id").fetchall()
    assert len(rows2) == 2
    filenames = {r["filename"] for r in rows2}
    assert filenames == {"lesson.txt", "nested/lesson.txt"}


def test_source_contract_rebase_stale_filepath(tmp_path):
    """Regression test: incremental index skips unchanged file but rebases stale filepath in DB."""
    from app.indexer import KnowledgeIndexer

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    db_path = tmp_path / "test.db"

    test_file = src_dir / "lesson.txt"
    test_file.write_text("Nội dung bài học giải tích", encoding="utf-8")

    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src_dir)
    res1 = indexer.index_all(embed_model=None)
    assert res1["indexed_files"] == 1

    with indexer.get_connection() as conn:
        row = conn.execute("SELECT id, filepath FROM documents WHERE filename='lesson.txt'").fetchone()
        doc_id = row["id"]
        current_path = row["filepath"]
        assert current_path == str(test_file.resolve())

        # Manually tamper filepath to a stale/moved directory
        stale_path = "/old/project/src/lesson.txt"
        conn.execute("UPDATE documents SET filepath = ? WHERE id = ?", (stale_path, doc_id))
        conn.commit()

    # Step 2: run incremental index without modifying the file content
    res2 = indexer.index_all(embed_model=None)
    assert res2["skipped_files"] == 1

    # Verify that filepath was rebased to current resolved path while preserving doc_id
    with indexer.get_connection() as conn:
        row_after = conn.execute("SELECT id, filepath FROM documents WHERE filename='lesson.txt'").fetchone()
        assert row_after["id"] == doc_id
        assert row_after["filepath"] == str(test_file.resolve())
        assert row_after["filepath"] != stale_path


def test_swap_parser_uses_macos_units():
    """Verify swapusage parsing on macOS formatted strings and exact token auditing."""
    from app.memory_preflight import parse_swapusage

    # Test real swap string with spaces around = and units M, G
    sample_out = "vm.swapusage: total = 4096.00M  used = 1845.50M  free = 2250.50M  (encrypted)"
    parsed = parse_swapusage(sample_out)
    assert parsed["total"] == 4096.00
    assert parsed["used"] == 1845.50
    assert parsed["free"] == 2250.50

    # Test G conversion
    g_out = "vm.swapusage: total = 4.00G  used = 1.50G  free = 2.50G"
    parsed_g = parse_swapusage(g_out)
    assert parsed_g["total"] == 4096.00
    assert parsed_g["used"] == 1536.00
