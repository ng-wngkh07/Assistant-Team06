"""Run one local MLX inference from JSON stdin (in the Python 3.13 training venv)."""

from collections import OrderedDict
import gc
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

import mlx.core as mx

from mlx_lm import stream_generate, load
from mlx_lm.sample_utils import make_logits_processors, make_sampler


def _clone_cache_value(val):
    if isinstance(val, mx.array):
        return mx.array(val)
    if isinstance(val, list):
        return [_clone_cache_value(item) for item in val]
    if isinstance(val, tuple):
        return tuple(_clone_cache_value(item) for item in val)
    return val


def _clone_prompt_cache(model, src_cache):
    from mlx_lm.models.cache import make_prompt_cache

    dst_cache = make_prompt_cache(model)
    for src_layer, dst_layer in zip(src_cache, dst_cache):
        dst_layer.state = _clone_cache_value(src_layer.state)
    return dst_cache


def _system_prefix_tokens(tokenizer, messages, full_prompt: str, full_tokens: list[int]) -> list[int]:
    if not messages or messages[0].get("role") != "system":
        return []
    marker = "<|im_start|>user\n"
    if marker not in full_prompt:
        return []
    sys_prefix = full_prompt.split(marker, 1)[0] + marker
    try:
        pref_tokens = tokenizer.encode(sys_prefix)
    except Exception:
        return []
    limit = min(len(pref_tokens), len(full_tokens) - 1)
    matched = 0
    for left, right in zip(pref_tokens[:limit], full_tokens[:limit]):
        if left != right:
            break
        matched += 1
    if matched < 64:
        return []
    return list(full_tokens[:matched])


def _resolve_prefill_step_size(request: dict | None = None) -> int:
    raw = request.get("prefill_step_size") if isinstance(request, dict) else None
    if raw is None:
        raw = os.getenv("MLX_PREFILL_STEP_SIZE")
    if raw is None or str(raw).strip() == "":
        return 128
    try:
        step = int(raw)
    except (TypeError, ValueError):
        return 128
    return max(64, min(1024, step))


def _prepare_stream_prefix_cache(
    model,
    tokenizer,
    prompt: str,
    tokens: list[int],
    request: dict,
    prefix_cache_store: OrderedDict,
    *,
    clone: bool = True,
    prefill_step_size: int = 128,
):
    pref_tokens = _system_prefix_tokens(tokenizer, request.get("messages"), prompt, tokens)
    if not pref_tokens:
        return prompt, {}, {"reused_tokens": 0}
    try:
        from mlx_lm.models.cache import make_prompt_cache
    except Exception:
        return prompt, {}, {"reused_tokens": 0}

    key = (
        str(request.get("model_path", "")),
        str(request.get("adapter_path") or ""),
        tuple(pref_tokens),
    )
    cached = prefix_cache_store.get(key)
    reused = cached is not None
    if cached is not None:
        prefix_cache_store.move_to_end(key)
        snap = cached
    else:
        snap = make_prompt_cache(model)
        n_pref = len(pref_tokens)
        step = max(64, int(prefill_step_size))
        for off in range(0, n_pref, step):
            model(mx.array(pref_tokens[off:min(off + step, n_pref)])[None], cache=snap)
            mx.eval([layer.state for layer in snap])
        prefix_cache_store[key] = snap
        prefix_cache_store.move_to_end(key)
        while len(prefix_cache_store) > 2:
            prefix_cache_store.popitem(last=False)
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()

    diagnostics = {
        "reused_tokens": len(pref_tokens) if reused else 0,
        "prefix_tokens": len(pref_tokens),
        "scope": "worker_system_prefix_kv",
        "warmed": not reused,
    }
    if not clone:
        return tokens[len(pref_tokens):], {}, diagnostics

    cloned = _clone_prompt_cache(model, snap)
    return tokens[len(pref_tokens):], {"prompt_cache": cloned}, diagnostics


def _validate_model_and_capacity(request: dict, state: dict | None) -> tuple[Path, Path | None, dict, int]:
    model_path = Path(request["model_path"])
    adapter_path = Path(request["adapter_path"]) if request.get("adapter_path") else None
    if not model_path.exists() or (adapter_path and not (adapter_path / "adapters.safetensors").exists()):
        raise FileNotFoundError("Chưa có mô hình hoặc adapter LoRA đã huấn luyện")

    # Read config to get model context capacity (missing/malformed config fails closed)
    config_path = model_path / "config.json"
    if not config_path.exists():
        raise ValueError(f"Không tìm thấy cấu hình mô hình tại {config_path}")
    try:
        model_key_cfg = str(model_path)
        cfg = state["cfg"].get(model_key_cfg) if state is not None else None
        if cfg is None:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            from app.model_resolver import validate_native_model_config
            validate_native_model_config(model_path)
            if state is not None:
                state["cfg"][model_key_cfg] = cfg
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

    return model_path, adapter_path, cfg, model_capacity


