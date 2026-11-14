"""Tests for canonical hosted shared source/data path resolution and retired user-library routing."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

PROJECT = Path(__file__).resolve().parents[1]

STRIPPED_ENV_KEYS = {
    "AGENT_DATA_DIR",
    "AGENT_SRC_DIR",
    "HISTORY_DB_PATH",
    "TIMETABLE_DB_PATH",
    "TIMETABLE_IMAGES_DIR",
    "STUDY_ACCOUNTS_DIR",
    "KNOWLEDGE_PACK_PATH",
    "KNOWLEDGE_PACK_SHA256",
    "KNOWLEDGE_PACK_USER_DIR",
    "KNOWLEDGE_PACK_IDENTITY",
    "DEFAULT_CHAT_MODEL",
    "EMBED_BACKEND",
}


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV_KEYS}
    env["PYTHONPATH"] = str(PROJECT)
    if extra:
        env.update(extra)
    return env


def _run_dev_subprocess(checkout: Path, argv: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    child = r'''
import importlib.util, json, os, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("launcher", sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2])
sys.argv = ["dev.py", *sys.argv[3:]]
rc = launcher.main()
print("ENV_JSON:" + json.dumps({
    "AGENT_SRC_DIR": os.environ.get("AGENT_SRC_DIR"),
    "AGENT_DATA_DIR": os.environ.get("AGENT_DATA_DIR"),
    "HISTORY_DB_PATH": os.environ.get("HISTORY_DB_PATH"),
    "TIMETABLE_DB_PATH": os.environ.get("TIMETABLE_DB_PATH"),
    "TIMETABLE_IMAGES_DIR": os.environ.get("TIMETABLE_IMAGES_DIR"),
    "STUDY_ACCOUNTS_DIR": os.environ.get("STUDY_ACCOUNTS_DIR"),
}))
sys.exit(rc)
'''
    return subprocess.run(
        [sys.executable, "-c", child, str(PROJECT / "scripts/dev.py"), str(checkout), *argv],
        cwd=checkout,
        env=_clean_env(env_extra),
        text=True,
        capture_output=True,
        timeout=35,
    )


def _extract_env_json(stdout: str) -> dict[str, str | None]:
    for line in reversed(stdout.splitlines()):
        if line.startswith("ENV_JSON:"):
            return json.loads(line[len("ENV_JSON:"):])
    raise AssertionError(f"ENV_JSON line missing from output:\n{stdout}")


def test_fresh_checkout_without_src_resolves_canonical_paths_and_no_implicit_writes(tmp_path):
    """A clean checkout without src/ resolves canonical src/data, creates no DB or user-library, and reports MISSING."""
    checkout = tmp_path / "fresh_checkout"
    checkout.mkdir()

    proc = _run_dev_subprocess(checkout, ["check", "--profile", "local"])
    assert proc.returncode == 0, proc.stderr + proc.stdout
    env_out = _extract_env_json(proc.stdout)
    assert env_out["AGENT_SRC_DIR"] == str(checkout / "src")
    assert env_out["AGENT_DATA_DIR"] == str(checkout / "data")
    assert "Library: MISSING" in proc.stdout
    assert "Mode: Full" not in proc.stdout
    assert not (checkout / "src").exists()
    assert not (checkout / "data/user-library").exists()
    assert not (checkout / "data/knowledge_base.db").exists()
    assert not (checkout / "data/history.db").exists()
    assert not (checkout / "data/timetable.db").exists()
    assert not (checkout / "data/knowledge-packs").exists()


def test_malformed_or_escaping_unused_descriptor_does_not_block_local_check(tmp_path):
    """Malformed or path-escaping resources/knowledge-packs.json is ignored in local mode even when src is absent."""
    checkout = tmp_path / "checkout_bad_desc"
    resources = checkout / "resources"
    resources.mkdir(parents=True)

    for payload in [
        "{invalid json",
        json.dumps({"format": 999}),
        json.dumps({"format": 1, "packs": {"native": {"path": "../../../etc/passwd", "sha256": "bad"}}}),
    ]:
        (resources / "knowledge-packs.json").write_text(payload, encoding="utf-8")
        proc = _run_dev_subprocess(checkout, ["check", "--profile", "local"])
        assert proc.returncode == 0, proc.stderr + proc.stdout
        env_out = _extract_env_json(proc.stdout)
        assert env_out["AGENT_SRC_DIR"] == str(checkout / "src")
        assert env_out["AGENT_DATA_DIR"] == str(checkout / "data")
        assert not (checkout / "data/user-library").exists()


def test_explicit_env_and_dotenv_paths_honored_end_to_end(tmp_path):
    """Explicit AGENT_SRC_DIR, AGENT_DATA_DIR, history, timetable, and account dirs override defaults cleanly."""
    checkout = tmp_path / "checkout_custom"
    checkout.mkdir()
    (checkout / ".env").write_text(
        "AGENT_SRC_DIR=shared/src\n"
        "AGENT_DATA_DIR=state/data\n"
        "HISTORY_DB_PATH=state/history/custom_history.db\n"
        "TIMETABLE_DB_PATH=state/calendar/custom_tt.db\n"
        "TIMETABLE_IMAGES_DIR=state/calendar/images\n"
        "STUDY_ACCOUNTS_DIR=state/accounts\n",
        encoding="utf-8",
    )

    child = r'''
import importlib.util, json, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("launcher", sys.argv[1])
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.ROOT = Path(sys.argv[2])
launcher.configure("local")

from app import accounts, config, server, timetable_store
print(json.dumps({
    "src_dir": str(config.SRC_DIR),
    "data_dir": str(config.DATA_DIR),
    "accounts_dir": str(accounts.root_dir()),
    "history_db": str(server.get_history_store().path),
    "timetable_db": str(timetable_store.get_db_path()),
    "timetable_images": str(timetable_store.get_images_dir()),
}))
'''
    proc_dotenv = subprocess.run(
        [sys.executable, "-c", child, str(PROJECT / "scripts/dev.py"), str(checkout)],
        cwd=checkout,
        env=_clean_env(),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc_dotenv.returncode == 0, proc_dotenv.stderr
    out_dotenv = json.loads(proc_dotenv.stdout.splitlines()[-1])
    assert out_dotenv["src_dir"] == str(checkout / "shared/src")
    assert out_dotenv["data_dir"] == str(checkout / "state/data")
    assert out_dotenv["accounts_dir"] == str(checkout / "state/accounts")
    assert out_dotenv["history_db"] == str(checkout / "state/history/custom_history.db")
    assert out_dotenv["timetable_db"] == str(checkout / "state/calendar/custom_tt.db")
    assert out_dotenv["timetable_images"] == str(checkout / "state/calendar/images")

    # Existing process env overrides .env
    explicit_src = tmp_path / "mounted_src"
    explicit_accounts = tmp_path / "mounted_accounts"
    proc_env = subprocess.run(
        [sys.executable, "-c", child, str(PROJECT / "scripts/dev.py"), str(checkout)],
        cwd=checkout,
        env=_clean_env({
            "AGENT_SRC_DIR": str(explicit_src),
            "STUDY_ACCOUNTS_DIR": str(explicit_accounts),
        }),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc_env.returncode == 0, proc_env.stderr
    out_env = json.loads(proc_env.stdout.splitlines()[-1])
    assert out_env["src_dir"] == str(explicit_src)
    assert out_env["accounts_dir"] == str(explicit_accounts)


def test_demo_profile_remains_explicit_fixtures_and_team_demo(tmp_path):
    """Demo profile stays isolated in tests/fixtures/documents and data/team-demo without touching user-library."""
    checkout = tmp_path / "checkout_demo"
    fixtures = checkout / "tests/fixtures/documents"
    fixtures.mkdir(parents=True)
    (fixtures / "sample.md").write_text("# Synthetic Demo\nDemo content only.\n", encoding="utf-8")

    proc = _run_dev_subprocess(checkout, ["demo"])
    assert proc.returncode == 0, proc.stderr + proc.stdout
    env_out = _extract_env_json(proc.stdout)
    assert env_out["AGENT_SRC_DIR"] == str(checkout / "tests/fixtures/documents")
    assert env_out["AGENT_DATA_DIR"] == str(checkout / "data/team-demo")
    assert env_out["HISTORY_DB_PATH"] == str(checkout / "data/team-demo/history.db")
    assert env_out["TIMETABLE_DB_PATH"] == str(checkout / "data/team-demo/timetable.db")
    assert env_out["TIMETABLE_IMAGES_DIR"] == str(checkout / "data/team-demo/timetable_images")
    assert (checkout / "data/team-demo/knowledge_base.db").is_file()
    assert not (checkout / "data/user-library").exists()


def test_install_pack_without_explicit_config_fails_clearly_with_zero_seeding(tmp_path):
    """--install-pack without explicit KNOWLEDGE_PACK_PATH+SHA256 fails cleanly and writes no pack or user-library."""
    checkout = tmp_path / "checkout_no_pack"
    resources = checkout / "resources"
    resources.mkdir(parents=True)
    (resources / "knowledge-packs.json").write_text(json.dumps({
        "format": 1,
        "packs": {
            "native": {"path": "resources/demo.zip", "sha256": "a" * 64},
            "ollama": {"path": "resources/demo.zip", "sha256": "a" * 64},
        },
    }), encoding="utf-8")

    # 1. Neither env var set
    proc_none = _run_dev_subprocess(checkout, ["check", "--profile", "local", "--install-pack"])
    assert proc_none.returncode == 1
    assert "No explicit knowledge pack configured" in proc_none.stderr
    assert "no seeding performed" in proc_none.stderr

    # 2. Only path set
    proc_half = _run_dev_subprocess(
        checkout,
        ["check", "--profile", "local", "--install-pack"],
        env_extra={"KNOWLEDGE_PACK_PATH": "resources/demo.zip"},
    )
    assert proc_half.returncode == 1
    assert "Set KNOWLEDGE_PACK_PATH and the trusted publisher KNOWLEDGE_PACK_SHA256 together" in proc_half.stderr

    # 3. Demo profile rejects --install-pack
    proc_demo = _run_dev_subprocess(checkout, ["check", "--profile", "demo", "--install-pack"])
    assert proc_demo.returncode == 1
    assert "No explicit knowledge pack configured" in proc_demo.stderr

    assert not (checkout / "data/user-library").exists()
    assert not (checkout / "data/knowledge-packs").exists()


def _mock_ready_models(monkeypatch) -> None:
    """Stub local QA/vision/embedding readiness without loading weights or contacting services."""
    import app.config as cfg
    from app.trained_client import TrainedModelClient
    from app.native_vision import NativeVisionClient
    from app.native_embedding import NativeEmbeddingClient
    from app import runtime_health
    from app.ollama_client import OllamaClient

    if sys.platform == "darwin":
        monkeypatch.setattr(cfg, "DEFAULT_CHAT_MODEL", "qwen3.5-9b-4bit")
        monkeypatch.setattr(TrainedModelClient, "available", classmethod(lambda cls, m=None: True))
        monkeypatch.setattr(NativeVisionClient, "available", classmethod(lambda cls: True))
        monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls: True))
        monkeypatch.setattr(runtime_health, "probe_mlx_runtime", lambda force=False: {"ready": True, "code": "ready", "message": "ok"})
    else:
        monkeypatch.setattr(cfg, "DEFAULT_CHAT_MODEL", "qwen3.5:9b")
        monkeypatch.setattr(cfg, "OLLAMA_AUX_CHAT_MODEL", "qwen3.5:9b")
        monkeypatch.setattr(OllamaClient, "check_health", lambda self: True)
        monkeypatch.setattr(OllamaClient, "list_models", lambda self: [
            {"name": "qwen3.5:9b"},
            {"name": "bge-m3:latest"},
        ])


def _seed_one_doc_db(db_path: Path) -> str:
    """Create a minimal SQLite documents DB with 1 row and return its initial SHA256."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO documents (id) VALUES (1)")
    return hashlib.sha256(db_path.read_bytes()).hexdigest()


