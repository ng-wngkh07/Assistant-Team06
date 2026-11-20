"""Run one local MLX inference from JSON stdin (in the Python 3.13 training venv)."""

from collections import OrderedDict
import gc
import json
import os
import re
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


def _prefix_tokens_for_split(tokenizer, prefix_text: str, full_tokens: list[int]) -> list[int]:
    try:
        pref_tokens = tokenizer.encode(prefix_text)
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


def _system_prefix_tokens(tokenizer, messages, full_prompt: str, full_tokens: list[int]) -> list[int]:
    if not messages or messages[0].get("role") != "system":
        return []
    marker = "<|im_start|>user\n"
    if marker not in full_prompt:
        return []
    sys_prefix = full_prompt.split(marker, 1)[0] + marker
    return _prefix_tokens_for_split(tokenizer, sys_prefix, full_tokens)


def _context_prefix_tokens(
    tokenizer,
    request: dict | None,
    full_prompt: str,
    full_tokens: list[int],
    *,
    min_len: int = 64,
) -> list[int]:
    marker = request.get("context_prefix_marker") if isinstance(request, dict) else None
    if not isinstance(marker, str) or not marker:
        return []
    idx = full_prompt.find(marker)
    if idx <= 0:
        return []
    split_at = idx + 1 if marker.startswith("\n") else idx
    pref = _prefix_tokens_for_split(tokenizer, full_prompt[:split_at], full_tokens)
    return pref if len(pref) >= max(64, int(min_len)) else []


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


def _fill_prefix_snapshot(
    model,
    pref_tokens: list[int],
    *,
    prefill_step_size: int,
    base_snap=None,
    base_len: int = 0,
):
    from mlx_lm.models.cache import make_prompt_cache

    if base_snap is not None and 0 < base_len < len(pref_tokens):
        snap = _clone_prompt_cache(model, base_snap)
        start = base_len
    else:
        snap = make_prompt_cache(model)
        start = 0
    n_pref = len(pref_tokens)
    step = max(64, int(prefill_step_size))
    for off in range(start, n_pref, step):
        model(mx.array(pref_tokens[off:min(off + step, n_pref)])[None], cache=snap)
        mx.eval([layer.state for layer in snap])
    return snap


def _store_lru_snapshot(cache_store: OrderedDict, key, snap, *, max_entries: int) -> None:
    cache_store[key] = snap
    cache_store.move_to_end(key)
    evicted = False
    while len(cache_store) > max_entries:
        cache_store.popitem(last=False)
        evicted = True
    if evicted or max_entries <= 1:
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()


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
    context_prefix_store: OrderedDict | None = None,
):
    sys_tokens = _system_prefix_tokens(tokenizer, request.get("messages"), prompt, tokens)
    ctx_tokens = (
        _context_prefix_tokens(
            tokenizer,
            request,
            prompt,
            tokens,
            min_len=len(sys_tokens) + 32,
        )
        if context_prefix_store is not None
        else []
    )
    if not sys_tokens and not ctx_tokens:
        return prompt, {}, {"reused_tokens": 0}
    try:
        from mlx_lm.models.cache import make_prompt_cache  # noqa: F401
    except Exception:
        return prompt, {}, {"reused_tokens": 0}

    model_id_key = (
        str(request.get("model_path", "")),
        str(request.get("adapter_path") or ""),
    )

    if ctx_tokens and context_prefix_store is not None:
        ctx_key = (*model_id_key, tuple(ctx_tokens))
        cached_ctx = context_prefix_store.get(ctx_key)
        if cached_ctx is not None:
            context_prefix_store.move_to_end(ctx_key)
            snap = cached_ctx
            diagnostics = {
                "reused_tokens": len(ctx_tokens),
                "prefix_tokens": len(ctx_tokens),
                "scope": "worker_context_prefix_kv",
                "warmed": False,
            }
        else:
            sys_snap = None
            sys_reused = False
            if sys_tokens:
                sys_key = (*model_id_key, tuple(sys_tokens))
                sys_snap = prefix_cache_store.get(sys_key)
                if sys_snap is not None:
                    prefix_cache_store.move_to_end(sys_key)
                    sys_reused = True
                else:
                    sys_snap = _fill_prefix_snapshot(
                        model,
                        sys_tokens,
                        prefill_step_size=prefill_step_size,
                    )
                    _store_lru_snapshot(prefix_cache_store, sys_key, sys_snap, max_entries=10)
            snap = _fill_prefix_snapshot(
                model,
                ctx_tokens,
                prefill_step_size=prefill_step_size,
                base_snap=sys_snap,
                base_len=len(sys_tokens) if sys_snap is not None else 0,
            )
            _store_lru_snapshot(context_prefix_store, ctx_key, snap, max_entries=1)
            diagnostics = {
                "reused_tokens": len(sys_tokens) if sys_reused else 0,
                "prefix_tokens": len(ctx_tokens),
                "scope": "worker_context_prefix_kv",
                "warmed": True,
            }
        if not clone:
            return tokens[len(ctx_tokens):], {}, diagnostics
        cloned = _clone_prompt_cache(model, snap)
        return tokens[len(ctx_tokens):], {"prompt_cache": cloned}, diagnostics

    key = (*model_id_key, tuple(sys_tokens))
    cached = prefix_cache_store.get(key)
    reused = cached is not None
    if cached is not None:
        prefix_cache_store.move_to_end(key)
        snap = cached
    else:
        snap = _fill_prefix_snapshot(
            model,
            sys_tokens,
            prefill_step_size=prefill_step_size,
        )
        _store_lru_snapshot(prefix_cache_store, key, snap, max_entries=10)

    diagnostics = {
        "reused_tokens": len(sys_tokens) if reused else 0,
        "prefix_tokens": len(sys_tokens),
        "scope": "worker_system_prefix_kv",
        "warmed": not reused,
    }
    if not clone:
        return tokens[len(sys_tokens):], {}, diagnostics

    cloned = _clone_prompt_cache(model, snap)
    return tokens[len(sys_tokens):], {"prompt_cache": cloned}, diagnostics


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


