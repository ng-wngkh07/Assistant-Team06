# Local MLX model selection and migration

The selected macOS architecture loads **Qwen3.5 9B 4-bit directly through MLX in a local Python worker**, for inference and the future gated fine-tuning pipeline. Hosted inference, remote teachers, cloud training and automatic cloud fallbacks are prohibited. Downloading public weights is an installation step; private questions and documents must never be sent with that download.

The web application and MLX runtime use separate Python environments. Direct MLX means the worker loads local tensors and executes on Apple Silicon; there is no model HTTP service between the application and the QA model. Installing weights, successfully running inference, passing content review, training an adapter and accepting that adapter are separate milestones.

## Exact artifact

```text
Repository: mlx-community/Qwen3.5-9B-4bit
Revision: 8b2b98c00a6b4d291155e4890773ca8f769aee53
Local directory: data/models/qwen3.5-9b-4bit
Quantization: affine, 4 bits, group size 64
Weight shards: 5,349,771,222 + 600,449,850 bytes
Operational context: 8192 tokens
```

The pinned [model configuration](https://huggingface.co/mlx-community/Qwen3.5-9B-4bit/blob/8b2b98c00a6b4d291155e4890773ca8f769aee53/config.json) contains a nested `text_config` and hybrid linear/full attention. Its advertised 262144-token capacity is not the application's memory budget. Record weight, tokenizer and template hashes when evaluating; the repository name alone does not identify all local bytes. Runtime loading must use local files and fail if required assets are missing.

The screenshot's `Alexander85/Qwen3.5-9B-Claude-4.6-Opus-Reasoning-Distilled-NVFP4-MLP-FP8KV` is a different community artifact, with different distillation and numeric formats. It is neither the MLX artifact above nor the previously evaluated Ollama package. This project has not completed equivalent content, runtime or adapter acceptance for that distilled artifact.

## Why Qwen3.5 9B is the current candidate

The choice balances available memory, Vietnamese source-grounded answers, latency, local library support and a path to native local fine-tuning. MLX also avoids an unverified MLX-adapter-to-GGUF conversion between training and serving. This is a deployment choice, not proof that Qwen3.5 9B is the best model in every task.

llmfit's **Perfect** label describes estimated hardware fit. Its [scoring documentation](https://github.com/AlexsJones/llmfit/blob/main/docs/how-it-works.md) separates memory fit, estimated speed, quality heuristics and context. It does not test this project's Vietnamese questions, theorem conditions, source attribution or exercise verification. Other “Perfect” models remain candidates until tested on the same inputs, code, budgets and review rubric.

The following development evidence belongs to the **previous Ollama/GGUF evaluation**, not to the new MLX artifact:

| Candidate and format | Observed result | What it supports |
| --- | --- | --- |
| Qwen3.5 9B Q4_K_M | Controlled same-code comparison: 12/13 actual-document cases passed content/source review. Later prompt-corrected run: 13/13 actual and 12/12 synthetic; separate careful probes: 3 actual and 1 synthetic. About 6.34 GB allocation and 4.65 decoded tokens/s in a separate context probe. | Promising candidate on Apple M1 16 GB. These figures do not establish MLX performance or quality. |
| Gemma 4 12B Q4_K_M | Same-code comparison: 11/13; omitted a definition premise and attributed a complete theorem to a truncated passage. | No advantage in that limited comparison. Later Qwen prompt tuning prevents treating final scores as an untouched comparative benchmark. |
| Qwen3.5 27B UD-IQ2_M | One correct direct probe, about 170.9 seconds, 1.81 decoded tokens/s and 11.48 GB loaded allocation; no complete RAG evaluation. | Greater observed memory demand and latency. Broad quality superiority remains unknown; these results do not describe a 27B Q4 artifact. |
| Qwen3 4B | Two correct thinking diagnostics took about 115 and 120 seconds. | These probes do not establish full RAG/source acceptance or lower end-to-end latency. |
| Other llmfit “Perfect” entries | No complete equivalent project evaluation. | No claim that they are inferior or unsuitable. |

The earlier Ollama artifact was `qwen3.5:9b`, digest `56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90`, download size 6,550,825,550 bytes. Retain that identity only to reproduce historical results; it is not the macOS direct-MLX installation target.

These development cases were used during diagnosis and prompt changes. They are not an independent held-out benchmark, course acceptance or a guarantee of correct answers. Every new artifact requires real-model tests and a separate review of meaning, conditions, units and cited passages. Private sources and raw reports stay out of Git.

## Inference and training boundaries

Use the [macOS setup guide](../README_MAC.md) to prepare the isolated runtime and local weights. Retired weights and incompatible adapters do not belong in the active application tree or automatic discovery paths. The resolver rejects retired aliases before provider selection. Preserve only necessary recovery/source-review evidence outside active discovery; an obsolete model is not an accepted rollback target. Missing 9B files must produce an actionable error rather than silently selecting another model or provider. The current 9B base has no accepted active adapter.

QA defaults to **Fast**. Complex questions can receive a suggestion to choose **Careful**, with no automatic resubmission or loss of the typed question. Careful adds application-level source checking; the model's hidden thinking remains disabled. Practice and exercise answers use their fixed verification workflow, with separate source-provided, model-verified and unverified outcomes. A second model pass is not independent human approval.

Changing the QA base does not authorize replacing the source database or mixing embedding spaces. Embeddings and image recognition need their own local runtime/artifact validation; their readiness must be reported separately from text QA. A local OCR draft still requires review before calendar confirmation and export.

Embedding candidates must be compared in isolation using the current fixed chunker, the same source snapshot and precommitted source labels. Record full-corpus tokenizer coverage, retrieval recall/ranking, actual document throughput, query latency and memory with each model's documented pooling and query instructions. Reject silent truncation. Faster query inference alone cannot justify losing source coverage or ranking quality. Validate a complete new vector stage against exact model/text/blob identities before atomic activation; never relabel or mix old vectors.

## Future fine-tuning

The intended fine-tuning base is the same pinned local MLX 9B artifact. No training run or adapter activation is authorized by installation or a successful inference check. A printed training plan is a blocked proposal, not an executable accepted release. On Apple M1 16 GB, successful inference does not establish backward-pass memory feasibility.

Before any run, verify source approvals, exact-tokenizer auditing, representative disjoint splits, leakage checks, finite-gradient and peak-memory behavior, retention, grounding and an independently reviewed baseline on the same artifact. Preserve rejected adapters and evidence. Promote only an adapter bound to the exact base and accepted through the project's quality gates.

Question-complexity examples in [the public fixtures](../tests/fixtures/EXTENDED_CORPUS.md) support software reproduction and labeling review. They are provisional development labels marked `training_allowed=false`, not an approved classifier training set. Future data should distinguish multiple requested actions from verification risk: a short numerical question can still need careful checking. Evaluate false suggestions and missed complex requests across held-out topics before claiming classifier accuracy.


## Release and rollback

Record the deployed Git commit, exact QA and embedding artifact identities, runtime versions and whether an accepted adapter is active. Complete the real-model and source/content checks before publishing a migration. Verification must include the final application path, actual serving URL, process/file identity, browser flows and a fresh-clone installation of the intended distribution. Rerun affected acceptance after changing its code, model, data or configuration. Keep runtime reports local; the public change should describe scope, measured limitations and reproducible public tests.

If a regression requires rollback, preserve the failing reports and private data first. Revert the migration code/configuration as one reviewed change to a previously accepted deployment, then rerun model readiness, a source-grounded answer, practice verification and timetable confirmation/export. Select the intended local MLX artifact explicitly and verify the reported backend after restart; keeping an old directory is not proof that it is an accepted rollback target. Do not activate a legacy adapter, reinterpret vector identities or replace a corpus database as an incidental rollback step.

QA and embedding migrations are separate operations. A deployment using the old corpus may use explicit text search until native vector coverage is separately accepted. Missing or incompatible weights must remain visible instead of causing an automatic provider or model change.

## Measured embedding comparison

A bounded isolated comparison on Apple M1 with 16 GB retained the pinned native BGE-M3. The frozen selection rule required complete unchanged-chunk coverage and at least the baseline paraphrase Recall@6 and MRR, together with a material speed or memory benefit. Inputs used the current 1200-character/200-overlap boundaries: 668 chunks including distractors and duplicates, 12 development queries and 12 source-labeled paraphrases fixed before inference. Tokenizer coverage was checked separately across the pre-refresh snapshot of 52,711 chunks. After correcting obsolete extracted text, coverage must be measured again on every final chunk before vector activation. This is dense ranking evidence, not final hybrid retrieval or answer accuracy.

| Candidate and protocol | Warm query median | Chunks/s, batch 4 | Peak Metal allocation | Chunks over token cap | Paraphrase Recall@6 / MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| BGE-M3 fp16, CLS + L2, cap 2048 | 68.7 ms | 5.44 | 2.44 GiB | 0 | 1.000 / 0.944 |
| multilingual E5-small fp32, mean + L2, query/passage prefixes, cap 512 | 5.2 ms | 28.16 | 1.39 GiB | 417 | 0.917 / 0.883 |
| Qwen3-Embedding 0.6B bf16, last valid token + L2, instructed queries, cap 2048 | 44.9 ms | 4.67 | 1.79 GiB | 0 | 1.000 / 0.875 |

Warm query timing excludes model loading, artifact validation, database search and answer generation. Metal allocation is not total machine RAM. E5 skipped over-limit evaluation chunks rather than truncating. Qwen remains a candidate for wider evaluation; a twelve-query ranking difference does not prove universal inferiority. BGE met the comparison gate. The full rebuild must bind vectors to the corrected text and exact artifact, with complete token coverage checked again. Private queries, passages and raw reports are excluded from Git.

Primary model protocols: [BGE-M3](https://huggingface.co/BAAI/bge-m3), [multilingual E5-small](https://huggingface.co/intfloat/multilingual-e5-small), [Qwen3 Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B). See [retrieval and training](rag-and-training.md) for chunking, top-K, privacy and teacher-assisted learning boundaries.
