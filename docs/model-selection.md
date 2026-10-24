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

Use the [macOS setup guide](../README_MAC.md) to prepare the isolated runtime and local weights. Keep old 3B weights and registry evidence for rollback. A legacy 3B alias must retain its own identity, and its adapter must never be loaded onto 9B. Missing 9B files must not silently select another model or provider.

QA defaults to **Fast**. Complex questions can receive a suggestion to choose **Careful**, with no automatic resubmission or loss of the typed question. Careful adds application-level source checking; the model's hidden thinking remains disabled. Practice and exercise answers use their fixed verification workflow, with separate source-provided, model-verified and unverified outcomes. A second model pass is not independent human approval.

Changing the QA base does not authorize replacing the source database or mixing embedding spaces. Embeddings and image recognition need their own local runtime/artifact validation; their readiness must be reported separately from text QA. A local OCR draft still requires review before calendar confirmation and export.

## Future fine-tuning

The intended fine-tuning base is the same pinned local MLX 9B artifact. No training run or adapter activation is authorized by installation or a successful inference check. A printed training plan is a blocked proposal, not an executable accepted release. On Apple M1 16 GB, successful inference does not establish backward-pass memory feasibility.

Before any run, verify source approvals, exact-tokenizer auditing, representative disjoint splits, leakage checks, finite-gradient and peak-memory behavior, retention, grounding and an independently reviewed baseline on the same artifact. Preserve rejected adapters and evidence. Promote only an adapter bound to the exact base and accepted through the project's quality gates.

Question-complexity examples in [the public fixtures](../tests/fixtures/EXTENDED_CORPUS.md) support software reproduction and labeling review. They are provisional development labels marked `training_allowed=false`, not an approved classifier training set. Future data should distinguish multiple requested actions from verification risk: a short numerical question can still need careful checking. Evaluate false suggestions and missed complex requests across held-out topics before claiming classifier accuracy.


## Release and rollback

Record the deployed Git commit, exact QA and embedding artifact identities, runtime versions and whether an accepted adapter is active. Complete the real-model and source/content checks before publishing a migration. Keep runtime reports local; the public change should describe scope, measured limitations and reproducible public tests.

If a regression requires rollback, preserve the failing reports and private data first. Revert the migration code/configuration as one reviewed change to a previously accepted deployment, then rerun model readiness, a source-grounded answer, practice verification and timetable confirmation/export. Select the intended local MLX artifact explicitly and verify the reported backend after restart; keeping an old directory is not proof that it is an accepted rollback target. Do not activate a legacy adapter, reinterpret vector identities or replace a corpus database as an incidental rollback step.

QA and embedding migrations are separate operations. A deployment using the old corpus may use explicit text search until native vector coverage is separately accepted. Missing or incompatible weights must remain visible instead of causing an automatic provider or model change.