def _warmup_matches_qa_prompts(request: dict) -> bool:
    if request.get("op") != "warmup":
        return False
    raw_prompts = request.get("system_prompts")
    if not isinstance(raw_prompts, list) or not raw_prompts:
        return False
    from app.qa_prompts import qa_system_prompt_matches

    saw_prompt = False
    for item in raw_prompts:
        if not isinstance(item, str) or not item.strip():
            continue
        saw_prompt = True
        if not qa_system_prompt_matches(item):
            return False
    return saw_prompt


def _warmup_matches_planner_prompts(request: dict) -> bool:
    if request.get("op") != "warmup":
        return False
    raw_prompts = request.get("system_prompts")
    if not isinstance(raw_prompts, list) or not raw_prompts:
        return False
    from app.planner_prompts import planner_system_prompt_matches

    saw_prompt = False
    for item in raw_prompts:
        if not isinstance(item, str) or not item.strip():
            continue
        saw_prompt = True
        if not planner_system_prompt_matches(item):
            return False
    return saw_prompt


def _validate_adapter_acceptance(model_path: Path, adapter_path: Path | None, request: dict) -> tuple[dict | None, float]:
    adapter_acceptance = None
    validation_started = time.perf_counter()
    if adapter_path is not None:
        from app.qa_adapter import load_qa_adapter, request_matches_qa_adapter_scope
        qa_acceptance = load_qa_adapter(model_path, require_active=False)
        if qa_acceptance is not None and adapter_path.resolve() == Path(qa_acceptance["path"]).resolve():
            if not (request_matches_qa_adapter_scope(request) or _warmup_matches_qa_prompts(request)):
                raise ValueError("Adapter chưa được nghiệm thu cho loại yêu cầu này.")
            request["seed"] = 42
            return qa_acceptance, time.perf_counter() - validation_started

        from app.planner_adapter import load_planner_adapter, request_matches_planner_adapter_scope
        planner_acceptance = load_planner_adapter(model_path, require_active=False)
        if planner_acceptance is not None and adapter_path.resolve() == Path(planner_acceptance["path"]).resolve():
            if not (request_matches_planner_adapter_scope(request) or _warmup_matches_planner_prompts(request)):
                raise ValueError("Adapter chưa được nghiệm thu cho loại yêu cầu này.")
            request["seed"] = 42
            return planner_acceptance, time.perf_counter() - validation_started

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
            tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True, trust_remote_code=False)  # nosec B615
            if state is not None:
                state["tok"][tok_key] = tokenizer
        return tokenizer
    except Exception as exc:
        raise ValueError("Không thể tải hoặc xác minh tokenizer của mô hình") from exc


