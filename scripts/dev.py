"""Portable team launcher. Demo data stays separate from the coordinator corpus."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys

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
    "MLX_MODEL_DIR", "MLX_9B_MODEL_DIR", "MLX_3B_MODEL_DIR", "MLX_BGE_MODEL_DIR",
    "BGE_MODEL_DIR", "TRAINED_MODEL_NAME", "TRAINING_MODEL_DIR",
}


def configure(profile: str) -> None:
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
        os.environ.setdefault("DEFAULT_CHAT_MODEL", "qwen2.5:3b")


def prepare_demo() -> dict:
    from app.config import DB_PATH, SRC_DIR
    from app.indexer import KnowledgeIndexer
    report = KnowledgeIndexer(DB_PATH, SRC_DIR).index_all(ocr=False, embed_model=None)
    if report.get("error") or report.get("error_files"):
        raise RuntimeError(f"Demo indexing failed: {report}")
    return report


def check_environment() -> bool:
    from app.config import DATA_DIR, SRC_DIR, DEFAULT_CHAT_MODEL, OLLAMA_BASE_URL, TRAINED_MODEL_NAME
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
    from app.model_resolver import is_mlx_model
    if sys.platform == "darwin" or is_mlx_model(DEFAULT_CHAT_MODEL):
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

        return qa_ready and vision_ready and embedding_ready

    client = OllamaClient()
    if not client.check_health():
        print(f"Ollama unavailable at {OLLAMA_BASE_URL}; FTS/demo/mocked tests still work.")
        return False
    names = {model.get("name") for model in client.list_models()}
    print(f"Ollama models: {', '.join(sorted(names))}")
    base_names = {n.split(":")[0] for n in names}
    qa_ready = (DEFAULT_CHAT_MODEL in names or DEFAULT_CHAT_MODEL in base_names or f"{DEFAULT_CHAT_MODEL}:latest" in names)
    if qa_ready:
        print(f"QA model: OK ({DEFAULT_CHAT_MODEL})")
    else:
        print(f"QA model: MISSING ({DEFAULT_CHAT_MODEL})")

    vision_ready = any("qwen2.5vl:3b" in m for m in names)
    if vision_ready:
        print("Vision model: OK (qwen2.5vl:3b)")
    else:
        print("Vision model: MISSING (qwen2.5vl:3b)")

    embedding_ready = any("bge-m3" in m for m in names)
    if embedding_ready:
        print("Embedding model: OK (bge-m3)")
    else:
        print("Embedding model: MISSING (bge-m3)")

    return qa_ready and vision_ready and embedding_ready


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["demo", "check", "serve"])
    parser.add_argument("--profile", choices=["demo", "local"], default="demo")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--require-model", action="store_true")
    args = parser.parse_args()
    if args.command == "demo" and args.profile != "demo":
        parser.error("The demo command requires --profile demo.")
    configure(args.profile)
    if args.command in {"demo", "serve"} and args.profile == "demo":
        print(prepare_demo())
        print("Synthetic demo only; this index is not a training approval.")
    if args.command == "check":
        ready = check_environment()
        return 1 if args.require_model and not ready else 0
    if args.command == "serve":
        import uvicorn
        from app.config import SERVER_HOST, SERVER_PORT
        print(f"Open http://{SERVER_HOST}:{SERVER_PORT}")
        reload_options = {"reload_dirs": [str(ROOT / "app")]} if args.reload else {}
        uvicorn.run("app.server:app", host=SERVER_HOST, port=SERVER_PORT, reload=args.reload, **reload_options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
