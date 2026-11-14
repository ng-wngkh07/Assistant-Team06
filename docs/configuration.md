# Configuration

The supported launcher is `scripts/dev.py`, also used by the Windows wrappers. It reads supported settings from `.env`; existing process environment variables take precedence. Relative paths are resolved from the repository root. Direct `run.py` execution does not load `.env` automatically.

## Profiles

| Profile | Sources and data | Startup behavior |
| --- | --- | --- |
| `demo` | Public fixtures and `data/team-demo` | Builds an isolated demonstration index |
| `local` | Configured directories | Reuses existing data; new shared sources need explicit synchronization unless the opt-in watcher is enabled |

Standard `--profile local` startup resolves `AGENT_SRC_DIR` (default `src`) and `AGENT_DATA_DIR` (default `data`), even when `src/` is not created yet or an unused `resources/knowledge-packs.json` file exists. Explicit overrides for `AGENT_SRC_DIR`, `AGENT_DATA_DIR`, `HISTORY_DB_PATH`, `TIMETABLE_DB_PATH`, `TIMETABLE_IMAGES_DIR` and `STUDY_ACCOUNTS_DIR` remain supported; relative explicit paths resolve against the repository root. Automatic `data/user-library/` seeding is retired, and `dev.py check --profile local` never creates `src/`, `data/knowledge_base.db`, or `data/user-library/`. Missing real libraries report honestly as empty/missing rather than full readiness.

When both `KNOWLEDGE_PACK_PATH` and `KNOWLEDGE_PACK_SHA256` are explicitly set and `--install-pack` is requested with `--profile local`, the launcher validates the archive, review coverage and embedding identity and installs into `data/knowledge-packs/<sha256>/` without modifying existing libraries, histories or timetables. Running `--install-pack` without both variables fails explicitly without seeding anything. Direct `run.py` execution does not load `.env` or install knowledge packs. For manual migration from a legacy `data/user-library/` directory, inventory and back up that directory while the backend is stopped before archiving; never auto-delete, move or merge it.

## Settings

See `.env.example` and `app/config.py` for current defaults.

| Setting | Purpose |
| --- | --- |
| `AGENT_SRC_DIR`, `AGENT_DATA_DIR` | Source and runtime data directories |
| `HISTORY_DB_PATH` | Optional history database override |
| `TIMETABLE_DB_PATH`, `TIMETABLE_IMAGES_DIR` | Optional timetable storage overrides |
| `OLLAMA_BASE_URL` | Loopback-only endpoint for the local Ollama backend, used by Windows/Linux and optional Ollama installations on Mac |
| `DEFAULT_CHAT_MODEL`, `DEFAULT_EMBED_MODEL` | Question answering and embedding model identifiers |
| `OLLAMA_AUX_CHAT_MODEL` | Local Ollama model for image recognition; native Mac vision uses direct MLX |
| `LLM_PROVIDER`, `CHAT_BACKEND`, `EMBED_BACKEND` | Explicit local backend selection; leave unset for platform defaults |
| `KNOWLEDGE_PACK_PATH`, `KNOWLEDGE_PACK_SHA256` | Prepared library archive and trusted publisher digest; set both together for explicit `--install-pack --profile local` |
| `MLX_MODEL_DIR`, `MLX_BGE_MODEL_DIR` | Optional paths to the installed native QA and embedding artifacts |
| `MLX_CONTEXT_TOKENS` | Operational context budget for the MLX backend |
| `HOST`, `PORT` | Listening address and port |

The sample leaves QA selection to the platform default. Existing process variables take precedence, so review stale model overrides during migration. The Mac requires a separate native MLX runtime and locally installed weights; see [macOS setup](../README_MAC.md). An unavailable selected artifact must fail visibly rather than choose a different model.

## Account storage and operator environment

The `scripts/dev.py` `.env` loader accepts its explicit `ENV_KEYS` allowlist (`STUDY_*` account, scaling, rate-limit, quota, disk-threshold, watcher, `FORWARDED_ALLOW_IPS`, and `SOFFICE_PATH` settings). Existing process variables take precedence over `.env`. Direct `run.py` and `uvicorn` do not load `.env`, so managed launchers or exported shell environments should still set them explicitly.

