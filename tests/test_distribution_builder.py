"""Test build_distribution.py with an isolated synthetic Git repository and toy pack."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from app.indexer import KnowledgeIndexer, pack_vector
from app.knowledge_pack import export_pack, NATIVE_PROTOCOL, OLLAMA_PROTOCOL
from scripts.build_distribution import build_distribution, validate_pack_archive
import scripts.build_distribution as builder
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


def make_toy_pack(tmp_path, identity=None, backend=None):
    if identity is None:
        identity = DEFAULT_IDENTITY
    if backend is None:
        backend = DEFAULT_BACKEND
    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    content = "Encapsulation keeps state behind a public interface.\n"
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
        "redistribution_reviewed": True,
    }]))
    archive = tmp_path / f"{backend}.zip"
    exported = export_pack(
        database, sources, allowlist, archive,
        expected_identity=identity,
        page_reviews=rev_manifest,
    )
    return archive, exported


def init_synthetic_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Committer"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "committer@example.com"], cwd=repo_dir, check=True, capture_output=True)

    # Tracked files
    (repo_dir / "app").mkdir()
    (repo_dir / "app/main.py").write_text("# public code\n")
    (repo_dir / "scripts").mkdir()
    (repo_dir / "scripts/dev.py").write_text("# launcher\n")
    (repo_dir / "README.md").write_text("# Test App\n")

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True)

    # Add untracked private sentinels
    (repo_dir / "private_sentinel.key").write_text("SUPER_SECRET_KEY_NEVER_PACK")
    (repo_dir / ".env").write_text("PRIVATE_API_TOKEN=do_not_publish\n")
    (repo_dir / "data").mkdir()
    (repo_dir / "data/private.db").write_text("PRIVATE DATABASE CONTENT")
    (repo_dir / "src").mkdir()
    (repo_dir / "src/confidential.txt").write_text("CONFIDENTIAL DOCUMENT")

    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True)
    return proc.stdout.strip()


def test_distribution_builder_excludes_untracked_sentinels(tmp_path):
    repo = tmp_path / "synthetic_repo"
    commit_sha = init_synthetic_repo(repo)
    archive, pack_res = make_toy_pack(tmp_path)

    out_dist = tmp_path / "dist"
    summary = build_distribution(
        out_dist,
        packs=[{"backend": DEFAULT_BACKEND, "path": archive, "sha256": pack_res["sha256"]}],
        ref="HEAD",
        git_repo=repo,
    )

    assert out_dist.is_dir()
    assert summary["source_revision"] == commit_sha

    # Verify untracked sentinels are completely excluded
    assert not (out_dist / "private_sentinel.key").exists()
    assert not (out_dist / ".env").exists()
    assert not (out_dist / "data").exists()
    assert not (out_dist / "src").exists()

    # Verify tracked files exist
    assert (out_dist / "app/main.py").read_text() == "# public code\n"
    assert (out_dist / "README.md").read_text() == "# Test App\n"

    # Verify bundled descriptor and metadata
    descriptor_file = out_dist / "resources/knowledge-packs.json"
    assert descriptor_file.is_file()
    desc = json.loads(descriptor_file.read_text())
    assert desc["format"] == 1
    assert DEFAULT_BACKEND in desc["packs"]
    assert desc["packs"][DEFAULT_BACKEND]["sha256"] == pack_res["sha256"]
    assert desc["packs"][DEFAULT_BACKEND]["path"] == f"resources/{DEFAULT_BACKEND}.zip"
    assert (out_dist / f"resources/{DEFAULT_BACKEND}.zip").is_file()

    meta_file = out_dist / "bundle-metadata.json"
    assert meta_file.is_file()
    meta = json.loads(meta_file.read_text())
    assert meta["source_revision"] == commit_sha
    assert meta["packs"][DEFAULT_BACKEND]["documents"] == 1
    assert meta["packs"][DEFAULT_BACKEND]["chunks"] > 0
    assert meta["prerequisites"]["python"] == "3.12"
    # Ensure no local absolute paths in metadata
    assert str(tmp_path) not in json.dumps(meta)


def test_distribution_builder_rejects_tampered_pack(tmp_path):
    repo = tmp_path / "synthetic_repo"
    init_synthetic_repo(repo)
    archive, pack_res = make_toy_pack(tmp_path)

    # Tamper with archive file
    with archive.open("ab") as f:
        f.write(b"CORRUPT")

    out_dist = tmp_path / "dist_tamper"
    with pytest.raises(ValueError, match="Pack SHA256 digest mismatch"):
        build_distribution(
            out_dist,
            packs=[{"backend": DEFAULT_BACKEND, "path": archive, "sha256": pack_res["sha256"]}],
            ref="HEAD",
            git_repo=repo,
        )
    # Output must not be left behind
    assert not out_dist.exists()


def test_distribution_builder_refuses_to_overwrite_existing_destination(tmp_path):
    repo = tmp_path / "synthetic_repo"
    init_synthetic_repo(repo)
    archive, pack_res = make_toy_pack(tmp_path)

    existing_dest = tmp_path / "existing_dir"
    existing_dest.mkdir()

    with pytest.raises(ValueError, match="Output directory exists"):
        build_distribution(
            existing_dest,
            packs=[{"backend": DEFAULT_BACKEND, "path": archive, "sha256": pack_res["sha256"]}],
            ref="HEAD",
            git_repo=repo,
        )


def test_distribution_builder_excludes_committed_private_assets(tmp_path):
    repo = tmp_path / "synthetic_repo"
    init_synthetic_repo(repo)

    # Accidentally commit private sensitive files
    sensitive = [
        "training/dataset.jsonl",
        "history/chat.txt",
        "models/adapter.safetensors",
        "docs/project/private-checkpoint.md",
        "secrets/credentials.pem",
    ]
    for rel in sensitive:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("TRACKED_SENSITIVE_DATA")

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Accidental sensitive commit"], cwd=repo, check=True, capture_output=True)

    archive, pack_res = make_toy_pack(tmp_path)
    out_dist = tmp_path / "dist_clean"
    build_distribution(
        out_dist,
        packs=[{"backend": DEFAULT_BACKEND, "path": archive, "sha256": pack_res["sha256"]}],
        ref="HEAD",
        git_repo=repo,
    )

    prohibited = [
        ".env", "data/private.db", "src/confidential.txt", "private_sentinel.key",
        "training/dataset.jsonl", "history/chat.txt", "models/adapter.safetensors",
        "docs/project/private-checkpoint.md", "secrets/credentials.pem",
    ]
    for p in prohibited:
        assert not (out_dist / p).exists(), f"Committed private file must not enter bundle: {p}"
    assert (out_dist / "app/main.py").is_file()


def test_pack_changed_after_validation_fails_publication(tmp_path, monkeypatch):
    repo = tmp_path / "synthetic_repo"
    init_synthetic_repo(repo)
    archive, pack_res = make_toy_pack(tmp_path)

    validate = builder.validate_pack_archive

    def validate_then_modify(*args, **kwargs):
        res = validate(*args, **kwargs)
        with archive.open("ab") as stream:
            stream.write(b"TAMPER_AFTER_INITIAL_VALIDATION")
        return res

    monkeypatch.setattr(builder, "validate_pack_archive", validate_then_modify)

    out_dist = tmp_path / "dist_tamper_after_val"
    with pytest.raises(ValueError, match="Knowledge pack changed after validation"):
        build_distribution(
            out_dist,
            packs=[{"backend": DEFAULT_BACKEND, "path": archive, "sha256": pack_res["sha256"]}],
            ref="HEAD",
            git_repo=repo,
        )
    assert not out_dist.exists(), "Output directory must not be published when pack changes after validation"


def test_bundle_metadata_prerequisites_specify_python_312(tmp_path):
    repo = tmp_path / "synthetic_repo"
    init_synthetic_repo(repo)
    archive, pack_res = make_toy_pack(tmp_path)

    out_dist = tmp_path / "dist_meta"
    build_distribution(
        out_dist,
        packs=[{"backend": DEFAULT_BACKEND, "path": archive, "sha256": pack_res["sha256"]}],
        ref="HEAD",
        git_repo=repo,
    )

    meta_file = out_dist / "bundle-metadata.json"
    assert meta_file.is_file()
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["prerequisites"]["python"] == "3.12"
    assert "native" in meta["prerequisites"]["models"].lower() or "ollama" in meta["prerequisites"]["models"].lower()


def test_export_preserves_public_runtime_entrypoint_and_launchers(tmp_path):
    repo = tmp_path / "synthetic_repo"
    init_synthetic_repo(repo)
    (repo / "run.py").write_text("#!/usr/bin/env python3\nprint('ENTRYPOINT')\n")
    (repo / "scripts/service.sh").write_text("python run.py serve\n")
    (repo / "start_app.command").write_text("scripts/service.sh start\n")
    subprocess.run(["git", "add", "run.py", "scripts/service.sh", "start_app.command"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add service entrypoint and launchers"], cwd=repo, check=True, capture_output=True)

    archive, pack_res = make_toy_pack(tmp_path)
    out_dist = tmp_path / "dist_entry"
    build_distribution(
        out_dist,
        packs=[{"backend": DEFAULT_BACKEND, "path": archive, "sha256": pack_res["sha256"]}],
        ref="HEAD",
        git_repo=repo,
    )

    assert (out_dist / "run.py").is_file(), "Public entrypoint run.py must be preserved in export"
    assert (out_dist / "scripts/service.sh").is_file()
    assert (out_dist / "start_app.command").is_file()


def test_distribution_builder_rejects_unreviewed_pack(tmp_path):
    repo = tmp_path / "synthetic_repo"
    init_synthetic_repo(repo)
    archive, pack_res = make_toy_pack(tmp_path)

    # Tamper with pack manifest by stripping page_reviews from documents
    import zipfile
    with zipfile.ZipFile(archive, "r") as zin:
        items = {item.filename: zin.read(item.filename) for item in zin.infolist()}
    manifest = json.loads(items["manifest.json"])
    for d in manifest.get("documents", []):
        d.pop("page_reviews", None)
    items["manifest.json"] = json.dumps(manifest).encode("utf-8")

    tampered_archive = tmp_path / "tampered_unreviewed.zip"
    with zipfile.ZipFile(tampered_archive, "w") as zout:
        for name, data in items.items():
            zout.writestr(name, data)

    tampered_sha = hashlib.sha256(tampered_archive.read_bytes()).hexdigest()

    out_dist = tmp_path / "dist_unreviewed"
    with pytest.raises(ValueError, match="missing complete physical page review coverage"):
        build_distribution(
            out_dist,
            packs=[{"backend": DEFAULT_BACKEND, "path": tampered_archive, "sha256": tampered_sha}],
            ref="HEAD",
            git_repo=repo,
        )
    assert not out_dist.exists()
