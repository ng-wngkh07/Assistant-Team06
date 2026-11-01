"""Controlled language and chat-template diagnostics before the next trial."""

import hashlib
import importlib.metadata
import json
from pathlib import Path

from app.config import MLX_MODEL_DIR


def main():
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler
    model, tokenizer = load(str(MLX_MODEL_DIR))
    probes = [
        ("vi_simple", "Bạn trả lời bằng tiếng Việt.", "Viết một câu giới thiệu về trí nhớ."),
        ("en_simple", "You are a helpful assistant.", "Explain memory in one sentence."),
        ("vi_controlled", "Answer only in Vietnamese (tiếng Việt), using Latin letters with Vietnamese diacritics. Be concise.", "Giải thích sự khác nhau giữa tương quan và quan hệ nhân quả trong hai câu."),
        ("vi_translation", "Translate English into Vietnamese. Preserve negations and qualifications. Output only the translation.", "A correlation does not prove causation. A third variable may influence both measures."),
        ("vi_arithmetic", "Bạn trả lời bằng tiếng Việt, nêu phép tính ngắn gọn.", "Một lớp có 40 người; 25% chọn môn A. Có bao nhiêu người chọn môn A?"),
        ("en_reasoning", "You are a helpful assistant. Be concise.", "A survey finds that students who exercise more report less stress. Does this prove exercise causes lower stress? Why?"),
    ]
    result = {"versions": {p: importlib.metadata.version(p) for p in ("mlx", "mlx-lm", "transformers")},
              "model_path": str(MLX_MODEL_DIR), "chat_template": tokenizer.chat_template,
              "special_tokens": tokenizer.special_tokens_map, "temperature": 0, "max_tokens": 220, "cases": []}
    for name, system, user in probes:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        answer = generate(model, tokenizer, prompt=prompt, max_tokens=220, sampler=make_sampler(0.0))
        result["cases"].append({"id": name, "messages": messages, "rendered_prompt": prompt, "answer": answer})
        print(name + ": " + answer, flush=True)
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"):
        path = MLX_MODEL_DIR/name
        if path.exists():
            h = hashlib.sha256()
            with path.open("rb") as f:
                for part in iter(lambda: f.read(1024*1024), b""):
                    h.update(part)
            result.setdefault("files", {})[name] = {"bytes": path.stat().st_size, "sha256": h.hexdigest()}
    Path("data/evaluation/v6-baseline-diagnostic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
