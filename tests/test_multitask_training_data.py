"""Unit tests for multitask training data models, prompt builders, and schemas."""

import json
import os
import pytest

from app.multitask_training_data import (
    DOMAINS,
    TASKS,
    SourceContext,
    build_grounded_qa,
    build_insufficient_qa,
    build_practice_generate,
    build_practice_verify_invalid,
    build_practice_verify_supported,
    build_session_summary,
)


@pytest.fixture
def sample_context():
    return SourceContext(
        domain="linear_algebra",
        doc_id=47,
        filename="test_linear_algebra.pdf",
        file_hash="dummy_hash_0123456789abcdef",
        page_num=1,
        chunk_id=1001,
        span_start=0,
        span_end=220,
        passage="Ánh xạ tuyến tính f từ không gian V vào W bảo toàn phép cộng vector và phép nhân với vô hướng. Giả thiết V và W là các không gian vector trên trường số thực R.",
    )


def test_domains_and_tasks_enumeration():
    assert len(DOMAINS) == 6
    assert len(TASKS) == 6
    assert "linear_algebra" in DOMAINS
    assert "grounded_qa" in TASKS
    assert "session_summary" in TASKS


def test_build_grounded_qa(sample_context):
    query = "Ánh xạ tuyến tính có đặc điểm gì?"
    answer = "Ánh xạ tuyến tính bảo toàn phép cộng vector và phép nhân vô hướng [S1]."
    messages, ref = build_grounded_qa(sample_context, query, answer)
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert sample_context.passage in messages[1]["content"]
    assert query in messages[1]["content"]
    assert messages[2]["content"] == answer == ref


def test_build_insufficient_qa(sample_context):
    query = "Thuật toán sắp xếp QuickSort hoạt động như thế nào?"
    answer = "Tôi chưa tìm thấy đủ căn cứ trong tài liệu cho câu hỏi này [S1]."
    messages, ref = build_insufficient_qa(sample_context, query, answer)
    assert sample_context.passage in messages[1]["content"]
    assert query in messages[1]["content"]
    assert messages[2]["content"] == answer == ref


def test_build_practice_generate_schema(sample_context):
    query = "Hãy tạo câu hỏi trắc nghiệm."
    q_dict = {
        "question": "Tính chất của ánh xạ tuyến tính là gì?",
        "choices": {
            "A": "Bảo toàn phép cộng vector.",
            "B": "Làm thay đổi số chiều.",
            "C": "Không bảo toàn nhân vô hướng.",
            "D": "Chỉ tác động lên ma trận 0.",
        },
        "answer": "A",
        "explanation": "Theo tài liệu, ánh xạ tuyến tính bảo toàn phép cộng vector.",
        "source_chunk_id": sample_context.chunk_id,
        "evidence_quote": "bảo toàn phép cộng vector",
    }
    messages, ref = build_practice_generate(sample_context, query, q_dict)
    assert sample_context.passage in messages[1]["content"]
    parsed = json.loads(ref)
    assert "questions" in parsed
    assert len(parsed["questions"]) == 1
    q = parsed["questions"][0]
    assert q["answer"] == "A"
    assert q["source_chunk_id"] == sample_context.chunk_id
    assert q["evidence_quote"] in sample_context.passage


def test_build_practice_verify_supported_schema(sample_context):
    query = "Kiểm định câu hỏi."
    q_dict = {
        "question": "Tính chất của ánh xạ tuyến tính?",
        "choices": {"A": "Bảo toàn cộng vector", "B": "Sai 1", "C": "Sai 2", "D": "Sai 3"},
        "answer": "A",
        "verifier_check": {"supported": True, "explanation_supported": True, "unique_answer": True, "qualifiers_preserved": True, "verified_answer": "A"},
        "explanation": "Căn cứ theo nguồn",
        "evidence_quote": "bảo toàn phép cộng vector",
    }
    messages, ref = build_practice_verify_supported(sample_context, query, q_dict)
    assert sample_context.passage in messages[1]["content"]
    parsed = json.loads(ref)
    assert "checks" in parsed
    c = parsed["checks"][0]
    assert c["index"] == 1
    assert c["supported"] is True
    assert c["unique_answer"] is True
    assert c["verified_answer"] == "A"