def _validate_adapter_acceptance(model_path: Path, adapter_path: Path | None, request: dict) -> tuple[dict | None, float]:
    adapter_acceptance = None
    validation_started = time.perf_counter()
    if adapter_path is not None:
        from app.practice_adapter import (
            load_practice_adapter, request_matches_scope,
            request_matches_grounded_adapter_scope, GROUNDED_REVIEW_PROFILE,
        )
        adapter_acceptance = load_practice_adapter(model_path, require_active=False)
        matches_scope = (request_matches_grounded_adapter_scope
                         if adapter_acceptance and adapter_acceptance.get('review_profile') == GROUNDED_REVIEW_PROFILE
                         else request_matches_scope)
        if (adapter_acceptance is None or
                adapter_path.resolve() != Path(adapter_acceptance["path"]).resolve() or
                not matches_scope(request)):
            raise ValueError("Adapter chưa được nghiệm thu cho loại yêu cầu này.")
        request["seed"] = 42
    return adapter_acceptance, time.perf_counter() - validation_started


def _resolve_num_ctx(request: dict, model_capacity: int) -> int:
    num_ctx_req = request.get("num_ctx")
    try:
        num_ctx = int(num_ctx_req) if num_ctx_req is not None else 8192
    except (TypeError, ValueError):
        raise ValueError("num_ctx không hợp lệ")
    if num_ctx <= 0:
        raise ValueError(f"num_ctx phải là số nguyên dương, nhận: {num_ctx}")
    return min(num_ctx, model_capacity)


def _get_offline_tokenizer(model_path: Path, state: dict | None):
    try:
        from transformers import AutoTokenizer
        tok_key = str(model_path)
        tokenizer = state["tok"].get(tok_key) if state is not None else None
        if tokenizer is None:
            tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)
            if state is not None:
                state["tok"][tok_key] = tokenizer
        return tokenizer
    except Exception as exc:
        raise ValueError("Không thể tải hoặc xác minh tokenizer của mô hình") from exc


def _ensure_loaded_model(model_path: Path, adapter_path: Path | None, state: dict | None):
    load_started = time.perf_counter()
    model_load_key = (str(model_path), str(adapter_path) if adapter_path else "")
    if state is not None and state.get("model_key") == model_load_key and state.get("model") is not None:
        return state["model"], state["loaded_tok"], 0.0
    if state is not None and state.get("model") is not None:
        state["model"] = None
        state["loaded_tok"] = None
        state["model_key"] = None
        state["prefix_caches"].clear()
        if "schema_processors" in state:
            state["schema_processors"].clear()
        gc.collect()
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
    model, loaded_tokenizer = load(str(model_path), adapter_path=str(adapter_path) if adapter_path else None)
    load_seconds = time.perf_counter() - load_started
    if state is not None:
        state["model_key"] = model_load_key
        state["model"] = model
        state["loaded_tok"] = loaded_tokenizer
    return model, loaded_tokenizer, load_seconds


