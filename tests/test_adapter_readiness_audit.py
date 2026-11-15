"""Tests for offline adapter and training readiness audit script."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

from app.corpus_readiness import audit_corpus
from app.indexer import KnowledgeIndexer, pack_vector
from app.retrieval_tree import build_tree
from scripts.audit_adapter_readiness import main as audit_cli_main, run_adapter_readiness_audit


def _build_complete_mini_root(tmp_path: Path, current_native_config: dict) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    note = src / "note.txt"
    note.write_text("Complete verified source passage with full condition. Second sentence.", encoding="utf-8")

    data = tmp_path / "data"
    data.mkdir()
    idx = KnowledgeIndexer(data / "knowledge_base.db", src)
    idx.index_all(embed_model=None)

    runtime = data / "runtime"
    runtime.mkdir()
    (runtime / "corpus_completion_policy.json").write_text(
        json.dumps({"require_complete_corpus": True, "embedding_model_digest": "pinned-bge"}),
        encoding="utf-8",
    )
    with idx.get_connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS corpus_source_pages(filename,page_num,source_sha256,state,chunks_sha256,review_path,review_sha256,checked_at)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS corpus_vector_provenance(chunk_id,text_sha256,model_digest,vector_sha256,dimensions,full_text,embedded_at)"
        )
        texts = [r["text"] for r in db.execute("SELECT text FROM chunks ORDER BY chunk_index")]
        db.execute(
            "INSERT OR REPLACE INTO corpus_source_pages VALUES(?,?,?,?,?,?,?,?)",
            (
                "note.txt",
                1,
                hashlib.sha256(note.read_bytes()).hexdigest(),
                "text_verified",
                hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest(),
                "",
                "",
                "now",
            ),
        )
        for row in db.execute("SELECT id,text FROM chunks").fetchall():
            blob = pack_vector([1.0] + [0.0] * 1023)
            db.execute("UPDATE chunks SET embedding=? WHERE id=?", (blob, row["id"]))
            db.execute(
                "INSERT OR REPLACE INTO corpus_vector_provenance VALUES(?,?,?,?,?,?,?)",
                (
                    row["id"],
                    hashlib.sha256(row["text"].encode()).hexdigest(),
                    "pinned-bge",
                    hashlib.sha256(blob).hexdigest(),
                    1024,
                    1,
                    "now",
                ),
            )
    build_tree(data / "knowledge_base.db", data / "retrieval_tree.db")

    model_dir = data / "models" / "qwen3.5-9b-4bit"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text(json.dumps(current_native_config), encoding="utf-8")
    (model_dir / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    header = b'{"__metadata__": {}}'
    (model_dir / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header)
    return tmp_path


def test_audit_reports_hold_on_pending_ocr_drifted_runtime_and_rejected_candidate(tmp_path, current_native_config, capsys):
    """Auditor stays read-only, surfaces OCR/corpus/dataset/protocol blockers, and never mutates registry files."""
    root = _build_complete_mini_root(tmp_path, current_native_config)
    assert audit_corpus(root)["complete"] is True

    ocr_cache = root / "data" / "ocr_cache"
    ocr_cache.mkdir()
    (ocr_cache / "page1.json").write_text(json.dumps({"status": "CAPTURED_PENDING_REVIEW", "text": "raw"}), encoding="utf-8")
    (ocr_cache / "page2.json").write_text(json.dumps({"text": "legacy unverified"}), encoding="utf-8")

    # Add rejected practice candidate manifest under data/adapters/
    adapters_dir = root / "data" / "adapters"
    adapters_dir.mkdir()
    strict_summary = root / "data" / "strict-summary.json"
    strict_summary.write_text(
        json.dumps({"decision": "DO_NOT_ACTIVATE", "gates": {"both_genuine_missing_cases_correct": False}}),
        encoding="utf-8",
    )
    (adapters_dir / "practice-9b.json").write_text(
        json.dumps({"schema": "study-practice-9b-v1", "stage": "native_qualified", "primary": {"strict_summary": {"path": str(strict_summary)}}}),
        encoding="utf-8",
    )

    # Dataset with drifted runtime hash relative to code_root
    code_root = root / "code"
    (code_root / "app").mkdir(parents=True)
    (code_root / "app" / "rag_agent.py").write_text("print('updated')\n", encoding="utf-8")

    ds = root / "data" / "training" / "cand"
    ds.mkdir(parents=True)
    row = {"source_file": "note.txt", "page": 1, "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}
    (ds / "approved_manifest.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (ds / "train.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (ds / "valid.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    digest = hashlib.sha256((ds / "train.jsonl").read_bytes() + (ds / "valid.jsonl").read_bytes()).hexdigest()
    (ds / "approval.json").write_text(json.dumps({"dataset_sha256": digest}), encoding="utf-8")
    (ds / "summary.json").write_text(
        json.dumps({
            "audit_complete": True,
            "training_approved": True,
            "runtime_files_sha256": {"app/rag_agent.py": "0" * 64},
        }),
        encoding="utf-8",
    )

    report = run_adapter_readiness_audit(root=root, dataset_dir=ds, code_root=code_root)
    assert report["training_verdict"] == "HOLD"
    assert report["ready_for_training"] is False
    assert report["adapter_activation_verdict"] == "HOLD"
    assert not (adapters_dir / "active_registry.json").exists(), "Audit must not create active_registry.json when absent"
    assert report["ocr_cache"]["statuses"] == {"CAPTURED_PENDING_REVIEW": 1, "unverified": 1}
    assert report["ocr_cache"]["pending_or_unverified"] == 2
    assert report["retrieval_tree"]["status"] == "ready"
    assert report["dataset"]["drifted_runtime_files"] == ["app/rag_agent.py"]
    assert report["adapters"]["practice_candidate"]["strict_decision"] == "DO_NOT_ACTIVATE"
    assert report["adapters"]["practice_candidate"]["failed_gates"] == ["both_genuine_missing_cases_correct"]

    exit_code = audit_cli_main(["--root", str(root), "--strict"])
    assert exit_code == 2
    cli_json = json.loads(capsys.readouterr().out)
    assert cli_json["training_verdict"] == "HOLD"


def test_audit_passes_gates_when_corpus_dataset_model_and_preflight_pass(tmp_path, current_native_config, monkeypatch):
    """When corpus, model, verified memory preflight, and bound dataset all pass, training_verdict is READY."""
    root = _build_complete_mini_root(tmp_path, current_native_config)
    code_root = root / "code"
    (code_root / "app").mkdir(parents=True)
    rag_file = code_root / "app" / "rag_agent.py"
    rag_file.write_text("# stable runtime\n", encoding="utf-8")
    rag_sha = hashlib.sha256(rag_file.read_bytes()).hexdigest()

    ds = root / "data" / "training" / "ok"
    ds.mkdir(parents=True)
    row = {"source_file": "note.txt", "page": 1, "messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}
    (ds / "approved_manifest.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (ds / "train.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (ds / "valid.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    digest = hashlib.sha256((ds / "train.jsonl").read_bytes() + (ds / "valid.jsonl").read_bytes()).hexdigest()
    (ds / "approval.json").write_text(json.dumps({"dataset_sha256": digest}), encoding="utf-8")
    (ds / "summary.json").write_text(
        json.dumps({"audit_complete": True, "training_approved": True, "runtime_files_sha256": {"app/rag_agent.py": rag_sha}}),
        encoding="utf-8",
    )

    import app.memory_preflight as mp

    monkeypatch.setattr(mp, "preflight_check_memory_training", lambda **kwargs: {"eligible_for_training": True, "violations": []})

    report = run_adapter_readiness_audit(root=root, dataset_dir=ds, code_root=code_root)
    assert report["training_verdict"] == "READY", report["blockers"]
    assert report["ready_for_training"] is True
    assert report["adapter_activation_verdict"] == "NONE"
    assert report["dataset"]["scope_verified"] is True
