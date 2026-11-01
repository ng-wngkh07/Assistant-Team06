"""Portable team launcher. Demo data stays separate from the coordinator corpus."""
import argparse
import contextlib
import json
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
ENV_KEYS = {
    "AGENT_DATA_DIR", "AGENT_SRC_DIR", "OLLAMA_BASE_URL", "DEFAULT_CHAT_MODEL",
    "DEFAULT_EMBED_MODEL", "OLLAMA_AUX_CHAT_MODEL", "MLX_CONTEXT_TOKENS", "HOST", "PORT",
    "HISTORY_DB_PATH", "TIMETABLE_DB_PATH", "TIMETABLE_IMAGES_DIR",
    "ANSWER_QUICK_MAX_TOKENS", "ANSWER_STEPS_MAX_TOKENS", "ANSWER_COMPARE_MAX_TOKENS",
    "MLX_MODEL_DIR", "MLX_9B_MODEL_DIR", "MLX_BGE_MODEL_DIR",
    "BGE_MODEL_DIR", "TRAINED_MODEL_NAME", "TRAINING_MODEL_DIR",
    "KNOWLEDGE_PACK_PATH", "KNOWLEDGE_PACK_SHA256",
    "LLM_PROVIDER", "CHAT_BACKEND", "EMBED_BACKEND",
}


_DEFER_INSTALL = False
_CONFIGURED_BUNDLED_PACK = None


def configure(profile: str = "local") -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or key not in ENV_KEYS or not value:
                raise ValueError(f"Invalid .env setting: {key}")
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)
    if profile == "demo":
        os.environ["AGENT_SRC_DIR"] = str(ROOT / "tests/fixtures/documents")
        os.environ["AGENT_DATA_DIR"] = str(ROOT / "data/team-demo")
        os.environ["HISTORY_DB_PATH"] = str(ROOT / "data/team-demo/history.db")
        os.environ["TIMETABLE_DB_PATH"] = str(ROOT / "data/team-demo/timetable.db")
        os.environ["TIMETABLE_IMAGES_DIR"] = str(ROOT / "data/team-demo/timetable_images")
        platform_model = "qwen3.5-9b-4bit" if sys.platform == "darwin" else "qwen3.5:9b"
        os.environ.setdefault("DEFAULT_CHAT_MODEL", platform_model)
    elif os.environ.get('KNOWLEDGE_PACK_PATH') or os.environ.get('KNOWLEDGE_PACK_SHA256'):
        configure_knowledge_pack(install=not _DEFER_INSTALL)
    elif (ROOT / "resources/knowledge-packs.json").exists():
        configure_bundled_library(ROOT / "resources/knowledge-packs.json", install=not _DEFER_INSTALL)


def configure_knowledge_pack(install: bool = True) -> None:
    """Select a prebuilt publisher library before importing app configuration."""
    pack_name, digest = os.environ.get('KNOWLEDGE_PACK_PATH'), os.environ.get('KNOWLEDGE_PACK_SHA256')
    if not pack_name or not digest or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Set KNOWLEDGE_PACK_PATH and the trusted publisher KNOWLEDGE_PACK_SHA256 together.')
    archive = Path(pack_name).expanduser()
    if not archive.is_absolute(): archive = ROOT / archive
    destination = ROOT / 'data/knowledge-packs' / digest

    old_data = Path(os.environ.get('AGENT_DATA_DIR', str(ROOT / 'data'))).expanduser()
    if not old_data.is_absolute(): old_data = ROOT / old_data
    # Keep shared model assets outside each installed knowledge library.
    os.environ.setdefault('MLX_MODEL_DIR', os.environ.get('MLX_9B_MODEL_DIR', str(old_data / 'models/qwen3.5-9b-4bit')))
    os.environ.setdefault('MLX_BGE_MODEL_DIR', os.environ.get('BGE_MODEL_DIR', str(old_data / 'models/bge-m3-mlx-fp16')))
    os.environ['AGENT_DATA_DIR'] = str(destination)
    os.environ['AGENT_SRC_DIR'] = str(destination / 'sources')
    os.environ['HISTORY_DB_PATH'] = str(destination / 'history.db')
    os.environ['TIMETABLE_DB_PATH'] = str(destination / 'timetable.db')
    os.environ['TIMETABLE_IMAGES_DIR'] = str(destination / 'timetable_images')

    if 'app.config' in sys.modules:
        import importlib
        importlib.reload(sys.modules['app.config'])

    if install:
        install_configured_knowledge_pack(archive=archive, destination=destination, digest=digest)


