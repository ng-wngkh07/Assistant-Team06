"""Focused unit tests for generation latency & compact auditor optimizations."""

from __future__ import annotations

import io
import json
import pathlib
import runpy
import socket
import sys
import types
from contextlib import nullcontext


def _fake_model_dir(tmp_path):
    model_dir = tmp_path / "qwen9b"
    model_dir.mkdir(exist_ok=True)
    (model_dir / "config.json").write_text(
        json.dumps(
            {
                "model_type": "qwen3_5",
                "architectures": ["Qwen3_5ForConditionalGeneration"],
                "text_config": {
                    "model_type": "qwen3_5_text",
                    "hidden_size": 4096,
                    "num_hidden_layers": 32,
                    "num_attention_heads": 16,
                    "max_position_embeddings": 262144,
                },
                "quantization": {"bits": 4, "group_size": 64, "mode": "affine"},
            }
        ),
        encoding="utf-8",
    )
    return model_dir


def test_compact_auditor_schema_and_claim_id_resolution():
    from app.reasoning_policy import (
        AUDITOR_SYSTEM_PROMPT,
        AUDITOR_SYSTEM_PROMPT_COMPACT,
        audit_draft_answer,
        build_auditor_messages,
        build_auditor_response_schema,
    )

    draft = "Stack dùng nguyên tắc LIFO [S1]. Queue dùng nguyên tắc FIFO [S1]."
    sources = {"S1": "Stack dùng nguyên tắc LIFO.\nQueue dùng nguyên tắc FIFO."}
    legacy_msgs = build_auditor_messages("Stack và Queue là gì?", draft, "", sources_map=sources)
    assert legacy_msgs[0]["content"] == AUDITOR_SYSTEM_PROMPT  # nosec B101

    compact_msgs = build_auditor_messages(
        "Stack và Queue là gì?", draft, "", sources_map=sources, compact=True
    )
    assert compact_msgs[0]["content"] == AUDITOR_SYSTEM_PROMPT_COMPACT  # nosec B101
    assert '"claims_to_verify"' in compact_msgs[1]["content"]  # nosec B101
    assert '"claim_id": 2' in compact_msgs[1]["content"]  # nosec B101

    schema = build_auditor_response_schema({"S1"}, {"S1": {"E1": "a", "E2": "b"}}, claim_count=2)
    claims_prop = schema["properties"]["claims"]
    assert claims_prop["minItems"] == 2  # nosec B101
    assert claims_prop["maxItems"] == 2  # nosec B101
    assert "claim_id" in claims_prop["items"]["properties"]  # nosec B101
    assert claims_prop["items"]["properties"]["claim_id"]["minimum"] == 1  # nosec B101
    assert claims_prop["items"]["properties"]["claim_id"]["maximum"] == 2  # nosec B101
    assert "statement" not in claims_prop["items"]["properties"]  # nosec B101

    good_out = json.dumps(
        {
            "claims": [
                {"claim_id": 1, "source_id": "S1", "evidence_id": "E1", "verdict": "supported"},
                {"claim_id": 2, "source_id": "S1", "evidence_id": "E2", "verdict": "supported"},
            ],
            "overall_verdict": "supported",
        }
    )
    status, verified = audit_draft_answer(
        good_out,
        {"S1"},
        sources,
        draft=draft,
        evidence_catalog={"S1": {"E1": "Stack dùng nguyên tắc LIFO.", "E2": "Queue dùng nguyên tắc FIFO."}},
        query="Stack và Queue là gì?",
    )
    assert status == "verified"  # nosec B101
    assert verified[0]["statement"] == "Stack dùng nguyên tắc LIFO ."  # nosec B101
    assert verified[1]["statement"] == "Queue dùng nguyên tắc FIFO ."  # nosec B101

    # Reject duplicate, boolean, and out-of-range claim_id fail-closed
    for bad_id in (True, 0, 3):
        bad_out = json.dumps(
            {
                "claims": [
                    {"claim_id": bad_id, "source_id": "S1", "evidence_id": "E1", "verdict": "supported"}
                ],
                "overall_verdict": "supported",
            }
        )
        assert audit_draft_answer(bad_out, {"S1"}, sources, draft=draft)[0] == "unverified"  # nosec B101

    dup_out = json.dumps(
        {
            "claims": [
                {"claim_id": 1, "source_id": "S1", "evidence_id": "E1", "verdict": "supported"},
                {"claim_id": 1, "source_id": "S1", "evidence_id": "E1", "verdict": "supported"},
            ],
            "overall_verdict": "supported",
        }
    )
    assert audit_draft_answer(dup_out, {"S1"}, sources, draft=draft)[0] == "unverified"  # nosec B101