def test_build_practice_verify_invalid_schema(sample_context):
    query = "Kiểm định câu hỏi."
    q_dict = {
        "question": "Tính chất khẳng định sai?",
        "choices": {"A": "Sai hoàn toàn", "B": "Sai 1", "C": "Sai 2", "D": "Sai 3"},
        "answer": "A",
        "verifier_check": {"supported": False, "explanation_supported": False, "unique_answer": False, "qualifiers_preserved": True, "verified_answer": ""},
        "explanation": "Không có căn cứ",
        "evidence_quote": "bảo toàn phép cộng vector",
    }
    messages, ref = build_practice_verify_invalid(sample_context, query, q_dict)
    assert sample_context.passage in messages[1]["content"]
    parsed = json.loads(ref)
    assert "checks" in parsed
    c = parsed["checks"][0]
    assert c["supported"] is False
    assert c["unique_answer"] is False
    assert c["verified_answer"] == ""
    assert c["qualifiers_preserved"] is True


def test_build_session_summary_schema(sample_context):
    query = "Hãy tóm tắt chủ đề."
    title = "Chủ đề ánh xạ tuyến tính"
    summary = "Phiên học thảo luận về định nghĩa và tính chất của ánh xạ tuyến tính trong đại số tuyến tính."
    dialog = ("Ánh xạ tuyến tính là gì?", "Là ánh xạ bảo toàn phép cộng và nhân vô hướng.")
    messages, ref = build_session_summary(sample_context, query, dialog, title, summary)
    assert sample_context.passage not in messages[1]["content"]
    assert dialog[0] in messages[1]["content"] and dialog[1] in messages[1]["content"]
    assert query not in messages[1]["content"]
    parsed = json.loads(ref)
    assert 3 <= len(parsed["title"].split()) <= 7
    assert 0 < len(parsed["summary"]) < 400


