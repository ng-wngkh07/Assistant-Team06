"""Build a distributable application bundle from committed Git sources and reviewed packs.

Exports the committed application tree via git archive (read-only), validates
supplied knowledge packs via isolated installation with NO model calls, generates
resources/knowledge-packs.json descriptor, and writes minimal bundle metadata.
Never includes working tree files, .env, untracked sentinels, or private corpora.
"""
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.file_lock import flock, LOCK_EX, LOCK_UN
from app.knowledge_pack import (
    install_pack,
    sha256_file,
    validate_and_get_contract,
    NATIVE_PROTOCOL,
    OLLAMA_PROTOCOL,
)


def resolve_commit_sha(git_repo: Path, ref: str = "HEAD") -> str:
    cmd = ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"]
    proc = subprocess.run(cmd, cwd=git_repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ValueError(f"Invalid Git ref: {ref}")
    commit_sha = proc.stdout.strip()
    if len(commit_sha) != 40 or any(c not in "0123456789abcdef" for c in commit_sha):
        raise ValueError(f"Resolved commit is not a valid 40-character SHA: {commit_sha}")
    return commit_sha


def validate_pack_archive(archive_path: Path, backend: str, expected_sha256: str) -> dict:
    archive = Path(archive_path).resolve()
    if not archive.is_file():
        raise ValueError(f"Knowledge pack archive not found: {archive}")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ValueError(f"Invalid expected SHA256 digest: {expected_sha256}")

    actual_digest = sha256_file(archive)
    if actual_digest != expected_sha256:
        raise ValueError(f"Pack SHA256 digest mismatch: got {actual_digest}, expected {expected_sha256}")

    with zipfile.ZipFile(archive) as z:
        if "manifest.json" not in z.namelist():
            raise ValueError("Knowledge pack missing manifest.json")
        try:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Malformed manifest.json in pack: {exc}")

    pack_id = manifest.get("model_identity")
    if not pack_id:
        raise ValueError("Pack manifest missing model_identity")

    contract = validate_and_get_contract(pack_id)
    if backend == "native":
        if contract["embedding_protocol"] != NATIVE_PROTOCOL:
            raise ValueError(f"Pack protocol {contract['embedding_protocol']} does not match native protocol {NATIVE_PROTOCOL}")
    elif backend == "ollama":
        if contract["embedding_protocol"] != OLLAMA_PROTOCOL:
            raise ValueError(f"Pack protocol {contract['embedding_protocol']} does not match Ollama protocol {OLLAMA_PROTOCOL}")
    else:
        raise ValueError(f"Unsupported backend '{backend}'")

    docs = manifest.get("documents")
    if not isinstance(docs, list) or not docs:
        raise ValueError("Knowledge pack missing document list")
    for doc in docs:
        page_reviews = doc.get("page_reviews")
        if not isinstance(page_reviews, list) or len(page_reviews) != doc.get("pages", 0) or not page_reviews:
            raise ValueError(f"Knowledge pack missing complete physical page review coverage for {doc.get('filename')}")
        for p_idx, p_rev in enumerate(page_reviews, 1):
            if (not isinstance(p_rev, dict)
                    or p_rev.get("page") != p_idx
                    or p_rev.get("completeness") != "complete"
                    or not isinstance(p_rev.get("text_sha256"), str)
                    or len(p_rev["text_sha256"]) != 64):
                raise ValueError(f"Knowledge pack contains incomplete or invalid page review for {doc.get('filename')}:{p_idx}")


    # Validate full extraction and indexing integrity in isolated temporary storage without model calls
    with tempfile.TemporaryDirectory(prefix=".dist-val-pack-") as tmp_val:
        val_dest = Path(tmp_val) / "test-install"
        report = install_pack(archive, val_dest, expected_sha256=expected_sha256, expected_identity=pack_id)

    return {
        "backend": backend,
        "archive_path": archive,
        "sha256": expected_sha256,
        "documents": report["documents"],
        "chunks": report["chunks"],
    }


def is_bundle_path_allowed(rel_path: str, is_dir: bool = False) -> bool:
    norm_path = PurePosixPath(rel_path)
    parts = norm_path.parts
    if not parts:
        return False
    if norm_path.is_absolute() or any(p in ("..", ".") for p in parts):
        return False

    # Check excluded cache or tool directories anywhere in parts
    if any(p in {
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".git", ".github", ".agents", ".gemini", ".codex", ".antigravity",
        ".idea", ".vscode", "node_modules",
    } for p in parts):
        return False

    top_dir = parts[0]
    # Explicitly prohibited directories
    if top_dir in {"data", "src", "training", "models", "history", "histories", "secrets", "scratch", "output"}:
        return False

    posix_str = norm_path.as_posix()
    if posix_str == "docs/project" or posix_str.startswith("docs/project/"):
        return False

    if is_dir:
        return top_dir in {"app", "scripts", "static", "tests", "docs"}

    filename = parts[-1]
    if filename in {".DS_Store", "Thumbs.db"}:
        return False
    if filename == ".env" or (filename.startswith(".env.") and filename != ".env.example"):
        return False

    ext = norm_path.suffix.lower()
    if ext in {
        ".key", ".pem", ".crt", ".pfx", ".p12",
        ".safetensors", ".gguf", ".bin", ".pt", ".pth", ".onnx",
        ".db", ".sqlite", ".sqlite3",
        ".jsonl", ".parquet", ".arrow", ".feather",
        ".pyc", ".pyo", ".swp", ".swo",
    }:
        return False

    if len(parts) == 1:
        allowed_exact_root = {
            "README.md", "README_MAC.md", "README_WINDOWS.md", "CONTRIBUTING.md",
            "LICENSE", "CHANGELOG.md", "SECURITY.md", "AGENT.md", "DEFINITION_OF_DONE.md",
            "pyproject.toml", "setup.py", "setup.cfg", ".env.example", ".gitignore",
            "Start Study Agent.bat", "Start Study Agent.command", "run.py",
        }
        if filename in allowed_exact_root:
            return True
        if filename.startswith("requirements") and (filename.endswith(".txt") or filename.endswith(".json")):
            return True
        if filename.startswith("pytest") and filename.endswith(".ini"):
            return True
        if filename.startswith("constraints") and filename.endswith(".txt"):
            return True
        if filename.startswith("start") and (filename.endswith(".command") or filename.endswith(".bat") or filename.endswith(".ps1")):
            return True
        if filename.startswith("setup") and filename.endswith(".ps1"):
            return True
        if filename.startswith("test") and filename.endswith(".ps1"):
            return True
        return False

    return top_dir in {"app", "scripts", "static", "tests", "docs"}


def build_distribution(
    output: Path,
    *,
    packs: list[dict],
    ref: str = "HEAD",
    git_repo: Path = None,
) -> dict:
    """Build a distribution from a committed git ref and validated packs."""
    repo = Path(git_repo or ROOT).resolve()
    output_path = Path(output).resolve()

    if output_path.exists():
        raise ValueError(f"Output directory exists; user data will not be overwritten: {output_path}")

    commit_sha = resolve_commit_sha(repo, ref)

    if not packs:
        raise ValueError("At least one knowledge pack is required for the distribution")

    validated_packs = []
    seen_backends = set()
    for pack_item in packs:
        backend = pack_item.get("backend")
        if backend in seen_backends:
            raise ValueError(f"Duplicate backend configuration: {backend}")
        seen_backends.add(backend)
        archive_path = pack_item.get("path")
        expected_sha = pack_item.get("sha256")
        validated = validate_pack_archive(archive_path, backend, expected_sha)
        validated_packs.append(validated)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dist-build-", dir=output_path.parent) as tmp_dir:
        staging_dir = Path(tmp_dir) / "bundle"
        staging_dir.mkdir()

        # Export committed source tree via git archive (read-only)
        archive_proc = subprocess.run(
            ["git", "archive", "--format=tar", commit_sha],
            cwd=repo,
            capture_output=True,
        )
        if archive_proc.returncode != 0:
            raise ValueError(f"git archive failed: {archive_proc.stderr.decode('utf-8', 'replace')}")

        with tarfile.open(fileobj=io.BytesIO(archive_proc.stdout)) as tar:
            staging_abs = staging_dir.resolve()
            for member in tar.getmembers():
                if member.issym() or member.islnk() or member.ischr() or member.isblk() or member.isfifo():
                    raise ValueError(f"Prohibited special archive entry: {member.name}")

                target = (staging_dir / member.name).resolve()
                if not target.is_relative_to(staging_abs):
                    raise ValueError(f"Tar entry escapes destination: {member.name}")

                if member.isdir():
                    if not is_bundle_path_allowed(member.name, is_dir=True):
                        continue
                elif member.isfile():
                    if not is_bundle_path_allowed(member.name, is_dir=False):
                        continue
                else:
                    continue

                if hasattr(tarfile, "data_filter"):
                    tar.extract(member, staging_dir, filter="data")
                else:
                    tar.extract(member, staging_dir)

        # Place knowledge packs into resources/
        resources_dir = staging_dir / "resources"
        resources_dir.mkdir(parents=True, exist_ok=True)

        descriptor_packs = {}
        metadata_packs = {}

        for val_pack in validated_packs:
            backend = val_pack["backend"]
            safe_name = f"{backend}.zip"
            dest_archive = resources_dir / safe_name
            shutil.copyfile(val_pack["archive_path"], dest_archive)

            # Revalidate SHA256 of the copied pack
            copied_digest = sha256_file(dest_archive)
            if copied_digest != val_pack["sha256"]:
                raise ValueError(
                    f"Knowledge pack changed after validation for backend '{backend}': "
                    f"got {copied_digest}, expected {val_pack['sha256']}"
                )

            descriptor_packs[backend] = {
                "path": f"resources/{safe_name}",
                "sha256": val_pack["sha256"],
            }
            metadata_packs[backend] = {
                "backend": backend,
                "sha256": val_pack["sha256"],
                "documents": val_pack["documents"],
                "chunks": val_pack["chunks"],
            }

        descriptor_content = {
            "format": 1,
            "packs": descriptor_packs,
        }
        (resources_dir / "knowledge-packs.json").write_text(
            json.dumps(descriptor_content, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        bundle_metadata = {
            "format": 1,
            "source_revision": commit_sha,
            "packs": metadata_packs,
            "prerequisites": {
                "python": "3.12",
                "web": "Supported Python 3.12 web prerequisites",
                "models": "Separate native runtime/model installation required",
            },
        }
        (staging_dir / "bundle-metadata.json").write_text(
            json.dumps(bundle_metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        lock_path = output_path.parent / f".{output_path.name}.build.lock"
        with open(lock_path, "a+") as lock_file:
            flock(lock_file.fileno(), LOCK_EX)
            try:
                if output_path.exists():
                    raise ValueError(f"Output directory exists; user data will not be overwritten: {output_path}")
                os.rename(staging_dir, output_path)
            finally:
                try:
                    flock(lock_file.fileno(), LOCK_UN)
                except Exception:
                    pass
                try:
                    lock_path.unlink()
                except Exception:
                    pass

    return {
        "output": str(output_path),
        "source_revision": commit_sha,
        "packs": metadata_packs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory for the built bundle")
    parser.add_argument("--ref", default="HEAD", help="Git ref to export (default: HEAD)")
    parser.add_argument("--git-repo", type=Path, default=ROOT, help="Path to git repository")
    parser.add_argument("--native-pack", type=Path, default=None, help="Path to native knowledge pack zip")
    parser.add_argument("--native-sha256", default=None, help="Expected SHA256 of native knowledge pack")
    parser.add_argument("--ollama-pack", type=Path, default=None, help="Path to Ollama knowledge pack zip")
    parser.add_argument("--ollama-sha256", default=None, help="Expected SHA256 of Ollama knowledge pack")
    parser.add_argument("--pack", action="append", default=[], help="Pack specification as backend:path:sha256")

    args = parser.parse_args()

    pack_specs = []
    if args.native_pack and args.native_sha256:
        pack_specs.append({"backend": "native", "path": args.native_pack, "sha256": args.native_sha256})
    elif bool(args.native_pack) != bool(args.native_sha256):
        parser.error("Specify --native-pack and --native-sha256 together.")

    if args.ollama_pack and args.ollama_sha256:
        pack_specs.append({"backend": "ollama", "path": args.ollama_pack, "sha256": args.ollama_sha256})
    elif bool(args.ollama_pack) != bool(args.ollama_sha256):
        parser.error("Specify --ollama-pack and --ollama-sha256 together.")

    for raw_pack in args.pack:
        parts = raw_pack.split(":")
        if len(parts) != 3:
            parser.error("Pack specification must be in format backend:path:sha256")
        pack_specs.append({"backend": parts[0], "path": Path(parts[1]), "sha256": parts[2]})

    if not pack_specs:
        parser.error("At least one knowledge pack is required (--native-pack/--native-sha256 or --ollama-pack/--ollama-sha256)")

    try:
        result = build_distribution(
            args.output,
            packs=pack_specs,
            ref=args.ref,
            git_repo=args.git_repo,
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Distribution build failed: {exc}\n")

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
