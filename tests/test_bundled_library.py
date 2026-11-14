"""Contracts for retired user-library routing, storage API semantics, and explicit knowledge-pack security."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import types
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.indexer import KnowledgeIndexer
from app.knowledge_pack import export_pack
from app.server import app
from tests.knowledge_pack_review_helpers import (
    create_synthetic_review,
    create_synthetic_review_manifest,
    setup_reviewed_chunk_in_db,
)

PROJECT = Path(__file__).resolve().parents[1]
IDENTITY = "bge-m3-mlx-fp16@" + "a" * 40
OLLAMA_ID = "ollama-sha256:" + "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"

DEFAULT_BACKEND = "ollama" if sys.platform != "darwin" else "native"
DEFAULT_IDENTITY = OLLAMA_ID if DEFAULT_BACKEND == "ollama" else IDENTITY


def make_toy_publisher(tmp_path, identity=None, filename="public.md"):
    if identity is None:
        identity = DEFAULT_IDENTITY
    sources = tmp_path / "sources"
    source_file = sources / filename
    source_file.parent.mkdir(parents=True, exist_ok=True)
    content = "Encapsulation keeps state behind a public interface.\n"
    source_file.write_text(content, encoding="utf-8")
    database = tmp_path / "publisher.db"
    KnowledgeIndexer(database, sources).index_all(ocr=False, embed_model=None)
    source_sha = hashlib.sha256(source_file.read_bytes()).hexdigest()
    _, rev = create_synthetic_review(
        tmp_path / "reviews",
        filename,
        1,
        source_sha,
        transcript=content,
    )
    rev_manifest = create_synthetic_review_manifest(tmp_path / "page-reviews.json", [rev])
    with sqlite3.connect(database) as db:
        doc_row = db.execute("SELECT id, clean_title FROM documents WHERE filename=?", (filename,)).fetchone()
        setup_reviewed_chunk_in_db(db, doc_row[0], filename, doc_row[1], 1, rev, identity)
    allowlist = tmp_path / "allowed.json"
    allowlist.write_text(json.dumps([{
        "filename": filename,
        "sha256": source_sha,
        "license": "CC0-1.0",
        "redistribution_reviewed": True,
    }]), encoding="utf-8")
    archive = tmp_path / f"{DEFAULT_BACKEND}.zip"
    exported = export_pack(
        database, sources, allowlist, archive,
        expected_identity=identity,
        page_reviews=rev_manifest,
    )
    return archive, exported


def test_storage_endpoint_returns_local_only_persistence_semantics():
    client = TestClient(app)
    res = client.get("/api/storage")
    assert res.status_code == 200
    data = res.json()
    assert "sources_dir" in data
    assert "data_dir" in data
    assert data["persistent"] is True
    assert data["local_only"] is True
    assert data["auto_watch"] is False
    assert "Tài liệu tải lên" in data["policy"]

    status_res = client.get("/api/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert "storage" in status_data
    assert status_data["storage"]["persistent"] is True
    assert status_data["storage"]["sources_dir"] == data["sources_dir"]


CHILD_TEMPLATE = r'''
import importlib, importlib.util, json, os, sys, types
from pathlib import Path

EXPECTED_ID = sys.argv[3]
CHECKOUT_ROOT = Path(sys.argv[2])
MODE = sys.argv[4] if len(sys.argv) > 4 else "configure"

native = types.ModuleType('app.native_embedding')
class NativeEmbeddingClient:
    @classmethod
    def available(cls): return False
    def get_identity(self): return EXPECTED_ID
native.NativeEmbeddingClient = NativeEmbeddingClient
sys.modules['app.native_embedding'] = native

ollama = types.ModuleType('app.ollama_client')
class StubOllamaClient:
    def __init__(self, *args, **kwargs): pass
    def check_health(self): return True
    def embedding_identity(self, model="bge-m3"): return EXPECTED_ID
ollama.OllamaClient = StubOllamaClient
sys.modules['app.ollama_client'] = ollama

spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = CHECKOUT_ROOT

from app.indexer import KnowledgeIndexer
from app.pdf_extractor import PDFExtractor
from app.chunker import TextChunker
def forbidden(*a, **kw): raise AssertionError('Unexpected extraction or reindex')
KnowledgeIndexer.index_all = forbidden
PDFExtractor.extract_file = forbidden
TextChunker.chunk_page = forbidden

if MODE == "main_install":
    sys.argv = ["dev.py", "check", "--profile", "local", "--install-pack"]
    rc = launcher.main()
    if rc != 0:
        sys.exit(rc)
else:
    try:
        launcher.configure('local')
    except Exception as exc:
        print(f"LAUNCHER_ERROR: {type(exc).__name__}: {exc}")
        sys.exit(2)

from app import config
importlib.reload(config)
print(json.dumps({'data': str(config.DATA_DIR), 'sources': str(config.SRC_DIR),
                  'db': str(config.DB_PATH), 'history': os.environ.get('HISTORY_DB_PATH')}))
'''


def run_launcher(checkout: Path, identity: str = None, env_overrides: dict = None, mode: str = "configure"):
    if identity is None:
        identity = DEFAULT_IDENTITY
    removed = {
        "AGENT_DATA_DIR", "AGENT_SRC_DIR", "HISTORY_DB_PATH", "TIMETABLE_DB_PATH",
        "TIMETABLE_IMAGES_DIR", "KNOWLEDGE_PACK_PATH", "KNOWLEDGE_PACK_SHA256",
        "KNOWLEDGE_PACK_USER_DIR", "STUDY_ACCOUNTS_DIR", "MLX_MODEL_DIR",
        "MLX_9B_MODEL_DIR", "MLX_BGE_MODEL_DIR", "BGE_MODEL_DIR",
        "EMBED_BACKEND", "LLM_PROVIDER", "CHAT_BACKEND",
    }
    env = {k: v for k, v in os.environ.items() if k not in removed}
    env["PYTHONPATH"] = str(PROJECT)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", CHILD_TEMPLATE, str(PROJECT / "scripts/dev.py"), str(checkout), identity, mode],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        timeout=35,
    )


def test_descriptor_variants_ignored_and_canonical_paths_preserved(tmp_path):
    """Valid, malformed, or escaping resources descriptors never block local mode or create user-library."""
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    descriptor = resources / "knowledge-packs.json"

    for payload in [
        "NOT_JSON",
        json.dumps({"format": 2, "packs": {}}),
        json.dumps({"format": 1, "packs": {"unsupported": {"path": "resources/x.zip", "sha256": "a" * 64}}}),
        json.dumps({"format": 1, "packs": {DEFAULT_BACKEND: {"path": "../../escape.zip", "sha256": "short"}}}),
    ]:
        descriptor.write_text(payload, encoding="utf-8")
        proc = run_launcher(checkout)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        report = json.loads(proc.stdout.splitlines()[-1])
        assert report["sources"] == str(checkout / "src")
        assert report["data"] == str(checkout / "data")
        assert not (checkout / "data/user-library").exists()


def test_explicit_pack_missing_pair_or_invalid_digest_fails_closed(tmp_path):
    """Explicit KNOWLEDGE_PACK_* boundary rejects missing pair, malformed digest, and missing archive."""
    checkout = tmp_path / "app"
    checkout.mkdir(parents=True)

    # 1. Missing SHA256 pair
    proc_pair = run_launcher(checkout, env_overrides={"KNOWLEDGE_PACK_PATH": "missing.zip"})
    assert proc_pair.returncode == 2
    assert "Set KNOWLEDGE_PACK_PATH and the trusted publisher KNOWLEDGE_PACK_SHA256 together" in proc_pair.stdout

    # 2. Invalid short / uppercase digest
    proc_digest = run_launcher(checkout, env_overrides={"KNOWLEDGE_PACK_PATH": "missing.zip", "KNOWLEDGE_PACK_SHA256": "short"})
    assert proc_digest.returncode == 2
    assert "KNOWLEDGE_PACK_SHA256" in proc_digest.stdout

    # 3. Missing archive on explicit install
    proc_missing_zip = run_launcher(
        checkout,
        env_overrides={"KNOWLEDGE_PACK_PATH": str(tmp_path / "absent.zip"), "KNOWLEDGE_PACK_SHA256": "a" * 64},
        mode="main_install",
    )
    assert proc_missing_zip.returncode == 1
    assert "Knowledge pack installation failed" in proc_missing_zip.stderr
    assert not (checkout / "data/knowledge-packs" / ("a" * 64) / "knowledge-pack.json").exists()


def test_existing_unrecognized_pack_and_legacy_directories_not_overwritten(tmp_path):
    """Pre-existing files/symlinks at data/knowledge-packs/<digest> or data/user-library are never overwritten."""
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    checkout.mkdir(parents=True)

    # Preexisting unrecognized directory at explicit pack destination
    unrecognized = checkout / "data/knowledge-packs" / result["sha256"]
    unrecognized.mkdir(parents=True)
    marker = unrecognized / "personal_notes.txt"
    marker.write_text("CRITICAL EXISTING USER DATA NEVER OVERWRITE", encoding="utf-8")

    # Legacy user-library also present
    legacy = checkout / "data/user-library"
    legacy.mkdir(parents=True)
    legacy_marker = legacy / "legacy_notes.txt"
    legacy_marker.write_text("PRESERVE LEGACY USER LIBRARY", encoding="utf-8")

    proc = run_launcher(
        checkout,
        env_overrides={
            "KNOWLEDGE_PACK_PATH": str(archive),
            "KNOWLEDGE_PACK_SHA256": result["sha256"],
        },
        mode="main_install",
    )
    assert proc.returncode == 1
    assert "Incomplete knowledge-pack directory contains files" in proc.stderr
    assert marker.read_text(encoding="utf-8") == "CRITICAL EXISTING USER DATA NEVER OVERWRITE"
    assert legacy_marker.read_text(encoding="utf-8") == "PRESERVE LEGACY USER LIBRARY"


def test_explicit_pack_receipt_mismatch_fails_with_actionable_guidance(tmp_path):
    """Installed pack receipt sha256 or model_identity mismatch fails closed and preserves existing files."""
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    checkout.mkdir(parents=True)

    env_pack = {
        "KNOWLEDGE_PACK_PATH": str(archive),
        "KNOWLEDGE_PACK_SHA256": result["sha256"],
    }
    first = run_launcher(checkout, env_overrides=env_pack, mode="main_install")
    assert first.returncode == 0, first.stderr

    dest = checkout / "data/knowledge-packs" / result["sha256"]
    note = dest / "personal_keep.txt"
    note.write_text("KEEP INSTALLED DATA", encoding="utf-8")

    # Mismatched model identity fails closed
    wrong_id = DEFAULT_IDENTITY[:-4] + ("0000" if not DEFAULT_IDENTITY.endswith("0000") else "1111")
    second = run_launcher(checkout, identity=wrong_id, env_overrides=env_pack, mode="main_install")
    assert second.returncode == 1
    assert "Installed knowledge pack receipt or embedding model differs" in second.stderr
    assert note.read_text(encoding="utf-8") == "KEEP INSTALLED DATA"

    # Tampered receipt sha256 fails closed
    receipt_path = dest / "knowledge-pack.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["sha256"] = "b" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    third = run_launcher(checkout, env_overrides=env_pack, mode="main_install")
    assert third.returncode == 1
    assert "Installed knowledge pack receipt or embedding model differs" in third.stderr
    assert note.read_text(encoding="utf-8") == "KEEP INSTALLED DATA"


def test_explicit_env_pack_overrides_malformed_descriptor(tmp_path):
    """Explicit KNOWLEDGE_PACK_* resolves under data/knowledge-packs/<digest> even if descriptor is broken."""
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    (resources / "knowledge-packs.json").write_text("CORRUPT_DESCRIPTOR_IGNORED", encoding="utf-8")

    env_override = {
        "KNOWLEDGE_PACK_PATH": str(archive),
        "KNOWLEDGE_PACK_SHA256": result["sha256"],
    }
    proc = run_launcher(checkout, env_overrides=env_override, mode="main_install")
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.splitlines()[-1])
    expected_dest = checkout / "data/knowledge-packs" / result["sha256"]
    assert report["data"] == str(expected_dest)
    assert not (checkout / "data/user-library").exists()


def test_nested_original_survives_explicit_pack_install(tmp_path):
    """Nested original file paths survive explicit knowledge pack installation without user-library creation."""
    archive, result = make_toy_publisher(tmp_path, filename="course/week 01/public.md")
    checkout = tmp_path / "app"
    checkout.mkdir(parents=True)

    proc = run_launcher(
        checkout,
        env_overrides={"KNOWLEDGE_PACK_PATH": str(archive), "KNOWLEDGE_PACK_SHA256": result["sha256"]},
        mode="main_install",
    )
    assert proc.returncode == 0, proc.stderr
    original = checkout / "data/knowledge-packs" / result["sha256"] / "sources/course/week 01/public.md"
    assert original.is_file(), "Nested source file must survive explicit pack install"
    assert "Encapsulation" in original.read_text(encoding="utf-8")
    assert not (checkout / "data/user-library").exists()


def test_malicious_archive_entry_and_digest_tampering_fail_closed(tmp_path):
    """Path traversal entries or wrong archive sha256 fail closed at explicit pack install."""
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    checkout.mkdir(parents=True)

    # 1. Tampered sha256
    wrong_digest = "c" * 64
    proc_sha = run_launcher(
        checkout,
        env_overrides={"KNOWLEDGE_PACK_PATH": str(archive), "KNOWLEDGE_PACK_SHA256": wrong_digest},
        mode="main_install",
    )
    assert proc_sha.returncode == 1
    assert not (checkout / "data/knowledge-packs" / wrong_digest / "knowledge-pack.json").exists()

    # 2. Malicious traversal entry with matching outer zip digest
    bad_zip = tmp_path / "traversal.zip"
    with zipfile.ZipFile(archive, "r") as src_z, zipfile.ZipFile(bad_zip, "w") as dst_z:
        for item in src_z.infolist():
            dst_z.writestr(item, src_z.read(item.filename))
        dst_z.writestr("sources/../../pwned.txt", "escape")
    bad_digest = hashlib.sha256(bad_zip.read_bytes()).hexdigest()

    proc_zip = run_launcher(
        checkout,
        env_overrides={"KNOWLEDGE_PACK_PATH": str(bad_zip), "KNOWLEDGE_PACK_SHA256": bad_digest},
        mode="main_install",
    )
    assert proc_zip.returncode == 1
    assert not (tmp_path / "pwned.txt").exists()
    assert not (checkout / "pwned.txt").exists()
    assert not (checkout / "data/knowledge-packs" / bad_digest / "knowledge-pack.json").exists()


def test_explicit_pack_recheck_and_unlisted_host_files_isolated(tmp_path):
    """Explicit pack reinstall without receipt preserves incomplete data; default check creates no user-library."""
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    checkout.mkdir(parents=True)
    dest = checkout / "data/knowledge-packs" / result["sha256"]

    env_pack = {"KNOWLEDGE_PACK_PATH": str(archive), "KNOWLEDGE_PACK_SHA256": result["sha256"]}
    p1 = run_launcher(checkout, env_overrides=env_pack, mode="main_install")
    p2 = run_launcher(checkout, env_overrides=env_pack, mode="main_install")
    assert p1.returncode == 0, p1.stderr
    assert p2.returncode == 0, p2.stderr
    assert (dest / "sources/public.md").is_file()
    assert (dest / "knowledge-pack.json").is_file()

    # Removing receipt while keeping files in dest must fail closed without resetting
    (dest / "knowledge-pack.json").unlink()
    note = dest / "sources/user_keep.txt"
    note.write_text("DO NOT RESET NONEMPTY PACK DIR", encoding="utf-8")
    p3 = run_launcher(checkout, env_overrides=env_pack, mode="main_install")
    assert p3.returncode == 1
    assert "Incomplete knowledge-pack directory contains files" in p3.stderr
    assert note.read_text(encoding="utf-8") == "DO NOT RESET NONEMPTY PACK DIR"
    assert not (checkout / "data/user-library").exists()