def _collect_lora_slots(model) -> list[tuple[object, str, object, object]]:
    named = getattr(model, "named_modules", None)
    if not callable(named):
        return []
    slots: list[tuple[object, str, object, object]] = []
    for name, mod in named():
        if not name:
            continue
        base_linear = getattr(mod, "linear", None)
        if base_linear is None or not (hasattr(mod, "lora_a") and hasattr(mod, "lora_b")):
            continue
        if "." in name:
            parent_path, attr = name.rsplit(".", 1)
            parent = model
            for part in parent_path.split("."):
                if isinstance(parent, list) or (isinstance(parent, dict) and part not in parent):
                    parent = parent[int(part)] if part.isdigit() and isinstance(parent, list) else None
                elif isinstance(parent, dict):
                    parent = parent.get(part)
                else:
                    parent = getattr(parent, part, None)
                if parent is None:
                    break
        else:
            parent, attr = model, name
        if parent is not None and hasattr(parent, attr):
            slots.append((parent, attr, mod, base_linear))
    return slots


def _toggle_lora_slots(bindings: dict, *, enable: bool) -> None:
    for parent, attr, lora_mod, base_linear in bindings["slots"]:
        setattr(parent, attr, lora_mod if enable else base_linear)
    bindings["active"] = enable


def _restore_base_slots(state: dict) -> None:
    """Restore any attached LoRA modules back to their underlying base linears in-memory."""
    for parent, attr, base_linear in state.get("base_slots", {}).values():
        setattr(parent, attr, base_linear)


def _record_lora_bank(
    state: dict,
    *,
    model_path: Path,
    adapter_str: str,
    adapter_sha256: str,
    slots: list[tuple[object, str, object, object]],
    max_banks: int = 4,
) -> None:
    """Cache LoRA slot bindings and base linears for O(1) multi-adapter hot-swapping."""
    base_slots = state.setdefault("base_slots", {})
    for parent, attr, _, base_linear in slots:
        base_slots[(id(parent), attr)] = (parent, attr, base_linear)
    banks = state.setdefault("lora_banks", OrderedDict())
    bank_key = (str(model_path), adapter_str, adapter_sha256)
    banks[bank_key] = [(parent, attr, lora_mod) for parent, attr, lora_mod, _ in slots]
    banks.move_to_end(bank_key)
    while len(banks) > max_banks:
        banks.popitem(last=False)
    state["lora_bindings"] = {
        "model_path": str(model_path),
        "adapter_path": adapter_str,
        "adapter_sha256": adapter_sha256,
        "slots": slots,
        "active": True,
    }


def _ensure_loaded_model(
    model_path: Path,
    adapter_path: Path | None,
    state: dict | None,
    *,
    adapter_sha256: str | None = None,
):
    load_started = time.perf_counter()
    adapter_str = str(adapter_path) if adapter_path else ""
    model_load_key = (str(model_path), adapter_str)
    bindings = state.get("lora_bindings") if state is not None else None
    banks = state.get("lora_banks") if state is not None else None

    if state is not None and state.get("model") is not None:
        loaded_base_path = state.get("model_key", (None, None))[0]
        if state.get("model_key") == model_load_key:
            if not (adapter_path and adapter_sha256 and bindings and bindings.get("adapter_sha256") != adapter_sha256):
                if bindings and bindings.get("model_path") == str(model_path):
                    _toggle_lora_slots(bindings, enable=bool(adapter_path))
                return state["model"], state["loaded_tok"], 0.0

        # Switch in-memory between unmodified base linears and multiple verified LoRA banks
        # without reloading 5.5GB base weights from disk or discarding warmed prefix KV caches.
        if loaded_base_path == str(model_path):
            if adapter_path is None:
                _restore_base_slots(state)
                if bindings and bindings.get("slots"):
                    _toggle_lora_slots(bindings, enable=False)
                state["model_key"] = model_load_key
                if "context_prefix_cache" in state:
                    state["context_prefix_cache"].clear()
                return state["model"], state["loaded_tok"], 0.0

            if adapter_sha256 and banks is not None:
                bank_key = (str(model_path), adapter_str, adapter_sha256)
                cached_slots = banks.get(bank_key)
                if cached_slots:
                    _restore_base_slots(state)
                    for parent, attr, lora_mod in cached_slots:
                        setattr(parent, attr, lora_mod)
                    banks.move_to_end(bank_key)
                    state["model_key"] = model_load_key
                    if "context_prefix_cache" in state:
                        state["context_prefix_cache"].clear()
                    return state["model"], state["loaded_tok"], 0.0

                try:
                    from mlx_lm.utils import load_adapters as _load_mlx_adapters
                    _restore_base_slots(state)
                    _load_mlx_adapters(state["model"], adapter_str)
                    new_slots = _collect_lora_slots(state["model"])
                    if new_slots:
                        _record_lora_bank(
                            state,
                            model_path=model_path,
                            adapter_str=adapter_str,
                            adapter_sha256=adapter_sha256,
                            slots=new_slots,
                        )
                        state["model_key"] = model_load_key
                        if "context_prefix_cache" in state:
                            state["context_prefix_cache"].clear()
                        return state["model"], state["loaded_tok"], time.perf_counter() - load_started
                except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    sys.stderr.write(f"[mlx_infer] LoRA hot-swap fallback: {exc}\n")

        state["model"] = None
        state["loaded_tok"] = None
        state["model_key"] = None
        state["lora_bindings"] = None
        if "base_slots" in state:
            state["base_slots"].clear()
        if "lora_banks" in state:
            state["lora_banks"].clear()
        if "prefix_caches" in state:
            state["prefix_caches"].clear()
        if "context_prefix_cache" in state:
            state["context_prefix_cache"].clear()
        if "schema_processors" in state:
            state["schema_processors"].clear()
        gc.collect()
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()

    model, loaded_tokenizer = load(str(model_path), adapter_path=adapter_str if adapter_path else None)
    load_seconds = time.perf_counter() - load_started
    if state is not None:
        state["model_key"] = model_load_key
        state["model"] = model
        state["loaded_tok"] = loaded_tokenizer
        if adapter_path and adapter_sha256:
            slots = _collect_lora_slots(model)
            if slots:
                _record_lora_bank(
                    state,
                    model_path=model_path,
                    adapter_str=adapter_str,
                    adapter_sha256=adapter_sha256,
                    slots=slots,
                )
            else:
                state["lora_bindings"] = None
        else:
            state["lora_bindings"] = None
    return model, loaded_tokenizer, load_seconds