def test_compact_auditor_preserves_directional_contradiction_fail_closed():
    from app.reasoning_policy import audit_draft_answer, build_evidence_catalog

    source = (
        "Queue\n"
        "- FIFO (First In First Out): First insert first remove.\n"
        "[Diagram: Queue top to bottom 5, 7, 2, 6; insertion at top labelled Last insert last remove; "
        "removal from bottom labelled First insert first remove.]"
    )
    wrong_draft = "Trong hình hàng đợi, số 5 được lấy ra đầu tiên [S1]."
    catalog = build_evidence_catalog({"S1": source})
    auditor_json = json.dumps(
        {
            "claims": [
                {"claim_id": 1, "source_id": "S1", "evidence_id": "E2", "verdict": "supported"},
            ],
            "overall_verdict": "supported",
        }
    )
    status, verified = audit_draft_answer(
        auditor_json,
        {"S1"},
        {"S1": source},
        draft=wrong_draft,
        evidence_catalog=catalog,
        query="Trong hình hàng đợi, số nào được lấy ra đầu tiên?",
    )
    assert status == "unverified"  # nosec B101
    assert verified[0]["verdict"] == "unsupported"  # nosec B101


def test_trained_client_defaults_step256_warmup_auditor_and_marker(tmp_path, monkeypatch):
    from app import trained_client
    from app.reasoning_policy import AUDITOR_SYSTEM_PROMPT_COMPACT

    monkeypatch.delenv("MLX_PREFILL_STEP_SIZE", raising=False)
    assert trained_client.TrainedModelClient._worker_env()["MLX_PREFILL_STEP_SIZE"] == "256"  # nosec B101

    monkeypatch.setenv("MLX_PREFILL_STEP_SIZE", "512")
    assert trained_client.TrainedModelClient._worker_env()["MLX_PREFILL_STEP_SIZE"] == "512"  # nosec B101
    monkeypatch.delenv("MLX_PREFILL_STEP_SIZE", raising=False)

    model_dir = _fake_model_dir(tmp_path)
    monkeypatch.setattr(trained_client, "resolve_mlx_model_dir", lambda *a: model_dir)
    monkeypatch.setattr(trained_client, "get_model_context_capacity", lambda *a: 8192)
    monkeypatch.setattr(trained_client.TrainedModelClient, "available", classmethod(lambda cls, *a: True))
    monkeypatch.setattr(trained_client.gpu_coordinator, "acquire_for_inference", lambda **kw: nullcontext())

    warmup_seen = []
    monkeypatch.setattr(
        trained_client._INFER_STREAM_WORKER,
        "request_json",
        lambda payload, **kw: warmup_seen.append(payload) or {"op": "warmup", "ok": True, "warmed": True},
    )
    res = trained_client.TrainedModelClient.warm_prefix_caches()
    assert res["warmed"] is True  # nosec B101
    assert AUDITOR_SYSTEM_PROMPT_COMPACT in warmup_seen[0]["system_prompts"]  # nosec B101
    assert warmup_seen[0]["prefill_step_size"] == 256  # nosec B101
    assert len(warmup_seen[0]["warmup_schemas"]) == 2  # nosec B101
    assert len(trained_client.TrainedModelClient._default_warmup_prompts()) == 4  # nosec B101

    stream_seen = []
    monkeypatch.setattr(trained_client, "BASE_DIR", tmp_path)

    def fake_run(*args, **kwargs):
        stream_seen.append(json.loads(kwargs["input"]))
        return types.SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({"answer": "ok", "done_reason": "stop", "input_tokens": 20, "output_tokens": 3}),
        )

    monkeypatch.setattr(trained_client.subprocess, "run", fake_run)
    client = trained_client.TrainedModelClient("qwen3.5-9b-4bit")
    assert (  # nosec B101
        "".join(
            client.chat_stream(
                [{"role": "system", "content": "SYS"}, {"role": "user", "content": "DOC\n<user_query>Q</user_query>"}],
                context_prefix_marker="\n<user_query>",
            )
        )
        == "ok"
    )
    assert stream_seen[0]["prefill_step_size"] == 256  # nosec B101
    assert stream_seen[0]["context_prefix_marker"] == "\n<user_query>"  # nosec B101


