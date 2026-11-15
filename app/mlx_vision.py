"""Run offline native MLX vision extraction worker from JSON stdin (in Python 3.13 training venv)."""

import gc
import json
import os
import sys
from pathlib import Path

# Enforce offline HuggingFace / Transformers worker environment
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

# Outbound network denial
import socket
def _deny_network(*args, **kwargs):
    raise RuntimeError("Outbound network disabled for native vision worker")
socket.socket.connect = _deny_network
socket.create_connection = _deny_network

import mlx.core as mx
from mlx_vlm import generate, load
from mlx_vlm.prompt_utils import apply_chat_template


def _run_vision_request(request: dict, model_cache: dict | None = None) -> dict:
    model_path = Path(request["model_path"])
    image_path = request["image_path"]
    prompt_text = request["prompt"]
    max_tokens = int(request.get("max_tokens", 2048))
    temperature = float(request.get("temperature", 0.0))
    response_schema = request.get("response_schema")
    repetition_penalty = request.get("repetition_penalty")
    repetition_context_size = int(request.get("repetition_context_size", 64))

    if not model_path.exists():
        raise FileNotFoundError(f"Thư mục mô hình vision không tồn tại: {model_path}")

    cache_key = str(model_path)
    if model_cache is not None and model_cache.get("path") == cache_key and model_cache.get("model") is not None:
        model = model_cache["model"]
        processor = model_cache["processor"]
        config = model_cache["config"]
    else:
        if model_cache is not None and model_cache.get("model") is not None:
            model_cache.clear()
            gc.collect()
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
        model, processor = load(str(model_path), local_files_only=True, trust_remote_code=False)
        config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
        if model_cache is not None:
            model_cache["path"] = cache_key
            model_cache["model"] = model
            model_cache["processor"] = processor
            model_cache["config"] = config

    prompt = apply_chat_template(
        processor, config, prompt_text, num_images=1, enable_thinking=False
    )
    mx.random.seed(42)
    # Bounded image options verified in offline probe to avoid Metal GPU timeout
    options = {"resize_shape": (1024, 1024), "prefill_step_size": 128}
    if repetition_penalty is not None:
        options["repetition_penalty"] = float(repetition_penalty)
        options["repetition_context_size"] = repetition_context_size
    if response_schema is not None:
        if not isinstance(response_schema, dict):
            raise ValueError("response_schema phải là JSON object.")
        from mlx_vlm.structured import build_json_schema_logits_processor

        tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
        options["logits_processors"] = [build_json_schema_logits_processor(tokenizer, response_schema)]
    response = generate(
        model,
        processor,
        prompt=prompt,
        image=[image_path],
        max_tokens=max_tokens,
        temperature=temperature,
        verbose=False,
        **options,
    )

    finish_reason = getattr(response, "finish_reason", "stop") or "stop"
    text = getattr(response, "text", "")

    return {
        "text": text,
        "finish_reason": finish_reason,
        "is_truncated": (finish_reason == "length"),
        "generation_tokens": getattr(response, "generation_tokens", None),
        "prompt_tokens": getattr(response, "prompt_tokens", None),
    }


def serve_stream():
    model_cache: dict = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Payload phải là JSON object.")
            result = _run_vision_request(request, model_cache=model_cache)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
        finally:
            gc.collect()


def main():
    if "--stream" in sys.argv[1:]:
        serve_stream()
        return
    request = json.load(sys.stdin)
    print(json.dumps(_run_vision_request(request, model_cache=None), ensure_ascii=False))


if __name__ == "__main__":
    main()