def _get_or_compile_schema_processor(
    tokenizer,
    response_schema: dict,
    state: dict | None,
    *,
    max_entries: int = 16,
):
    from mlx_vlm.structured import build_json_schema_logits_processor

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
            return cached_proc.clone()
    compiled = build_json_schema_logits_processor(tokenizer, response_schema)
    if schema_cache is not None and callable(getattr(compiled, "clone", None)):
        schema_cache[schema_key] = compiled
        schema_cache.move_to_end(schema_key)
        while len(schema_cache) > max_entries:
            schema_cache.popitem(last=False)
        return compiled.clone()
    return compiled


def _encode_warmup_prompts(raw_prompts, tokenizer, context_limit: int) -> list[tuple[list[dict], str, list[int]]]:
    if raw_prompts is None:
        return []
    if not isinstance(raw_prompts, list):
        raise ValueError("system_prompts phải là danh sách chuỗi.")
    seen = set()
    prepared = []
    for item in raw_prompts:
        if not isinstance(item, str):
            raise ValueError("Mỗi phần tử trong system_prompts phải là chuỗi.")
        if not item.strip() or item in seen:
            continue
        seen.add(item)
        sample_messages = [{"role": "system", "content": item}, {"role": "user", "content": "x"}]
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
        prepared.append((sample_messages, formatted, encoded))
    return prepared


def _warm_prompts_and_schemas(
    model,
    tokenizer,
    active_tok,
    model_path: Path,
    adapter_path: Path | None,
    prepared_prompts: list[tuple[list[dict], str, list[int]]],
    raw_schemas,
    state: dict | None,
    prefix_cache_store: OrderedDict,
    prefill_step_size: int,
) -> tuple[list[dict], int]:
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

    warmed_schemas = 0
    if raw_schemas is not None:
        if not isinstance(raw_schemas, list):
            raise ValueError("warmup_schemas phải là danh sách JSON schema object.")
        for schema in raw_schemas:
            if not isinstance(schema, dict):
                raise ValueError("Mỗi phần tử trong warmup_schemas phải là JSON object.")
            _get_or_compile_schema_processor(tokenizer, schema, state)
            warmed_schemas += 1
    return warmed_prefixes, warmed_schemas