def test_mlx_infer_context_prefix_cache_reuses_system_and_bounds_slots(tmp_path, monkeypatch, capsys):
    model_dir = _fake_model_dir(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    monkeypatch.setenv("HF_DATASETS_OFFLINE", "0")

    class FakeArray:
        def __init__(self, data):
            self.data = list(data.data) if isinstance(data, FakeArray) else list(data)

        def __getitem__(self, item):
            return self if item is None else self.data[item]

    class FakeLayer:
        def __init__(self, state):
            self.state = state

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return f"<|im_start|>system\n{messages[0]['content']}<|im_end|>\n<|im_start|>user\n{messages[-1]['content']}<|im_end|>\n<|im_start|>assistant\n"

        def encode(self, text):
            return [ord(ch) for ch in text]

    tokenizer = Tokenizer()

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            return tokenizer

    class FakeSchemaProc:
        def __init__(self, tag):
            self.tag = tag
            self.clones = 0

        def clone(self):
            self.clones += 1
            return FakeSchemaProc(f"{self.tag}-clone-{self.clones}")

    compile_calls = []
    prefill_chunks = []
    gen_prompts = []

    def fake_model(arr, cache=None):
        prefill_chunks.append(len(arr.data))
        cache[0].state.extend(arr.data)

    lm = types.ModuleType("mlx_lm")
    lm.load = lambda path, **kw: (fake_model, tokenizer)

    def stream_generate(model, active_tok, *, prompt=None, prompt_cache=None, **kwargs):
        gen_prompts.append((prompt, list(prompt_cache[0].state)))
        yield types.SimpleNamespace(
            text="ok", finish_reason="stop", prompt_tokens=10, generation_tokens=2,
            prompt_tps=50.0, generation_tps=20.0, peak_memory=5.5,
        )

    lm.stream_generate = stream_generate
    sampling = types.ModuleType("mlx_lm.sample_utils")
    sampling.make_sampler = lambda **kw: kw
    sampling.make_logits_processors = lambda **kw: [kw]
    cache_mod = types.ModuleType("mlx_lm.models.cache")
    cache_mod.make_prompt_cache = lambda _m: [FakeLayer([])]
    models_mod = types.ModuleType("mlx_lm.models")
    models_mod.cache = cache_mod
    core = types.ModuleType("mlx.core")
    core.array = FakeArray
    core.eval = lambda *a, **kw: None
    core.clear_cache = lambda: None
    core.random = types.SimpleNamespace(seed=lambda value: None)
    mlx = types.ModuleType("mlx")
    mlx.core = core
    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = AutoTokenizer
    structured = types.ModuleType("mlx_vlm.structured")
    structured.build_json_schema_logits_processor = lambda tok, sch: (compile_calls.append(sch) or FakeSchemaProc("p0"))
    mlx_vlm = types.ModuleType("mlx_vlm")
    mlx_vlm.structured = structured

    for name, mod in [
        ("mlx_lm", lm),
        ("mlx_lm.sample_utils", sampling),
        ("mlx_lm.models", models_mod),
        ("mlx_lm.models.cache", cache_mod),
        ("mlx", mlx),
        ("mlx.core", core),
        ("transformers", transformers),
        ("mlx_vlm", mlx_vlm),
        ("mlx_vlm.structured", structured),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    sys_prompt = "S" * 80
    doc_a = "D" * 120
    doc_b = "B" * 120
    warmup_schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    reqs = [
        {"op": "warmup", "model_path": str(model_dir), "system_prompts": [sys_prompt], "warmup_schemas": [warmup_schema], "num_ctx": 8192},
        # Cold context A: reuses warmed system snapshot, extends only with doc_a delta
        {"model_path": str(model_dir), "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": doc_a + "\n<user_query>Q1</user_query>"}], "max_tokens": 16, "context_prefix_marker": "\n<user_query>", "response_schema": warmup_schema},
        # Exact context A with different query: hits 1-slot context_prefix_cache!
        {"model_path": str(model_dir), "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": doc_a + "\n<user_query>Q2</user_query>"}], "max_tokens": 16, "context_prefix_marker": "\n<user_query>", "response_schema": warmup_schema},
        # Context B replaces 1-slot context_prefix_cache while still reusing system snapshot
        {"model_path": str(model_dir), "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": doc_b + "\n<user_query>Q3</user_query>"}], "max_tokens": 16, "context_prefix_marker": "\n<user_query>"},
    ]
    monkeypatch.setattr(sys, "argv", ["mlx_infer.py", "--stream"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(json.dumps(r) for r in reqs) + "\n"))

    runpy.run_path(
        str(pathlib.Path(__file__).resolve().parent.parent / "app" / "mlx_infer.py"),
        run_name="__main__",
    )
    events = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip()]
    warmup_evt, q1_evt, q2_evt, q3_evt = [e for e in events if e.get("done")]

    assert warmup_evt["warmed_schemas"] == 1  # nosec B101
    assert len(compile_calls) == 1  # nosec B101
    assert q1_evt["review_cache"]["scope"] == "worker_context_prefix_kv"  # nosec B101
    assert q1_evt["review_cache"]["reused_tokens"] == warmup_evt["warmed_prefixes"][0]["prefix_tokens"]  # nosec B101
    assert q2_evt["review_cache"]["scope"] == "worker_context_prefix_kv"  # nosec B101
    assert q2_evt["review_cache"]["reused_tokens"] == q2_evt["review_cache"]["prefix_tokens"]  # nosec B101
    assert q3_evt["review_cache"]["reused_tokens"] == warmup_evt["warmed_prefixes"][0]["prefix_tokens"]  # nosec B101

    # Confirm Q2 executed zero new prefill steps and retained full cached context + suffix Q2
    assert len(prefill_chunks) == 3  # warmup(sys), Q1(delta doc_a), Q3(delta doc_b)  # nosec B101
    q1_full = gen_prompts[0][1] + gen_prompts[0][0]
    q2_full = gen_prompts[1][1] + gen_prompts[1][0]
    assert q1_full[:-len("<user_query>Q1</user_query>\n<|im_end|>\n<|im_start|>assistant\n")] == q2_full[:-len("<user_query>Q2</user_query>\n<|im_end|>\n<|im_start|>assistant\n")]  # nosec B101


