# macOS setup

Use Python 3.12 for the supported web dependency profile. Keep the web environment separate from any existing MLX runtime. Do not recreate an environment while it is serving a running job.

## Install and run

Install [Python](https://www.python.org/downloads/macos/) and Git, then run:

```bash
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python scripts/dev.py serve --profile demo
```

Open `http://127.0.0.1:8000`. Stop the server with Ctrl+C. The demo uses `data/team-demo`; it does not replace the local document library. Search works without a model.

## Direct local MLX runtime

Apple Silicon uses a separate native Python 3.13 runtime for local model loading. Preserve an existing runtime and never recreate it while a model job is running. For a fresh checkout:

```bash
python3.13 -m venv .train-venv
.venv/bin/python -m pip --python .train-venv/bin/python install -r requirements-mlx.txt
.venv/bin/python -m pip --python .train-venv/bin/python check
```

Download the pinned public model into ignored local storage:

```bash
.train-venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/Qwen3.5-9B-4bit', revision='8b2b98c00a6b4d291155e4890773ca8f769aee53', local_dir='data/models/qwen3.5-9b-4bit')"
```

The two weight shards total about 5.95 GB; allow additional disk space for tokenizer assets and downloads. Runtime memory also includes caches, retrieval and the operating system. The application uses an 8192-token budget, not the advertised maximum context. A successful inference check on a 16 GB Mac does not prove that training fits.

Dense retrieval additionally needs the pinned local BGE-M3 artifact:

```bash
.train-venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/bge-m3-mlx-fp16', revision='a37eddded9a6a1273a87fb8b0da0d1cdbd98aeec', local_dir='data/models/bge-m3-mlx-fp16')"
```

Its weights occupy about 1.14 GB. The native dense representation uses 1024-dimensional CLS pooling with L2 normalization. An old database with unlabelled or differently generated vectors cannot establish compatibility; retain it and use explicit text search until a separately authorized reindex. Do not relabel old vectors as native MLX vectors. Image inference uses the same local Qwen weights through MLX-VLM, with separate image bounds and readiness checks.

The main QA worker loads local weights directly. Ollama is not the Mac QA installation target. See [model selection](docs/model-selection.md) for the distinction between this MLX artifact, the screenshot's distilled model and historical Ollama measurements. Keep the old 3B files and adapter evidence; they cannot be reused as a 9B adapter.

## Local documents and verification

Copy `.env.example` to `.env` only if no settings file exists. Set `AGENT_SRC_DIR` and `AGENT_DATA_DIR` to your permitted source and data directories. An existing `DEFAULT_CHAT_MODEL` override wins over the platform default; review old overrides during migration.

```bash
.venv/bin/python scripts/dev.py check --profile local --require-model
.venv/bin/python scripts/dev.py serve --profile local
```

Check the model and health endpoints after restarting the backend. Make a real sourced request and review its answer and citations. Installed weights, reported readiness and verified model quality are separate checks. Embeddings and image recognition have their own local artifact requirements and must not silently fall back to cloud or daemon inference. Do not replace the serving database or mix embedding spaces as part of QA model setup.

Synchronize new permitted sources explicitly in the application; local startup does not index automatically. Review recognized text and formulas against the original. Installing packages or weights does not approve a dataset, start training or activate an adapter.

## Working without a network

Install dependencies, both pinned model artifacts and the selected OCR engine while a network is available. Keep the complete tokenizer, processor, template, configuration and weight files together. The application must load these local files; missing files must produce a readiness error rather than start a download or select a hosted model.

Start the local profile after installation, then verify sourced QA, dense retrieval, image recognition, practice, history and timetable export with outbound connections denied. Confirm the selected artifact and review the actual answers separately. A healthy web page or a successful file export alone does not establish that these features work offline. Offline execution does not guarantee correct answers; source checks and unresolved-result labels still apply.

Keep installation and update operations separate from ordinary use. See [Testing](docs/testing.md) for acceptance boundaries and [data preparation](docs/data-preparation.md) for the complete-page review process.

## Optional OCR

Apple Vision support can be built after installing Xcode Command Line Tools:

```bash
mkdir -p data/runtime
swiftc scripts/apple_vision_ocr.swift -o data/runtime/apple_vision_ocr
```

Tesseract is optional and requires its binary and language data. OCR availability does not establish extraction quality. See [usage](docs/usage.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Imports fail | Use `.venv/bin/python`, install the selected requirements and run `pip check`. |
| Port 8000 is occupied | Stop your own server or choose another `PORT`; do not stop unrelated services. |
| Model unavailable | Check the native runtime, local weight shards/tokenizer, environment overrides and `check --profile local --require-model`. |
| Library is empty | Use demo or add permitted sources and synchronize the local library. |
| New code does not appear | Check the serving process, restart it if needed and refresh browser assets. |

For verification commands, see [Testing](docs/testing.md).
