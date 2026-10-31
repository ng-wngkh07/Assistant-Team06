"""Regression tests for Ollama portable pack launcher, path configuration, retry, and provider decoupling."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

import pytest

from app.indexer import KnowledgeIndexer, pack_vector
from app.knowledge_pack import export_pack, install_pack, NATIVE_PROTOCOL, OLLAMA_PROTOCOL
from app.vector_rebuild import protocol_for_identity, apply_stage, nonvector_fingerprint
from tests.knowledge_pack_review_helpers import (
    create_synthetic_review,
    create_synthetic_review_manifest,
    setup_reviewed_chunk_in_db,
)

OLLAMA_IDENTITY = "ollama-sha256:" + "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"
NATIVE_IDENTITY = "bge-m3-mlx-fp16@" + "a" * 40
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def make_ollama_pack(tmp_path):
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
        setup_reviewed_chunk_in_db(db, doc_row[0], "public.md", doc_row[1], 1, rev, OLLAMA_IDENTITY)
    allowlist = tmp_path / "allowed.json"
    allowlist.write_text(json.dumps([{
        "filename": "public.md",
        "sha256": source_sha,
        "license": "CC0-1.0",
        "redistribution_reviewed": True
    }]))
    archive = tmp_path / "knowledge_ollama.zip"
    exported = export_pack(
        database, sources, allowlist, archive,
        expected_identity=OLLAMA_IDENTITY,
        page_reviews=rev_manifest,
    )
    return archive, exported


def test_subprocess_launcher_config_path_ordering(tmp_path):
    """Subprocess test: verify app.config imports get the knowledge-pack paths, not stale default data paths."""
    archive, exported = make_ollama_pack(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    old_data = checkout / "initial-data"

    child_code = r'''
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

# Stub Ollama client to return the exact identity without contacting live daemon
ollama_mod = types.ModuleType("app.ollama_client")
class StubOllamaClient:
    def __init__(self, *args, **kwargs):
        pass
    def check_health(self):
        return True
    def embedding_identity(self, model="bge-m3"):
        return sys.argv[3]
ollama_mod.OllamaClient = StubOllamaClient
sys.modules["app.ollama_client"] = ollama_mod

spec = importlib.util.spec_from_file_location("launcher", sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2])
launcher.configure("local")

import app.config as c
dest = Path(os.environ["AGENT_DATA_DIR"])
report = {
    "db_same": c.DB_PATH == dest / "knowledge_base.db",
    "src_same": c.SRC_DIR == dest / "sources",
    "data_dir_same": c.DATA_DIR == dest,
    "history_env_same": os.environ.get("HISTORY_DB_PATH") == str(dest / "history.db"),
    "timetable_env_same": os.environ.get("TIMETABLE_DB_PATH") == str(dest / "timetable.db"),
    "receipt_exists": (dest / "knowledge-pack.json").is_file(),
}
print(json.dumps(report))
'''

    env = {k: v for k, v in os.environ.items() if k not in {
        "AGENT_DATA_DIR", "AGENT_SRC_DIR", "HISTORY_DB_PATH", "TIMETABLE_DB_PATH",
        "TIMETABLE_IMAGES_DIR", "KNOWLEDGE_PACK_PATH", "KNOWLEDGE_PACK_SHA256",
        "MLX_MODEL_DIR", "MLX_9B_MODEL_DIR", "MLX_BGE_MODEL_DIR", "BGE_MODEL_DIR"
    }}
    env["PYTHONPATH"] = str(WORKSPACE_ROOT)
    env["AGENT_DATA_DIR"] = str(old_data)
    env["KNOWLEDGE_PACK_PATH"] = str(archive)
    env["KNOWLEDGE_PACK_SHA256"] = exported["sha256"]
    env["EMBED_BACKEND"] = "ollama"

    launcher_path = WORKSPACE_ROOT / "scripts/dev.py"
    cmd = [sys.executable, "-c", child_code, str(launcher_path), str(checkout), OLLAMA_IDENTITY]
    res = subprocess.run(cmd, env=env, cwd=checkout, text=True, capture_output=True, timeout=30)
    assert res.returncode == 0, f"Child failed with:\n{res.stderr}\n{res.stdout}"

    report = json.loads(res.stdout.strip().splitlines()[-1])
    assert report["db_same"] is True
    assert report["src_same"] is True
    assert report["data_dir_same"] is True
    assert report["history_env_same"] is True
    assert report["timetable_env_same"] is True
    assert report["receipt_exists"] is True


def test_launcher_retry_and_directory_safety(tmp_path):
    """Launcher retry removes empty tree on failure, but preserves non-empty files and symlinks."""
    archive, exported = make_ollama_pack(tmp_path)
    checkout = tmp_path / "checkout_retry"
    checkout.mkdir()
    destination = checkout / "data/knowledge-packs" / exported["sha256"]

    child_code = r'''
import importlib.util
import os
from pathlib import Path
import sys
import types

fail_mode = sys.argv[4]

ollama_mod = types.ModuleType("app.ollama_client")
class StubOllamaClient:
    def __init__(self, *args, **kwargs):
        pass
    def check_health(self):
        return True
    def embedding_identity(self, model="bge-m3"):
        if fail_mode == "error":
            raise OSError("Injected artifact read failure")
        return sys.argv[3]
ollama_mod.OllamaClient = StubOllamaClient
sys.modules["app.ollama_client"] = ollama_mod

spec = importlib.util.spec_from_file_location("launcher", sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2])
try:
    launcher.configure("local")
except (OSError, ValueError) as exc:
    print(f"{type(exc).__name__}: {exc}")
    sys.exit(2)
'''

    env = {k: v for k, v in os.environ.items() if k not in {
        "AGENT_DATA_DIR", "AGENT_SRC_DIR", "HISTORY_DB_PATH", "TIMETABLE_DB_PATH",
        "TIMETABLE_IMAGES_DIR", "KNOWLEDGE_PACK_PATH", "KNOWLEDGE_PACK_SHA256"
    }}
    env["PYTHONPATH"] = str(WORKSPACE_ROOT)
    env["KNOWLEDGE_PACK_PATH"] = str(archive)
    env["KNOWLEDGE_PACK_SHA256"] = exported["sha256"]
    env["EMBED_BACKEND"] = "ollama"

    launcher_path = WORKSPACE_ROOT / "scripts/dev.py"

    # 1. First run creates empty destination tree then fails
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "empty_sub").mkdir()

    # Second run with fail_mode='ready' cleans empty tree and succeeds
    res_ready = subprocess.run(
        [sys.executable, "-c", child_code, str(launcher_path), str(checkout), OLLAMA_IDENTITY, "ready"],
        env=env, cwd=checkout, text=True, capture_output=True, timeout=30
    )
    assert res_ready.returncode == 0, f"Retry on empty tree failed:\n{res_ready.stderr}\n{res_ready.stdout}"
    assert (destination / "knowledge-pack.json").is_file()

    # 2. Existing nonempty file must be preserved and raise ValueError
    checkout2 = tmp_path / "checkout_nonempty"
    checkout2.mkdir()
    dest2 = checkout2 / "data/knowledge-packs" / exported["sha256"]
    dest2.mkdir(parents=True, exist_ok=True)
    marker = dest2 / "existing_user_data.sqlite"
    marker_content = b"CRITICAL USER DATA DO NOT DELETE"
    marker.write_bytes(marker_content)

    res_nonempty = subprocess.run(
        [sys.executable, "-c", child_code, str(launcher_path), str(checkout2), OLLAMA_IDENTITY, "ready"],
        env=env, cwd=checkout2, text=True, capture_output=True, timeout=30
    )
    assert res_nonempty.returncode == 2
    assert "Incomplete knowledge-pack directory contains files" in res_nonempty.stdout
    assert marker.read_bytes() == marker_content, "Existing user data was modified or removed!"

    # 3. Existing symlink must be preserved and raise ValueError
    checkout3 = tmp_path / "checkout_symlink"
    checkout3.mkdir()
    dest3 = checkout3 / "data/knowledge-packs" / exported["sha256"]
    dest3.parent.mkdir(parents=True, exist_ok=True)
    target_link = tmp_path / "symlink_target"
    target_link.mkdir()
    dest3.symlink_to(target_link)

    res_symlink = subprocess.run(
        [sys.executable, "-c", child_code, str(launcher_path), str(checkout3), OLLAMA_IDENTITY, "ready"],
        env=env, cwd=checkout3, text=True, capture_output=True, timeout=30
    )
    assert res_symlink.returncode == 2
    assert "Incomplete knowledge-pack directory contains files" in res_symlink.stdout
    assert dest3.is_symlink()


def test_vector_rebuild_cleanliness_and_contract(tmp_path):
    """Verify vector_rebuild has zero references to private operator files and enforces contracts."""
    vector_rebuild_text = (WORKSPACE_ROOT / "app/vector_rebuild.py").read_text(encoding="utf-8")
    assert "/private/tmp" not in vector_rebuild_text
    assert "ollama-gpu-permit" not in vector_rebuild_text
    assert "gpu-permit" not in vector_rebuild_text

    # Protocol binding
    proto_ollama, max_len_ollama = protocol_for_identity(OLLAMA_IDENTITY)
    assert proto_ollama == OLLAMA_PROTOCOL
    assert max_len_ollama == "8192"

    proto_native, max_len_native = protocol_for_identity(NATIVE_IDENTITY)
    assert proto_native == NATIVE_PROTOCOL
    assert max_len_native == "2048"

    from app.knowledge_pack import validate_and_get_contract
    with pytest.raises(ValueError, match="Unsupported or unverified"):
        validate_and_get_contract("unknown-digest-1234")


def test_launcher_cold_daemon_and_untrusted_identity_rejection(tmp_path):
    """A stopped Ollama daemon without --start-model-service raises actionable error; env var cannot bypass."""
    archive, exported = make_ollama_pack(tmp_path)
    checkout = tmp_path / "checkout_cold"
    checkout.mkdir()

    child_code = r'''
import importlib.util
from pathlib import Path
import sys
import types

ollama_mod = types.ModuleType("app.ollama_client")
class ColdOllamaClient:
    def __init__(self, *args, **kwargs):
        pass
    def check_health(self):
        return False
    def embedding_identity(self, model="bge-m3"):
        return None
ollama_mod.OllamaClient = ColdOllamaClient
sys.modules["app.ollama_client"] = ollama_mod

spec = importlib.util.spec_from_file_location("launcher", sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2])
sys.argv = ["dev.py", "check", "--profile", "local"]
sys.exit(launcher.main())
'''

    env = {k: v for k, v in os.environ.items() if k not in {
        "AGENT_DATA_DIR", "AGENT_SRC_DIR", "HISTORY_DB_PATH", "TIMETABLE_DB_PATH",
        "TIMETABLE_IMAGES_DIR", "KNOWLEDGE_PACK_PATH", "KNOWLEDGE_PACK_SHA256"
    }}
    env["PYTHONPATH"] = str(WORKSPACE_ROOT)
    env["KNOWLEDGE_PACK_PATH"] = str(archive)
    env["KNOWLEDGE_PACK_SHA256"] = exported["sha256"]
    env["EMBED_BACKEND"] = "ollama"
    # Attempt bypass via untrusted env var
    env["KNOWLEDGE_PACK_IDENTITY"] = OLLAMA_IDENTITY

    launcher_path = WORKSPACE_ROOT / "scripts/dev.py"
    res = subprocess.run(
        [sys.executable, "-c", child_code, str(launcher_path), str(checkout)],
        env=env, cwd=checkout, text=True, capture_output=True, timeout=30
    )
    assert res.returncode == 1
    assert "Could not verify Ollama embedding identity" in res.stderr
    assert "Ensure Ollama is running" in res.stderr


def test_provider_envs_and_decoupled_coverage(tmp_path, monkeypatch):
    """Verify new provider envs are recognized and coverage is decoupled between chat and embed."""
    from scripts.dev import ENV_KEYS
    assert "LLM_PROVIDER" in ENV_KEYS
    assert "CHAT_BACKEND" in ENV_KEYS
    assert "EMBED_BACKEND" in ENV_KEYS

    from app import config
    from app import server
    from app.server import _configured_index_coverage
    from app.native_embedding import BGE_NATIVE_IDENTITY

    blob = pack_vector([1.0] + [0.0] * 1023)
    text = "Encapsulation concept"
    t_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    v_sha = hashlib.sha256(blob).hexdigest()

    # Case A: Database has native BGE vectors
    native_db = tmp_path / "native.db"
    with sqlite3.connect(native_db) as conn:
        conn.execute("CREATE TABLE chunks (id INT, text TEXT, embedding BLOB)")
        conn.execute("INSERT INTO chunks VALUES (1, ?, ?)", (text, blob))
        conn.execute("""CREATE TABLE corpus_vector_provenance
            (chunk_id INT, text_sha256 TEXT, model_digest TEXT, vector_sha256 TEXT, dimensions INT, full_text INT)""")
        conn.execute("INSERT INTO corpus_vector_provenance VALUES (1, ?, ?, ?, 1024, 1)",
                     (t_sha, BGE_NATIVE_IDENTITY, v_sha))

    monkeypatch.setattr(config, "DB_PATH", native_db)
    if sys.platform == "darwin":
        from app.native_embedding import NativeEmbeddingClient
        monkeypatch.setattr(NativeEmbeddingClient, "get_identity", lambda self: BGE_NATIVE_IDENTITY)
    cov_native = _configured_index_coverage()
    if sys.platform == "darwin":
        assert cov_native["status"] == "full"
        assert cov_native["total_chunks"] == 1
        assert cov_native["compatible_chunks"] == 1

    # Case B: Database has Ollama vectors
    ollama_db = tmp_path / "ollama.db"
    with sqlite3.connect(ollama_db) as conn:
        conn.execute("CREATE TABLE chunks (id INT, text TEXT, embedding BLOB)")
        conn.execute("INSERT INTO chunks VALUES (1, ?, ?)", (text, blob))
        conn.execute("""CREATE TABLE corpus_vector_provenance
            (chunk_id INT, text_sha256 TEXT, model_digest TEXT, vector_sha256 TEXT, dimensions INT, full_text INT)""")
        conn.execute("INSERT INTO corpus_vector_provenance VALUES (1, ?, ?, ?, 1024, 1)",
                     (t_sha, OLLAMA_IDENTITY, v_sha))

    monkeypatch.setattr(config, "DB_PATH", ollama_db)
    monkeypatch.setattr(server.ollama, "embedding_identity", lambda model="bge-m3": OLLAMA_IDENTITY)
    cov_ollama = _configured_index_coverage()
    assert cov_ollama["status"] == "full"
    assert cov_ollama["total_chunks"] == 1
    assert cov_ollama["compatible_chunks"] == 1
    assert cov_ollama["status"] == "full"
    assert cov_ollama["total_chunks"] == 1
    assert cov_ollama["compatible_chunks"] == 1
