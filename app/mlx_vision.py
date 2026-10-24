"""Run offline native MLX vision extraction worker from JSON stdin (in Python 3.13 training venv)."""

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


def main():
    request = json.load(sys.stdin)
    model_path = Path(request["model_path"])
    image_path = request["image_path"]
    prompt_text = request["prompt"]
    max_tokens = int(request.get("max_tokens", 2048))
    temperature = float(request.get("temperature", 0.0))

    if not model_path.exists():
        raise FileNotFoundError(f"Thư mục mô hình vision không tồn tại: {model_path}")

    model, processor = load(str(model_path), local_files_only=True, trust_remote_code=False)
    config = json.loads((model_path / "config.json").read_text(encoding="utf-8"))
    prompt = apply_chat_template(
        processor, config, prompt_text, num_images=1, enable_thinking=False
    )
    mx.random.seed(42)
    # Bounded image options verified in offline probe to avoid Metal GPU timeout
    options = {"resize_shape": (1024, 1024), "prefill_step_size": 128}
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

    print(json.dumps({
        "text": text,
        "finish_reason": finish_reason,
        "is_truncated": (finish_reason == "length"),
        "generation_tokens": getattr(response, "generation_tokens", None),
        "prompt_tokens": getattr(response, "prompt_tokens", None),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
