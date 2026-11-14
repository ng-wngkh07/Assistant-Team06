# Windows setup

This guide is for operators and developers running the backend. Learners open the hosted website by link and do not need this installation.

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

Run setup without `-DemoOnly` to prepare the tools and models listed in `requirements-windows-tools.json` (Git, Python 3.12, Node.js LTS, Ollama, and Tesseract OCR for literal timetable table recognition):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile local
```

The installer uses WinGet and may request installation permissions or package agreements. Model downloads require additional disk space and network access. Existing settings are preserved; a mismatch with the manifest must be resolved explicitly rather than silently replacing your provider configuration.

Copy `.env.example` to `.env` if needed. Configure source and data paths for your own library, then synchronize it in the application. Local startup reuses existing data. Export `$env:STUDY_SRC_AUTO_WATCH = '1'` before launch for opt-in new-file ingestion, or synchronize manually. Changed/missing reviewed originals require operator review/re-indexing. Account uploads stay in separate private storage. See [configuration](docs/configuration.md#automatic-source-ingestion). Search does not need a language model; question answering, embeddings and image recognition need their configured runtimes.

## Prepared libraries and platform limits

Standard `--profile local` startup resolves your configured shared source directory (`AGENT_SRC_DIR`, defaulting to `src\`) and runtime data directory (`AGENT_DATA_DIR`, defaulting to `data\`), even when `src\` does not exist yet or an unused `resources\knowledge-packs.json` file is present. Automatic `data\user-library\` seeding and bundled-descriptor routing are retired; missing libraries stay honestly empty until populated or pointed at prepared data.

The application still supports explicit operator import of verified knowledge packs (`bge-m3-ollama-l2-v1` portable packs on Windows) when both `KNOWLEDGE_PACK_PATH` and `KNOWLEDGE_PACK_SHA256` are set in `.env` or the process environment and you run `.venv\Scripts\python.exe scripts\dev.py --install-pack --profile local`. Read [knowledge packs](docs/knowledge-packs.md) for privacy, license, and protocol specifications. Do not import native macOS BGE vectors under an Ollama model; cross-protocol isolation prevents incompatible index loading.

If migrating from an older checkout containing `data\user-library\`, stop the backend, inventory and back up that entire folder manually outside the repository before archiving; the launcher never deletes, moves or merges it automatically.

After full Windows setup, `Start Study Agent.bat` starts the local Ollama service, checks model readiness and opens the application. The public 144-chunk pack was verified on a Mac host running local Ollama; physical Windows machine acceptance with native PowerShell setup remains `NOT_RUN` on macOS hosts.

## First-use verification

1. After configuring `.env` or prepared `src\` / `data\` paths, run `.venv\Scripts\python.exe scripts\dev.py serve --profile local` (or double-click `Start Study Agent.bat`). Keep its window open while using it; close the server with Ctrl+C when finished.
2. Use **Kiểm tra mô hình**, then ask a question covered by the configured library. Open the cited passage and original document.
3. In study tools, choose a broad topic, count and difficulty. Check the actual returned count, answer a question and inspect its explanation. A partial-result notice means the requested set is incomplete.
4. Generate a document study set, check the source and flip a flashcard. Review timetable image recognition before confirming rows and exporting the semester calendar.
5. Restart with the same launcher. Configured sources, private account storage (`STUDY_ACCOUNTS_DIR` or `data\accounts\`), local history and confirmed calendar remain durable and should persist without a new embedding build.

See [Using Study Agent](docs/usage.md) for source scopes, uploads, result states, study materials, history and [the calendar procedure](docs/timetable-workflow.md). Model setup is a one-time download; ordinary inference uses local resources. Additional personal documents need local extraction/indexing, not language-model retraining.

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
| Library is empty | Point `AGENT_SRC_DIR` and `AGENT_DATA_DIR` to your permitted library, or install a compatible Ollama pack explicitly with `--install-pack` (`KNOWLEDGE_PACK_PATH` + `KNOWLEDGE_PACK_SHA256`). `--profile demo` is search-only. |
| Port is occupied | Stop your own server or change `PORT` in `.env`. |
| Invalid `.env` setting | Use supported variables from `.env.example`; do not copy private machine paths into Git. |

Report the revision, OS, Python version, profile and reproduction steps. Remove private content from logs and screenshots.
