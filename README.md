# Study Agent

Study Agent helps learners ask questions about their own course materials and check the answer against the source. It runs a local document library, retrieval pipeline and language model behind a FastAPI web application.

## What you can do

- Ask questions about an indexed document or an uploaded attachment. Keep follow-up questions within the same source and open the supporting passages.
- Read PDFs and extracted documents in the document viewer, search their content and download the original file.
- Generate quizzes and source-based flashcards. Check the cited passage before relying on a generated answer.
- Find exercises, review uncertain answers and keep a local conversation history. Practice uses its fixed careful verification workflow.
- Start QA in **Nhanh** (fast) and select **Kiểm tra kỹ** (careful) for an additional source audit. Complex questions can prompt a suggestion; the user controls the choice.
- Extract a timetable from an image, correct the draft and confirm it before saving or exporting.

Question answering is the core product. An answer should identify its evidence, preserve the source's conditions and say when the available material is insufficient. A valid citation alone does not prove that the answer is correct.

For a prepared local library, follow [knowledge pack installation](docs/knowledge-packs.md#user-installation-and-no-index-workflow). An application bundle includes the matching pack and its configuration; recipients do not prepare an index or edit pack settings. Install the platform's dependencies and local models once. The pack supplies approved originals, source passages and compatible vectors. Private training data and the publisher's working database are not needed. The public sample covers twelve subjects; it is a demonstration library, not a complete course collection.

## Quick start

### Choose a starting library

For generated answers, quizzes, flashcards and timetable recognition, use the [macOS](README_MAC.md#start-with-a-prepared-library) or [Windows](README_WINDOWS.md#prepared-libraries-and-platform-limits) full-model instructions with a matching prepared pack. Model and pack downloads are one-time setup steps. Follow the first-use checks in the platform guide before relying on generated content.

On a fresh clone, the **search-only demo** runs immediately with **no model downloads required**:
- SQLite FTS5 keyword search, document viewing, and extractive reading work out of the box using public synthetic fixtures in `tests/fixtures/documents`.
- Full generative QA, semantic embeddings, and timetable vision require platform-specific local model runtimes described below.
- **Privacy-safe per-user local indexing**: All document parsing, index storage (`data/`), conversation history, and model execution remain strictly local to your machine. There is no requirement or mechanism to publish private indexes, vectors, or documents to Git.

### Windows

With Git installed:

```powershell
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 -DemoOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile demo
```

For the search-only demo, Ollama is optional. For full local QA on Windows, see [Windows setup](README_WINDOWS.md). MLX is Apple-only and not supported on Windows.

### macOS

Install Git and Python 3.12, then run:

```bash
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/dev.py serve --profile demo
```

Open **http://127.0.0.1:8000**. The demo builds an isolated library from public test fixtures. Keyword search and document viewing work immediately without downloading models. Full QA on Apple Silicon uses the native MLX runtime. See [macOS setup](README_MAC.md).

## QA model and platform contracts

- **macOS (Apple Silicon)**: Uses **Qwen3.5 9B 4-bit directly through MLX** (native Python 3.13 runtime in `.train-venv/`) with an 8192-token operational budget, plus native BGE-M3 fp16 for dense embeddings. Cloud fallbacks and hosted APIs are prohibited.
- **Windows**: Uses **local Ollama** (`qwen3.5:9b`, `bge-m3`). The Apple-only MLX runtime is explicitly marked `NOT_RUN` / platform unsupported on Windows. Launcher scripts check and propagate endpoint readiness before reporting QA ready.
- **Failure diagnostics**: If local weights or runtimes are missing, the application displays actionable diagnostics and continues serving model-free search; it never silently downloads unapproved models or fabricates unverified fallback text as model output.

## Use your own materials

In an application bundle, the launcher creates a writable personal library at `data/user-library/src/` and seeds its index from the publisher's precomputed data. Upload additional documents through the application; it saves the originals and processes the new files locally. The library area shows the actual source directory. Restarting preserves the originals, working index, history and timetable. These additions are private and do not enter the publisher's pack or automatically become approved training data.

For a source checkout without a bundled pack, copy `.env.example` to `.env` if you do not already have local settings. Configure source and data directories, prepare the required models, then start with `--profile local`. Files copied directly into a source directory require synchronization in the application; startup does not watch for filesystem changes.

Supported uploads include PDF, DOCX, PPTX, TXT, Markdown, PNG, JPEG and WebP, up to 20 MB. Images can be attached and viewed without readable text; source-based QA and study generation require readable extracted content. Extracted DOCX sections are reading sections, not original printed page numbers.

## Documentation

| Guide | Purpose |
| --- | --- |
| [Usage](docs/usage.md) | Sources, attachments, answer states and study tools |
| [Architecture](docs/architecture.md) | Components, data flow and API boundaries |
| [Configuration](docs/configuration.md) | Profiles, storage and local runtimes |
| [Local performance](docs/local-performance.md) | Answer latency, safe caches and local measurement |
| [Context management](docs/context-management.md) | Conversation budget, warnings and memory tradeoffs |
| [Question complexity](docs/question-complexity.md) | Suggestion policy, verification risk and future labeling |
| [Model migration](docs/model-selection.md) | Exact QA artifact, evidence, setup and rollback |
| [RAG and future training](docs/rag-and-training.md) | Hybrid retrieval, top-K, chunking, local distribution and teacher supervision |
| [Data preparation](docs/data-preparation.md) | Every-page inventory, source review, vector integrity and training gates |
| [Document distribution](docs/data-distribution.md) | Publication rights, privacy, full-library coverage and safe knowledge-pack delivery |
| [Timetable workflow](docs/timetable-workflow.md) | Recognition, user review, confirmation and calendar export |
| [Deployment](docs/deployment.md) | Running, updating and checking the application |
| [Testing](docs/testing.md) | Regression checks and real-model evaluation |
| [Roadmap](docs/roadmap.md) | Priorities and acceptance criteria |
| [Contributing](CONTRIBUTING.md) | Changes, review and pull requests |
| [Changelog](CHANGELOG.md) | Product changes |
| [Security](SECURITY.md) | Data handling and vulnerability reporting |

The [extended public debugging samples](tests/fixtures/EXTENDED_CORPUS.md) add 12 synthetic subjects, 96 review cases and a 14-course timetable. They contain no personal documents or schedules.

The API reference is available at `/docs` on a running instance.

## Current limits

The application targets a trusted local environment. It does not yet provide authenticated users, per-user source permissions or a service-level availability guarantee. Local model work is serialized. Do not expose the API as a shared public service without addressing access control and deployment hardening.

Generated content still needs source review, especially formulas, OCR output and multiple-choice answers. Software tests and small synthetic model evaluations do not establish accuracy across courses. See [testing](docs/testing.md) for the acceptance process.

## License

Code is available under the [MIT license](LICENSE). Documents, images and model weights retain their own licenses and access restrictions.
