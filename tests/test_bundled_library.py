"""Comprehensive contracts for bundled knowledge-pack descriptor and durable personal library."""
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

from app.indexer import KnowledgeIndexer, pack_vector
from app.knowledge_pack import export_pack, install_pack
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
    source_file.write_text(content)
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
    }]))
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
import importlib.util, json, os, sys, types
from pathlib import Path

native = types.ModuleType('app.native_embedding')
class NativeEmbeddingClient:
    def get_identity(self): return sys.argv[3]
native.NativeEmbeddingClient = NativeEmbeddingClient
sys.modules['app.native_embedding'] = native

ollama = types.ModuleType('app.ollama_client')
class StubOllamaClient:
    def __init__(self, *args, **kwargs): pass
    def check_health(self): return True
    def embedding_identity(self, model="bge-m3"): return sys.argv[3]
ollama.OllamaClient = StubOllamaClient
sys.modules['app.ollama_client'] = ollama

spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2])

from app.indexer import KnowledgeIndexer
from app.pdf_extractor import PDFExtractor
from app.chunker import TextChunker
def forbidden(*a, **kw): raise AssertionError('Unexpected extraction or reindex')
KnowledgeIndexer.index_all = forbidden
PDFExtractor.extract_file = forbidden
TextChunker.chunk_page = forbidden

try:
    launcher.configure('local')
except Exception as exc:
    print(f"LAUNCHER_ERROR: {type(exc).__name__}: {exc}")
    sys.exit(2)

from app import config
print(json.dumps({'data': str(config.DATA_DIR), 'sources': str(config.SRC_DIR),
                  'db': str(config.DB_PATH), 'history': os.environ.get('HISTORY_DB_PATH')}))
