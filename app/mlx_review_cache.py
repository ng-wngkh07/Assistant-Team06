"""Reuse only trusted instruction prefill inside one native review request."""
import hashlib
import json
from pathlib import Path


def review_prefix_length(prompt, tokens, encode):
    markers = ("Dữ liệu cần kiểm định:\n", "Đối chiếu chỉ số từ 0 do ứng dụng chép trực tiếp từ question:\n")
    ends = [prompt.index(marker) for marker in markers if marker in prompt]
    if not ends:
        return 0
    prefix = encode(prompt[:min(ends)])
    length = 0
    for left, right in zip(prefix, tokens):
        if left != right:
            break
        length += 1
    return (min(length, len(tokens) - 1) // 128) * 128


def prepare_review_cache(model, tokenizer, prompt, request, adapter_acceptance):
    from app.practice_adapter import request_matches_grounded_adapter_scope
    directory = request.get("review_cache_dir")
    if not directory or not request_matches_grounded_adapter_scope(request):
        return prompt, {}, {"reused_tokens": 0}
    from mlx_lm.models.cache import make_prompt_cache, save_prompt_cache, load_prompt_cache
    import mlx.core as mx
    add_special = tokenizer.bos_token is None or not prompt.startswith(tokenizer.bos_token)
    encode = lambda text: tokenizer.encode(text, add_special_tokens=add_special)
    tokens = encode(prompt)
    count = review_prefix_length(prompt, tokens, encode)
    if count == 0:
        return prompt, {}, {"reused_tokens": 0}
    model_path = Path(request["model_path"])
    files = sorted(set(model_path.glob("*.safetensors")) | {
        model_path / "config.json", model_path / "tokenizer.json", model_path / "tokenizer_config.json"})
    identity = [(str(p), p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in files if p.is_file()]
    binding = dict(prefix=tokens[:count], model=identity,
                   adapter=(adapter_acceptance or {}).get("sha256"), adapter_path=request.get("adapter_path"))
    digest = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    path = Path(directory) / (digest + ".safetensors")
    metadata = {"binding": digest, "tokens": str(count)}
    cache = None
    if path.is_file():
        try:
            loaded, saved = load_prompt_cache(str(path), return_metadata=True)
            if saved == metadata:
                cache = loaded
        except (ValueError, OSError, KeyError, RuntimeError):
            cache = None
    reused = cache is not None
    if cache is None:
        cache = make_prompt_cache(model)
        for offset in range(0, count, 128):
            model(mx.array(tokens[offset:offset + 128])[None], cache=cache)
            mx.eval([c.state for c in cache])
        save_prompt_cache(str(path), cache, metadata)
    return tokens[count:], {"prompt_cache": cache}, {
        "reused_tokens": count if reused else 0, "prefix_tokens": count, "binding": digest,
        "scope": "request_local_trusted_instructions_only"}