def test_mlx_stream_retains_alternating_context_prefixes_and_short_system_prompts(tmp_path, monkeypatch, capsys):
    """Alternating generation/review prefixes must not evict each other, and fallback works when prepare_review_cache returns empty."""
    model_dir = _fake_model_dir(tmp_path)
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    monkeypatch.setenv("HF_DATASETS_OFFLINE", "0")

    prefill_chunks = []

    class FakeArray:
        def __init__(self, data):
            self.data = list(data.data) if isinstance(data, FakeArray) else list(data)

        def __getitem__(self, item):
            return self if item is None else self.data[item]

    class FakeLayer:
        def __init__(self, state=None):
            self.state = list(state or [])

    class FakeModel:
        def __call__(self, arr, cache=None):
            prefill_chunks.append(list(arr.data))
            if cache:
                base = list(cache[0].state or [])
                cache[0].state = base + list(arr.data)

    class Tok:
        bos_token = None

        def encode(self, text, add_special_tokens=True):
            return [ord(ch) for ch in str(text)]

        def apply_chat_template(self, messages, tokenize=False, **kwargs):
            sys_txt = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
            usr_txt = messages[-1]["content"]
            return f"<|im_start|>system\n{sys_txt}<|im_end|>\n<|im_start|>user\n{usr_txt}<|im_end|>\n<|im_start|>assistant\n"

    tok = Tok()

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            return tok

    lm = types.ModuleType("mlx_lm")
    lm.load = lambda path, **kw: (FakeModel(), tok)
    lm.stream_generate = lambda model, active_tok, *, prompt=None, prompt_cache=None, **kw: iter([
        types.SimpleNamespace(text="ok", finish_reason="stop", prompt_tokens=10, generation_tokens=2, prompt_tps=50.0, generation_tps=20.0, peak_memory=5.5)
    ])
    sampling = types.ModuleType("mlx_lm.sample_utils")
    sampling.make_sampler = lambda **kw: kw
    sampling.make_logits_processors = lambda **kw: [kw]
    cache_mod = types.ModuleType("mlx_lm.models.cache")
    cache_mod.make_prompt_cache = lambda _m: [FakeLayer([])]
    models_mod = types.ModuleType("mlx_lm.models")
    models_mod.cache = cache_mod
    core = types.ModuleType("mlx.core")
    core.array = FakeArray
    core.eval = lambda *a, **kw: None
    core.clear_cache = lambda: None
    core.random = types.SimpleNamespace(seed=lambda value: None)
    mlx = types.ModuleType("mlx")
    mlx.core = core
    transformers = types.ModuleType("transformers")
    transformers.AutoTokenizer = AutoTokenizer
    for name, mod in [
        ("mlx_lm", lm), ("mlx_lm.sample_utils", sampling), ("mlx_lm.models", models_mod),
        ("mlx_lm.models.cache", cache_mod), ("mlx", mlx), ("mlx.core", core), ("transformers", transformers),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    short_sys = "S" * 24  # >= 16 tokens, < 64 tokens
    gen_ctx = "P" * 90 + "\n<task>\n"
    rev_ctx = "R" * 90 + "\nDữ liệu cần kiểm định:\n"
    review_dir = str(tmp_path / "rev_cache")
    reqs = [
        {"model_path": str(model_dir), "messages": [{"role": "system", "content": short_sys}, {"role": "user", "content": gen_ctx + "q1"}], "max_tokens": 16, "context_prefix_marker": "\n<task>\n", "review_cache_dir": review_dir},
        {"model_path": str(model_dir), "messages": [{"role": "system", "content": short_sys}, {"role": "user", "content": rev_ctx + "r1"}], "max_tokens": 16, "context_prefix_marker": "\nDữ liệu cần kiểm định:\n", "review_cache_dir": review_dir},
        {"model_path": str(model_dir), "messages": [{"role": "system", "content": short_sys}, {"role": "user", "content": gen_ctx + "flash"}], "max_tokens": 16, "context_prefix_marker": "\n<task>\n", "review_cache_dir": review_dir},
    ]
    monkeypatch.setattr(sys, "argv", ["mlx_infer.py", "--stream"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(json.dumps(r) for r in reqs) + "\n"))
    runpy.run_path(str(pathlib.Path(__file__).resolve().parent.parent / "app" / "mlx_infer.py"), run_name="__main__")
    events = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip()]
    errors = [e for e in events if e.get("error")]
    assert not errors, f"Unexpected stream errors: {errors}"
    done = [e for e in events if e.get("done")]
    assert len(done) == 3
    assert done[1]["review_cache"]["scope"] == "worker_context_prefix_kv"
    assert done[2]["review_cache"]["reused_tokens"] == done[2]["review_cache"]["prefix_tokens"] > 64