def install_configured_knowledge_pack(archive: Path = None, destination: Path = None, digest: str = None) -> None:
    if archive is None or destination is None or digest is None:
        pack_name, digest = os.environ.get('KNOWLEDGE_PACK_PATH'), os.environ.get('KNOWLEDGE_PACK_SHA256')
        if not pack_name or not digest:
            return
        archive = Path(pack_name).expanduser()
        if not archive.is_absolute(): archive = ROOT / archive
        destination = ROOT / 'data/knowledge-packs' / digest

    receipt_path = destination / 'knowledge-pack.json'
    was_installed = receipt_path.is_file()

    expected_identity = None
    if was_installed:
        try:
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            receipt_id = receipt.get('model_identity')
            if receipt_id and receipt_id.startswith('ollama-sha256:'):
                from app.ollama_client import OllamaClient
                client = OllamaClient()
                expected_identity = client.embedding_identity(os.environ.get('DEFAULT_EMBED_MODEL', 'bge-m3'))
                if not expected_identity:
                    raise RuntimeError(f'Could not verify Ollama embedding identity for model {os.environ.get("DEFAULT_EMBED_MODEL", "bge-m3")}. Ensure Ollama is running.')
            elif receipt_id and receipt_id.startswith('bge-m3-mlx-fp16@'):
                if sys.platform != 'darwin':
                    raise ValueError('This native BGE knowledge pack requires macOS. Other backends need a separately verified pack.')
                from app.native_embedding import NativeEmbeddingClient
                expected_identity = NativeEmbeddingClient().get_identity()
        except (ValueError, RuntimeError):
            raise
        except Exception:
            pass

    if not expected_identity and archive.is_file():
        import zipfile
        try:
            with zipfile.ZipFile(archive) as z:
                manifest = json.loads(z.read('manifest.json'))
                pack_id = manifest.get('model_identity')
                if pack_id and pack_id.startswith('ollama-sha256:'):
                    from app.ollama_client import OllamaClient
                    client = OllamaClient()
                    expected_identity = client.embedding_identity(os.environ.get('DEFAULT_EMBED_MODEL', 'bge-m3'))
                    if not expected_identity:
                        raise RuntimeError(f'Could not verify Ollama embedding identity for model {os.environ.get("DEFAULT_EMBED_MODEL", "bge-m3")}. Ensure Ollama is running.')
                elif pack_id and pack_id.startswith('bge-m3-mlx-fp16@'):
                    if sys.platform != 'darwin':
                        raise ValueError('This native BGE knowledge pack requires macOS. Other backends need a separately verified pack.')
                    from app.native_embedding import NativeEmbeddingClient
                    expected_identity = NativeEmbeddingClient().get_identity()
        except (ValueError, RuntimeError):
            raise
        except Exception:
            pass

    if not expected_identity:
        if sys.platform != 'darwin' or os.environ.get('EMBED_BACKEND') == 'ollama':
            from app.ollama_client import OllamaClient
            client = OllamaClient()
            expected_identity = client.embedding_identity(os.environ.get('DEFAULT_EMBED_MODEL', 'bge-m3'))
            if not expected_identity:
                raise RuntimeError(f'Could not verify Ollama embedding identity for model {os.environ.get("DEFAULT_EMBED_MODEL", "bge-m3")}. Ensure Ollama is running.')
        else:
            from app.native_embedding import NativeEmbeddingClient
            expected_identity = NativeEmbeddingClient().get_identity()

    if sys.platform != 'darwin' and expected_identity and expected_identity.startswith('bge-m3-mlx-fp16@'):
        raise ValueError('This native BGE knowledge pack requires macOS. Other backends need a separately verified pack.')

    identity = expected_identity
    from app.knowledge_pack import install_pack
    if was_installed:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if receipt.get('sha256') != digest or receipt.get('model_identity') != identity:
            raise ValueError('Installed knowledge pack receipt or embedding model differs; choose a new verified installation.')
    else:
        # Imports, including an earlier failed launch, can leave empty runtime
        # directories. A receipt establishes installation; folder existence
        # alone does not. Retry only an entirely empty directory tree, never
        # delete files, symlinks or an incomplete user's database.
        if destination.exists():
            children = list(destination.rglob('*'))
            if destination.is_symlink() or not destination.is_dir() or any(
                    child.is_symlink() or not child.is_dir() for child in children):
                raise ValueError('Incomplete knowledge-pack directory contains files; data was preserved. Inspect it before retrying.')
            for child in sorted(children, key=lambda p: len(p.parts), reverse=True):
                child.rmdir()
            destination.rmdir()
        print(install_pack(archive, destination, expected_sha256=digest, expected_identity=identity))
        print('Prebuilt publisher knowledge installed. No document extraction, training or embedding was run.')


