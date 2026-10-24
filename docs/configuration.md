# Configuration

The supported launcher is `scripts/dev.py`, also used by the Windows wrappers. It reads supported settings from `.env`; existing process environment variables take precedence. Relative paths are resolved from the repository root. Direct `run.py` execution does not load `.env` automatically.

## Profiles

| Profile | Sources and data | Startup behavior |
| --- | --- | --- |
| `demo` | Public fixtures and `data/team-demo` | Builds an isolated demonstration index |
| `local` | Configured directories | Uses existing local data; synchronize new sources explicitly |

## Settings

See `.env.example` and `app/config.py` for current defaults.

| Setting | Purpose |
| --- | --- |
| `AGENT_SRC_DIR`, `AGENT_DATA_DIR` | Source and runtime data directories |
| `HISTORY_DB_PATH` | Optional history database override |
| `TIMETABLE_DB_PATH`, `TIMETABLE_IMAGES_DIR` | Optional timetable storage overrides |
| `OLLAMA_BASE_URL` | Loopback-only daemon endpoint for the optional Windows/legacy local backend |
| `DEFAULT_CHAT_MODEL`, `DEFAULT_EMBED_MODEL` | Question answering and embedding model identifiers |
| `OLLAMA_AUX_CHAT_MODEL` | Auxiliary image model for the optional Windows/legacy local backend; Mac vision uses direct MLX |
| `MLX_CONTEXT_TOKENS` | Operational context budget for the MLX backend |
| `HOST`, `PORT` | Listening address and port |

The sample leaves QA selection to the platform default. Existing process variables take precedence, so review stale model overrides during migration. The Mac requires a separate native MLX runtime and locally installed weights; see [macOS setup](../README_MAC.md). An unavailable selected artifact must fail visibly rather than choose a different model.

## macOS model architecture

The selected QA base is `mlx-community/Qwen3.5-9B-4bit`, pinned to revision `8b2b98c00a6b4d291155e4890773ca8f769aee53`, under `data/models/qwen3.5-9b-4bit`. The worker executes directly in Python using local MLX tensors. Hosted inference, remote teachers, cloud training and automatic cloud fallbacks are prohibited. Downloading public weights is a distinct installation step.

The Mac embedding name `bge-m3` is a compatibility alias for the installed native BGE-M3 artifact; it must never trigger an Ollama probe on Mac. Use `MLX_BGE_MODEL_DIR` for an explicit native path and retain exact vector provenance. The Mac image model shares the local 9B weights and requires the image processor assets and MLX-VLM runtime.

The operational context is 8192 tokens. Token counting and inference must use the exact selected tokenizer and template, with hidden thinking disabled. Careful mode adds application source verification. Advertised context capacity and hardware-fit estimates do not establish answer accuracy; perform real-model source/content review as described in [testing](testing.md).

Retain old 3B artifacts and registry evidence. A 3B adapter cannot be applied to the 9B base, and a trained-adapter label must not appear when serving an unadapted base. Changing QA does not authorize replacing the source database, rebuilding the corpus or mixing embedding spaces. Embeddings and vision report their own readiness. Windows compatibility, if used, must remain explicitly local.

Keep `.env`, registries and private artifact paths out of Git. An installation, environment setting or successful inference does not authorize training or activation. See [source handling](../corpus/README.md).

## Training plans

`python run.py train-plan --model-dir /path/to/base --data /path/to/data --adapter-dir /path/to/new-adapter` prints a draft Qwen3.5-9B MLX LoRA plan. It validates configuration identity and path separation without loading weights, creating directories or starting training. The draft uses a quantized base, small batches and gradient accumulation; memory feasibility, tokenizer auditing, source approval and holdout quality remain separate gates. Its output always marks execution and training authorization false.

## Fast and careful QA

The primary interface offers **Nhanh** (fast, `standard`) and **Kiểm tra kỹ** (careful). Both use the selected QA model. Careful mode adds an automatic claim-to-source audit and returns an explicit failure if that audit cannot establish coverage; it can take longer and is not an independent human review. The API continues to accept `auto` for compatibility. Existing browser `auto` preferences migrate to fast mode; an explicit careful preference is retained. Neither mode activates an adapter or switches to an unaccepted model.

See [model selection and migration](model-selection.md) for the artifact revision and hashes, bounded historical comparison evidence and rollback procedure.