def test_missing_or_empty_library_never_claims_full_qa_ready(tmp_path, monkeypatch, capsys):
    """Even when models appear ready, missing src or 0-doc DB never prints full QA ready."""
    from scripts import dev
    import app.config as cfg

    missing_src = tmp_path / "missing_src"
    empty_data = tmp_path / "empty_data"
    empty_data.mkdir()
    monkeypatch.setattr(cfg, "SRC_DIR", missing_src)
    monkeypatch.setattr(cfg, "DATA_DIR", empty_data)
    monkeypatch.setattr(cfg, "DB_PATH", empty_data / "knowledge_base.db")
    _mock_ready_models(monkeypatch)

    dev.check_environment()
    out = capsys.readouterr().out
    assert "Library: MISSING" in out
    assert "Mode: Full" not in out
    assert "Library: UNAVAILABLE" in out


def test_inspect_library_state_distinguishes_empty_nested_dirs_from_nested_originals(tmp_path, monkeypatch):
    """Empty/nested/hidden directories do not count as source content; nested originals do, read-only."""
    from scripts import dev

    db_path = tmp_path / "data/knowledge_base.db"
    db_sha_before = _seed_one_doc_db(db_path)

    # 1. Missing src directory
    assert dev._inspect_library_state(tmp_path / "missing_src", db_path) == (False, 1)

    # 2. Regular file passed instead of a directory
    file_src = tmp_path / "file_as_src"
    file_src.write_text("not a directory\n", encoding="utf-8")
    assert dev._inspect_library_state(file_src, db_path) == (False, 1)

    # 3. Root with nested empty directories and hidden dotfiles/dotdirs only
    empty_nested = tmp_path / "empty_nested"
    (empty_nested / "nested/empty/deep").mkdir(parents=True)
    (empty_nested / ".DS_Store").write_text("finder\n", encoding="utf-8")
    (empty_nested / ".hidden/subdir").mkdir(parents=True)
    (empty_nested / ".hidden/subdir/ignored.md").write_text("hidden\n", encoding="utf-8")
    assert dev._inspect_library_state(empty_nested, db_path) == (False, 1)

    # 4. Symlink safety: broken or escaping symlinks alone do not count as valid source content
    symlink_only = tmp_path / "symlink_only"
    symlink_only.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (symlink_only / "escape.md").symlink_to(outside)
        (symlink_only / "broken.md").symlink_to(tmp_path / "does_not_exist.md")
    except OSError:
        pass
    else:
        assert dev._inspect_library_state(symlink_only, db_path) == (False, 1)

    # 5. Root containing empty nested dir AND an actual nested original file
    nested_with_file = tmp_path / "nested_with_file"
    (nested_with_file / "empty_branch/leaf").mkdir(parents=True)
    original = nested_with_file / "nested/topic/original.md"
    original.parent.mkdir(parents=True)
    original.write_text("A source file exists.\n", encoding="utf-8")
    orig_sha_before = hashlib.sha256(original.read_bytes()).hexdigest()

    assert dev._inspect_library_state(nested_with_file, db_path) == (True, 1)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == orig_sha_before
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == db_sha_before

    # 6. Graceful handling when missing/invalid/unreadable DB or unreadable directory
    absent_db = tmp_path / "absent/knowledge_base.db"
    assert dev._inspect_library_state(nested_with_file, absent_db) == (True, 0)
    assert not absent_db.exists()

    no_table_db = tmp_path / "no_table.db"
    with sqlite3.connect(no_table_db) as conn:
        conn.execute("CREATE TABLE other (x INT)")
    assert dev._inspect_library_state(nested_with_file, no_table_db) == (True, 0)

    def _deny_scandir(_path):
        raise PermissionError("simulated unreadable dir")

    with monkeypatch.context() as m:
        m.setattr(os, "scandir", _deny_scandir)
        assert dev._inspect_library_state(nested_with_file, db_path) == (False, 1)

    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0:
        mixed = tmp_path / "mixed_perms"
        locked = mixed / "locked"
        locked.mkdir(parents=True)
        locked.chmod(0o000)
        try:
            assert dev._inspect_library_state(mixed, db_path) == (False, 1)
            sibling = mixed / "readable/note.md"
            sibling.parent.mkdir(parents=True)
            sibling.write_text("readable sibling\n", encoding="utf-8")
            assert dev._inspect_library_state(mixed, db_path) == (True, 1)
        finally:
            locked.chmod(0o755)