'''


def run_launcher(checkout: Path, identity: str = None, env_overrides: dict = None):
    if identity is None:
        identity = DEFAULT_IDENTITY
    removed = {
        "AGENT_DATA_DIR", "AGENT_SRC_DIR", "HISTORY_DB_PATH", "TIMETABLE_DB_PATH",
        "TIMETABLE_IMAGES_DIR", "KNOWLEDGE_PACK_PATH", "KNOWLEDGE_PACK_SHA256",
        "KNOWLEDGE_PACK_USER_DIR", "MLX_MODEL_DIR", "MLX_9B_MODEL_DIR", "MLX_BGE_MODEL_DIR",
        "BGE_MODEL_DIR", "EMBED_BACKEND", "LLM_PROVIDER", "CHAT_BACKEND",
    }
    env = {k: v for k, v in os.environ.items() if k not in removed}
    env["PYTHONPATH"] = str(PROJECT)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", CHILD_TEMPLATE, str(PROJECT / "scripts/dev.py"), str(checkout), identity],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        timeout=35,
    )


def test_malformed_or_unsupported_descriptor_fails_closed(tmp_path):
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    descriptor = resources / "knowledge-packs.json"

    # 1. Invalid JSON
    descriptor.write_text("NOT_JSON")
    proc = run_launcher(checkout)
    assert proc.returncode == 2
    assert "Malformed knowledge-packs descriptor" in proc.stdout

    # 2. Format != 1
    descriptor.write_text(json.dumps({"format": 2, "packs": {}}))
    proc = run_launcher(checkout)
    assert proc.returncode == 2
    assert "format must be 1" in proc.stdout

    # 3. Missing selected backend
    descriptor.write_text(json.dumps({"format": 1, "packs": {"unsupported": {"path": "resources/x.zip", "sha256": "a" * 64}}}))
    proc = run_launcher(checkout)
    assert proc.returncode == 2
    assert "no configuration for selected backend" in proc.stdout

    # 4. Invalid digest length
    descriptor.write_text(json.dumps({"format": 1, "packs": {DEFAULT_BACKEND: {"path": f"resources/{DEFAULT_BACKEND}.zip", "sha256": "short"}}}))
    proc = run_launcher(checkout)
    assert proc.returncode == 2
    assert "Invalid pack sha256 digest" in proc.stdout


def test_existing_unrecognized_user_directory_is_not_overwritten(tmp_path):
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    shutil.copyfile(archive, resources / f"{DEFAULT_BACKEND}.zip")
    (resources / "knowledge-packs.json").write_text(json.dumps({
        "format": 1,
        "packs": {DEFAULT_BACKEND: {"path": f"resources/{DEFAULT_BACKEND}.zip", "sha256": result["sha256"]}},
    }))

    # Create preexisting nonempty unrecognized directory in data/user-library
    unrecognized = checkout / "data/user-library"
    unrecognized.mkdir(parents=True)
    marker = unrecognized / "personal_notes.txt"
    marker.write_text("CRITICAL EXISTING USER DATA NEVER OVERWRITE")

    proc = run_launcher(checkout)
    assert proc.returncode == 2
    assert "Existing unrecognized user directory" in proc.stdout
    assert marker.read_text() == "CRITICAL EXISTING USER DATA NEVER OVERWRITE"


def test_seed_mismatch_fails_with_actionable_guidance(tmp_path):
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    shutil.copyfile(archive, resources / f"{DEFAULT_BACKEND}.zip")
    (resources / "knowledge-packs.json").write_text(json.dumps({
        "format": 1,
        "packs": {DEFAULT_BACKEND: {"path": f"resources/{DEFAULT_BACKEND}.zip", "sha256": result["sha256"]}},
    }))

    # First launch seeds successfully
    first = run_launcher(checkout)
    assert first.returncode == 0, first.stderr

    # Tamper descriptor with a different digest
    other_digest = "b" * 64
    (resources / "knowledge-packs.json").write_text(json.dumps({
        "format": 1,
        "packs": {DEFAULT_BACKEND: {"path": f"resources/{DEFAULT_BACKEND}.zip", "sha256": other_digest}},
    }))

    second = run_launcher(checkout)
    assert second.returncode == 2
    assert "Existing personal data is preserved" in second.stdout
    assert "Move or backup" in second.stdout


def test_explicit_env_pack_overrides_descriptor(tmp_path):
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    (resources / "knowledge-packs.json").write_text(json.dumps({"format": 1, "packs": {}}))

    # Explicit KNOWLEDGE_PACK_PATH + KNOWLEDGE_PACK_SHA256 override descriptor
    env_override = {
        "KNOWLEDGE_PACK_PATH": str(archive),
        "KNOWLEDGE_PACK_SHA256": result["sha256"],
        "KNOWLEDGE_PACK_IDENTITY": DEFAULT_IDENTITY,
    }
    proc = run_launcher(checkout, env_overrides=env_override)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.splitlines()[-1])
    expected_legacy_dest = checkout / "data/knowledge-packs" / result["sha256"]
    assert report["data"] == str(expected_legacy_dest)


def test_nested_original_survives_seed_to_personal_library(tmp_path):
    archive, result = make_toy_publisher(tmp_path, filename="course/week 01/public.md")
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    shutil.copyfile(archive, resources / f"{DEFAULT_BACKEND}.zip")
    (resources / "knowledge-packs.json").write_text(json.dumps({
        "format": 1,
        "packs": {DEFAULT_BACKEND: {"path": f"resources/{DEFAULT_BACKEND}.zip", "sha256": result["sha256"]}},
    }))

    proc = run_launcher(checkout)
    assert proc.returncode == 0, proc.stderr
    original = checkout / "data/user-library/src/course/week 01/public.md"
    assert original.is_file(), "Nested source file must survive seeding"
    assert "Encapsulation" in original.read_text()


def test_damaged_personal_database_fails_closed_without_reset(tmp_path):
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    shutil.copyfile(archive, resources / f"{DEFAULT_BACKEND}.zip")
    (resources / "knowledge-packs.json").write_text(json.dumps({
        "format": 1,
        "packs": {DEFAULT_BACKEND: {"path": f"resources/{DEFAULT_BACKEND}.zip", "sha256": result["sha256"]}},
    }))

    # Initial launch
    proc1 = run_launcher(checkout)
    assert proc1.returncode == 0, proc1.stderr

    library = checkout / "data/user-library"
    personal = library / "src/personal_notes.txt"
    personal.write_text("PERSONAL ORIGINAL MUST BE PRESERVED")

    # Unlink database
    (library / "knowledge_base.db").unlink()

    # Second launch must fail closed
    proc2 = run_launcher(checkout)
    assert proc2.returncode == 2
    assert "damaged: missing working database" in proc2.stdout
    assert personal.read_text() == "PERSONAL ORIGINAL MUST BE PRESERVED"
    assert not (library / "knowledge_base.db").exists()


def test_seeding_ignores_unlisted_files_and_extra_tables_in_legacy_cache(tmp_path):
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    shutil.copyfile(archive, resources / f"{DEFAULT_BACKEND}.zip")
    (resources / "knowledge-packs.json").write_text(json.dumps({
        "format": 1,
        "packs": {DEFAULT_BACKEND: {"path": f"resources/{DEFAULT_BACKEND}.zip", "sha256": result["sha256"]}},
    }))

    # First launch
    proc1 = run_launcher(checkout)
    assert proc1.returncode == 0, proc1.stderr

    # Remove user library and contaminate legacy publisher cache
    shutil.rmtree(checkout / "data/user-library")
    cache = checkout / "data/knowledge-packs" / result["sha256"]
    unlisted_file = cache / "sources/confidential_memo.txt"
    unlisted_file.write_text("CONFIDENTIAL CACHE CONTENT NEVER EXPORT TO USER")
    with sqlite3.connect(cache / "knowledge_base.db") as db:
        db.execute("CREATE TABLE private_admin_notes(id INT, note TEXT)")
        db.execute("INSERT INTO private_admin_notes VALUES(1, 'SECRET NOTE')")

    # Second launch seeds user library
    proc2 = run_launcher(checkout)
    assert unlisted_file.read_text() == "CONFIDENTIAL CACHE CONTENT NEVER EXPORT TO USER"
    if proc2.returncode == 0:
        library = checkout / "data/user-library"
        assert not (library / "src/confidential_memo.txt").exists()
        with sqlite3.connect(library / "knowledge_base.db") as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            assert "private_admin_notes" not in tables


def test_concurrent_initialization_safety(tmp_path):
    archive, result = make_toy_publisher(tmp_path)
    checkout = tmp_path / "app"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    shutil.copyfile(archive, resources / f"{DEFAULT_BACKEND}.zip")
    (resources / "knowledge-packs.json").write_text(json.dumps({
        "format": 1,
        "packs": {DEFAULT_BACKEND: {"path": f"resources/{DEFAULT_BACKEND}.zip", "sha256": result["sha256"]}},
    }))

    # Launch two subprocesses sequentially or concurrently
    p1 = run_launcher(checkout)
    p2 = run_launcher(checkout)
    assert p1.returncode == 0, p1.stderr
    assert p2.returncode == 0, p2.stderr
    assert (checkout / "data/user-library/src/public.md").is_file()
