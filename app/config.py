import os
import sys
from pathlib import Path


def _positive_int_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _context_tokens_setting(name: str, default: int = 8192, min_val: int = 2048, max_val: int = 32768) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    if min_val <= value <= max_val:
        return value
    return default

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
def _workspace_path(name: str, default: str) -> Path:
    path = Path(os.getenv(name, default)).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


SRC_DIR = _workspace_path("AGENT_SRC_DIR", "src")
DATA_DIR = _workspace_path("AGENT_DATA_DIR", "data")
DB_PATH = DATA_DIR / "knowledge_base.db"

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Model settings
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_CHAT_MODEL = os.getenv(
    "DEFAULT_CHAT_MODEL",
    "qwen3.5-9b-4bit" if sys.platform == "darwin" else "qwen3.5:9b"
)
DEFAULT_EMBED_MODEL = os.getenv("DEFAULT_EMBED_MODEL", "bge-m3")
TRAINED_MODEL_NAME = os.getenv("TRAINED_MODEL_NAME", DEFAULT_CHAT_MODEL)
OLLAMA_AUX_CHAT_MODEL = os.getenv("OLLAMA_AUX_CHAT_MODEL", DEFAULT_CHAT_MODEL)
MLX_9B_MODEL_DIR = Path(
    os.getenv("MLX_MODEL_DIR")
    or os.getenv("MLX_9B_MODEL_DIR")
    or str(DATA_DIR / "models" / "qwen3.5-9b-4bit")
)
MLX_BGE_MODEL_DIR = Path(
    os.getenv("MLX_BGE_MODEL_DIR")
    or os.getenv("BGE_MODEL_DIR")
    or str(DATA_DIR / "models" / "bge-m3-mlx-fp16")
)
MLX_MODEL_DIR = MLX_9B_MODEL_DIR
# No adapter is approved for the current base. Training tools must receive an
# explicitly reviewed target; the runtime never discovers trial adapters here.
MLX_ADAPTER_DIR = DATA_DIR / "adapters" / "unvalidated"
MLX_CONTEXT_TOKENS = _context_tokens_setting("MLX_CONTEXT_TOKENS", 8192, 2048, 32768)
# Base model configuration for training pipeline
TRAINING_MODEL_DIR = Path(os.getenv("TRAINING_MODEL_DIR", str(MLX_9B_MODEL_DIR)))
TRAINING_MODEL_REPO = "mlx-community/Qwen3.5-9B-4bit"
TRAINING_MODEL_REVISION = "8b2b98c00a6b4d291155e4890773ca8f769aee53"

# Text chunking settings
CHUNK_SIZE_CHARS = 1200
CHUNK_OVERLAP_CHARS = 200
MIN_TEXT_PER_PAGE = 30  # Pages with fewer chars are treated as blank/image-only

# Search & Retrieval settings
DEFAULT_TOP_K = 6
FTS_WEIGHT = 0.5
SEMANTIC_WEIGHT = 0.5
RRF_K = 60
SIMILARITY_THRESHOLD = 0.15  # Minimum score before declaring lack of info

# Reply-style limits remain configurable per deployment and are token ceilings,
# not quality targets. Concise mode uses fewer tokens; instructional modes need
# room for structure while remaining bounded.
ANSWER_MODE_MAX_TOKENS = {
    "quick": _positive_int_setting("ANSWER_QUICK_MAX_TOKENS", 360),
    "steps": _positive_int_setting("ANSWER_STEPS_MAX_TOKENS", 900),
    "compare": _positive_int_setting("ANSWER_COMPARE_MAX_TOKENS", 900),
}

# Server settings
SERVER_HOST = os.getenv("HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("PORT", "8000"))
VISION_TIMEOUT_SECONDS = float(_positive_int_setting("VISION_TIMEOUT_SECONDS", 360))

# Emergency medical service in Vietnam. Do not add local counselling numbers
# without checking their current availability and operating hours.
CRISIS_HOTLINES = [
    {
        "name": "Cấp cứu y tế (Việt Nam)",
        "phone": "115",
        "hours": "Khi có nguy cơ tức thời"
    }
]