def test_empty_nested_src_with_indexed_db_never_claims_full_qa_ready(tmp_path, monkeypatch, capsys):
    """Empty nested src dirs with retained documents DB report MISSING/UNAVAILABLE until an original exists."""
    from app.indexer import KnowledgeIndexer
    from scripts import dev
    import app.config as cfg

    checkout = tmp_path / "checkout_nested"
    src_dir = checkout / "src"
    (src_dir / "nested/empty").mkdir(parents=True)
    original = src_dir / "nested/original.md"
    original.write_text("Actual course note.\n", encoding="utf-8")
    db_path = checkout / "data/knowledge_base.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    report = KnowledgeIndexer(db_path, src_dir).index_all(ocr=False, embed_model=None)
    assert report["indexed_files"] == 1
    original.unlink()
    db_sha_before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    # CLI end-to-end check on empty nested src + retained real-schema indexed DB
    proc = _run_dev_subprocess(checkout, ["check", "--profile", "local"])
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Library: MISSING" in proc.stdout
    assert "Mode: Full" not in proc.stdout
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == db_sha_before

    # Mocked ready-model check_environment: empty nested -> UNAVAILABLE, then with file -> Full
    monkeypatch.setattr(cfg, "SRC_DIR", src_dir)
    monkeypatch.setattr(cfg, "DATA_DIR", checkout / "data")
    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    _mock_ready_models(monkeypatch)

    dev.check_environment()
    out_empty = capsys.readouterr().out
    assert "Library: MISSING" in out_empty
    assert "Library: UNAVAILABLE" in out_empty
    assert "Mode: Full" not in out_empty
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == db_sha_before

    original.write_text("Actual course note.\n", encoding="utf-8")
    orig_sha_before = hashlib.sha256(original.read_bytes()).hexdigest()

    dev.check_environment()
    out_ready = capsys.readouterr().out
    assert "Library: OK (1 indexed documents)" in out_ready
    assert "Mode: Full" in out_ready
    assert hashlib.sha256(original.read_bytes()).hexdigest() == orig_sha_before
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == db_sha_before


def test_legacy_user_library_untouched_by_local_check(tmp_path):
    """Legacy data/user-library files remain unrouted and untouched without auto-move or deletion."""
    checkout = tmp_path / "checkout_legacy"
    legacy_dir = checkout / "data/user-library/src"
    legacy_dir.mkdir(parents=True)
    legacy_note = legacy_dir / "old_notes.md"
    legacy_note.write_text("Legacy user note backup candidate\n", encoding="utf-8")
    legacy_db = checkout / "data/user-library/knowledge_base.db"
    with sqlite3.connect(legacy_db) as conn:
        conn.execute("CREATE TABLE t(x INT)")
        conn.execute("INSERT INTO t VALUES (42)")

    proc = _run_dev_subprocess(checkout, ["check", "--profile", "local"])
    assert proc.returncode == 0, proc.stderr + proc.stdout
    env_out = _extract_env_json(proc.stdout)
    assert env_out["AGENT_SRC_DIR"] == str(checkout / "src")
    assert env_out["AGENT_DATA_DIR"] == str(checkout / "data")
    assert legacy_note.read_text(encoding="utf-8") == "Legacy user note backup candidate\n"
    assert legacy_db.is_file()
    assert not (checkout / "src/old_notes.md").exists()