| Process setting | Behavior |
| --- | --- |
| `STUDY_ACCOUNTS_DIR` | Stable private account root; default `AGENT_DATA_DIR/accounts` |
| `STUDY_ACCOUNTS_REQUIRED=1` | Require login even on loopback; non-loopback API access already requires it |
| `STUDY_ALLOW_REGISTRATION=0` | Keep remote registration closed; provision invited accounts locally |
| `STUDY_SECURE_COOKIES=1` | HTTPS cookies and `Strict-Transport-Security` header for hosted HTTPS origins; use `0` only for a separate loopback HTTP session |
| `FORWARDED_ALLOW_IPS` | Trusted proxy IP list for `X-Forwarded-*` headers (`127.0.0.1,::1` default for local tunnels/Caddy) |
| `STUDY_GPU_RUNTIME_DIR` | Explicit shared GPU lock/state directory for `gpu_coordinator` and native child workers; defaults to `AGENT_DATA_DIR/runtime` (keeps watcher state, accounts, and history in their separate durable paths) |
| `STUDY_MAX_INFERENCE_QUEUE` | Maximum FIFO waiters for global model inference admission (default `20`) |
| `STUDY_INFERENCE_QUEUE_TIMEOUT_SECONDS` | Per-request FIFO queue wait deadline in seconds before returning busy `409`/`503` (default `25.0`) |
| `STUDY_RATE_LIMIT_ENABLED` | Enable early ASGI sliding-window rate limiter (`1` default; set `0` to disable) |
| `STUDY_API_RATE_LIMIT_PER_MINUTE` | General `/api/*` sliding-window request cap per account session / IP (default `300`) |
| `STUDY_INFERENCE_RATE_LIMIT_PER_MINUTE` | Heavy inference/upload/OCR request cap per account session / IP (default `30`) |
| `STUDY_RATE_LIMIT_WINDOW_SECONDS` | Sliding-window interval in seconds (default `60.0`) |
| `STUDY_ACCOUNT_MAX_FILES` | Maximum non-deduplicated uploaded files per account (default `50`) |
| `STUDY_ACCOUNT_MAX_STORAGE_MB` | Maximum non-deduplicated upload storage in MiB per account (default `250`) |
| `STUDY_MIN_FREE_DISK_MB` | Minimum free disk headroom threshold for `/api/health` infrastructure warning (`512` default) and `backend_service.py` preflight |

For example, a loopback local session with account login and watched shared sources:

```sh
export STUDY_ACCOUNTS_REQUIRED=1
export STUDY_SRC_AUTO_WATCH=1
.venv/bin/python scripts/dev.py serve --profile local
```

```powershell
$env:STUDY_ACCOUNTS_REQUIRED = '1'
$env:STUDY_SRC_AUTO_WATCH = '1'
.venv\Scripts\python.exe scripts/dev.py serve --profile local
```

Use an ignored private operator environment file for deployment paths and source it before launch. If relocating `AGENT_DATA_DIR`, set explicit existing model paths as needed; changing the data directory also changes default artifact and runtime-state locations. See [native host setup](native-host-pilot.md) and [account persistence](service-persistence.md). Do not copy private settings into public examples.

## Automatic source ingestion

`STUDY_SRC_AUTO_WATCH=1` (alias `AGENT_SRC_AUTO_WATCH`) enables the backend-managed watcher; default is off. `STUDY_SRC_WATCH_POLL_SECONDS` defaults to `2.0`, and `STUDY_SRC_WATCH_DEBOUNCE_SECONDS` to `1.5`. Export them in the launching environment before startup. The local profile does not otherwise rebuild an existing library automatically.

Settled new supported files under `AGENT_SRC_DIR` are indexed incrementally under the corpus writer lock and model coordinator. Status is visible through the library UI and `/api/storage` or `/api/status`; private state lives at `AGENT_DATA_DIR/runtime/src_watcher_state.json`. Extraction/indexing is retrieval preparation, not model training or source approval.