def validate_personal_library(user_library: Path) -> None:
    src_dir = user_library / "src"
    if not src_dir.is_dir():
        raise ValueError(f"Personal library at {user_library} is damaged: missing source directory 'src'. Existing personal data is preserved.")
    db_file = user_library / "knowledge_base.db"
    if not db_file.is_file():
        raise ValueError(f"Personal library at {user_library} is damaged: missing working database 'knowledge_base.db'. Existing personal data is preserved.")

    uri = f"{db_file.resolve().as_uri()}?mode=ro"
    try:
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as conn:
            cursor = conn.execute("PRAGMA quick_check(10)")
            row = cursor.fetchone()
            if not row or row[0] != "ok":
                raise ValueError(f"Personal database at {db_file} quick_check failed: {row[0] if row else 'empty'}")
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cursor.fetchall()}
            required_tables = {"documents", "chunks", "document_source_quality", "corpus_vector_provenance"}
            missing = required_tables - tables
            if missing:
                raise ValueError(f"Personal database at {db_file} missing required tables: {missing}")
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Personal database at {db_file} is corrupt or unreadable: {exc}")


def configure_bundled_library(descriptor_file: Path, install: bool = True) -> None:
    global _CONFIGURED_BUNDLED_PACK
    try:
        content = descriptor_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        raise ValueError(f"Malformed knowledge-packs descriptor: {exc}")

    if not isinstance(data, dict) or data.get("format") != 1:
        raise ValueError("Invalid knowledge-packs descriptor format: format must be 1")

    packs = data.get("packs")
    if not isinstance(packs, dict) or not packs:
        raise ValueError("Invalid knowledge-packs descriptor: 'packs' dictionary is required")

    embed_backend = os.environ.get("EMBED_BACKEND")
    if embed_backend == "ollama" or sys.platform != "darwin":
        selected_backend = "ollama"
    else:
        selected_backend = "native"

    if selected_backend not in packs:
        raise ValueError(f"Knowledge pack descriptor has no configuration for selected backend '{selected_backend}'")

    pack_config = packs[selected_backend]
    if not isinstance(pack_config, dict):
        raise ValueError(f"Invalid pack entry for backend '{selected_backend}'")

    rel_path = pack_config.get("path")
    digest = pack_config.get("sha256")

    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"Invalid pack sha256 digest: {digest}")

    if not isinstance(rel_path, str) or not rel_path or "\\" in rel_path or ":" in rel_path:
        raise ValueError(f"Invalid resource path: {rel_path}")

    posix_path = PurePosixPath(rel_path)
    if posix_path.is_absolute() or any(part == ".." for part in posix_path.parts):
        raise ValueError(f"Resource path escapes bundle: {rel_path}")

    archive = (ROOT / rel_path).resolve()
    root_resolved = ROOT.resolve()
    try:
        if not archive.is_relative_to(root_resolved):
            raise ValueError(f"Resource path escapes bundle: {rel_path}")
    except AttributeError:
        if not str(archive).startswith(str(root_resolved)):
            raise ValueError(f"Resource path escapes bundle: {rel_path}")

    user_library = ROOT / "data/user-library"
    seed_receipt = user_library / "seed-receipt.json"

    is_seeded = False
    if seed_receipt.is_file():
        try:
            receipt_data = json.loads(seed_receipt.read_text(encoding="utf-8"))
            if receipt_data.get("sha256") == digest:
                validate_personal_library(user_library)
                is_seeded = True
        except Exception:
            pass

    if not is_seeded and not archive.is_file():
        raise ValueError(f"Bundled knowledge pack archive missing at {rel_path} and user library is not seeded")

    user_sources = user_library / "src"
    user_db = user_library / "knowledge_base.db"
    user_history = user_library / "history.db"
    user_timetable = user_library / "timetable.db"
    user_timetable_images = user_library / "timetable_images"

    old_data = Path(os.environ.get('AGENT_DATA_DIR', str(ROOT / 'data'))).expanduser()
    if not old_data.is_absolute():
        old_data = ROOT / old_data

    os.environ.setdefault('MLX_MODEL_DIR', os.environ.get('MLX_9B_MODEL_DIR', str(old_data / 'models/qwen3.5-9b-4bit')))
    os.environ.setdefault('MLX_BGE_MODEL_DIR', os.environ.get('BGE_MODEL_DIR', str(old_data / 'models/bge-m3-mlx-fp16')))

    os.environ["AGENT_DATA_DIR"] = str(user_library)
    os.environ["AGENT_SRC_DIR"] = str(user_sources)
    os.environ["HISTORY_DB_PATH"] = str(user_history)
    os.environ["TIMETABLE_DB_PATH"] = str(user_timetable)
    os.environ["TIMETABLE_IMAGES_DIR"] = str(user_timetable_images)

    if 'app.config' in sys.modules:
        import importlib
        importlib.reload(sys.modules['app.config'])

    _CONFIGURED_BUNDLED_PACK = {
        "archive": archive,
        "digest": digest,
        "backend": selected_backend,
    }

    if install:
        install_configured_bundled_library(archive=archive, digest=digest, backend=selected_backend)


