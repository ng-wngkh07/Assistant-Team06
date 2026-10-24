"""Conservative normalization for Vietnamese questions before retrieval."""

import re
import unicodedata
import json


from app.config import DEFAULT_CHAT_MODEL, OLLAMA_BASE_URL, TRAINED_MODEL_NAME


_REPLACEMENTS = {
    r"\btâm\s+lí\b": "tâm lý",
    r"\btam\s+ly\b": "tâm lý",
    r"\btâm\s+lý\s+học\b": "tâm lý học",
    r"\bsang\s+chan\b": "sang chấn",
    r"\bcam\s+xuc\b": "cảm xúc",
    r"\blo\s+au\b": "lo âu",
    r"\btram\s+cam\b": "trầm cảm",
    r"\bbong\s+toi\b": "bóng tối",
}


def normalize_query(query: str) -> str:
    cleaned = unicodedata.normalize("NFC", query)
    cleaned = re.sub(r"[\x00-\x1f]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for pattern, replacement in _REPLACEMENTS.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)
    return cleaned


def is_unclear_query(query: str) -> bool:
    words = re.findall(r"\w+", query, re.UNICODE)
    return len(words) < 2 or len(query.strip()) < 5


def _comparison_words(value: str) -> set[str]:
    decomposed = unicodedata.normalize("NFD", value.casefold()).replace("đ", "d")
    plain = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return set(re.findall(r"\w+", plain))


def normalize_query_with_model(query: str, model: str = DEFAULT_CHAT_MODEL) -> str:
    """Use the local model to repair typos while preserving the user's intent."""
    base = normalize_query(query)
    if is_unclear_query(base) or len(base) > 500:
        return base
    prompt = (
        "Chỉ sửa chính tả, dấu tiếng Việt và dấu câu của câu hỏi sau. "
        "Không trả lời câu hỏi. Không thêm hay bỏ ý, phủ định, số, tên riêng hoặc thuật ngữ. "
        'Trả JSON duy nhất: {"normalized_query":"..."}.\nCâu hỏi: ' + base
    )
    raw_content = None
    from app.trained_client import TrainedModelClient
    from app.model_resolver import is_mlx_model
    import sys
    if sys.platform == "darwin":
        if not is_mlx_model(model) or not TrainedModelClient.available(model):
            return base
        use_mlx = True
    else:
        use_mlx = is_mlx_model(model)
        if use_mlx and not TrainedModelClient.available(model):
            return base
    if use_mlx:
        try:
            raw_content = TrainedModelClient(model_name=model).chat_complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=160,
            )
        except Exception:
            raw_content = None

    if raw_content is None and use_mlx:
        return base
    if raw_content is None:
        try:
            from app.ollama_client import OllamaClient
            client = OllamaClient()
            client._verify_model_locality(model)
            resp = client.chat_sync(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                temperature=0,
                num_predict=160,
                num_ctx=8192,
            )
            raw_content = resp.get("message", {}).get("content", "")
        except Exception:
            return base

    try:
        m = re.search(r"\{.*\}", raw_content, re.DOTALL)
        candidate = normalize_query(json.loads(m.group(0) if m else raw_content)["normalized_query"])
    except (ValueError, KeyError, TypeError):
        return base
    if not (0.6 * len(base) <= len(candidate) <= 1.5 * len(base)):
        return base
    if re.findall(r"\d+", candidate) != re.findall(r"\d+", base):
        return base
    for negation in ("không", "chưa", "đừng", "not", "never"):
        if bool(re.search(rf"\b{negation}\b", candidate, re.I)) != bool(re.search(rf"\b{negation}\b", base, re.I)):
            return base
    original_words = _comparison_words(base)
    overlap = len(original_words & _comparison_words(candidate)) / max(1, len(original_words))
    return candidate if overlap >= 0.5 else base