Changed indexed/reviewed originals become `needs_update`; missing originals become `missing_source`. They require operator review and explicit re-indexing, remain in the database and are blocked/excluded from source-bound use while stale. Preserve original files and previous reviews. The watcher does not monitor account-private uploads, publish sources, activate adapters or rebuild the separate taxonomy sidecar. Without it, synchronize new shared files explicitly. See [usage](usage.md) for account uploads and [architecture](architecture.md) for sidecar fallback.

## macOS model architecture

The selected QA base is `mlx-community/Qwen3.5-9B-4bit`, pinned to revision `8b2b98c00a6b4d291155e4890773ca8f769aee53`, under `data/models/qwen3.5-9b-4bit`. The worker executes directly in Python using local MLX tensors. Hosted inference, remote teachers, cloud training and automatic cloud fallbacks are prohibited. Downloading public weights is a distinct installation step.

With the default native backend, the Mac embedding name `bge-m3` resolves to the installed native BGE-M3 artifact without an Ollama probe. Use `MLX_BGE_MODEL_DIR` for an explicit native path and retain exact vector provenance. An explicitly configured Ollama installation uses a separate artifact-bound pack. The native Mac image model shares the local 9B weights and requires the image processor assets and MLX-VLM runtime.

The operational context is 8192 tokens. Token counting and inference must use the exact selected tokenizer and template, with hidden thinking disabled. Careful mode adds application source verification. Advertised context capacity and hardware-fit estimates do not establish answer accuracy; perform real-model source/content review as described in [testing](testing.md).

Keep retired 3B artifacts outside the active application tree and retain recovery evidence separately. A 3B adapter cannot be applied to the 9B base, and a trained-adapter label must not appear when serving an unadapted base. Changing QA does not authorize replacing the source database, rebuilding the corpus or mixing embedding spaces. Embeddings and vision report their own readiness. Windows inference also runs locally.

Keep `.env`, registries and private artifact paths out of Git. An installation, environment setting or successful inference does not authorize training or activation. See [source handling](../corpus/README.md).

## Training plans

`python run.py train-plan --model-dir /path/to/base --data /path/to/data --adapter-dir /path/to/new-adapter` prints a draft Qwen3.5-9B MLX LoRA plan. It validates configuration identity and path separation without loading weights, creating directories or starting training. The draft uses a quantized base, small batches and gradient accumulation; memory feasibility, tokenizer auditing, source approval and holdout quality remain separate gates. Its output always marks execution and training authorization false.

## Fast and careful QA

The primary interface offers **Nhanh** (fast, `standard`) and **Kiểm tra kỹ** (careful). Both use the selected QA model. Careful mode adds an automatic claim-to-source audit and returns an explicit failure if that audit cannot establish coverage; it can take longer and is not an independent human review. The API continues to accept `auto` for compatibility. Existing browser `auto` preferences migrate to fast mode; an explicit careful preference is retained. Neither mode activates an adapter or switches to an unaccepted model.

See [model selection and migration](model-selection.md) for the artifact revision and hashes, bounded historical comparison evidence and rollback procedure.

## Office source previews

PPTX slide images use a local LibreOffice renderer or an explicitly imported static PDF rendition. The application checks `SOFFICE_PATH`, `LIBREOFFICE_PATH`, the executable search path and standard vendor installation locations. An override points to the executable, not a directory. A missing renderer leaves original downloads available; it does not produce a substitute text image. This dependency is separate from the model runtime and is not proof that a distribution includes LibreOffice.

Converted previews are private cache data under `AGENT_DATA_DIR/office_preview_cache`, bound to source content and renderer identity. A publisher can explicitly import a static rendition through `register_precomputed_pptx_preview` in `app.slide_preview`, supplying the original PPTX, its rendered PDF and renderer provenance. The private manifest binds both SHA256 digests and the physical slide count; changed or damaged assets are rejected. This local operation does not index sources or approve their content. Static previews work without LibreOffice and do not execute embedded Office objects. Conversion uses a temporary isolated profile. Files with unsupported external references or active content are rejected for preview; their originals remain unchanged. Word reading sections are independent of physical print pagination.