def test_fail_closed_admission_validator(tmp_path, monkeypatch):
    """Exercise admission failures without reading the operator's corpus."""
    import hashlib
    import sqlite3
    import scripts.prepare_multitask_training as admission

    source = tmp_path / "synthetic-source.txt"
    passage = "A linear map preserves vector addition and scalar multiplication."
    source.write_text(passage, encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    text_hash = hashlib.sha256(passage.encode("utf-8")).hexdigest()
    monkeypatch.setattr(admission, "FROZEN_HOLDOUT_SOURCE_HASHES", set())
    monkeypatch.setattr(admission, "EXCLUDED_PAGES_MANIFEST", [
        {"filename": source.name, "page": 2, "reason": "synthetic exclusion"},
    ])
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript("""
            CREATE TABLE documents(id INTEGER, filename TEXT, file_hash TEXT, filepath TEXT);
            CREATE TABLE corpus_source_pages(filename TEXT, page_num INTEGER, state TEXT,
                source_sha256 TEXT, review_path TEXT);
            CREATE TABLE chunks(id INTEGER, filename TEXT, page_num INTEGER, text TEXT);
            CREATE TABLE corpus_vector_provenance(chunk_id INTEGER, text_sha256 TEXT,
                dimensions INTEGER, full_text INTEGER);
        """)
        conn.execute("INSERT INTO documents VALUES(1, ?, ?, ?)",
                     (source.name, source_hash, str(source)))
        for page, state in [(1, "text_verified"), (2, "text_verified"), (3, "ocr_pending_review")]:
            conn.execute("INSERT INTO corpus_source_pages VALUES(?, ?, ?, ?, '')",
                         (source.name, page, state, source_hash))
            conn.execute("INSERT INTO chunks VALUES(?, ?, ?, ?)",
                         (page, source.name, page, passage))
            conn.execute("INSERT INTO corpus_vector_provenance VALUES(?, ?, 1024, 1)",
                         (page, text_hash))

        def check(doc=1, page=1, start=0, end=len(passage), text=passage, quote=passage[:25]):
            return admission.validate_live_canonical_admission(
                conn, doc, page, page, start, end, text, quote)

        # A valid control proves the fixture reaches admission, not an earlier failure.
        assert check()["admitted"] is True
        for kwargs, reason in [
            ({"doc": 999999}, "not found"),
            ({"page": 2}, "approved exclusion manifest"),
            ({"page": 3}, "state"),
            ({"text": "WRONG TEXT"}, "literal slice mismatch"),
            ({"quote": passage[:10]}, "too short"),
            ({"start": -1}, "invalid span bounds"),
        ]:
            result = check(**kwargs)
            assert result["admitted"] is False
            assert reason in result["reason"].lower()
        source.unlink()
        result = check()
        assert result["admitted"] is False
        assert "source file does not exist" in result["reason"].lower()
    finally:
        conn.close()


def test_candidate_dataset_exact_slice_binding():
    """Verify all 216 records in candidate manifest bind literally to raw chunk text."""
    import sqlite3
    from pathlib import Path

    selected = os.environ.get("DATASET_CONTRACT_DIR")
    if not selected:
        pytest.skip("Explicit candidate dataset required")
    manifest_path = Path(selected)/"approved_manifest.jsonl"
    db_path = Path("data/knowledge_base.db")
    if not manifest_path.exists() or not db_path.exists():
        pytest.skip("Candidate manifest and database required")

    records = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 216

    conn = sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro", uri=True)
    c = conn.cursor()

    for r in records:
        cid = r["chunk_id"]
        c.execute("SELECT text FROM chunks WHERE id = ?", (cid,))
        row = c.fetchone()
        assert row is not None, f"Chunk {cid} missing for {r['id']}"
        raw_text = row[0]
        s, e = r["span_start"], r["span_end"]
        assert 0 <= s < e <= len(raw_text)
        assert raw_text[s:e] == r["passage"], f"Slice mismatch for {r['id']}"

    conn.close()


def test_candidate_dataset_mcq_balance_and_distractor_quality():
    """Verify MCQ answer distribution is balanced and distractors are not universal meta-templates."""
    from collections import Counter
    from pathlib import Path

    selected = os.environ.get("DATASET_CONTRACT_DIR")
    if not selected:
        pytest.skip("Explicit candidate dataset required")
    manifest_path = Path(selected)/"approved_manifest.jsonl"
    if not manifest_path.exists():
        pytest.skip("Candidate manifest required")

    records = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # MCQ balance
    mcq_records = [r for r in records if r["task"] == "practice_generate"]
    assert len(mcq_records) == 36

    counts = Counter()
    seen_distractors = {}
    for r in mcq_records:
        parsed = json.loads(r["reference"])
        q = parsed["questions"][0]
        ans = q["answer"]
        counts[ans] += 1
        distractor_tuple = tuple(sorted(v for k, v in q["choices"].items() if k != ans))
        seen_distractors.setdefault(distractor_tuple, set()).add(r["domain"])

    # No single choice letter dominates (>40%)
    for letter in ("A", "B", "C", "D"):
        assert counts[letter] > 0, f"Option {letter} has 0 occurrences"
        ratio = counts[letter] / len(mcq_records)
        assert ratio <= 0.40, f"Option {letter} dominates with ratio {ratio:.2f} > 0.40"

    # Distractor quality: no cross-domain distractor template sharing across 2+ domains
    shared = {d: doms for d, doms in seen_distractors.items() if len(doms) >= 2}
    assert not shared, f"Found shared distractor templates across domains: {shared}"
