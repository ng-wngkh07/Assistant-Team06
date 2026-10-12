"""Run one local MLX inference from JSON stdin (in the Python 3.13 training venv)."""

import json
import sys
from pathlib import Path

from mlx_lm import generate, load
from mlx_lm.sample_utils import make_sampler
import mlx.core as mx


def main():
    request = json.load(sys.stdin)
    model_path = Path(request["model_path"])
    adapter_path = Path(request["adapter_path"]) if request.get("adapter_path") else None
    if not model_path.exists() or (adapter_path and not (adapter_path / "adapters.safetensors").exists()):
        raise FileNotFoundError("Chưa có mô hình hoặc adapter LoRA đã huấn luyện")

    # Read config to get model context capacity (missing/malformed config fails closed)
    config_path = model_path / "config.json"
    if not config_path.exists():
        raise ValueError(f"Không tìm thấy cấu hình mô hình tại {config_path}")
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        raw_cap = cfg.get("max_position_embeddings") or cfg.get("seq_length")
        if raw_cap is None:
            raise ValueError("Thiếu trường max_position_embeddings hoặc seq_length trong config.json")
        model_capacity = int(raw_cap)
        if model_capacity <= 0:
            raise ValueError("Cấu hình giới hạn ngữ cảnh mô hình không hợp lệ")
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Cấu hình mô hình không hợp lệ hoặc thiếu giới hạn ngữ cảnh") from exc

    try:
        max_tokens = int(request.get("max_tokens", 700))
    except (TypeError, ValueError):
        raise ValueError("max_tokens không hợp lệ")
    if max_tokens <= 0:
        raise ValueError(f"max_tokens phải là số nguyên dương, nhận: {max_tokens}")

    num_ctx_req = request.get("num_ctx")
    try:
        num_ctx = int(num_ctx_req) if num_ctx_req is not None else 8192
    except (TypeError, ValueError):
        raise ValueError("num_ctx không hợp lệ")
    if num_ctx <= 0:
        raise ValueError(f"num_ctx phải là số nguyên dương, nhận: {num_ctx}")

    context_limit = min(num_ctx, model_capacity)

    # Required exact input+output validation before loading weights.
    # Fail closed if AutoTokenizer is unavailable or malformed.
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
    except Exception as exc:
        raise ValueError("Không thể tải hoặc xác minh tokenizer của mô hình") from exc

    try:
        prompt = tokenizer.apply_chat_template(
            request["messages"], tokenize=False, add_generation_prompt=True
        )
        prompt_tokens = tokenizer.encode(prompt)
        input_tokens = len(prompt_tokens)
    except Exception as exc:
        raise ValueError("Không thể mã hóa câu hỏi bằng tokenizer của mô hình") from exc

    if input_tokens + max_tokens > context_limit:
        raise ValueError(
            f"Tổng số token ({input_tokens} input + {max_tokens} output = {input_tokens + max_tokens}) "
            f"vượt quá giới hạn ngữ cảnh {context_limit}."
        )

    model, loaded_tokenizer = load(str(model_path), adapter_path=str(adapter_path) if adapter_path else None)
    active_tok = loaded_tokenizer or tokenizer

    seed = request.get("seed")
    if seed is not None:
        mx.random.seed(int(seed))

    temperature = request.get("temperature")
    gen_kwargs = {"max_tokens": max_tokens}
    if temperature is not None:
        gen_kwargs["sampler"] = make_sampler(temp=float(temperature))

    answer = generate(
        model, active_tok, prompt=prompt, **gen_kwargs
    )
    tokens = active_tok.encode(answer)
    output_tokens = len(tokens)
    is_truncated = output_tokens >= max_tokens
    print(json.dumps({
        "answer": answer,
        "is_truncated": is_truncated,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "context_limit": context_limit,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
