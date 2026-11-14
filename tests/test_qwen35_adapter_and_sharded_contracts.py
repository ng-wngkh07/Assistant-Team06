"""Unit tests for Qwen3.5 hybrid linear_attn LoRA layout checks and sharded base weight binding."""

import hashlib
import json
import struct
from pathlib import Path

import pytest

from app.adapter_integrity import inspect_mlx_adapter_layout
from app.model_resolver import resolve_base_weight_files
from app.run_ledger import RunLedger, compute_signature_hash


def _write_safetensors(path: Path, tensors: dict[str, tuple[tuple[int, ...], float]]) -> None:
    payload = bytearray()
    header: dict[str, dict] = {}
    for name, (shape, val) in tensors.items():
        count = 1
        for dim in shape:
            count *= dim
        start = len(payload)
        payload.extend(struct.pack(f"<{count}f", *([val] * count)))
        end = len(payload)
        header[name] = {
            "dtype": "F32",
            "shape": list(shape),
            "data_offsets": [start, end],
        }
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(header_bytes)) + header_bytes + bytes(payload))


def _qwen35_hybrid_config() -> dict:
    return {
        "architectures": ["Qwen3_5ForConditionalGeneration"],
        "model_type": "qwen3_5",
        "text_config": {
            "model_type": "qwen3_5_text",
            "hidden_size": 16,
            "intermediate_size": 32,
            "num_hidden_layers": 4,
            "num_attention_heads": 4,
            "num_key_value_heads": 2,
            "head_dim": 4,
            "attn_output_gate": True,
            "layer_types": [
                "linear_attention",
                "linear_attention",
                "linear_attention",
                "full_attention",
            ],
            "linear_num_key_heads": 2,
            "linear_num_value_heads": 4,
            "linear_key_head_dim": 4,
            "linear_value_head_dim": 4,
        },
    }


def _valid_qwen35_hybrid_tensors(rank: int = 2) -> dict[str, tuple[tuple[int, ...], float]]:
    return {
        "language_model.model.layers.0.linear_attn.in_proj_qkv.lora_a": ((16, rank), 0.1),
        "language_model.model.layers.0.linear_attn.in_proj_qkv.lora_b": ((rank, 32), 0.1),
        "language_model.model.layers.0.linear_attn.out_proj.lora_a": ((16, rank), 0.1),
        "language_model.model.layers.0.linear_attn.out_proj.lora_b": ((rank, 16), 0.1),
        "language_model.model.layers.3.self_attn.q_proj.lora_a": ((16, rank), 0.1),
        "language_model.model.layers.3.self_attn.q_proj.lora_b": ((rank, 32), 0.1),
        "language_model.model.layers.3.self_attn.v_proj.lora_a": ((16, rank), 0.1),
        "language_model.model.layers.3.self_attn.v_proj.lora_b": ((rank, 8), 0.1),
    }


def test_qwen35_hybrid_linear_and_full_attention_layout_passes(tmp_path):
    model = tmp_path / "qwen35-model"
    model.mkdir()
    (model / "config.json").write_text(json.dumps(_qwen35_hybrid_config()), encoding="utf-8")
    adapter = tmp_path / "adapters.safetensors"
    _write_safetensors(adapter, _valid_qwen35_hybrid_tensors())

    result = inspect_mlx_adapter_layout(model, adapter)
    assert result["layout_matches"] is True
    assert result["finite"] is True
    assert result["tensor_count"] == 8


def test_qwen35_hybrid_rejects_wrong_layer_section_or_gate_dimension(tmp_path):
    model = tmp_path / "qwen35-model"
    model.mkdir()
    (model / "config.json").write_text(json.dumps(_qwen35_hybrid_config()), encoding="utf-8")

    # 1. linear_attn on layer 3 (full_attention layer) must be rejected
    bad_layer = tmp_path / "bad_layer.safetensors"
    _write_safetensors(
        bad_layer,
        {
            "language_model.model.layers.3.linear_attn.in_proj_qkv.lora_a": ((16, 2), 0.1),
            "language_model.model.layers.3.linear_attn.in_proj_qkv.lora_b": ((2, 32), 0.1),
        },
    )
    assert not inspect_mlx_adapter_layout(model, bad_layer)["layout_matches"]

    # 2. self_attn on layer 0 (linear_attention layer) must be rejected
    bad_self = tmp_path / "bad_self.safetensors"
    _write_safetensors(
        bad_self,
        {
            "language_model.model.layers.0.self_attn.q_proj.lora_a": ((16, 2), 0.1),
            "language_model.model.layers.0.self_attn.q_proj.lora_b": ((2, 32), 0.1),
        },
    )
    assert not inspect_mlx_adapter_layout(model, bad_self)["layout_matches"]

    # 3. q_proj without 2x attn_output_gate output dim (16 instead of 32) must be rejected
    bad_q = tmp_path / "bad_q_dim.safetensors"
    _write_safetensors(
        bad_q,
        {
            "language_model.model.layers.3.self_attn.q_proj.lora_a": ((16, 2), 0.1),
            "language_model.model.layers.3.self_attn.q_proj.lora_b": ((2, 16), 0.1),
        },
    )
    assert not inspect_mlx_adapter_layout(model, bad_q)["layout_matches"]


