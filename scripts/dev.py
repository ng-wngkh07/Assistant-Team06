"""Portable team launcher. Demo data stays separate from the coordinator corpus."""
import argparse
import contextlib
import json
import os
import shutil
import subprocess
from pathlib import Path
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
    "HISTORY_DB_PATH", "TIMETABLE_DB_PATH", "TIMETABLE_IMAGES_DIR", "STUDY_ACCOUNTS_DIR",
    "ANSWER_QUICK_MAX_TOKENS", "ANSWER_STEPS_MAX_TOKENS", "ANSWER_COMPARE_MAX_TOKENS",
    "MLX_MODEL_DIR", "MLX_9B_MODEL_DIR", "MLX_BGE_MODEL_DIR",
    "BGE_MODEL_DIR", "TRAINED_MODEL_NAME", "TRAINING_MODEL_DIR",
    "KNOWLEDGE_PACK_PATH", "KNOWLEDGE_PACK_SHA256",
    "LLM_PROVIDER", "CHAT_BACKEND", "EMBED_BACKEND", "SOFFICE_PATH",
    "STUDY_ACCOUNTS_REQUIRED", "STUDY_ALLOW_REGISTRATION", "STUDY_SECURE_COOKIES",
    "STUDY_SRC_AUTO_WATCH", "STUDY_GPU_RUNTIME_DIR",
    "STUDY_MAX_INFERENCE_QUEUE", "STUDY_INFERENCE_QUEUE_TIMEOUT_SECONDS",
    "STUDY_RATE_LIMIT_ENABLED", "STUDY_API_RATE_LIMIT_PER_MINUTE",
    "STUDY_INFERENCE_RATE_LIMIT_PER_MINUTE", "STUDY_RATE_LIMIT_WINDOW_SECONDS",
    "STUDY_ACCOUNT_MAX_FILES", "STUDY_ACCOUNT_MAX_STORAGE_MB",
    "STUDY_MIN_FREE_DISK_MB", "FORWARDED_ALLOW_IPS",
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
    else:
        for key, default_rel in (("AGENT_SRC_DIR", "src"), ("AGENT_DATA_DIR", "data")):
            raw = os.environ.get(key)
            if not raw:
                os.environ[key] = str(ROOT / default_rel)
            else:
                candidate = Path(raw).expanduser()
                os.environ[key] = str(candidate if candidate.is_absolute() else ROOT / candidate)
        for key in ("HISTORY_DB_PATH", "TIMETABLE_DB_PATH", "TIMETABLE_IMAGES_DIR", "STUDY_ACCOUNTS_DIR"):
            raw = os.environ.get(key)
            if raw:
                candidate = Path(raw).expanduser()
                if not candidate.is_absolute():
                    os.environ[key] = str(ROOT / candidate)


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


def _inspect_library_state(src_dir: Path, db_path: Path) -> tuple[bool, int]:
    """Return (src_has_files, indexed_doc_count) without mutating files."""
    src_exists = False
    try:
        is_src_dir = src_dir.is_dir()
        resolved_root = src_dir.resolve() if is_src_dir else None
    except OSError:
        is_src_dir = False
        resolved_root = None

    if is_src_dir and resolved_root is not None:
        stack = [src_dir]
        visited: set[tuple[int, int]] = set()
        while stack and not src_exists:
            current = stack.pop()
            try:
                st = current.stat()
            except OSError:
                continue
            dir_key = (st.st_dev, st.st_ino)
            if dir_key in visited:
                continue
            visited.add(dir_key)
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.name.startswith("."):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                src_exists = True
                                break
                            elif entry.is_symlink():
                                target = Path(entry.path)
                                if target.is_file() and target.resolve().is_relative_to(resolved_root):
                                    src_exists = True
                                    break
                        except OSError:
                            continue
            except OSError:
                continue

    indexed_docs = 0
    try:
        db_uri = db_path.resolve().as_uri() if db_path.is_file() else None
    except OSError:
        db_uri = None
    if db_uri:
        uri = f"{db_uri}?mode=ro"
        for target_uri in (uri, f"{uri}&immutable=1"):
            try:
                with contextlib.closing(sqlite3.connect(target_uri, uri=True)) as conn:
                    row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
                    indexed_docs = int(row[0]) if row else 0
                break
            except sqlite3.OperationalError:
                indexed_docs = 0
            except sqlite3.Error:
                indexed_docs = 0
                break
    return src_exists, indexed_docs


