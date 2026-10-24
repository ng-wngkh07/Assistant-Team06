"""Run one local MLX inference from JSON stdin (in the Python 3.13 training venv)."""

import json
import os
import sys
import time
from pathlib import Path

# Enforce offline HuggingFace / Transformers worker environment
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

# Outbound network denial
import socket
def _deny_network(*args, **kwargs):
    raise RuntimeError("Outbound network disabled for native inference worker")
socket.socket.connect = _deny_network
socket.create_connection = _deny_network

try:
    from mlx_lm import stream_generate, load, generate
except ImportError:
    from mlx_lm import load, generate
    stream_generate = None
from mlx_lm.sample_utils import make_sampler
import mlx.core as mx


def main():
    started = time.perf_counter()
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
        text_cfg = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}
        raw_cap = (
            text_cfg.get("max_position_embeddings")
            or text_cfg.get("seq_length")
            or cfg.get("max_position_embeddings")
            or cfg.get("seq_length")
        )
        if raw_cap is None:
            raise ValueError("Thiếu trường max_position_embeddings hoặc seq_length trong config.json")
        model_capacity = int(raw_cap)
        if model_capacity <= 0:
            raise ValueError("Cấu hình giới hạn ngữ cảnh mô hình không hợp lệ")
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("Cấu hình mô hình không hợp lệ hoặc thiếu giới hạn ngữ cảnh") from exc

    # Incompatible adapter rejection: 3B adapters cannot attach to 9B
    is_9b = (
        cfg.get("model_type") == "qwen3_5"
        or text_cfg.get("model_type") == "qwen3_5_text"
        or "9b" in model_path.name.lower()
    )
    if is_9b and adapter_path is not None:
        raise ValueError(f"Không thể gắn adapter LoRA 3B vào mô hình nền tảng 9B ({model_path.name})")

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
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
    except Exception as exc:
        raise ValueError("Không thể tải hoặc xác minh tokenizer của mô hình") from exc

    try:
        prompt = tokenizer.apply_chat_template(
            request["messages"],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
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

    load_started = time.perf_counter()
    model, loaded_tokenizer = load(str(model_path), adapter_path=str(adapter_path) if adapter_path else None)
    load_seconds = time.perf_counter() - load_started
    active_tok = loaded_tokenizer or tokenizer

    seed = request.get("seed")
    if seed is not None:
        mx.random.seed(int(seed))

    temperature = request.get("temperature")
    # Small prefill batches reduce transient Metal memory on the 16GB Mac.
    # All prompt tokens remain present; this does not rotate or truncate context.
    gen_kwargs = {"max_tokens": max_tokens, "prefill_step_size": 128}
    if temperature is not None:
        gen_kwargs["sampler"] = make_sampler(temp=float(temperature))

    generation_started = time.perf_counter()
    first_token_seconds = None
    last_chunk = None
    if stream_generate is not None:
        parts = []
        last_chunk = None
        for chunk in stream_generate(model, active_tok, prompt=prompt, **gen_kwargs):
            if first_token_seconds is None:
                first_token_seconds = time.perf_counter() - generation_started
            parts.append(chunk.text)
            last_chunk = chunk
        answer = "".join(parts)

        if last_chunk is not None:
            done_reason = getattr(last_chunk, "finish_reason", "stop") or "stop"
            output_tokens = getattr(last_chunk, "generation_tokens", len(active_tok.encode(answer)))
        else:
            done_reason = "stop"
            output_tokens = len(active_tok.encode(answer))
    else:
        answer = generate(model, active_tok, prompt=prompt, **gen_kwargs)
        done_reason = "stop"
        output_tokens = len(active_tok.encode(answer))

    generation_seconds = time.perf_counter() - generation_started

    is_truncated = (done_reason == "length")

    base_identity = cfg.get("_name_or_path") or model_path.name
    has_adapter = bool(adapter_path and (adapter_path / "adapters.safetensors").exists())

    print(json.dumps({
        "answer": answer,
        "is_truncated": is_truncated,
        "done_reason": done_reason,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "context_limit": context_limit,
        "base_model": str(base_identity),
        "adapter_loaded": has_adapter,
        "adapter_path": str(adapter_path) if has_adapter else None,
        "timings": {
            "load_seconds": load_seconds,
            "first_token_seconds": first_token_seconds,
            "generation_seconds": generation_seconds,
            "worker_seconds": time.perf_counter() - started,
            "prompt_tokens_per_second": getattr(last_chunk, "prompt_tps", None),
            "generation_tokens_per_second": getattr(last_chunk, "generation_tps", None),
            "peak_memory_gb": getattr(last_chunk, "peak_memory", None),
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
