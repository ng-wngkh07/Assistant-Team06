# Windows setup

Use Windows 10/11 64-bit and PowerShell. The web dependency profile uses Python 3.12. MLX is not part of the Windows runtime.

## Install and run

If Git is installed:

```powershell
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 -DemoOnly
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile demo
```

Open `http://127.0.0.1:8000`. Stop with Ctrl+C. `ExecutionPolicy Bypass` applies to this process; it does not change the machine-wide policy.

Without Git, download the [main ZIP](https://github.com/ng-wngkh07/Assistant-Team06/archive/refs/heads/main.zip), extract it and run the DemoOnly setup from that directory. Reopen PowerShell after tool installation. Clone a fresh checkout before contributing; a ZIP has no Git history.

## Full model setup

Run setup without `-DemoOnly` to prepare the tools and models listed in `requirements-windows-tools.json`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile local
```

The installer uses WinGet and may request installation permissions or package agreements. Model downloads require additional disk space and network access. Existing settings are preserved; a mismatch with the manifest must be resolved explicitly rather than silently replacing your provider configuration.

Copy `.env.example` to `.env` if needed. Configure source and data paths for your own library, then synchronize it in the application. Local startup does not automatically index documents. Search does not need a language model; question answering, embeddings and image recognition need their configured runtimes.

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
| Library is empty | Use `-Profile demo`, or add permitted documents and synchronize the local library. |
| Port is occupied | Stop your own server or change `PORT` in `.env`. |
| Invalid `.env` setting | Use supported variables from `.env.example`; do not copy private machine paths into Git. |

Report the revision, OS, Python version, profile and reproduction steps. Remove private content from logs and screenshots.