def _print_readiness_summary(models_ready: bool, library_ready: bool, label: str, missing_hint: str) -> None:
    """Print honest library vs model readiness without falsely claiming full QA ready."""
    if models_ready and library_ready:
        print(f"Mode: Full {label}QA, embedding and vision ready.")
    elif models_ready:
        print(
            "Models: OK (QA, embedding and vision ready); "
            "Library: UNAVAILABLE (missing or empty library; full QA requires shared sources and indexed database)."
        )
    elif library_ready:
        print(f"Search mode: OK (model-free FTS keyword search ready; full QA requires {missing_hint}).")
    else:
        print(f"Search mode: OK (model-free FTS engine ready; library currently missing/empty; full QA requires shared sources and {missing_hint}).")


def check_environment(allow_wait: bool = False) -> bool:
    from app.config import DATA_DIR, DB_PATH, SRC_DIR, DEFAULT_CHAT_MODEL, OLLAMA_AUX_CHAT_MODEL
    from app.ollama_client import OllamaClient
    from app.trained_client import TrainedModelClient
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE VIRTUAL TABLE smoke USING fts5(text)")
    import pymupdf as fitz
    from PIL import Image  # noqa: F401
    src_exists, indexed_docs = _inspect_library_state(SRC_DIR, DB_PATH)
    library_ready = src_exists and indexed_docs > 0
    if DB_PATH.is_file() or "app.server" in sys.modules:
        from app.server import app
        app_status = f"App routes: {len(app.routes)}"
    else:
        import importlib.util
        import fastapi
        if importlib.util.find_spec("app.server") is None:
            raise ImportError("app.server module not found")
        app_status = f"App module: OK (FastAPI {fastapi.__version__}; no DB initialized)"
    print(f"Python: {sys.version.split()[0]} | platform: {sys.platform}")
    print(f"Sources: {SRC_DIR}\nLocal data: {DATA_DIR}")
    if not src_exists:
        print(f"Library: MISSING ({SRC_DIR} not found or empty; no sample or user-library seeded)")
    elif indexed_docs == 0:
        print(f"Library: EMPTY (0 indexed documents in {DB_PATH})")
    else:
        print(f"Library: OK ({indexed_docs} indexed documents)")
    print(f"{app_status} | SQLite FTS5: OK | PDF: {fitz.VersionBind}")
    from app.model_resolver import is_mlx_model, reject_retired_model
    reject_retired_model(DEFAULT_CHAT_MODEL)
    if is_mlx_model(DEFAULT_CHAT_MODEL):
        if sys.platform != "darwin":
            print(f"QA model: NOT_RUN (MLX native {DEFAULT_CHAT_MODEL} is Apple-only; Windows/Linux uses Ollama)")
            _print_readiness_summary(False, library_ready, "local ", "Ollama models")
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
        models_ready = qa_ready and vision_ready and embedding_ready and runtime.get("ready", False)
        _print_readiness_summary(models_ready, library_ready, "local ", "missing models")
        return models_ready

    client = OllamaClient()
    if not client.check_health():
        if allow_wait:
            for _ in range(15):
                time.sleep(1)
                if client.check_health():
                    break
    if not client.check_health():
        print("Configured local Ollama is unavailable; FTS/demo/mocked tests still work.")
        _print_readiness_summary(False, library_ready, "", "Ollama service")
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

    models_ready = qa_ready and vision_ready and embedding_ready
    _print_readiness_summary(models_ready, library_ready, "", "missing models")
    return models_ready


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo", "check", "serve"])
    parser.add_argument("--profile", choices=["demo", "local"], default=None)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--require-model", action="store_true")
    parser.add_argument("--start-model-service", action="store_true", help="Start installed local Ollama if needed; never downloads models.")
    parser.add_argument("--open-browser", action="store_true", help="Open the local app after its web server responds.")
    parser.add_argument("--install-pack", action="store_true", help="Explicitly install configured KNOWLEDGE_PACK_PATH/KNOWLEDGE_PACK_SHA256 pack.")
    args = parser.parse_args()
    if args.command == "demo":
        if args.profile is not None and args.profile != "demo":
            parser.error("The demo command requires --profile demo.")
        args.profile = "demo"
    else:
        if args.profile is None:
            args.profile = "local"
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
    # DATA01/DATA02: check/serve default do not install a pack
    if args.install_pack:
        if args.profile == "local" and os.environ.get('KNOWLEDGE_PACK_PATH') and os.environ.get('KNOWLEDGE_PACK_SHA256'):
            try:
                install_configured_knowledge_pack()
            except (ValueError, RuntimeError, OSError) as exc:
                print(f'Knowledge pack installation failed: {exc}', file=sys.stderr)
                return 1
        else:
            print(
                'No explicit knowledge pack configured. Set KNOWLEDGE_PACK_PATH and KNOWLEDGE_PACK_SHA256 together; no seeding performed.',
                file=sys.stderr,
            )
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
