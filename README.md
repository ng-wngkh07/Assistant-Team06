# Study Agent

Study Agent is a source-grounded learning web application. Learners ask questions, open the cited originals, generate practice and plan study time. The browser interface is also labelled **ARYM — Adaptive Revision Yields Mastery**. One FastAPI backend in `app/` serves the primary interface in `static/`.

## Choose how to use it

| Mode | What you need | Where processing and data live |
| --- | --- | --- |
| Hosted website | The operator's HTTPS URL and an invited account | On the configured backend host; uploads leave your browser and are stored privately for your account |
| Operator/developer backend | Git/ZIP, Python and the platform setup below | On the backend host; generation needs separately installed local models |
| Developer search-only demo | Web dependencies and public synthetic fixtures | An isolated `data/team-demo` library, with no model download |

Hosted deployment is the main delivery target, with account uploads and histories stored separately on the server; local source-checkout execution remains supported for operators and developers. This repository does not publish a live service URL or include private course documents, databases, vectors or weights. A GitHub push is code publication, not a running backend. Operators follow [deployment](docs/deployment.md) and the [native host runbook](docs/native-host-pilot.md) — including zero-cost HTTPS options via Cloudflare Quick Tunnel (`*.trycloudflare.com`), Tailscale Funnel (`*.ts.net`), or DuckDNS + Caddy TLS — before sharing a link.

## What you can do

- Ask questions about an indexed document or account-private attachment, retain source scope in follow-ups and open the supporting passages and originals.
- Search and read documents, inspect PDF pages or extracted reading sections, and download authorized originals.
- Generate quizzes, flashcards and topic practice; inspect the actual returned count, answer, explanation and source.
- Use **Nhanh** (Fast) or **Kiểm tra kỹ** (Careful). Careful adds a source audit; it does not guarantee correctness.
- Review recognized timetable rows, add commitments and deadlines, approve study sessions and export an iCalendar file.
- Keep account-isolated uploads, conversation history and planner data across backend restarts. Browser-only review items have separate persistence limits.

An answer must preserve the source's conditions and acknowledge insufficient evidence. A citation identifier, exact quotation or successful model call alone does not prove the answer correct.

## Operator/developer quick start: search-only demo

### macOS

Install Git and Python 3.12, then run:

```bash
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/dev.py serve --profile demo
```

### Windows

With Git installed, open PowerShell:

```powershell
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 -DemoOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile demo
```

Without Git, download the [main ZIP](https://github.com/ng-wngkh07/Assistant-Team06/archive/refs/heads/main.zip), extract it and run the same setup/start scripts from the extracted directory.

Open **http://127.0.0.1:8000**. Stop your server with Ctrl+C. Keyword search and document viewing use public fixtures without model downloads. Generated QA, semantic retrieval and image recognition need the [macOS](README_MAC.md) or [Windows](README_WINDOWS.md) full-model setup. Demo startup builds only its isolated fixture index.

## Operator backend and source ingestion

Learners use the operator's HTTPS link and invited account; no backend or model installation is needed on their computers. Operators use the platform guide to install model prerequisites on the backend host. Configure permitted source and data directories using [.env.example](.env.example) and [configuration](docs/configuration.md), then run `python scripts/dev.py check --profile local --require-model` and `python scripts/dev.py serve --profile local` with the project Python executable (or manage a session-detached macOS backend instance with [scripts/backend_service.py](scripts/backend_service.py) and the reusable templates in [launchers/macos/backend-quick-start/](launchers/macos/backend-quick-start/README.md) using a private JSON config outside Git). By default, `--profile local` uses configured `AGENT_SRC_DIR` (`src`) and `AGENT_DATA_DIR` (`data`) and reports a missing/empty library honestly rather than seeding a sample `data/user-library`; explicit operator import of a reviewed [knowledge pack](docs/knowledge-packs.md) uses `--install-pack` with both `KNOWLEDGE_PACK_PATH` and `KNOWLEDGE_PACK_SHA256`, and no public pack release or complete course library is claimed here.

Local guest use can explicitly save uploads into the configured shared library. Authenticated uploads instead stay in the account's private store and cannot be added to shared `src/` through that upload control. Guest instant attachments are temporary; account originals and records are durable. See [usage](docs/usage.md) and [persistence](docs/service-persistence.md) for the distinction and backup procedure.

Operators can enable new-file detection with `STUDY_SRC_AUTO_WATCH=1` in the launching process environment. The watcher is off by default. Settled new supported files in `AGENT_SRC_DIR` are processed incrementally; changed or missing indexed originals require operator review/re-indexing and are excluded while stale. Without the watcher, synchronize manually. It neither watches account-private storage nor publishes documents or trains weights. See [configuration](docs/configuration.md#automatic-source-ingestion).

Supported attachments include PDF, DOCX, PPTX, TXT, Markdown, PNG, JPEG and WebP, up to 20 MB. Source-based generation requires readable content. DOCX reading sections are not physical print pages; PPTX image previews need a compatible local renderer or source-bound static rendition.

## Models and release limits

Apple Silicon uses pinned local **Qwen3.5 9B 4-bit through MLX** and native BGE-M3, with a separate native runtime and an 8192-token operational context. Windows uses local Ollama; this application's native MLX path is unsupported there. Model execution stays on the backend host, with no external model API or automatic cloud fallback. Public weight downloads are a separate installation step.

The selected base has **no accepted active adapter**. Adding or indexing a document changes retrieval, not model weights. Training and activation require separate permission and data, memory and quality gates. See [model selection](docs/model-selection.md) and [RAG and LoRA requirements](docs/lora-architecture.md).

The account/persistence and request-admission foundation targets a ~20-user single-host pilot with per-account `dialogue.lock` isolation, bounded FIFO model queueing (`acquire_inference`), sliding-window rate limits, early body caps, HTTP/HTTPS security headers, per-account upload quotas, and `/api/health` + `backend_service.py` infrastructure checks, while keeping future multi-host scaling cleanly behind `AccountService`. Full hosted source/model acceptance, durable long-job handling, measured capacity and operational recovery remain separate release criteria. Do not infer production readiness from an available page or green tests. Source content, formulas, OCR and generated alternatives still need review.

## Documentation and contributions

- [All guides](docs/README.md): learner, operator, developer and data/model workflows.
- [Usage](docs/usage.md), [study planning](docs/study-planning.md) and [timetable review](docs/timetable-workflow.md).
- [Deployment](docs/deployment.md), [configuration](docs/configuration.md) and [service persistence](docs/service-persistence.md).
- [Architecture](docs/architecture.md), [testing](docs/testing.md), [contributing](CONTRIBUTING.md) and [changelog](CHANGELOG.md).

Changes affecting workflows or use must update README, CONTRIBUTING and the relevant guides in the same change; an unaffected entry file needs a specific PR explanation. See the [documentation rule](docs/documentation.md#required-documentation-updates). The running `/docs` page is the API reference.

The [public debugging fixtures](tests/fixtures/EXTENDED_CORPUS.md) cover twelve synthetic subjects and a fictional timetable. They are test inputs, not a complete real learning library or an approved training dataset.

## License

Code uses the [MIT license](LICENSE). Documents, images and model weights retain their own licenses and access restrictions. Keep credentials and private runtime assets outside public Git; see [Security](SECURITY.md).
