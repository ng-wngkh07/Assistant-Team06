"""Local, cached English-to-Vietnamese translation of retrieved passages."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import requests

from app.config import DATA_DIR, OLLAMA_BASE_URL, DEFAULT_CHAT_MODEL
from app.gpu_lock import gpu_coordinator, GPUBusyError
from app.training_data import detect_language
from app.bilingual_terms import translation_guidance, translation_is_plausible

CACHE_VERSION = "en-vi-context-v2"


class LocalTranslator:
    def __init__(self, cache_path: Path = DATA_DIR / "translations.db", model: str | None = None):
        self.cache_path = cache_path
        self.model = model or DEFAULT_CHAT_MODEL
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.cache_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS translations (cache_key TEXT PRIMARY KEY, text_vi TEXT NOT NULL)")

    def translate(self, chunks: list[dict[str, Any]]) -> dict[int, str]:
        """Translate selected English excerpts in one local call; reuse cache."""
        pending: dict[int, tuple[str, str]] = {}
        result: dict[int, str] = {}
        with sqlite3.connect(self.cache_path) as conn:
            for index, chunk in enumerate(chunks):
                source = chunk.get("safe_text", chunk.get("text", ""))[:700]
                if detect_language(source) != "en" or len(source) < 100:
                    continue
                key = hashlib.sha256((CACHE_VERSION + "\0" + self.model + "\0" + source).encode()).hexdigest()
                row = conn.execute("SELECT text_vi FROM translations WHERE cache_key=?", (key,)).fetchone()
                if row and translation_is_plausible(source, row[0]) and len(row[0].strip()) >= 0.65 * len(source.strip()):
                    result[index] = row[0]
                else:
                    pending[index] = (key, source)
        if not pending:
            return result
        # Small batches avoid truncated JSON when several English sources are retrieved.
        pending_items = list(pending.items())
        from app.trained_client import TrainedModelClient
        use_mlx = self.model in (DEFAULT_CHAT_MODEL, "qwen2.5-3b-4bit", "local")
        if use_mlx and not TrainedModelClient.available():
            return result

        for start in range(0, len(pending_items), 2):
            batch = dict(pending_items[start:start + 2])
            numbered = "\n\n".join(f"<source id=\"{index}\">\n{text}\n</source>" for index, (_, text) in batch.items())
            prompt = (
                "Dịch từng đoạn sau sang tiếng Việt chính xác, giữ nguyên ý và thuật ngữ. "
                + translation_guidance(" ".join(text for _, text in batch.values())) +
                "Không trả lời câu hỏi, không thêm kiến thức. Chỉ trả JSON với khóa là id đoạn "
                "và giá trị là bản dịch.\n\n" + numbered
            )
            try:
                with gpu_coordinator.acquire_for_inference():
                    if use_mlx:
                        raw_content = TrainedModelClient().chat_complete(
                            messages=[
                                {"role": "system", "content": "You are an English-to-Vietnamese translator. Output Vietnamese (tiếng Việt, vi) using Latin letters with Vietnamese diacritics. Preserve meaning, do not complete cut-off sentences. Return the requested JSON."},
                                {"role": "user", "content": prompt}
                            ],
                            temperature=0,
                            max_tokens=420 * len(batch),
                        )
                    else:
                        response = requests.post(
                            f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat",
                            json={
                                "model": self.model,
                                "stream": False,
                                "format": "json",
                                "think": False,
                                "options": {"temperature": 0, "num_predict": 420 * len(batch)},
                                "messages": [{"role": "system", "content": "You are an English-to-Vietnamese translator. Output Vietnamese (tiếng Việt, vi) using Latin letters with Vietnamese diacritics. Preserve meaning, do not complete cut-off sentences. Return the requested JSON."}, {"role": "user", "content": prompt}],
                            },
                            timeout=180,
                        )
                        response.raise_for_status()
                        raw_content = response.json()["message"]["content"]

                import re
                m = re.search(r"\{.*\}", raw_content, re.DOTALL)
                translated = json.loads(m.group(0) if m else raw_content)
                if isinstance(translated, dict) and isinstance(translated.get("translations"), dict):
                    translated = translated["translations"]
                if not isinstance(translated, dict):
                    continue
                with sqlite3.connect(self.cache_path) as conn:
                    for index, (key, _) in batch.items():
                        value = translated.get(str(index), "")
                        if isinstance(value, str) and translation_is_plausible(batch[index][1], value) and len(value.strip()) >= 0.65 * len(batch[index][1].strip()):
                            result[index] = value
                            conn.execute("INSERT OR REPLACE INTO translations VALUES (?, ?)", (key, value))
            except (requests.RequestException, ValueError, KeyError, GPUBusyError):
                # The original bilingual model can still read English if translation fails or GPU is busy.
                continue
        return result