def _run_warmup(request: dict, state: dict | None = None) -> dict:
    started = time.perf_counter()
    model_path, adapter_path, cfg, model_capacity = _validate_model_and_capacity(request, state)
    adapter_acceptance, _ = _validate_adapter_acceptance(model_path, adapter_path, request)
    context_limit = _resolve_num_ctx(request, model_capacity)
    tokenizer = _get_offline_tokenizer(model_path, state)

    raw_prompts = request.get("system_prompts")
    if raw_prompts is None:
        raw_prompts = []
    if not isinstance(raw_prompts, list):
        raise ValueError("system_prompts phải là danh sách chuỗi.")

    seen = set()
    prepared_prompts = []
    for item in raw_prompts:
        if not isinstance(item, str):
            raise ValueError("Mỗi phần tử trong system_prompts phải là chuỗi.")
        if not item.strip() or item in seen:
            continue
        seen.add(item)
        sample_messages = [
            {"role": "system", "content": item},
            {"role": "user", "content": "x"},
        ]
        try:
            formatted = tokenizer.apply_chat_template(
                sample_messages,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=False,
            )
            encoded = list(tokenizer.encode(formatted))
        except Exception as exc:
            raise ValueError("Không thể mã hóa system_prompt bằng tokenizer của mô hình") from exc
        if len(encoded) >= context_limit:
            raise ValueError(
                f"System prompt warmup ({len(encoded)} tokens) vượt quá giới hạn ngữ cảnh {context_limit}."
            )
        prepared_prompts.append((sample_messages, formatted, encoded))

    model, loaded_tokenizer, load_seconds = _ensure_loaded_model(model_path, adapter_path, state)
    active_tok = loaded_tokenizer or tokenizer
    prefix_cache_store = state["prefix_caches"] if state is not None else OrderedDict()
    prefill_step_size = _resolve_prefill_step_size(request)

    cache_started = time.perf_counter()
    warmed_prefixes = []
    for sample_messages, formatted, encoded in prepared_prompts:
        active_tokens = encoded if active_tok is tokenizer else list(active_tok.encode(formatted))
        _, _, diag = _prepare_stream_prefix_cache(
            model,
            active_tok,
            formatted,
            active_tokens,
            {
                "model_path": str(model_path),
                "adapter_path": str(adapter_path) if adapter_path else "",
                "messages": sample_messages,
            },
            prefix_cache_store,
            clone=False,
            prefill_step_size=prefill_step_size,
        )
        warmed_prefixes.append(diag)
    cache_seconds = time.perf_counter() - cache_started
    base_identity = cfg.get("_name_or_path") or model_path.name

    return {
        "op": "warmup",
        "ok": True,
        "warmed": True,
        "base_model": str(base_identity),
        "adapter_path": str(adapter_path) if adapter_path else None,
        "adapter_sha256": adapter_acceptance["sha256"] if adapter_acceptance else None,
        "context_limit": context_limit,
        "warmed_prefixes": warmed_prefixes,
        "timings": {
            "load_seconds": load_seconds,
            "cache_seconds": cache_seconds,
            "worker_seconds": time.perf_counter() - started,
        },
    }


