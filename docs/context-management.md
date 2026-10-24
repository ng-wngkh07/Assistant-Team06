# Local context management

A saved conversation and a model request are different sizes. The application keeps the saved conversation, but packs only whole recent question/answer pairs and selected source passages into each request. The current question is never silently truncated. A question that cannot fit with the output reserve is rejected before generation.

The budget is:

`system + chat template + current question + selected sources + admitted history + output reserve <= configured context`

An 8,192-token limit does not imply a fixed number of questions. For example, if instructions, sources, the current question and output reserve use 4,000 tokens, 4,192 tokens remain for history: approximately six 650-token question/answer pairs or two 1,500-token pairs. Vietnamese, formulas, tables and long answers change token counts. A practical starting point is a new conversation per topic and roughly five medium-length exchanges before checking the usage indicator; this is guidance, not a guaranteed capacity.

The chat displays the most recent packed request, including its reserved answer budget, and identifies exact-tokenizer counts versus conservative estimates. It warns when history was omitted or the budget reaches 80%. The new-chat button preserves an unsent draft. Draft size updates while typing in UTF-8 bytes and is explicitly not a token count or a preview of retrieved sources. A request without a generation/context event does not update the last measurement. Switching conversations clears the old measurement.

Opening a new chat is explicit. Automatically switching during a follow-up could lose the referents needed to answer correctly. A saved summary is also not a replacement for original evidence: retrieve the actual source again and preserve document/page scope. Do not promote a generated summary into training data without review.

## Increasing capacity

`MLX_CONTEXT_TOKENS` limits native MLX requests. Increasing this value is a deployment change: first measure an isolated representative workload with the installed model, tokenizer and runtime. Measure input/output tokens, time to first token, generation speed, peak model memory, swap growth and other application responsiveness. A model advertising a large maximum does not establish that a laptop can serve it comfortably.

Apple Silicon uses unified memory shared by CPU, GPU and desktop applications. Quantized weights, attention caches, recurrent state, prefill workspace and training activations have different memory costs. The Qwen3.5 hybrid architecture has both recurrent layers and full-attention layers; a full-attention-only memory formula is not an adequate measurement. Inference fitting in memory does not establish that a backward training pass fits.

Keep the deployed context unchanged if the larger workload times out, increases swap substantially or harms desktop responsiveness. Smaller prefill batches can reduce peak memory with a speed tradeoff. Serialize generation, vision, embedding and training through the existing resource coordinator. Never change system wired-memory limits to make a benchmark pass.

## Options and tradeoffs

| Method | Use here | Limitation |
| --- | --- | --- |
| Exact request packing and whole-pair history window | Existing default | Older details need restating or retrieval |
| Retrieve fewer, relevant source passages | Existing default | Preserve enough evidence and conditions to answer |
| Usage warning and explicit new chat | Chat UI | No fixed question count guarantees retained history |
| Reviewed, source-linked session summary | Optional continuation aid | A generated summary can omit or distort facts |
| Prompt/KV reuse | Future measured optimization | Speeds repeated prefixes; does not increase semantic capacity |
| Smaller prefill batches | Existing 128-token native default | May reduce processing throughput |
| Rotating or quantized KV cache | Requires model/runtime-specific validation | Can discard earlier context or reduce quality; hybrid recurrent state also needs support |
| Larger configured context | Only after local measurement | More memory and longer prefill; may increase swap |

MLX-LM documents rotating caches, prompt reuse and prefill-size tradeoffs in its [long-prompt guidance](https://github.com/ml-explore/mlx-lm#long-prompts-and-generations). The [llama.cpp server reference](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) documents context shifting and cache controls for its own backend; those flags do not configure this application's native MLX worker. Neither feature is a guarantee that old conversation details remain available.

[Community reports on LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/comments/1t9p4oe/as_of_today_whats_the_most_stable_model_to_run_on/) describe crashes and latency when long context is actually used, and emphasize memory shared with other applications. These are workload anecdotes, not measurements of this project or acceptance criteria.

All model inference, embedding, OCR/vision, supervision generation and training remain local. External model APIs, cloud teachers and automatic cloud fallback are prohibited. Missing local capability produces an explicit failure or HOLD state.
