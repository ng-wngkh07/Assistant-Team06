@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Follow the installation steps in README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\dev.py serve --profile local --start-model-service --require-model --open-browser
if errorlevel 1 (
  echo Startup failed. Read the message above and retry after correcting the configuration.
  pause
  exit /b 1
)
endlocal
