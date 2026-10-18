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

## Local models and documents

Install [Ollama](https://ollama.com/download/mac). Start its service once and prepare the default development models:

```bash
ollama pull qwen2.5:3b
ollama pull bge-m3
ollama pull qwen2.5vl:3b
ollama list
```

Copy `.env.example` to `.env` only if no settings file exists. Set `AGENT_SRC_DIR` and `AGENT_DATA_DIR` to your own source and data directories. Preserve existing provider settings unless you intend to change them.

```bash
.venv/bin/python scripts/dev.py check --profile local --require-model
.venv/bin/python scripts/dev.py serve --profile local
```

Synchronize the library in the application and check a phrase that appears in a source. Local startup does not index automatically. Review image recognition and formulas against the original document.

## Optional MLX runtime

MLX requires Apple Silicon and a compatible native Python/macOS environment. The separate dependency profile uses Python 3.13:

```bash
python3.13 -m venv .train-venv
.train-venv/bin/python -m pip install -r requirements-mlx.txt
.train-venv/bin/python -m pip check
```

Installing these packages does not download model weights or authorize training. Configure approved model artifacts separately. Keep the web process and the MLX runtime distinct; do not activate an unreviewed adapter to make a check pass. See [configuration](docs/configuration.md).

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
| Model unavailable | Check Ollama, model names and `check --profile local --require-model`. |
| Library is empty | Use demo or add permitted sources and synchronize the local library. |
| New code does not appear | Check the serving process, restart it if needed and refresh browser assets. |

For verification commands, see [Testing](docs/testing.md).
