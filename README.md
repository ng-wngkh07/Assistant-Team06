# Study Agent

Study Agent helps learners ask questions about their own course materials and check the answer against the source. It runs a local document library, retrieval pipeline and language model behind a FastAPI web application.

## What you can do

- Ask questions about an indexed document or an uploaded attachment. Keep follow-up questions within the same source and open the supporting passages.
- Read PDFs and extracted documents in the document viewer, search their content and download the original file.
- Generate quizzes and source-based flashcards. Check the cited passage before relying on a generated answer.
- Find exercises, review uncertain answers and keep a local conversation history.
- Extract a timetable from an image, correct the draft and confirm it before saving or exporting.

Question answering is the core product. An answer should identify its evidence, preserve the source's conditions and say when the available material is insufficient. A valid citation alone does not prove that the answer is correct.

## Quick start

### Windows

With Git installed:

```powershell
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 -DemoOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile demo
```

See [Windows setup](README_WINDOWS.md) for installation without Git and local models.

### macOS

Install Git and Python 3.12, then run:

```bash
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/dev.py serve --profile demo
```

Open **http://127.0.0.1:8000**. The demo builds a separate library from public test fixtures. Document search works without a language model; answers, embeddings and image recognition require the configured local runtimes. See [macOS setup](README_MAC.md).

## Use your own materials

Copy `.env.example` to `.env` if you do not already have local settings. Configure source and data directories, prepare the required models, then start with `--profile local`. Synchronize the library in the web application; local startup does not automatically index your files. You can also attach a supported document directly in chat.

Supported uploads include PDF, DOCX, PPTX, TXT, Markdown, PNG, JPEG and WebP, up to 20 MB. Image content requires usable OCR. Extracted DOCX sections are reading sections, not original printed page numbers.

## Documentation

| Guide | Purpose |
| --- | --- |
| [Usage](docs/usage.md) | Sources, attachments, answer states and study tools |
| [Architecture](docs/architecture.md) | Components, data flow and API boundaries |
| [Configuration](docs/configuration.md) | Profiles, storage and local runtimes |
| [Deployment](docs/deployment.md) | Running, updating and checking the application |
| [Testing](docs/testing.md) | Regression checks and real-model evaluation |
| [Roadmap](docs/roadmap.md) | Priorities and acceptance criteria |
| [Contributing](CONTRIBUTING.md) | Changes, review and pull requests |
| [Changelog](CHANGELOG.md) | Product changes |
| [Security](SECURITY.md) | Data handling and vulnerability reporting |

The API reference is available at `/docs` on a running instance.

## Current limits

The application targets a trusted local environment. It does not yet provide authenticated users, per-user source permissions or a service-level availability guarantee. Local model work is serialized. Do not expose the API as a shared public service without addressing access control and deployment hardening.

Generated content still needs source review, especially formulas, OCR output and multiple-choice answers. Software tests and small synthetic model evaluations do not establish accuracy across courses. See [testing](docs/testing.md) for the acceptance process.

## License

Code is available under the [MIT license](LICENSE). Documents, images and model weights retain their own licenses and access restrictions.