def _run_warmup(request: dict, state: dict | None = None) -> dict:
    started = time.perf_counter()
    model_path, adapter_path, cfg, model_capacity = _validate_model_and_capacity(request, state)
    adapter_acceptance, _ = _validate_adapter_acceptance(model_path, adapter_path, request)
    context_limit = _resolve_num_ctx(request, model_capacity)
    tokenizer = _get_offline_tokenizer(model_path, state)
    prepared_prompts = _encode_warmup_prompts(request.get("system_prompts"), tokenizer, context_limit)

    adapter_sha = adapter_acceptance["sha256"] if adapter_acceptance else None
    model, loaded_tokenizer, load_seconds = _ensure_loaded_model(
        model_path,
        adapter_path,
        state,
        adapter_sha256=adapter_sha,
    )
    prefix_cache_store = state["prefix_caches"] if state is not None else OrderedDict()
    prefill_step_size = _resolve_prefill_step_size(request)

    cache_started = time.perf_counter()
    warmed_prefixes, warmed_schemas = _warm_prompts_and_schemas(
        model,
        tokenizer,
        loaded_tokenizer or tokenizer,
        model_path,
        adapter_path,
        prepared_prompts,
        request.get("warmup_schemas"),
        state,
        prefix_cache_store,
        prefill_step_size,
    )

    extra_base = request.get("extra_base_warmup")
    if isinstance(extra_base, dict) and adapter_path is not None and state is not None:
        extra_prompts = _encode_warmup_prompts(extra_base.get("system_prompts"), tokenizer, context_limit)
        extra_schemas = extra_base.get("warmup_schemas")
        if extra_prompts or extra_schemas:
            base_model, base_tok, _ = _ensure_loaded_model(model_path, None, state)
            bp_warm, bs_cnt = _warm_prompts_and_schemas(
                base_model,
                tokenizer,
                base_tok or tokenizer,
                model_path,
                None,
                extra_prompts,
                extra_schemas,
                state,
                prefix_cache_store,
                prefill_step_size,
            )
            warmed_prefixes.extend(bp_warm)
            warmed_schemas += bs_cnt
            _ensure_loaded_model(model_path, adapter_path, state, adapter_sha256=adapter_sha)

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
        "warmed_schemas": warmed_schemas,
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

    model, loaded_tokenizer, load_seconds = _ensure_loaded_model(
        model_path,
        adapter_path,
        state,
        adapter_sha256=adapter_acceptance["sha256"] if adapter_acceptance else None,
    )
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
        if not isinstance(response_schema, dict):
            raise ValueError("response_schema phải là JSON object.")
        gen_kwargs["logits_processors"].append(
            _get_or_compile_schema_processor(tokenizer, response_schema, state)
        )
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
            context_prefix_store=state.get("context_prefix_cache"),
        )
    gen_kwargs.update(cache_kwargs)
    cache_seconds = time.perf_counter() - cache_started

    generation_started = time.perf_counter()
    first_token_seconds = None
    last_chunk = None
    parts = []
    loop_guarded = False
    chars_since_loop_check = 0
    for chunk in stream_generate(model, active_tok, prompt=generation_prompt, **gen_kwargs):
        if first_token_seconds is None:
            first_token_seconds = time.perf_counter() - generation_started
        text_piece = chunk.text
        parts.append(text_piece)
        last_chunk = chunk
        if stream_tokens and text_piece:
            print(json.dumps({"token": text_piece}, ensure_ascii=False), flush=True)
        chars_since_loop_check += len(text_piece)
        if chars_since_loop_check >= 24:
            chars_since_loop_check = 0
            if _has_streaming_repetition_loop("".join(parts[-400:])):
                loop_guarded = True
                break
    answer = "".join(parts)
    done_reason = "length" if loop_guarded else getattr(last_chunk, "finish_reason", None)
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
        "loop_guarded": loop_guarded,
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


def _has_streaming_repetition_loop(
    text: str,
    *,
    min_period: int = 40,
    max_period: int = 260,
    repeats: int = 4,
) -> bool:
    """Detect degenerate consecutive or paraphrased clause-level loops in streaming JSON/text output."""
    if not text:
        return False
    if len(text) >= min_period * repeats:
        window = text[-(max_period * repeats):]
        limit = min(max_period, len(window) // repeats)
        for period in range(min_period, limit + 1):
            unit = window[-period:]
            if window.endswith(unit * repeats) and len(re.findall(r"\w+", unit)) >= 3:
                return True
    if len(text) >= 360:
        from collections import Counter

        tail = text[-2400:]
        counts: Counter[str] = Counter()
        for raw_clause in re.split(r"[;\n]+|(?<!\d)[.!?]+(?!\d)", tail):
            cleaned = re.sub(
                r'^\s*[\{\[\"\':,]+(?:\s*(?:claims|text)\s*[\"\':,\{\[]*)*',
                "",
                raw_clause,
            ).strip()
            norm = re.sub(r"\s+", " ", cleaned).lower()
            if len(norm) >= 30 and len(re.findall(r"\w+", norm)) >= 6:
                counts[norm] += 1
                if counts[norm] >= 3:
                    return True
    return False


def serve_stream():
    state = {
        "cfg": {},
        "tok": {},
        "model_key": None,
        "model": None,
        "loaded_tok": None,
        "lora_bindings": None,
        "base_slots": {},
        "lora_banks": OrderedDict(),
        "prefix_caches": OrderedDict(),
        "context_prefix_cache": OrderedDict(),
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
