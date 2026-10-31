# Windows setup

Use Windows 10/11 64-bit and PowerShell. The web dependency profile uses Python 3.12. **MLX is Apple Silicon only and not supported on Windows** (marked `NOT_RUN` / platform unsupported). Full QA on Windows uses local Ollama.

## Clean-clone search-only demo (no model download needed)

You can run the web interface and document keyword search immediately without downloading any model weights or installing Ollama:

```powershell
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 -DemoOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile demo
```

Open `http://127.0.0.1:8000`. Stop with Ctrl+C. The demo uses public fixtures in `tests/fixtures/documents` and isolated SQLite FTS5 search in `data/team-demo`. All user data remains local; no private indexes or vectors need to be published to Git. `ExecutionPolicy Bypass` applies only to the process and does not alter system-wide security policies.

Without Git, download the [main ZIP](https://github.com/ng-wngkh07/Assistant-Team06/archive/refs/heads/main.zip), extract it and run the DemoOnly setup from that directory. Reopen PowerShell after tool installation. Clone a fresh checkout before contributing; a ZIP has no Git history.

## Full model setup

Run setup without `-DemoOnly` to prepare the tools and models listed in `requirements-windows-tools.json`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile local
```

The installer uses WinGet and may request installation permissions or package agreements. Model downloads require additional disk space and network access. Existing settings are preserved; a mismatch with the manifest must be resolved explicitly rather than silently replacing your provider configuration.

Copy `.env.example` to `.env` if needed. Configure source and data paths for your own library, then synchronize it in the application. Local startup does not automatically index documents. Search does not need a language model; question answering, embeddings and image recognition need their configured runtimes.

## Prepared libraries and platform limits

The ready-to-use distribution supports publisher-prepared knowledge libraries so recipients do not need to collect documents or run local embedding builds. Read [knowledge packs](docs/knowledge-packs.md) for privacy, license, and protocol specifications.

The application supports both macOS native MLX packs and cross-platform Ollama portable packs (`bge-m3-ollama-l2-v1`). A publisher application bundle includes its Ollama pack and a descriptor with the trusted SHA256, so Windows recipients do not need to edit pack settings or run indexing. The launcher creates `data/user-library/src/` for the original library and persistent personal uploads. Advanced source-checkout installation can still use `KNOWLEDGE_PACK_PATH` and `KNOWLEDGE_PACK_SHA256` in `.env`. Do not import native macOS BGE vectors under an Ollama model; cross-protocol isolation prevents incompatible index loading.

After full Windows setup, `Start Study Agent.bat` starts the local Ollama service, checks model readiness and opens the application. The public 144-chunk pack was verified on a Mac host running local Ollama; physical Windows machine acceptance with native PowerShell setup remains `NOT_RUN` on macOS hosts.

## First-use verification

1. After setup, double-click `Start Study Agent.bat` in the extracted application bundle. Keep its `resources/` directory with the application and its window open while using it; close the server with Ctrl+C when finished.
2. Use **Kiểm tra mô hình**, then ask a question covered by the installed library. Open the cited passage and original document.
3. In study tools, choose a broad topic, count and difficulty. Check the actual returned count, answer a question and inspect its explanation. A partial-result notice means the requested set is incomplete.
4. Generate a document study set, check the source and flip a flashcard. Review timetable image recognition before confirming rows and exporting the semester calendar.
5. Restart with the same launcher. The installed library, personal history and confirmed calendar remain local and should persist without a new embedding build.

See [Using Study Agent](docs/usage.md) for source scopes, uploads, result states, study materials, history and [the calendar procedure](docs/timetable-workflow.md). Models and the pack are one-time downloads; ordinary inference uses local resources. Additional personal documents need local extraction/indexing, not language-model retraining.

## Verify

```powershell
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe scripts/dev.py check --profile local --require-model
.\test_windows.ps1
```

See [Testing](docs/testing.md) for UI checks and model acceptance. A demo installer check does not verify a full installation on a new machine or the quality of model output.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| A `.ps1` file opens in an editor | Invoke it with `powershell -NoProfile -ExecutionPolicy Bypass -File`. |
| Wrong Python version | Use the project `.venv` and inspect setup output; preserve the old environment before replacing it. |
| Ollama or a model is missing | Check the installer manifest, local service and `ollama list`. |
| Library is empty | Install the publisher's compatible Ollama pack and trusted SHA256. The demo is search-only; synchronize only additional personal documents. |
| Port is occupied | Stop your own server or change `PORT` in `.env`. |
| Invalid `.env` setting | Use supported variables from `.env.example`; do not copy private machine paths into Git. |

Report the revision, OS, Python version, profile and reproduction steps. Remove private content from logs and screenshots.