def _run_request(
    request: dict,
    *,
    stream_tokens: bool = False,
    state: dict | None = None,
) -> dict:
    started = time.perf_counter()
    model_path, adapter_path, cfg, model_capacity = _validate_model_and_capacity(request, state)
    adapter_acceptance, adapter_validation_seconds = _validate_adapter_acceptance(model_path, adapter_path, request)

    try:
        max_tokens = int(request.get("max_tokens", 700))
    except (TypeError, ValueError):
        raise ValueError("max_tokens không hợp lệ")
    if max_tokens <= 0:
        raise ValueError(f"max_tokens phải là số nguyên dương, nhận: {max_tokens}")

    context_limit = _resolve_num_ctx(request, model_capacity)
    tokenizer = _get_offline_tokenizer(model_path, state)

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

    model, loaded_tokenizer, load_seconds = _ensure_loaded_model(model_path, adapter_path, state)
    active_tok = loaded_tokenizer or tokenizer

    seed = request.get("seed")
    if seed is not None:
        mx.random.seed(int(seed))

    temperature = request.get("temperature")
    prefill_step_size = _resolve_prefill_step_size(request)
    # Bounded prefill step size keeps transient Metal activation memory low on 16GB Macs.
    # All prompt tokens remain present; this does not rotate or truncate context.
    gen_kwargs = {"max_tokens": max_tokens, "prefill_step_size": prefill_step_size}
    # Qwen3.5's non-thinking profile uses presence penalty 1.5 for ordinary QA.
    # For grammar-constrained structured schema generation, disable presence penalty
    # (zero) so recurring JSON keys and booleans are not penalized.
    response_schema = request.get("response_schema")
    if response_schema is not None:
        if not isinstance(response_schema, dict):
            raise ValueError("response_schema phải là JSON object.")
        presence_penalty = 0.0
    else:
        presence_penalty = 1.5

    gen_kwargs["sampler"] = make_sampler(
        temp=float(temperature) if temperature is not None else 0.7,
        top_p=0.8,
        top_k=20,
    )
    gen_kwargs["logits_processors"] = make_logits_processors(
        presence_penalty=presence_penalty, presence_context_size=2048,
    )
    schema_compile_started = time.perf_counter()
    if response_schema is not None:
        from mlx_vlm.structured import build_json_schema_logits_processor
        schema_proc = None
        schema_cache = state.get("schema_processors") if state is not None else None
        schema_key = None
        if schema_cache is not None:
            schema_key = (
                id(tokenizer),
                json.dumps(response_schema, sort_keys=True, ensure_ascii=False),
            )
            cached_proc = schema_cache.get(schema_key)
            if cached_proc is not None:
                schema_cache.move_to_end(schema_key)
                schema_proc = cached_proc.clone()
        if schema_proc is None:
            compiled = build_json_schema_logits_processor(tokenizer, response_schema)
            if schema_cache is not None and callable(getattr(compiled, "clone", None)):
                schema_cache[schema_key] = compiled
                schema_cache.move_to_end(schema_key)
                while len(schema_cache) > 8:
                    schema_cache.popitem(last=False)
                schema_proc = compiled.clone()
            else:
                schema_proc = compiled
        gen_kwargs["logits_processors"].append(schema_proc)
    schema_compile_seconds = time.perf_counter() - schema_compile_started

    cache_started = time.perf_counter()
    from app.mlx_review_cache import prepare_review_cache
    generation_prompt, cache_kwargs, cache_diagnostics = prepare_review_cache(
        model, active_tok, prompt, request, adapter_acceptance)
    if (
        not cache_kwargs
        and state is not None
        and not request.get("review_cache_dir")
    ):
        active_prompt_tokens = (
            list(prompt_tokens)
            if active_tok is tokenizer
            else list(active_tok.encode(prompt))
        )
        generation_prompt, cache_kwargs, cache_diagnostics = _prepare_stream_prefix_cache(
            model,
            active_tok,
            prompt,
            active_prompt_tokens,
            request,
            state["prefix_caches"],
            prefill_step_size=prefill_step_size,
        )
    gen_kwargs.update(cache_kwargs)
    cache_seconds = time.perf_counter() - cache_started

    generation_started = time.perf_counter()
    first_token_seconds = None
    last_chunk = None
    parts = []
    for chunk in stream_generate(model, active_tok, prompt=generation_prompt, **gen_kwargs):
        if first_token_seconds is None:
            first_token_seconds = time.perf_counter() - generation_started
        text_piece = chunk.text
        parts.append(text_piece)
        last_chunk = chunk
        if stream_tokens and text_piece:
            print(json.dumps({"token": text_piece}, ensure_ascii=False), flush=True)
    answer = "".join(parts)
    done_reason = getattr(last_chunk, "finish_reason", None)
    if done_reason not in {"stop", "length"}:
        done_reason = None
    output_tokens = getattr(last_chunk, "generation_tokens", len(active_tok.encode(answer)))

    generation_seconds = time.perf_counter() - generation_started

    is_truncated = (done_reason == "length")

    base_identity = cfg.get("_name_or_path") or model_path.name
    has_adapter = bool(adapter_path and (adapter_path / "adapters.safetensors").exists())

    return {
        "answer": answer,
        "is_truncated": is_truncated,
        "done_reason": done_reason,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "context_limit": context_limit,
        "base_model": str(base_identity),
        "adapter_loaded": has_adapter,
        "adapter_path": str(adapter_path) if has_adapter else None,
        "adapter_sha256": adapter_acceptance["sha256"] if adapter_acceptance else None,
        "adapter_scope": adapter_acceptance["scope"] if adapter_acceptance else None,
        "adapter_review_profile": adapter_acceptance.get("review_profile") if adapter_acceptance else None,
        "review_cache": cache_diagnostics,
        "generation_profile": {"presence_penalty": presence_penalty, "presence_context_size": 2048},
        "timings": {
            "load_seconds": load_seconds,
            "schema_compile_seconds": schema_compile_seconds,
            "review_cache_seconds": cache_seconds,
            "adapter_validation_seconds": adapter_validation_seconds,
            "first_token_seconds": first_token_seconds,
            "generation_seconds": generation_seconds,
            "worker_seconds": time.perf_counter() - started,
            "prompt_tokens_per_second": getattr(last_chunk, "prompt_tps", None),
            "generation_tokens_per_second": getattr(last_chunk, "generation_tps", None),
            "peak_memory_gb": getattr(last_chunk, "peak_memory", None),
        },
    }


def serve_stream():
    state = {
        "cfg": {},
        "tok": {},
        "model_key": None,
        "model": None,
        "loaded_tok": None,
        "prefix_caches": OrderedDict(),
        "schema_processors": OrderedDict(),
    }
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Payload phải là JSON object.")
            if request.get("op") == "warmup":
                payload = _run_warmup(request, state=state)
            else:
                payload = _run_request(request, stream_tokens=True, state=state)
            payload["done"] = True
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
        finally:
            gc.collect()
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()


def main():
    if "--stream" in sys.argv[1:]:
        serve_stream()
        return
    request = json.load(sys.stdin)
    if isinstance(request, dict) and request.get("op") == "warmup":
        payload = _run_warmup(request, state=None)
    else:
        payload = _run_request(request, stream_tokens=False, state=None)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
