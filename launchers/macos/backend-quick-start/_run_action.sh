#!/bin/zsh
set -euo pipefail

ACTION="${1:-status}"
if [[ $# -gt 0 ]]; then
  shift
fi

SCRIPT_DIR="${0:A:h}"
CONFIG_FILE="${STUDY_BACKEND_CONFIG:-$SCRIPT_DIR/backend.config.json}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: Missing backend service config: $CONFIG_FILE" >&2
  echo "Copy $SCRIPT_DIR/config.example.json to $SCRIPT_DIR/backend.config.json and configure explicit local paths first." >&2
  exit 1
fi

HOST_PY="$(command -v python3 || true)"
if [[ -z "$HOST_PY" ]]; then
  echo "ERROR: python3 is required to bootstrap the Study Agent launcher." >&2
  exit 1
fi

RESOLVED_PATHS=$("$HOST_PY" -c '
import json, pathlib, sys
cfg_path = pathlib.Path(sys.argv[1])
data = json.loads(cfg_path.read_text(encoding="utf-8"))
if not isinstance(data, dict) or data.get("configured") is False:
    sys.stderr.write(f"ERROR: Config file is unconfigured (configured=false): {cfg_path}\n")
    sys.exit(1)
raw_code = str(data.get("code_dir") or "").strip()
if not raw_code or "<" in raw_code or "PLACEHOLDER" in raw_code.upper():
    sys.stderr.write(f"ERROR: Config key code_dir is unconfigured in {cfg_path}\n")
    sys.exit(1)
base = cfg_path.resolve().parent
code_dir = pathlib.Path(raw_code).expanduser()
if not code_dir.is_absolute():
    code_dir = base / code_dir
raw_py = str(data.get("python_bin") or "").strip()
if raw_py and "<" not in raw_py and "PLACEHOLDER" not in raw_py.upper():
    py_bin = pathlib.Path(raw_py).expanduser()
    if not py_bin.is_absolute():
        py_bin = base / py_bin
else:
    py_bin = code_dir / ".venv" / "bin" / "python"
print(str(code_dir))
print(str(py_bin))
' "$CONFIG_FILE")

CODE_DIR="${RESOLVED_PATHS%%$'\n'*}"
PY_BIN="${RESOLVED_PATHS#*$'\n'}"
CONTROLLER="$CODE_DIR/scripts/backend_service.py"

if [[ ! -f "$CONTROLLER" ]]; then
  echo "ERROR: Controller script not found at $CONTROLLER" >&2
  exit 1
fi

if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="$HOST_PY"
fi

exec "$PY_BIN" "$CONTROLLER" --config "$CONFIG_FILE" "$ACTION" "$@"
