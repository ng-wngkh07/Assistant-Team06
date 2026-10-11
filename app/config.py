import os
import sys
from pathlib import Path


def _positive_int_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default

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

# Ollama settings
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_CHAT_MODEL = os.getenv("DEFAULT_CHAT_MODEL", "qwen2.5:3b" if sys.platform == "win32" else "qwen2.5-3b-4bit")
DEFAULT_EMBED_MODEL = os.getenv("DEFAULT_EMBED_MODEL", "bge-m3")
TRAINED_MODEL_NAME = os.getenv("TRAINED_MODEL_NAME", "qwen2.5-3b-4bit")
OLLAMA_AUX_CHAT_MODEL = os.getenv("OLLAMA_AUX_CHAT_MODEL", "qwen2.5vl:3b")
MLX_MODEL_DIR = DATA_DIR / "models" / "qwen2.5-3b-4bit"
MLX_ADAPTER_DIR = DATA_DIR / "adapters" / "psychology-v1"
# Unified single base model for training and inference based on real 16GB RAM profile:
TRAINING_MODEL_DIR = Path(os.getenv("TRAINING_MODEL_DIR", str(MLX_MODEL_DIR)))
TRAINING_MODEL_REPO = "mlx-community/Qwen2.5-3B-Instruct-4bit"
TRAINING_MODEL_REVISION = "4f83f8f146fdf28b512a06562b671d7af4fab457"

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

# Emergency medical service in Vietnam. Do not add local counselling numbers
# without checking their current availability and operating hours.
CRISIS_HOTLINES = [
    {
        "name": "Cấp cứu y tế (Việt Nam)",
        "phone": "115",
        "hours": "Khi có nguy cơ tức thời"
    }
]
