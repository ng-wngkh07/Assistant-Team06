"""Run offline native MLX embedding worker from JSON stdin (in Python 3.13 training venv)."""

import hashlib
import json
import os
import socket
import sys
from pathlib import Path

# Enforce offline HuggingFace / Transformers worker environment
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

# Outbound network denial
def _deny_network(*args, **kwargs):
    raise RuntimeError("Outbound network disabled for native embedding worker")
socket.socket.connect = _deny_network
socket.create_connection = _deny_network

import mlx.core as mx
from mlx_embeddings import load

PINNED_BGE_IDENTITY = "bge-m3-mlx-fp16@a37eddded9a6a1273a87fb8b0da0d1cdbd98aeec"

PINNED_BGE_FILES = {
    "config.json": {"bytes": 715, "sha256": "2f0b6a5c00cf8653f67d93dd611b119f57e9199f876e137de63090dab4dfb426"},
    "tokenizer.json": {"bytes": 17098085, "sha256": "5df1f55d60c9705a501ab9a75550728625740741fe4be308dac4806c16b7d51d"},
    "tokenizer_config.json": {"bytes": 379, "sha256": "c2ef99124628ae6f79a847ad67e5d3f5b016d0387de90c9fbaf17c45a98933af"},
    "special_tokens_map.json": {"bytes": 964, "sha256": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835"},
    "model.safetensors": {"bytes": 1135554209, "sha256": "e8028acb2e77e0010d35ef75832d823edb5851c95682deef2bde86a45bd4441d"},
}


def determine_identity(model_path: Path) -> str:
    resolved = model_path.resolve()

    # Verify against pinned BGE files
    all_match = True
    for fname, meta in PINNED_BGE_FILES.items():
        f = resolved / fname
        if not f.is_file():
            all_match = False
            break
        st = f.stat()
        if st.st_size != meta["bytes"]:
            all_match = False
            break
        with f.open("rb") as stream:
            h = hashlib.file_digest(stream, "sha256").hexdigest()
        if h != meta["sha256"]:
            all_match = False
            break

    if all_match:
        return PINNED_BGE_IDENTITY

    tracked_fnames = (
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "sentence_bert_config.json",
        "modules.json",
        "model.safetensors.index.json",
        "model.safetensors",
    )
    content_hasher = hashlib.sha256()
    found_any = False
    for fname in sorted(tracked_fnames):
        f = resolved / fname
        if f.is_file():
            found_any = True
            content_hasher.update(fname.encode())
            with f.open("rb") as stream:
                content_hasher.update(hashlib.file_digest(stream, "sha256").digest())
    for f in sorted(resolved.glob("model-*.safetensors")):
        found_any = True
        content_hasher.update(f.name.encode())
        with f.open("rb") as stream:
            content_hasher.update(hashlib.file_digest(stream, "sha256").digest())

    if found_any:
        cfg_hash = content_hasher.hexdigest()[:16]
        return f"bge-m3-custom@{cfg_hash}"
    return "bge-m3-unknown@unverified"


def main():
    request = json.load(sys.stdin)
    model_path = Path(request["model_path"])
    texts = request.get("texts", [])
    max_length = int(request.get("max_length", 512))

    if not model_path.exists():
        raise FileNotFoundError(f"Thư mục mô hình embedding không tồn tại: {model_path}")

    model, tokenizer = load(
        str(model_path),
        tokenizer_config={"local_files_only": True, "trust_remote_code": False},
    )

    model_identity = determine_identity(model_path)

    if not texts:
        print(json.dumps({
            "vectors": [],
            "dimensions": 1024,
            "pooling": "CLS, float32 L2 normalization",
            "model_identity": model_identity,
        }))
        return

    # Lossless source chunk coverage: reject over-limit instead of false full_text
    for idx, text in enumerate(texts):
        encoded = tokenizer.encode(text)
        if len(encoded) > max_length:
            raise ValueError(
                f"Văn bản tại vị trí {idx} có độ dài {len(encoded)} tokens, vượt quá giới hạn tokenizer {max_length}. "
                "Bảo toàn lossless chunk bị vi phạm (không được cắt cụt khi full_text=1)."
            )

    tokens = tokenizer(texts, padding=True, truncation=False, return_tensors="mlx")
    result = model(tokens["input_ids"], attention_mask=tokens["attention_mask"])
    cls = result.last_hidden_state[:, 0, :].astype(mx.float32)
    vecs = cls / mx.maximum(mx.linalg.norm(cls, axis=-1, keepdims=True), 1e-12)
    mx.eval(vecs)

    vectors = vecs.tolist()
    print(json.dumps({
        "vectors": vectors,
        "count": len(vectors),
        "dimensions": len(vectors[0]) if vectors else 1024,
        "pooling": "CLS, float32 L2 normalization",
        "model_identity": model_identity,
    }))


if __name__ == "__main__":
    main()
