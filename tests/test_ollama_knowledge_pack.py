"""Focused tests for Ollama BGE-M3 public pack protocol, export, install, and tamper rejection."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile
import pytest

from app.indexer import KnowledgeIndexer, pack_vector
from app.knowledge_pack import (
    export_pack, install_pack, validate_and_get_contract,
    NATIVE_PROTOCOL, OLLAMA_PROTOCOL
)
from app.vector_rebuild import (
    prepare_stage, apply_stage, nonvector_fingerprint, protocol_for_identity
)
from tests.knowledge_pack_review_helpers import (
    create_synthetic_review,
    create_synthetic_review_manifest,
    setup_reviewed_chunk_in_db,
)

OLLAMA_IDENTITY = "ollama-sha256:" + "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"
NATIVE_IDENTITY = "bge-m3-mlx-fp16@" + "a" * 40


def make_publisher(tmp_path, identity=OLLAMA_IDENTITY):
    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    content = "# Concept\nEncapsulation keeps state hidden.\n"
    (sources / "public.md").write_text(content)
    database = tmp_path / "publisher.db"
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    source_sha = hashlib.sha256((sources / "public.md").read_bytes()).hexdigest()
    _, rev = create_synthetic_review(
        tmp_path / "reviews",
        "public.md",
        1,
        source_sha,
        transcript=content,
    )
    rev_manifest = create_synthetic_review_manifest(tmp_path / "page-reviews.json", [rev])
    with sqlite3.connect(database) as db:
        doc_row = db.execute("SELECT id, clean_title FROM documents WHERE filename='public.md'").fetchone()
        setup_reviewed_chunk_in_db(db, doc_row[0], "public.md", doc_row[1], 1, rev, identity)
    allowlist = tmp_path / "allowed.json"
    allowlist.write_text(json.dumps([{
        "filename": "public.md",
        "sha256": source_sha,
        "license": "CC0-1.0",
        "redistribution_reviewed": True
    }]))
    return database, sources, allowlist, rev_manifest


def test_ollama_identity_and_protocol_contract():
    # Valid Ollama identity contract
    contract = validate_and_get_contract(OLLAMA_IDENTITY)
    assert contract["embedding_protocol"] == OLLAMA_PROTOCOL
    assert contract["dimensions"] == 1024
    assert contract["max_length"] == 8192
    assert contract["format"] == 1

    # Valid Native identity contract
    native_contract = validate_and_get_contract(NATIVE_IDENTITY)
    assert native_contract["embedding_protocol"] == NATIVE_PROTOCOL
    assert native_contract["dimensions"] == 1024
    assert native_contract["max_length"] == 2048

    # Rejection of invalid/tampered identities
    with pytest.raises(ValueError, match="artifact-bound Ollama"):
        validate_and_get_contract("ollama-sha256:" + "a" * 63)
    with pytest.raises(ValueError, match="artifact-bound Ollama"):
        validate_and_get_contract("ollama-sha256:" + "a" * 65)
    with pytest.raises(ValueError, match="artifact-bound Ollama"):
        validate_and_get_contract("ollama-sha256:" + "g" * 64)
    with pytest.raises(ValueError, match="Unsupported or unverified"):
        validate_and_get_contract("unknown-identity@123")


def test_cross_protocol_isolation_native_and_ollama(tmp_path):
    # Export native pack
    native_db, native_src, native_allow, native_revs = make_publisher(tmp_path / "native_pub", NATIVE_IDENTITY)
    native_zip = tmp_path / "native.zip"
    exported_native = export_pack(
        native_db, native_src, native_allow, native_zip,
        expected_identity=NATIVE_IDENTITY, page_reviews=native_revs
    )

    # Export Ollama pack
    ollama_db, ollama_src, ollama_allow, ollama_revs = make_publisher(tmp_path / "ollama_pub", OLLAMA_IDENTITY)
    ollama_zip = tmp_path / "ollama.zip"
    exported_ollama = export_pack(
        ollama_db, ollama_src, ollama_allow, ollama_zip,
        expected_identity=OLLAMA_IDENTITY, page_reviews=ollama_revs
    )

    # Native archive cannot install when expected_identity is Ollama
    with pytest.raises(ValueError, match="Pack uses a different chunking or embedding contract"):
        install_pack(native_zip, tmp_path / "install_fail_1",
                     expected_sha256=exported_native["sha256"], expected_identity=OLLAMA_IDENTITY)

    # Ollama archive cannot install when expected_identity is Native
    with pytest.raises(ValueError, match="Pack uses a different chunking or embedding contract"):
        install_pack(ollama_zip, tmp_path / "install_fail_2",
                     expected_sha256=exported_ollama["sha256"], expected_identity=NATIVE_IDENTITY)


def test_ollama_export_and_install_without_indexing(tmp_path, monkeypatch):
    database, sources, allowlist, rev_manifest = make_publisher(tmp_path / "pub", OLLAMA_IDENTITY)
    archive = tmp_path / "ollama_pack.zip"
    exported = export_pack(
        database, sources, allowlist, archive,
        expected_identity=OLLAMA_IDENTITY, page_reviews=rev_manifest
    )
    assert exported["documents"] == 1
    assert exported["chunks"] > 0

    # Ensure manifest has correct Ollama metadata
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["embedding_protocol"] == OLLAMA_PROTOCOL
        assert manifest["model_identity"] == OLLAMA_IDENTITY
        assert manifest["dimensions"] == 1024
        assert manifest["max_length"] == 8192

    # Installing must never invoke index_all or extraction
    monkeypatch.setattr(KnowledgeIndexer, "index_all", lambda *a, **k: pytest.fail("Must not index on installation"))
    dest = tmp_path / "installed_ollama"
    result = install_pack(archive, dest, expected_sha256=exported["sha256"], expected_identity=OLLAMA_IDENTITY)
    assert result["documents"] == 1
    assert result["chunks"] == exported["chunks"]

    # Verify receipt and installed DB provenance
    receipt = json.loads((dest / "knowledge-pack.json").read_text(encoding="utf-8"))
    assert receipt["model_identity"] == OLLAMA_IDENTITY
    assert receipt["embedding_protocol"] == OLLAMA_PROTOCOL

    with sqlite3.connect(dest / "knowledge_base.db") as db:
        prov = db.execute("SELECT DISTINCT model_digest, dimensions FROM corpus_vector_provenance").fetchall()
        assert prov == [(OLLAMA_IDENTITY, 1024)]
        fts_count = db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'Encapsulation'").fetchone()[0]
        assert fts_count > 0


@pytest.mark.parametrize("fault", ["corrupt-vector", "tampered-text", "tampered-protocol", "tampered-hash", "traversal"])
def test_ollama_pack_tamper_rejection(tmp_path, fault):
    database, sources, allowlist, rev_manifest = make_publisher(tmp_path / "pub", OLLAMA_IDENTITY)
    archive = tmp_path / "ollama_pack.zip"
    exported = export_pack(
        database, sources, allowlist, archive,
        expected_identity=OLLAMA_IDENTITY, page_reviews=rev_manifest
    )

    with zipfile.ZipFile(archive) as z:
        members = {name: z.read(name) for name in z.namelist()}

    if fault == "corrupt-vector":
        lines = members["index.jsonl"].splitlines()
        row = json.loads(lines[0])
        row["vector"] = base64.b64encode(b"invalid").decode()
        lines[0] = json.dumps(row).encode()
        members["index.jsonl"] = b"\n".join(lines) + b"\n"
    elif fault == "tampered-text":
        lines = members["index.jsonl"].splitlines()
        row = json.loads(lines[0])
        row["text"] = "tampered text content"
        lines[0] = json.dumps(row).encode()
        members["index.jsonl"] = b"\n".join(lines) + b"\n"
    elif fault == "tampered-protocol":
        manifest = json.loads(members["manifest.json"])
        manifest["embedding_protocol"] = "bge-cls-l2-v1"
        members["manifest.json"] = json.dumps(manifest).encode()
    elif fault == "traversal":
        members["../escaped.txt"] = b"bad file"

    with zipfile.ZipFile(archive, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)

    new_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected_hash = "0" * 64 if fault == "tampered-hash" else new_hash

    target = tmp_path / "install_target"
    with pytest.raises(ValueError):
        install_pack(archive, target, expected_sha256=expected_hash, expected_identity=OLLAMA_IDENTITY)
    assert not target.exists()


def test_ollama_vector_staging_and_apply(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "entropy.md").write_text("# Entropy\nMeasures uncertainty in a signal.\n")
    database = tmp_path / "library.db"
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    with sqlite3.connect(database) as db:
        before = nonvector_fingerprint(db)

    stage = tmp_path / "stage.sqlite"
    meta = prepare_stage(database, stage, OLLAMA_IDENTITY)
    assert meta["protocol"] == OLLAMA_PROTOCOL
    assert meta["max_length"] == "8192"

    # Resume with same identity succeeds
    resumed_meta = prepare_stage(database, stage, OLLAMA_IDENTITY)
    assert resumed_meta == meta

    # Resume with different identity fails
    with pytest.raises(ValueError, match="Source or embedding identity changed"):
        prepare_stage(database, stage, NATIVE_IDENTITY)

    # Populate valid vector
    blob = pack_vector([1.0] + [0.0] * 1023)
    blob_hash = hashlib.sha256(blob).hexdigest()
    with sqlite3.connect(stage) as db:
        db.execute("UPDATE texts SET vector=?, vector_sha256=?", (blob, blob_hash))

    # Apply stage with Ollama identity
    backup = tmp_path / "backup.sqlite"
    result = apply_stage(database, stage, backup, expected_identity=OLLAMA_IDENTITY)
    assert result["applied_chunks"] > 0

    with sqlite3.connect(database) as db:
        assert nonvector_fingerprint(db) == before
        assert db.execute("SELECT count(*) FROM chunks WHERE embedding IS NULL").fetchone()[0] == 0
        prov = db.execute("SELECT DISTINCT model_digest, dimensions FROM corpus_vector_provenance").fetchall()
        assert prov == [(OLLAMA_IDENTITY, 1024)]
