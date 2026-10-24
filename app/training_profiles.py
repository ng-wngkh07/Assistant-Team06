"""Read-only draft plans for a new base; never authorize or execute training."""

import hashlib
import json
from pathlib import Path


PROFILE = "qwen35-9b-m1-16gb"
BASE_REPO = "mlx-community/Qwen3.5-9B-4bit"
BASE_REVISION = "8b2b98c00a6b4d291155e4890773ca8f769aee53"


def build_training_plan(model_dir: Path, data_dir: Path, adapter_dir: Path,
                        profile: str = PROFILE) -> dict:
    """Describe a conservative hypothesis, without writing or loading a model.

    The config fingerprint identifies configuration only. Weight/tokenizer
    identity, backward memory, data gates and runtime acceptance remain blockers.
    """
    if profile != PROFILE:
        raise ValueError("Unknown training profile")
    model, data, adapter = (Path(p).expanduser().resolve()
                            for p in (model_dir, data_dir, adapter_dir))
    for source in (model, data):
        if adapter == source or adapter in source.parents or source in adapter.parents:
            raise ValueError("Adapter output must be separate from model and data")
    if adapter.exists() and (not adapter.is_dir() or any(adapter.iterdir())):
        raise ValueError("Adapter output must be a fresh or empty directory")

    config_path = model / "config.json"
    config_hash = None
    if config_path.exists():
        try:
            raw = config_path.read_bytes()
            config = json.loads(raw)
            text = config["text_config"]
            expected = {
                "model_type": "qwen3_5_text", "hidden_size": 4096,
                "num_hidden_layers": 32, "num_attention_heads": 16,
                "num_key_value_heads": 4, "head_dim": 256,
                "intermediate_size": 12288, "full_attention_interval": 4,
                "attn_output_gate": True,
                "layer_types": (["linear_attention"] * 3 + ["full_attention"]) * 8,
            }
            quant = config["quantization"]
            valid = config["model_type"] == "qwen3_5" and all(
                type(text.get(key)) is type(value) and text[key] == value
                for key, value in expected.items()
            ) and all(type(quant.get(key)) is type(value) and quant[key] == value
                      for key, value in {"bits": 4, "group_size": 64, "mode": "affine"}.items())
            if not valid:
                raise ValueError("Configuration does not match the pinned hybrid architecture")
            config_hash = hashlib.sha256(raw).hexdigest()
        except (OSError, TypeError, AttributeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid model configuration") from exc

    blockers = [
        "Exact base weight revision and tokenizer identity have not been verified.",
        "Real backward-pass peak memory, swap and finite gradients have not been measured on this hardware.",
        "Corpus/source approvals, disjoint holdout and training authorization are required.",
        "Exact-tokenizer data audit must reject examples that would be truncated.",
        "An independently reviewed baseline on the same exact MLX base is required.",
        "Adapter compatibility and native runtime/export acceptance are unverified; legacy adapters cannot be reused.",
    ]
    if config_hash is None:
        blockers.append("Model configuration is missing; architecture availability is unverified.")
    if not model.is_dir() or not any(model.glob("*.safetensors")):
        blockers.append("Local base weights are missing.")
    if not (model / "tokenizer.json").is_file():
        blockers.append("Local tokenizer assets are missing.")
    if not data.is_dir():
        blockers.append("Training data directory is missing.")
    return {
        "profile": profile, "status": "draft", "execution_ready": False,
        "training_authorized": False, "activation": False,
        "base": {"repo": BASE_REPO, "revision": BASE_REVISION,
                 "model_dir": str(model), "config_sha256": config_hash},
        "effective_batch_size": 4, "optimizer_updates": 100,
        "mlx_config": {
            "model": str(model), "data": str(data), "adapter_path": str(adapter),
            "train": False, "fine_tune_type": "lora", "optimizer": "adamw",
            "seed": 42, "num_layers": 4, "batch_size": 1,
            "grad_accumulation_steps": 4, "iters": 400, "learning_rate": 2e-5,
            "max_seq_length": 1024, "grad_checkpoint": True, "mask_prompt": True,
            "resume_adapter_file": None, "steps_per_report": 4,
            "steps_per_eval": 40, "save_every": 40, "val_batches": -1,
            "lora_parameters": {"rank": 8, "scale": 2.0, "dropout": 0.05,
                                "keys": ["self_attn.q_proj", "self_attn.v_proj",
                                         "linear_attn.in_proj_qkv", "linear_attn.out_proj"]},
        },
        "blockers": blockers,
        "notes": [
            "Conservative starting hypothesis for mlx-lm 0.31.3, not an optimized or executable training profile.",
            "MLX scale 2 directly scales the update; it corresponds to PEFT alpha 16 / rank 8.",
            "400 microsteps with accumulation 4 yield 100 optimizer updates.",
            "Compare learning rates 1e-5, 2e-5 and 5e-5 only after valid memory/data gates and a fixed holdout.",
            "Increase layers or sequence length only after measured headroom; inference fit does not establish training fit.",
        ],
    }
