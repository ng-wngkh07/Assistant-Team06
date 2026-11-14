"""Inspect actual MLX LoRA matrices without loading a model or using the GPU."""

import hashlib
import json
import math
from pathlib import Path
import re
import struct


def inspect_mlx_adapter_layout(model_dir: Path, adapter_file: Path) -> dict:
    """Validate file structure, finite values and paired matrix shapes.

    This proves layout compatibility, not identity of the pretrained weights.
    Release metadata must separately bind the exact base/tokenizer revision.
    """
    try:
        config_path = model_dir / "config.json"
        config_bytes = config_path.read_bytes()
        cfg = json.loads(config_bytes)
        has_text_config = isinstance(cfg.get("text_config"), dict)
        arch_cfg = cfg["text_config"] if has_text_config else cfg
        hidden = int(arch_cfg["hidden_size"])
        intermediate = int(arch_cfg["intermediate_size"])
        layers = int(arch_cfg["num_hidden_layers"])
        heads = int(arch_cfg["num_attention_heads"])
        kv_heads = int(arch_cfg["num_key_value_heads"])
        if min(hidden, intermediate, layers, heads, kv_heads) <= 0 or hidden % heads:
            raise ValueError("Invalid base architecture")
        head_dim = int(arch_cfg.get("head_dim", hidden // heads))
        q_mult = 2 if arch_cfg.get("attn_output_gate") else 1
        kv_dim = head_dim * kv_heads
        o_in = heads * head_dim if (has_text_config or str(arch_cfg.get("model_type", "")).startswith("qwen3")) else hidden
        dimensions = {
            "q_proj": (hidden, heads * head_dim * q_mult),
            "k_proj": (hidden, kv_dim),
            "v_proj": (hidden, kv_dim),
            "o_proj": (o_in, hidden),
            "gate_proj": (hidden, intermediate),
            "up_proj": (hidden, intermediate),
            "down_proj": (intermediate, hidden),
        }
        linear_modules = {"in_proj_qkv", "in_proj_z", "in_proj_a", "in_proj_b", "out_proj"}
        has_linear_attn = (
            "linear_num_key_heads" in arch_cfg
            and "linear_num_value_heads" in arch_cfg
            and "linear_key_head_dim" in arch_cfg
            and "linear_value_head_dim" in arch_cfg
        )
        if has_linear_attn:
            lk_heads = int(arch_cfg["linear_num_key_heads"])
            lv_heads = int(arch_cfg["linear_num_value_heads"])
            lk_dim = int(arch_cfg["linear_key_head_dim"])
            lv_dim = int(arch_cfg["linear_value_head_dim"])
            if min(lk_heads, lv_heads, lk_dim, lv_dim) <= 0:
                raise ValueError("Invalid linear attention architecture")
            val_dim = lv_heads * lv_dim
            qkv_dim = lk_heads * lk_dim * 2 + val_dim
            dimensions.update({
                "in_proj_qkv": (hidden, qkv_dim),
                "in_proj_z": (hidden, val_dim),
                "in_proj_a": (hidden, lv_heads),
                "in_proj_b": (hidden, lv_heads),
                "out_proj": (val_dim, hidden),
            })
        layer_types = arch_cfg.get("layer_types")
        if layer_types is not None and (not isinstance(layer_types, list) or len(layer_types) != layers):
            raise ValueError("Invalid layer_types configuration")
        byte_sizes = {"F32": 4, "F16": 2, "BF16": 2}
        prefix_pat = r"(?:language_model\.)?model\.layers\." if has_text_config else r"model\.layers\."
        section_pat = r"(?:self_attn|linear_attn|mlp)" if has_linear_attn else r"(?:self_attn|mlp)"
        pattern = re.compile(rf"^{prefix_pat}(\d+)\.{section_pat}\.(\w+)\.lora_([ab])$")
        groups = {}
        with adapter_file.open("rb") as f:
            size = adapter_file.stat().st_size
            prefix = f.read(8)
            if len(prefix) != 8:
                raise ValueError("Missing safetensors header")
            header_size = struct.unpack("<Q", prefix)[0]
            if not 2 <= header_size <= min(1024 * 1024, size - 8):
                raise ValueError("Invalid safetensors header size")
            header = json.loads(f.read(header_size))
            if not isinstance(header, dict):
                raise ValueError("Safetensors header must be an object")
            entries = [(k, v) for k, v in header.items() if k != "__metadata__"]
            if not entries:
                raise ValueError("No adapter tensors")
            intervals = []
            payload_start = 8 + header_size
            for name, info in entries:
                match = pattern.fullmatch(name)
                if not match or int(match[1]) >= layers or match[2] not in dimensions:
                    raise ValueError("Unknown tensor or incompatible layer: " + name)
                layer_idx = int(match[1])
                module_name = match[2]
                section = name.rsplit(".", 3)[-3]
                if module_name in {"gate_proj", "up_proj", "down_proj"}:
                    expected_section = "mlp"
                elif module_name in linear_modules:
                    expected_section = "linear_attn"
                else:
                    expected_section = "self_attn"
                if section != expected_section:
                    raise ValueError("Tensor targets a nonexistent module: " + name)
                if isinstance(layer_types, list):
                    lt = layer_types[layer_idx]
                    if expected_section == "linear_attn" and lt != "linear_attention":
                        raise ValueError("Tensor targets linear_attn on non-linear layer: " + name)
                    if expected_section == "self_attn" and lt != "full_attention":
                        raise ValueError("Tensor targets self_attn on non-full-attention layer: " + name)
                shape = info["shape"]
                dtype = info["dtype"]
                lo, hi = info["data_offsets"]
                if dtype not in byte_sizes or len(shape) != 2 or any(type(x) is not int or x <= 0 for x in shape):
                    raise ValueError("Invalid LoRA shape/dtype: " + name)
                if type(lo) is not int or type(hi) is not int or lo < 0 or hi <= lo:
                    raise ValueError("Invalid tensor offsets")
                if hi - lo != math.prod(shape) * byte_sizes[dtype] or payload_start + hi > size:
                    raise ValueError("Truncated or inconsistent tensor data: " + name)
                groups.setdefault(name[:-1], {})[match[3]] = shape
                intervals.append((lo, hi))
                f.seek(payload_start + lo)
                remaining = hi - lo
                fmt = {"F32": "<f", "F16": "<e", "BF16": "<H"}[dtype]
                while remaining:
                    block = f.read(min(65536, remaining))
                    if not block:
                        raise ValueError("Unexpected end of tensor data")
                    values = struct.iter_unpack(fmt, block)
                    finite = all((v[0] & 0x7F80) != 0x7F80 for v in values) if dtype == "BF16" else all(math.isfinite(v[0]) for v in values)
                    if not finite:
                        raise ValueError("Nonfinite adapter weights: " + name)
                    remaining -= len(block)
            end = 0
            for lo, hi in sorted(intervals):
                if lo != end:
                    raise ValueError("Overlapping or unaccounted tensor data")
                end = hi
            if payload_start + end != size:
                raise ValueError("Unaccounted trailing bytes")
            for prefix, pair in groups.items():
                if set(pair) != {"a", "b"}:
                    raise ValueError("Unpaired LoRA matrices: " + prefix)
                module = prefix.split(".")[-2]
                inputs, outputs = dimensions[module]
                a, b = pair["a"], pair["b"]
                if a[0] != inputs or b[1] != outputs or a[1] != b[0]:
                    raise ValueError("LoRA dimensions do not match base: " + prefix)
        with adapter_file.open("rb") as f:
            digest = hashlib.file_digest(f, "sha256").hexdigest()
        return {"layout_matches": True, "identity_verified": False, "finite": True,
                "tensor_count": len(entries), "base_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
                "adapter_sha256": digest}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error, OverflowError) as exc:
        return {"layout_matches": False, "identity_verified": False, "error": str(exc)}
