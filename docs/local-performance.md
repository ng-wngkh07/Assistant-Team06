# Local answer latency

Measure the complete request, including queueing, query embedding, retrieval, optional translation, prompt processing, generation, repair and auditing. Tokens per second alone excludes much of the waiting time. Record the model and tokenizer revision, input/output token counts, mode, cache state, hardware and competing work. Compare identical questions against an unchanged source snapshot and review the answers separately.

## Current controls

- **Fast** is the default. The compatible API value is `standard`; it does not automatically become Careful for a complex question. It makes one answer attempt and may make one bounded repair when validation fails. It reads existing citation-preview translations without generating new ones before the answer.
- **Careful** uses the API value `careful` and adds an audit. An explicitly selected mode persists in the browser. The legacy `auto` API value remains available for existing clients; new UI requests use an explicit selection. Practice has its own fixed verification workflow.
- Query embeddings use a bounded, process-local cache of 128 vectors. Keys bind the query digest, token limit, model directory and freshly verified model bytes. Every retrieval still validates stored chunk provenance. Source changes therefore produce a fresh search even when the query vector is reused. Answers are not cached.
- Native text generation processes the complete prompt in prefill steps of 128 tokens. This limits transient memory without discarding context. Worker diagnostics distinguish model loading, time to first generated token and generation, and include available throughput and peak-memory measurements. The worker's first-token time excludes application queueing and retrieval.
- Generation, vision, embedding and training share the resource coordinator. Run performance measurements without a concurrent test suite or training job. The development reloader watches backend code rather than the model and data trees.

Exact token budgeting, source selection, numeric/formula checks, citation validation and failed-answer handling still apply in Fast. Removing those checks would change correctness. A shorter maximum answer can reduce latency but must not truncate the requested reasoning or conditions.

## Diagnose before changing the model

| Observation | Next check | Suitable response |
| --- | --- | --- |
| Repeated questions spend time embedding | Cold versus repeated embedding with fixed model bytes | Reuse validated query vectors |
| Retrieval is fast but the answer is slow | Prompt length, time to first token and generated tokens | Reduce irrelevant context; keep sufficient evidence |
| English sources are much slower | Optional translation calls before generation | Use cached previews in Fast; retain original evidence |
| Most requests need repairs | Raw draft, selected evidence lines and validation reason | Fix structured output and source fidelity; keep the validator |
| Careful takes several model calls | Resolved mode and generation/audit counts | Select Fast explicitly for ordinary questions |
| All applications slow down | Memory pressure, swap growth, reloader and competing jobs | Serialize model work and retain a smaller context |
| Only some documents are slow or unanswered | Extraction quality and verified vector coverage | Repair source data and index admitted text; do not fabricate vectors |

Test a complete-vector scenario in a separate database. Count vectors, validate their dimensions, finite values, normalization, source-text digest and model identity, then time lexical and semantic retrieval independently. Compare result IDs as well as speed. A small complete fixture does not establish full-corpus coverage or large-corpus performance.

## Further optimizations require evidence

Persistent model workers and prefix caches could avoid repeated startup and prefill. They also keep weights and state resident, compete with local vision and embedding, and need bounded eviction, cancellation, model/adapter identity checks and the same coordinator ownership. Do not add an uncoordinated background model server to reduce a benchmark number.

MLX-LM documents [prompt reuse and prefill-size tradeoffs](https://github.com/ml-explore/mlx-lm#long-prompts-and-generations). Smaller prefill can reduce peak memory and may reduce throughput. Rotating or quantized KV caches need validation for the installed Qwen hybrid architecture and can affect retained context or answer quality. Backend-specific options from the [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) do not configure this application's direct MLX worker.

A [LocalLLaMA benchmark discussion](https://www.reddit.com/r/LocalLLaMA/comments/1rs059a/mlx_is_not_faster_i_benchmarked_mlx_vs_llamacpp/) illustrates why prompt length, caching and warm/cold measurements matter. Its hardware, quantization and runtime differ from this deployment; community results are hypotheses to test, not acceptance evidence for this application.

Fine-tuning can improve structured answers, source selection and the rate of successful first attempts. It does not make a model with the same architecture generate each token inherently faster. Build reviewed examples covering definitions, calculations, follow-ups, missing evidence, document scope, formulas, incomplete uploads and timetable warnings. Split related documents/images across train and holdout without leakage. Keep unreviewed answers and uncertain OCR out of approved targets. Evaluate factual and citation correctness alongside latency before accepting an adapter.

All inference, embeddings, translation, OCR/vision, supervision generation and training remain local. Missing local assets produce an explicit failure rather than a cloud fallback. See [context management](context-management.md) for the request budget and memory tradeoffs.