def test_sharded_base_resolution_ledger_signature_and_traversal_block(tmp_path):
    base = tmp_path / "sharded-base"
    base.mkdir()
    (base / "config.json").write_text("{}", encoding="utf-8")
    (base / "tokenizer.json").write_text("{}", encoding="utf-8")
    shard1 = base / "model-00001-of-00002.safetensors"
    shard2 = base / "model-00002-of-00002.safetensors"
    shard1.write_bytes(b"shard-one-v1")
    shard2.write_bytes(b"shard-two-v1")
    index_path = base / "model.safetensors.index.json"
    index_path.write_text(
        json.dumps(
            {
                "metadata": {},
                "weight_map": {
                    "language_model.model.layers.0.weight": "model-00001-of-00002.safetensors",
                    "language_model.model.layers.1.weight": "model-00002-of-00002.safetensors",
                },
            }
        ),
        encoding="utf-8",
    )

    is_sharded, files = resolve_base_weight_files(base)
    assert is_sharded is True
    assert [f.name for f in files] == [
        "model.safetensors.index.json",
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
    ]

    sig1 = compute_signature_hash(
        config={"iters": 1},
        base_model_dir=base,
        dataset_sha256="ds",
        target_hypothesis="hyp",
        code_fingerprint={"v": "1"},
    )
    shard2.write_bytes(b"shard-two-v2")
    sig2 = compute_signature_hash(
        config={"iters": 1},
        base_model_dir=base,
        dataset_sha256="ds",
        target_hypothesis="hyp",
        code_fingerprint={"v": "1"},
    )
    assert sig1 != sig2

    ledger = RunLedger(tmp_path / "runs", tmp_path / "index.jsonl")
    run = ledger.create_run(
        config={"iters": 1},
        base_model_dir=base,
        dataset_dir=tmp_path,
        target_hypothesis="sharded run manifest",
        baseline_metrics={"score": 0.5},
        frozen_holdout_id="frozen",
    )
    assert run["manifest"]["base_model"]["weights_exists"] is True

    # Shard traversal attempt must raise ValueError
    index_path.write_text(
        json.dumps({"weight_map": {"w": "../outside.safetensors"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        resolve_base_weight_files(base)


def test_sharded_base_works_with_registry_and_study_acceptance_helpers(tmp_path, monkeypatch):
    from app import active_registry as ar
    from app import study_acceptance as sa

    base = tmp_path / "sharded-base"
    base.mkdir()
    (base / "config.json").write_text(json.dumps(_qwen35_hybrid_config()), encoding="utf-8")
    (base / "tokenizer.json").write_text("{}", encoding="utf-8")
    shard1 = base / "model-00001-of-00002.safetensors"
    shard2 = base / "model-00002-of-00002.safetensors"
    shard1.write_bytes(b"shard1-content")
    shard2.write_bytes(b"shard2-content")
    idx_path = base / "model.safetensors.index.json"
    idx_path.write_text(
        json.dumps(
            {
                "weight_map": {
                    "language_model.model.layers.0.w": "model-00001-of-00002.safetensors",
                    "language_model.model.layers.3.w": "model-00002-of-00002.safetensors",
                }
            }
        ),
        encoding="utf-8",
    )

    is_sharded, weight_files = resolve_base_weight_files(base)
    assert is_sharded is True
    base_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in base.iterdir()}
    req_names = {wf.relative_to(base).as_posix() for wf in weight_files} | {"config.json", "tokenizer.json"}
    sha_fn = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    sa.identity_files(base, base_hashes, req_names, sha_fn)

    shard_digest_map = {
        wf.relative_to(base).as_posix(): hashlib.sha256(wf.read_bytes()).hexdigest()
        for wf in weight_files
    }
    expected_base_sha256 = hashlib.sha256(json.dumps(shard_digest_map, sort_keys=True).encode("utf-8")).hexdigest()

    adapter = tmp_path / "candidate-adapter"
    adapter.mkdir()
    _write_safetensors(adapter / "adapters.safetensors", _valid_qwen35_hybrid_tensors())
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")

    evidence_file = tmp_path / "dossier.json"
    evidence_file.write_text(json.dumps({"schema": "codex-study-acceptance-v1"}), encoding="utf-8")

    monkeypatch.setattr(ar, "_is_valid_evidence_payload", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        ar,
        "_compute_dossier_hashes",
        lambda payload: {
            "dossier_sha256": "d" * 64,
            "protocol_sha256": "p" * 64,
            "review_sha256": "r" * 64,
            "decision_hash": "x" * 64,
        },
        raising=False,
    )
    # Bypass full dossier schema check in this unit check to directly verify Stage 3 sharded base binding
    import app.study_acceptance as sa_mod
    monkeypatch.setattr(
        sa_mod,
        "assess_dossier",
        lambda path, **kwargs: {
            "dossier_sha256": "d" * 64,
            "protocol_sha256": "p" * 64,
            "review_sha256": "r" * 64,
            "decision_hash": "x" * 64,
        },
    )

    result = ar.validate_adapter_acceptance(
        adapter,
        {
            "semantically_accepted": True,
            "reviewed_by_codex": True,
            "evidence_path": str(evidence_file),
            "base_sha256": expected_base_sha256,
        },
        base,
    )
    assert result["base_sha256"] == expected_base_sha256

    # Tampering a shard must fail base hash verification in registry AND study_acceptance identity check
    shard2.write_bytes(b"tampered-shard2-content")
    with pytest.raises(ValueError):
        ar.validate_adapter_acceptance(
            adapter,
            {
                "semantically_accepted": True,
                "reviewed_by_codex": True,
                "evidence_path": str(evidence_file),
                "base_sha256": expected_base_sha256,
            },
            base,
        )
    with pytest.raises(ValueError):
        sa.identity_files(base, base_hashes, req_names, sha_fn)

    # Removing a shard must fail closed with FileNotFoundError
    shard2.unlink()
    with pytest.raises(FileNotFoundError):
        resolve_base_weight_files(base)