def install_configured_bundled_library(archive: Path = None, digest: str = None, backend: str = None) -> None:
    global _CONFIGURED_BUNDLED_PACK
    if archive is None or digest is None or backend is None:
        if not _CONFIGURED_BUNDLED_PACK:
            return
        archive = _CONFIGURED_BUNDLED_PACK["archive"]
        digest = _CONFIGURED_BUNDLED_PACK["digest"]
        backend = _CONFIGURED_BUNDLED_PACK["backend"]

    user_library = ROOT / "data/user-library"
    seed_receipt_path = user_library / "seed-receipt.json"
    publisher_cache = ROOT / "data/knowledge-packs" / digest

    expected_identity = None
    if backend == "ollama":
        from app.ollama_client import OllamaClient
        client = OllamaClient()
        expected_identity = client.embedding_identity(os.environ.get("DEFAULT_EMBED_MODEL", "bge-m3"))
        if not expected_identity:
            raise RuntimeError(
                f'Could not verify Ollama embedding identity for model {os.environ.get("DEFAULT_EMBED_MODEL", "bge-m3")}. Ensure Ollama is running.'
            )
    elif backend == "native":
        if sys.platform != "darwin":
            raise ValueError("This native BGE knowledge pack requires macOS. Other backends need a separately verified pack.")
        from app.native_embedding import NativeEmbeddingClient
        expected_identity = NativeEmbeddingClient().get_identity()
    else:
        raise ValueError(f"Unsupported backend '{backend}'")

    lock_path = ROOT / "data" / ".user-library.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    from app.file_lock import flock, LOCK_EX, LOCK_UN
    with open(lock_path, "a+") as lock_file:
        flock(lock_file.fileno(), LOCK_EX)
        try:
            if seed_receipt_path.is_file():
                try:
                    receipt = json.loads(seed_receipt_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise ValueError(f"Corrupt seed receipt at {seed_receipt_path}: {exc}")
                if receipt.get("sha256") != digest:
                    raise ValueError(
                        f"Personal library at {user_library} was seeded with pack {receipt.get('sha256')}, "
                        f"which does not match descriptor pack {digest}. Existing personal data is preserved. "
                        f"Move or backup {user_library} to upgrade."
                    )
                if receipt.get("model_identity") != expected_identity:
                    raise ValueError(
                        f"Personal library at {user_library} was seeded with model {receipt.get('model_identity')}, "
                        f"which does not match current model {expected_identity}. Existing personal data is preserved. "
                        f"Move or backup {user_library} to re-seed."
                    )
                validate_personal_library(user_library)
                return

            if user_library.exists():
                children = list(user_library.rglob('*'))
                if user_library.is_symlink() or not user_library.is_dir() or any(
                        child.is_symlink() or not child.is_dir() for child in children):
                    raise ValueError(
                        f"Existing unrecognized user directory at {user_library} contains files; "
                        f"data was preserved. Move or inspect it before seeding."
                    )
                for child in sorted(children, key=lambda p: len(p.parts), reverse=True):
                    child.rmdir()
                user_library.rmdir()

            if not archive.is_file():
                raise ValueError(f"Bundled knowledge pack archive missing: {archive}")

            if not publisher_cache.exists():
                from app.knowledge_pack import install_pack
                install_pack(archive, publisher_cache, expected_sha256=digest, expected_identity=expected_identity)

            import tempfile
            with tempfile.TemporaryDirectory(prefix=".user-library-stage-", dir=ROOT / "data") as temp_dir:
                staging_pack = Path(temp_dir) / "pack_ready"
                from app.knowledge_pack import install_pack
                install_pack(archive, staging_pack, expected_sha256=digest, expected_identity=expected_identity)

                staging = Path(temp_dir) / "user-library"
                staging.mkdir()
                os.replace(staging_pack / "sources", staging / "src")
                os.replace(staging_pack / "knowledge_base.db", staging / "knowledge_base.db")

                receipt_content = {
                    "sha256": digest,
                    "model_identity": expected_identity,
                    "backend": backend,
                    "seed_version": 1,
                }
                (staging / "seed-receipt.json").write_text(json.dumps(receipt_content, ensure_ascii=False), encoding="utf-8")

                if user_library.exists():
                    children = list(user_library.rglob('*'))
                    for child in sorted(children, key=lambda p: len(p.parts), reverse=True):
                        child.rmdir()
                    user_library.rmdir()
                os.replace(staging, user_library)
        finally:
            try:
                flock(lock_file.fileno(), LOCK_UN)
            except Exception:
                pass


def prepare_demo() -> dict:
    from app.config import DB_PATH, SRC_DIR
    from app.indexer import KnowledgeIndexer
    report = KnowledgeIndexer(DB_PATH, SRC_DIR).index_all(ocr=False, embed_model=None)
    if report.get("error") or report.get("error_files"):
        raise RuntimeError(f"Demo indexing failed: {report}")
    return report


def ensure_local_model_service(timeout_seconds: float = 15) -> None:
    """Start an installed local daemon after configuration, with a bounded wait."""
    from app.config import DEFAULT_CHAT_MODEL, OLLAMA_BASE_URL
    from app.model_resolver import is_mlx_model, reject_retired_model
    from app.ollama_client import OllamaClient
    reject_retired_model(DEFAULT_CHAT_MODEL)
    needs_ollama = not is_mlx_model(DEFAULT_CHAT_MODEL) or os.environ.get('EMBED_BACKEND') == 'ollama' or sys.platform != 'darwin'
    if not needs_ollama:
        pack_name = os.environ.get('KNOWLEDGE_PACK_PATH')
        if pack_name:
            archive = Path(pack_name).expanduser()
            if not archive.is_absolute(): archive = ROOT / archive
            if archive.is_file():
                import zipfile
                try:
                    with zipfile.ZipFile(archive) as z:
                        manifest = json.loads(z.read('manifest.json'))
                        if manifest.get('model_identity', '').startswith('ollama-sha256:'):
                            needs_ollama = True
                except Exception:
                    pass
    if not needs_ollama:
        return
    client = OllamaClient(OLLAMA_BASE_URL)
    if client.check_health():
        return
    executable = shutil.which('ollama')
    if not executable:
        raise RuntimeError('Local Ollama is unavailable. Install it and the documented Qwen3.5 9B model first.')
    env = os.environ.copy()
    env['OLLAMA_HOST'] = client.base_url
    process = subprocess.Popen([executable, 'serve'], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if client.check_health():
            return
        if process.poll() is not None:
            break
        time.sleep(min(0.25, max(0, deadline-time.monotonic())))
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    raise RuntimeError('Local model service did not become ready. Check its configured port and retry; no model was downloaded.')


def check_environment(allow_wait: bool = False) -> bool:
    from app.config import DATA_DIR, SRC_DIR, DEFAULT_CHAT_MODEL, OLLAMA_AUX_CHAT_MODEL
    from app.ollama_client import OllamaClient
    from app.trained_client import TrainedModelClient
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE VIRTUAL TABLE smoke USING fts5(text)")
    import pymupdf as fitz
    from PIL import Image  # noqa: F401
    from app.server import app
    print(f"Python: {sys.version.split()[0]} | platform: {sys.platform}")
    print(f"Sources: {SRC_DIR}\nLocal data: {DATA_DIR}")
    print(f"App routes: {len(app.routes)} | SQLite FTS5: OK | PDF: {fitz.VersionBind}")
    from app.model_resolver import is_mlx_model, reject_retired_model
    reject_retired_model(DEFAULT_CHAT_MODEL)
    if is_mlx_model(DEFAULT_CHAT_MODEL):
        if sys.platform != "darwin":
            print(f"QA model: NOT_RUN (MLX native {DEFAULT_CHAT_MODEL} is Apple-only; Windows/Linux uses Ollama)")
            print("Search mode: OK (model-free FTS keyword search ready; full QA/vision requires Ollama models).")
            return False

        qa_ready = TrainedModelClient.available(DEFAULT_CHAT_MODEL)
        if qa_ready:
            print(f"QA model: OK (MLX native: {DEFAULT_CHAT_MODEL})")
        else:
            print(f"QA model: MISSING (MLX native: {DEFAULT_CHAT_MODEL})")

        from app.native_vision import NativeVisionClient
        vision_ready = NativeVisionClient.available()
        if vision_ready:
            print("Vision model: OK (MLX native: qwen3.5-9b-4bit)")
        else:
            print("Vision model: MISSING (MLX native: qwen3.5-9b-4bit)")

        from app.native_embedding import NativeEmbeddingClient
        embedding_ready = NativeEmbeddingClient.available()
        if embedding_ready:
            print("Embedding model: OK (MLX native: bge-m3)")
        else:
            print("Embedding model: MISSING (MLX native: bge-m3)")

        from app.runtime_health import probe_mlx_runtime
        runtime = probe_mlx_runtime(force=True)
        print(f"Runtime: {runtime['code']} | {runtime['message']}")
        all_ready = qa_ready and vision_ready and embedding_ready and runtime.get("ready", False)
        if all_ready:
            print("Mode: Full local QA, embedding and vision ready.")
        else:
            print("Search mode: OK (model-free FTS keyword search ready; full QA/vision requires missing models).")
        return all_ready

    client = OllamaClient()
    if not client.check_health():
        if allow_wait:
            for _ in range(15):
                time.sleep(1)
                if client.check_health():
                    break
    if not client.check_health():
        print("Configured local Ollama is unavailable; FTS/demo/mocked tests still work.")
        print("Search mode: OK (model-free FTS keyword search ready; full QA requires Ollama service).")
        return False
    names = {model.get("name") for model in client.list_models()}
    print(f"Ollama models: {', '.join(sorted(names)) if names else 'none'}")
    base_names = {n.split(":")[0] for n in names}
    qa_ready = (DEFAULT_CHAT_MODEL in names or DEFAULT_CHAT_MODEL in base_names or f"{DEFAULT_CHAT_MODEL}:latest" in names)
    if qa_ready:
        print(f"QA model: OK ({DEFAULT_CHAT_MODEL})")
    else:
        print(f"QA model: MISSING ({DEFAULT_CHAT_MODEL})")

    vision_ready = OLLAMA_AUX_CHAT_MODEL in names
    if vision_ready:
        print(f"Vision model: OK ({OLLAMA_AUX_CHAT_MODEL})")
    else:
        print(f"Vision model: MISSING ({OLLAMA_AUX_CHAT_MODEL})")

    embedding_ready = any("bge-m3" in m for m in names)
    if embedding_ready:
        print("Embedding model: OK (bge-m3)")
    else:
        print("Embedding model: MISSING (bge-m3)")

    all_ready = qa_ready and vision_ready and embedding_ready
    if all_ready:
        print("Mode: Full QA, embedding and vision ready.")
    else:
        print("Search mode: OK (model-free FTS keyword search ready; full QA requires missing models).")
    return all_ready


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo", "check", "serve"])
    parser.add_argument("--profile", choices=["demo", "local"], default="demo")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--require-model", action="store_true")
    parser.add_argument("--start-model-service", action="store_true", help="Start installed local Ollama if needed; never downloads models.")
    parser.add_argument("--open-browser", action="store_true", help="Open the local app after its web server responds.")
    args = parser.parse_args()
    if args.command == "demo" and args.profile != "demo":
        parser.error("The demo command requires --profile demo.")
    global _DEFER_INSTALL
    _DEFER_INSTALL = True
    try:
        configure(args.profile)
    except (ValueError, OSError, KeyError) as exc:
        print(f'Configuration failed: {exc}', file=sys.stderr)
        return 1
    finally:
        _DEFER_INSTALL = False
    if args.start_model_service:
        try:
            ensure_local_model_service()
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Model startup failed: {exc}", file=sys.stderr)
            return 1
    if os.environ.get('KNOWLEDGE_PACK_PATH') or os.environ.get('KNOWLEDGE_PACK_SHA256'):
        try:
            install_configured_knowledge_pack()
        except (ValueError, RuntimeError, OSError) as exc:
            print(f'Knowledge pack installation failed: {exc}', file=sys.stderr)
            return 1
    elif _CONFIGURED_BUNDLED_PACK:
        try:
            install_configured_bundled_library()
        except (ValueError, RuntimeError, OSError) as exc:
            print(f'Bundled library installation failed: {exc}', file=sys.stderr)
            return 1
    if args.command in {"demo", "serve"} and args.profile == "demo":
        print(prepare_demo())
        print("Synthetic demo only; this index is not a training approval.")
    if args.command == "check":
        ready = check_environment()
        return 1 if args.require_model and not ready else 0
    if args.command == "serve":
        import uvicorn
        from app.config import SERVER_HOST, SERVER_PORT
        if args.require_model and not check_environment(allow_wait=True):
            return 1
        wildcard_hosts = {".".join(["0"] * 4), "::"}
        local_host = "127.0.0.1" if SERVER_HOST in wildcard_hosts else SERVER_HOST
        url = f"http://{local_host}:{SERVER_PORT}"
        print(f"Open {url}")
        if args.open_browser:
            def open_when_ready():
                import http.client
                for _ in range(90):
                    try:
                        conn = http.client.HTTPConnection(local_host, int(SERVER_PORT), timeout=1)
                        try:
                            conn.request("GET", "/api/health")
                            response = conn.getresponse()
                            if response.status == 200:
                                webbrowser.open(url)
                                return
                        finally:
                            conn.close()
                    except OSError:
                        pass
                    time.sleep(0.5)
            threading.Thread(target=open_when_ready, daemon=True).start()
        reload_options = {"reload_dirs": [str(ROOT / "app")]} if args.reload else {}
        try:
            uvicorn.run("app.server:app", host=SERVER_HOST, port=SERVER_PORT, reload=args.reload, **reload_options)
        except OSError as exc:
            if getattr(exc, "errno", None) in (48, 98, 10048) or "address already in use" in str(exc).lower():
                print(f"Lỗi: Cổng {SERVER_PORT} đang được sử dụng bởi một tiến trình khác. "
                      f"Vui lòng dừng máy chủ cũ trước khi khởi động máy chủ mới.")
                return 1
            raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
