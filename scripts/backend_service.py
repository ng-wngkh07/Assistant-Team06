"""Native macOS operator recovery and supervision controller for Study Agent."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
import urllib.error
import urllib.request
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback for static import tests
    fcntl = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "data" / "runtime" / "backend-service.json"
DEFAULT_SERVER_APP = "app.server:app"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
PLACEHOLDER_PATTERNS = (
    "<",
    ">",
    "PLACEHOLDER",
    "/PATH/TO/",
    "/ABSOLUTE/PATH/",
    "CHANGE_ME",
)
SERVER_APP_RE = re.compile(r"^[A-Za-z0-9_.]+:[A-Za-z0-9_.]+$")


class ServiceError(RuntimeError):
    """Actionable operator-facing controller error."""

    def __init__(self, message: str, *, exit_code: int = 1, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.details = details or {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_bool(value: Any, name: str, *, default: Optional[bool] = None) -> bool:
    if value is None:
        if default is None:
            raise ServiceError(f"Missing required boolean config key '{name}'.")
        return default
    if isinstance(value, bool):
        return value
    raise ServiceError(f"Config key '{name}' must be a boolean (true/false), got {type(value).__name__}.")


def _normalize_int(value: Any, name: str, *, minimum: int = 1, maximum: Optional[int] = None, default: Optional[int] = None) -> int:
    if value is None:
        if default is None:
            raise ServiceError(f"Missing required integer config key '{name}'.")
        value = default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ServiceError(f"Config key '{name}' must be an integer.")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ServiceError(f"Config key '{name}' out of range ({bound}): {value}.")
    return value


def _normalize_float(value: Any, name: str, *, minimum: float = 0.05, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ServiceError(f"Config key '{name}' must be numeric.")
    num = float(value)
    if num < minimum:
        raise ServiceError(f"Config key '{name}' must be >= {minimum}, got {num}.")
    return num


def _has_placeholder(text: str) -> bool:
    upper = text.upper()
    return any(token in text or token in upper for token in PLACEHOLDER_PATTERNS)


def _require_path_str(raw: Dict[str, Any], key: str, *, base_dir: Optional[Path] = None) -> Path:
    val = raw.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ServiceError(
            f"Config key '{key}' is missing or empty. Configure all explicit private paths in JSON."
        )
    text = val.strip()
    if _has_placeholder(text):
        raise ServiceError(
            f"Config key '{key}' still contains an unconfigured placeholder ({text}). Replace template placeholders first."
        )
    p = Path(text).expanduser()
    return p if p.is_absolute() else ((base_dir or REPO_ROOT) / p)


def resolve_config_path(explicit_config: Optional[str]) -> Path:
    if explicit_config and explicit_config.strip():
        return Path(explicit_config.strip()).expanduser()
    env_cfg = os.environ.get("STUDY_BACKEND_CONFIG", "").strip()
    if env_cfg:
        return Path(env_cfg).expanduser()
    return DEFAULT_CONFIG_PATH


def load_and_validate_config(config_path: Path, *, check_runtime_paths: bool = True) -> Dict[str, Any]:
    """Load strict JSON configuration and optionally validate real target paths without seeding."""
    if not config_path.is_file():
        raise ServiceError(
            f"Backend service config not found: {config_path}. "
            "Copy launchers/macos/backend-quick-start/config.example.json to backend.config.json and configure explicit paths."
        )
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ServiceError(f"Invalid JSON in config file {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ServiceError(f"Config file {config_path} must contain a JSON object.")
    if raw.get("configured") is False:
        raise ServiceError(
            f"Config file {config_path} is still marked 'configured': false. "
            "Review and update all explicit local paths, then set 'configured': true."
        )

    cfg_dir = config_path.resolve().parent
    code_dir = _require_path_str(raw, "code_dir", base_dir=cfg_dir)

    def _opt_path(key: str, default_path: Path) -> Path:
        val = raw.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            return default_path
        return _require_path_str(raw, key, base_dir=cfg_dir)

    python_bin = _opt_path("python_bin", code_dir / ".venv" / "bin" / "python")
    train_python = _opt_path("train_python", code_dir / ".train-venv" / "bin" / "python")
    agent_src_dir = _require_path_str(raw, "agent_src_dir", base_dir=cfg_dir)
    agent_data_dir = _require_path_str(raw, "agent_data_dir", base_dir=cfg_dir)
    knowledge_base_db = _opt_path("knowledge_base_db", agent_data_dir / "knowledge_base.db")
    study_accounts_dir = _require_path_str(raw, "study_accounts_dir", base_dir=cfg_dir)
    history_db_path = _require_path_str(raw, "history_db_path", base_dir=cfg_dir)
    timetable_db_path = _require_path_str(raw, "timetable_db_path", base_dir=cfg_dir)
    timetable_images_dir = _require_path_str(raw, "timetable_images_dir", base_dir=cfg_dir)
    mlx_model_dir = _require_path_str(raw, "mlx_model_dir", base_dir=cfg_dir)
    mlx_9b_model_dir = _opt_path("mlx_9b_model_dir", mlx_model_dir)
    mlx_bge_model_dir = _require_path_str(raw, "mlx_bge_model_dir", base_dir=cfg_dir)
    study_gpu_runtime_dir = _require_path_str(raw, "study_gpu_runtime_dir", base_dir=cfg_dir)

    default_runtime_base = cfg_dir / ".backend-service"
    state_dir = _opt_path("state_dir", default_runtime_base / "state")
    log_dir = _opt_path("log_dir", default_runtime_base / "logs")

    host = str(raw.get("host") or "127.0.0.1").strip()
    if host not in LOOPBACK_HOSTS:
        raise ServiceError(
            f"Refusing non-loopback bind host '{host}'. Supported bind hosts: {', '.join(sorted(LOOPBACK_HOSTS))}."
        )
    port = _normalize_int(raw.get("port"), "port", minimum=1, maximum=65535, default=8000)

    forwarded_allow_ips = str(raw.get("forwarded_allow_ips") or "127.0.0.1").strip()
    if not forwarded_allow_ips or "*" in forwarded_allow_ips:
        raise ServiceError("Refusing empty or wildcard forwarded_allow_ips; keep explicit trusted proxy IPs (e.g. 127.0.0.1).")

    server_app = str(raw.get("server_app") or DEFAULT_SERVER_APP).strip()
    if not SERVER_APP_RE.match(server_app):
        raise ServiceError(f"Invalid server_app target: {server_app!r}")

    public_url_raw = raw.get("public_url")
    if isinstance(public_url_raw, str) and public_url_raw.strip():
        public_url = public_url_raw.strip().rstrip("/")
        if _has_placeholder(public_url) or not (public_url.startswith("http://") or public_url.startswith("https://")):
            raise ServiceError(f"Invalid configured public_url: {public_url!r}")
    else:
        public_url = f"http://{host}:{port}"

    cfg: Dict[str, Any] = {
        "config_path": config_path.resolve(),
        "code_dir": code_dir,
        "python_bin": python_bin,
        "train_python": train_python,
        "agent_src_dir": agent_src_dir,
        "agent_data_dir": agent_data_dir,
        "knowledge_base_db": knowledge_base_db,
        "study_accounts_dir": study_accounts_dir,
        "history_db_path": history_db_path,
        "timetable_db_path": timetable_db_path,
        "timetable_images_dir": timetable_images_dir,
        "mlx_model_dir": mlx_model_dir,
        "mlx_9b_model_dir": mlx_9b_model_dir,
        "mlx_bge_model_dir": mlx_bge_model_dir,
        "study_gpu_runtime_dir": study_gpu_runtime_dir,
        "state_dir": state_dir,
        "log_dir": log_dir,
        "host": host,
        "port": port,
        "forwarded_allow_ips": forwarded_allow_ips,
        "server_app": server_app,
        "public_url": public_url,
        "study_src_auto_watch": _normalize_bool(raw.get("study_src_auto_watch"), "study_src_auto_watch", default=False),
        "study_accounts_required": _normalize_bool(raw.get("study_accounts_required"), "study_accounts_required", default=True),
        "study_allow_registration": _normalize_bool(raw.get("study_allow_registration"), "study_allow_registration", default=False),
        "study_secure_cookies": _normalize_bool(raw.get("study_secure_cookies"), "study_secure_cookies", default=False),
        "require_nonempty_src": _normalize_bool(raw.get("require_nonempty_src"), "require_nonempty_src", default=False),
        "max_log_bytes": _normalize_int(raw.get("max_log_bytes"), "max_log_bytes", minimum=1024, default=2 * 1024 * 1024),
        "max_log_backups": _normalize_int(raw.get("max_log_backups"), "max_log_backups", minimum=1, maximum=20, default=3),
        "restart_backoff_seconds": _normalize_float(raw.get("restart_backoff_seconds"), "restart_backoff_seconds", minimum=0.05, default=1.0),
        "max_restart_backoff_seconds": _normalize_float(raw.get("max_restart_backoff_seconds"), "max_restart_backoff_seconds", minimum=0.1, default=16.0),
        "max_restarts": _normalize_int(raw.get("max_restarts"), "max_restarts", minimum=0, maximum=50, default=3),
        "restart_window_seconds": _normalize_float(raw.get("restart_window_seconds"), "restart_window_seconds", minimum=1.0, default=60.0),
        "startup_timeout_seconds": _normalize_float(raw.get("startup_timeout_seconds"), "startup_timeout_seconds", minimum=1.0, default=30.0),
        "stop_timeout_seconds": _normalize_float(raw.get("stop_timeout_seconds"), "stop_timeout_seconds", minimum=0.5, default=10.0),
        "min_free_disk_mb": _normalize_int(raw.get("min_free_disk_mb"), "min_free_disk_mb", minimum=1, maximum=1024 * 1024, default=128),
    }

    if check_runtime_paths:
        validate_runtime_paths(cfg)
    return cfg


def inspect_infrastructure_posture(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Check disk headroom and HTTPS/cookie security posture without mutating runtime state."""
    warnings: List[str] = []
    target = cfg["agent_data_dir"] if cfg["agent_data_dir"].exists() else cfg["code_dir"]
    min_hard_mb = int(cfg.get("min_free_disk_mb") or 128)
    warn_env = os.getenv("STUDY_MIN_FREE_DISK_MB", "").strip()
    try:
        warn_mb = max(min_hard_mb, int(warn_env)) if warn_env else max(min_hard_mb, 512)
    except ValueError:
        warn_mb = max(min_hard_mb, 512)

    disk_free_bytes = 0
    disk_total_bytes = 0
    disk_healthy = False
    try:
        usage = shutil.disk_usage(target)
        disk_free_bytes = int(usage.free)
        disk_total_bytes = int(usage.total)
        disk_healthy = disk_free_bytes >= min_hard_mb * 1024 * 1024
        if disk_free_bytes < warn_mb * 1024 * 1024:
            warnings.append(
                f"Low free disk space at {target} ({disk_free_bytes // (1024 * 1024)} MiB < {warn_mb} MiB)"
            )
    except OSError as exc:
        warnings.append(f"Unable to inspect free disk space at {target}: {exc}")

    public_url = str(cfg.get("public_url") or "")
    is_https = public_url.lower().startswith("https://")
    if is_https and not cfg.get("study_secure_cookies"):
        warnings.append(
            "HTTPS public_url is configured while study_secure_cookies=False; enable study_secure_cookies=true"
        )
    if is_https and not cfg.get("study_accounts_required"):
        warnings.append(
            "HTTPS public_url is configured while study_accounts_required=False; enable study_accounts_required=true"
        )
    if cfg.get("study_allow_registration"):
        warnings.append(
            "study_allow_registration=True enables self-registration; keep rate limits and account storage quotas active"
        )

    accounts_writable = bool(
        cfg["study_accounts_dir"].is_dir()
        and os.access(cfg["study_accounts_dir"], os.W_OK | os.X_OK)
    )
    if not accounts_writable:
        warnings.append(f"study_accounts_dir is not writable: {cfg['study_accounts_dir']}")

    return {
        "disk_free_bytes": disk_free_bytes,
        "disk_total_bytes": disk_total_bytes,
        "disk_healthy": disk_healthy,
        "accounts_dir_writable": accounts_writable,
        "https_public_url": is_https,
        "study_accounts_required": bool(cfg.get("study_accounts_required")),
        "study_secure_cookies": bool(cfg.get("study_secure_cookies")),
        "study_allow_registration": bool(cfg.get("study_allow_registration")),
        "warnings": warnings,
    }


def validate_runtime_paths(cfg: Dict[str, Any]) -> None:
    """Preflight explicit checkout, runtimes, DBs, source library, and model directories without seeding."""
    issues: List[str] = []
    code_dir: Path = cfg["code_dir"]
    if not code_dir.is_dir() or not (code_dir / "app" / "server.py").is_file():
        issues.append(f"Missing target checkout or app/server.py at code_dir: {code_dir}")

    for label in ("python_bin", "train_python"):
        exe: Path = cfg[label]
        if not (exe.is_file() and os.access(exe, os.X_OK)):
            issues.append(f"Missing executable {label}: {exe}")

    for label in (
        "agent_src_dir",
        "agent_data_dir",
        "study_accounts_dir",
        "mlx_model_dir",
        "mlx_9b_model_dir",
        "mlx_bge_model_dir",
    ):
        d: Path = cfg[label]
        if not d.is_dir():
            issues.append(f"Missing directory for {label}: {d}")

    acc_dir: Path = cfg["study_accounts_dir"]
    if acc_dir.is_dir() and not os.access(acc_dir, os.W_OK | os.X_OK):
        issues.append(f"study_accounts_dir is not writable: {acc_dir}")

    disk_target = cfg["agent_data_dir"] if cfg["agent_data_dir"].exists() else cfg["code_dir"]
    min_free_mb = int(cfg.get("min_free_disk_mb") or 128)
    try:
        usage = shutil.disk_usage(disk_target)
        if usage.free < min_free_mb * 1024 * 1024:
            issues.append(
                f"Insufficient free disk space at {disk_target}: "
                f"{usage.free // (1024 * 1024)} MiB < {min_free_mb} MiB required"
            )
    except OSError as exc:
        issues.append(f"Unable to check free disk space at {disk_target}: {exc}")

    if cfg["require_nonempty_src"] and cfg["agent_src_dir"].is_dir():
        has_file = any(p.is_file() and not p.name.startswith(".") for p in cfg["agent_src_dir"].rglob("*"))
        if not has_file:
            issues.append(f"agent_src_dir is empty (no source files found): {cfg['agent_src_dir']}")

    kb_db: Path = cfg["knowledge_base_db"]
    if not kb_db.is_file() or kb_db.stat().st_size == 0:
        issues.append(f"Missing or empty knowledge_base.db (refusing implicit empty DB init): {kb_db}")

    for label in ("history_db_path", "timetable_db_path", "timetable_images_dir"):
        parent = cfg[label].parent
        if not parent.is_dir():
            issues.append(f"Missing parent directory for {label}: {parent}")

    for label in ("mlx_model_dir", "mlx_9b_model_dir", "mlx_bge_model_dir"):
        mdir: Path = cfg[label]
        if mdir.is_dir() and not any(mdir.iterdir()):
            issues.append(f"Model directory {label} is empty: {mdir}")

    gpu_parent = cfg["study_gpu_runtime_dir"].parent
    if not gpu_parent.is_dir():
        issues.append(f"Missing parent directory for study_gpu_runtime_dir: {gpu_parent}")

    if issues:
        raise ServiceError("Preflight path validation failed:\n- " + "\n- ".join(issues))


def ensure_controller_dirs(cfg: Dict[str, Any]) -> None:
    cfg["state_dir"].mkdir(parents=True, exist_ok=True)
    cfg["log_dir"].mkdir(parents=True, exist_ok=True)
    cfg["study_gpu_runtime_dir"].mkdir(parents=True, exist_ok=True)


def build_uvicorn_command(cfg: Dict[str, Any]) -> List[str]:
    return [
        str(cfg["python_bin"]),
        "-m",
        "uvicorn",
        cfg["server_app"],
        "--host",
        cfg["host"],
        "--port",
        str(cfg["port"]),
        "--workers",
        "1",
        "--proxy-headers",
        "--forwarded-allow-ips",
        cfg["forwarded_allow_ips"],
        "--timeout-keep-alive",
        "15",
    ]


def build_child_env(cfg: Dict[str, Any], instance_id: str) -> Dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AGENT_SRC_DIR": str(cfg["agent_src_dir"]),
            "AGENT_DATA_DIR": str(cfg["agent_data_dir"]),
            "STUDY_ACCOUNTS_DIR": str(cfg["study_accounts_dir"]),
            "HISTORY_DB_PATH": str(cfg["history_db_path"]),
            "TIMETABLE_DB_PATH": str(cfg["timetable_db_path"]),
            "TIMETABLE_IMAGES_DIR": str(cfg["timetable_images_dir"]),
            "MLX_MODEL_DIR": str(cfg["mlx_model_dir"]),
            "MLX_9B_MODEL_DIR": str(cfg["mlx_9b_model_dir"]),
            "MLX_BGE_MODEL_DIR": str(cfg["mlx_bge_model_dir"]),
            "STUDY_GPU_RUNTIME_DIR": str(cfg["study_gpu_runtime_dir"]),
            "STUDY_SRC_AUTO_WATCH": "1" if cfg["study_src_auto_watch"] else "0",
            "STUDY_ACCOUNTS_REQUIRED": "1" if cfg["study_accounts_required"] else "0",
            "STUDY_ALLOW_REGISTRATION": "1" if cfg["study_allow_registration"] else "0",
            "STUDY_SECURE_COOKIES": "1" if cfg["study_secure_cookies"] else "0",
            "FORWARDED_ALLOW_IPS": str(cfg["forwarded_allow_ips"]),
            "SERVER_HOST": cfg["host"],
            "SERVER_PORT": str(cfg["port"]),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "PYTHONUNBUFFERED": "1",
            "STUDY_BACKEND_MANAGED": "1",
            "STUDY_BACKEND_INSTANCE_ID": instance_id,
        }
    )
    return env


class RotatingByteWriter:
    """Thread-safe size-bounded log writer with backup rotation."""

    def __init__(self, path: Path, max_bytes: int, backup_count: int):
        self.path = path
        self.max_bytes = max(1024, max_bytes)
        self.backup_count = max(1, backup_count)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("ab")

    def _rotate_locked(self) -> None:
        self._fh.flush()
        self._fh.close()
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        if oldest.exists():
            oldest.unlink()
        for idx in range(self.backup_count - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{idx}")
            dst = self.path.with_name(f"{self.path.name}.{idx + 1}")
            if src.exists():
                src.replace(dst)
        if self.path.exists():
            self.path.replace(self.path.with_name(f"{self.path.name}.1"))
        self._fh = self.path.open("ab")

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            try:
                cur_size = self.path.stat().st_size if self.path.exists() else 0
            except OSError:
                cur_size = 0
            if cur_size > 0 and cur_size + len(data) > self.max_bytes:
                self._rotate_locked()
            self._fh.write(data)
            self._fh.flush()
            try:
                if self.path.stat().st_size >= self.max_bytes:
                    self._rotate_locked()
            except OSError:
                pass

    def write_line(self, text: str) -> None:
        payload = f"[{_utc_now()}] {text.rstrip()}\n".encode("utf-8", errors="replace")
        self.write(payload)

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.flush()
                self._fh.close()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _state_paths(cfg: Dict[str, Any]) -> Dict[str, Path]:
    state_dir: Path = cfg["state_dir"]
    log_dir: Path = cfg["log_dir"]
    return {
        "lock": state_dir / "control.lock",
        "supervisor": state_dir / "supervisor.json",
        "backend": state_dir / "backend.json",
        "stop_intent": state_dir / "stop.intent",
        "backend_log": log_dir / "backend.log",
        "supervisor_log": log_dir / "supervisor.log",
    }


@contextmanager
def _acquire_control_lock(lock_path: Path, timeout: float = 15.0) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+", encoding="utf-8")
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        if fcntl is not None:
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EINTR):
                        raise
                    if time.monotonic() >= deadline:
                        raise ServiceError("Timed out waiting for concurrent backend controller lock.")
                    time.sleep(0.05)
        yield
    finally:
        if fcntl is not None and acquired:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def _ps_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return env


def _lsof_env() -> Dict[str, str]:
    env = os.environ.copy()
    # Preserve UTF-8 handling so lsof does not hex-escape Unicode paths like 'học tập'.
    if env.get("LC_ALL", "").upper() == "C":
        env.pop("LC_ALL", None)
    env.setdefault("LC_CTYPE", "UTF-8")
    return env


def get_process_identity(pid: Optional[int]) -> Optional[Dict[str, str]]:
    if not pid or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None

    ps_env = _ps_env()
    meta = subprocess.run(
        ["ps", "-o", "pgid=,stat=,lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        env=ps_env,
        timeout=5,
        check=False,
    )
    if meta.returncode != 0 or not meta.stdout.strip():
        return None
    parts = meta.stdout.strip().split(None, 2)
    if len(parts) < 3:
        return None
    pgid, stat, lstart = parts[0], parts[1], parts[2].strip()

    cmd_res = subprocess.run(
        ["ps", "-ww", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        text=True,
        env=ps_env,
        timeout=5,
        check=False,
    )
    command = cmd_res.stdout.strip() if cmd_res.returncode == 0 else ""

    cwd_res = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        env=_lsof_env(),
        timeout=5,
        check=False,
    )
    proc_cwd = ""
    if cwd_res.returncode == 0:
        for line in cwd_res.stdout.splitlines():
            if line.startswith("n"):
                proc_cwd = line[1:]
                break

    return {
        "pid": str(pid),
        "pgid": pgid,
        "stat": stat,
        "lstart": lstart,
        "command": command,
        "cwd": proc_cwd,
    }


def verify_managed_process(
    state: Optional[Dict[str, Any]],
    *,
    expected_cwd: Path,
    required_cmd_tokens: Sequence[str],
) -> Tuple[bool, str, Optional[Dict[str, str]]]:
    """Fail-closed process identity verification matching PID, lstart, command tokens, and cwd."""
    if not state:
        return False, "missing_state", None
    pid = state.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False, "invalid_pid", None
    ident = get_process_identity(pid)
    if not ident:
        return False, "not_running", None
    if "Z" in ident["stat"]:
        return False, "zombie", ident
    expected_lstart = state.get("lstart")
    if not expected_lstart or ident["lstart"] != expected_lstart:
        return False, "lstart_mismatch", ident

    cmd_lower = ident["command"].lower()
    if "python" not in cmd_lower:
        return False, "command_not_python", ident
    for token in required_cmd_tokens:
        if token.lower() not in cmd_lower:
            return False, f"command_missing:{token}", ident

    proc_cwd = ident["cwd"]
    if not proc_cwd:
        return False, "cwd_unreadable", ident
    try:
        if not (proc_cwd == str(expected_cwd) or os.path.samefile(proc_cwd, expected_cwd)):
            return False, "cwd_mismatch", ident
    except OSError:
        return False, "cwd_check_error", ident

    return True, "verified", ident


def find_port_listeners(port: int) -> List[Dict[str, Any]]:
    res = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"],
        capture_output=True,
        text=True,
        env=_ps_env(),
        timeout=5,
        check=False,
    )
    listeners: List[Dict[str, Any]] = []
    if res.returncode != 0:
        return listeners
    current: Optional[Dict[str, Any]] = None
    for line in res.stdout.splitlines():
        if line.startswith("p"):
            if current is not None:
                listeners.append(current)
            try:
                current = {"pid": int(line[1:]), "command": ""}
            except ValueError:
                current = None
        elif line.startswith("c") and current is not None:
            current["command"] = line[1:]
    if current is not None:
        listeners.append(current)
    return listeners


def is_port_accepting(host: str, port: int) -> bool:
    target_host = "127.0.0.1" if host == "localhost" else host
    try:
        with socket.create_connection((target_host, port), timeout=0.35):
            return True
    except OSError:
        return False


def detect_foreign_port_owner(cfg: Dict[str, Any], verified_backend_pid: Optional[int]) -> Optional[Dict[str, Any]]:
    port = cfg["port"]
    listeners = find_port_listeners(port)
    for item in listeners:
        if verified_backend_pid is None or item["pid"] != verified_backend_pid:
            return item
    if is_port_accepting(cfg["host"], port) and verified_backend_pid is None:
        return {"pid": None, "command": "unknown listener"}
    return None


def _kill_verified_process(pid: int, state: Dict[str, Any], *, expected_cwd: Path, required_tokens: Sequence[str], timeout: float) -> None:
    ok, reason, _ = verify_managed_process(state, expected_cwd=expected_cwd, required_cmd_tokens=required_tokens)
    if not ok:
        if reason == "not_running":
            return
        raise ServiceError(
            f"Refusing to signal unverified PID {pid} ({reason}); clean up stale/foreign process safely."
        )
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)

    still_ok, _, _ = verify_managed_process(state, expected_cwd=expected_cwd, required_cmd_tokens=required_tokens)
    if still_ok:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)


def fetch_health_snapshot(cfg: Dict[str, Any], timeout: float = 3.0) -> Dict[str, Any]:
    url = f"http://{cfg['host']}:{cfg['port']}/api/health"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            return {
                "reachable": resp.status == 200,
                "http_status": resp.status,
                "payload": payload if isinstance(payload, dict) else {},
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        return {"reachable": False, "http_status": exc.code, "payload": {}, "error": str(exc)}
    except Exception as exc:
        return {"reachable": False, "http_status": None, "payload": {}, "error": str(exc)}


def inspect_corpus_readiness(cfg: Dict[str, Any]) -> Dict[str, Any]:
    src_dir: Path = cfg["agent_src_dir"]
    kb_db: Path = cfg["knowledge_base_db"]
    src_files = 0
    if src_dir.is_dir():
        src_files = sum(1 for p in src_dir.rglob("*") if p.is_file() and not p.name.startswith("."))

    docs = 0
    chunks = 0
    db_error: Optional[str] = None
    if kb_db.is_file():
        try:
            conn = sqlite3.connect(f"file:{kb_db}?mode=ro", uri=True, timeout=2.0)
            try:
                cur = conn.cursor()
                docs = int(cur.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
                chunks = int(cur.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            finally:
                conn.close()
        except sqlite3.Error as exc:
            db_error = str(exc)
    else:
        db_error = f"missing:{kb_db}"

    return {
        "src_dir": str(src_dir),
        "src_files": src_files,
        "knowledge_base_db": str(kb_db),
        "documents": docs,
        "chunks": chunks,
        "ready": bool(src_files > 0 and docs > 0 and chunks > 0 and db_error is None),
        "error": db_error,
    }


def inspect_git_checkout(code_dir: Path) -> Dict[str, Any]:
    head = "unknown"
    branch = "unknown"
    dirty_count: Optional[int] = None
    try:
        res_head = subprocess.run(
            ["git", "-C", str(code_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_head.returncode == 0:
            head = res_head.stdout.strip()
        res_branch = subprocess.run(
            ["git", "-C", str(code_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_branch.returncode == 0:
            branch = res_branch.stdout.strip()
        res_stat = subprocess.run(
            ["git", "-C", str(code_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_stat.returncode == 0:
            lines = [line for line in res_stat.stdout.splitlines() if line.strip()]
            dirty_count = len(lines)
    except OSError:
        pass
    return {
        "code_dir": str(code_dir),
        "git_head": head,
        "git_branch": branch,
        "git_dirty_files": dirty_count,
    }


def _sup_tokens(cfg: Dict[str, Any], sup_state: Optional[Dict[str, Any]]) -> List[str]:
    tokens = ["backend_service.py", "_supervise"]
    if sup_state and isinstance(sup_state.get("instance_id"), str):
        tokens.append(sup_state["instance_id"])
    return tokens


def _backend_tokens(cfg: Dict[str, Any]) -> List[str]:
    return ["uvicorn", cfg["server_app"], f"--port {cfg['port']}"]


def get_status(cfg: Dict[str, Any]) -> Dict[str, Any]:
    paths = _state_paths(cfg)
    sup_state = _read_json_safe(paths["supervisor"])
    back_state = _read_json_safe(paths["backend"])

    sup_ok, sup_reason, sup_ident = verify_managed_process(
        sup_state,
        expected_cwd=cfg["code_dir"],
        required_cmd_tokens=_sup_tokens(cfg, sup_state),
    )
    back_ok, back_reason, back_ident = verify_managed_process(
        back_state,
        expected_cwd=cfg["code_dir"],
        required_cmd_tokens=_backend_tokens(cfg),
    )

    verified_back_pid = int(back_ident["pid"]) if back_ok and back_ident else None
    foreign_listener = detect_foreign_port_owner(cfg, verified_back_pid)

    health = fetch_health_snapshot(cfg, timeout=2.5) if (back_ok or foreign_listener or is_port_accepting(cfg["host"], cfg["port"])) else {
        "reachable": False,
        "http_status": None,
        "payload": {},
        "error": "service_not_listening",
    }
    payload = health["payload"]
    web_ready = bool(back_ok and health["reachable"] and foreign_listener is None)

    model_qa_ready = bool(
        web_ready
        and payload.get("inference_ready") is True
        and payload.get("vision_ready") is not False
        and payload.get("embedding_ready") is not False
    )
    corpus = inspect_corpus_readiness(cfg)
    infra = inspect_infrastructure_posture(cfg)
    git_info = inspect_git_checkout(cfg["code_dir"])

    return {
        "service": "running" if (sup_ok and back_ok and foreign_listener is None) else (
            "conflict_foreign_port" if foreign_listener else (
                "recovering" if sup_ok and not back_ok else (sup_state.get("status") if sup_state else "stopped")
            )
        ),
        "web_ready": web_ready,
        "model_qa_ready": model_qa_ready,
        "corpus_ready": corpus["ready"],
        "url": cfg["public_url"],
        "listen": f"http://{cfg['host']}:{cfg['port']}",
        "checkout": git_info,
        "process": {
            "supervisor_verified": sup_ok,
            "supervisor_reason": sup_reason,
            "supervisor_pid": int(sup_ident["pid"]) if sup_ok and sup_ident else (sup_state.get("pid") if sup_state else None),
            "backend_verified": back_ok,
            "backend_reason": back_reason,
            "backend_pid": verified_back_pid,
            "foreign_port_owner": foreign_listener,
            "instance_id": sup_state.get("instance_id") if sup_state else None,
        },
        "recovery": {
            "supervisor_status": sup_state.get("status") if sup_state else None,
            "restarts": sup_state.get("restarts", 0) if sup_state else 0,
            "last_exit_code": sup_state.get("last_exit_code") if sup_state else None,
            "last_failure": sup_state.get("last_failure") if sup_state else None,
        },
        "health": {
            "http_status": health["http_status"],
            "service_state": payload.get("service_state"),
            "service_code": payload.get("service_code"),
            "service_message": payload.get("service_message"),
            "inference_ready": payload.get("inference_ready"),
            "vision_ready": payload.get("vision_ready"),
            "embedding_ready": payload.get("embedding_ready"),
            "gpu_state": (payload.get("gpu") or {}).get("state") if isinstance(payload.get("gpu"), dict) else None,
            "infrastructure": payload.get("infrastructure"),
            "admission": payload.get("admission"),
            "error": health["error"],
        },
        "infrastructure": infra,
        "corpus": corpus,
        "paths": {
            "config_path": str(cfg["config_path"]),
            "state_dir": str(cfg["state_dir"]),
            "log_dir": str(cfg["log_dir"]),
            "backend_log": str(paths["backend_log"]),
            "supervisor_log": str(paths["supervisor_log"]),
            "study_gpu_runtime_dir": str(cfg["study_gpu_runtime_dir"]),
            "agent_src_dir": str(cfg["agent_src_dir"]),
            "agent_data_dir": str(cfg["agent_data_dir"]),
            "study_accounts_dir": str(cfg["study_accounts_dir"]),
        },
    }


def _check_stop_intent(stop_intent_path: Path, instance_id: str) -> bool:
    data = _read_json_safe(stop_intent_path)
    if not data:
        return False
    target_id = data.get("instance_id")
    return target_id in ("*", instance_id)


def _pump_stream(stream: Any, writer: RotatingByteWriter) -> None:
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            writer.write(chunk)
    except OSError:
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def run_supervisor(cfg: Dict[str, Any], instance_id: str) -> int:
    """Detached supervisor loop owning one backend child with bounded restart backoff and logs."""
    ensure_controller_dirs(cfg)
    paths = _state_paths(cfg)
    sup_log = RotatingByteWriter(paths["supervisor_log"], cfg["max_log_bytes"], cfg["max_log_backups"])
    back_log = RotatingByteWriter(paths["backend_log"], cfg["max_log_bytes"], cfg["max_log_backups"])

    stop_requested = False
    active_proc: Optional[subprocess.Popen[bytes]] = None
    active_state: Optional[Dict[str, Any]] = None

    def _on_signal(signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True
        sup_log.write_line(f"Received signal {signum}; initiating controlled stop for instance {instance_id}.")
        if active_proc is not None and active_proc.poll() is None:
            try:
                active_proc.terminate()
            except OSError:
                pass

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    my_ident = get_process_identity(os.getpid()) or {"lstart": "", "pgid": str(os.getpid()), "command": ""}
    restarts = 0
    recent_crashes: List[float] = []
    last_exit_code: Optional[int] = None
    last_failure: Optional[str] = None

    def _save_sup(status: str) -> None:
        _atomic_write_json(
            paths["supervisor"],
            {
                "pid": os.getpid(),
                "pgid": int(my_ident["pgid"]) if str(my_ident["pgid"]).isdigit() else os.getpid(),
                "lstart": my_ident["lstart"],
                "cwd": str(cfg["code_dir"]),
                "instance_id": instance_id,
                "port": cfg["port"],
                "status": status,
                "restarts": restarts,
                "last_exit_code": last_exit_code,
                "last_failure": last_failure,
                "updated_at": _utc_now(),
            },
        )

    _save_sup("starting")
    sup_log.write_line(
        f"Supervisor started (pid={os.getpid()}, instance={instance_id}, checkout={cfg['code_dir']}, port={cfg['port']})."
    )

    try:
        while not stop_requested and not _check_stop_intent(paths["stop_intent"], instance_id):
            foreign = detect_foreign_port_owner(cfg, None)
            if foreign is not None:
                last_failure = f"Foreign listener occupying port {cfg['port']}: pid={foreign.get('pid')} cmd={foreign.get('command')}"
                sup_log.write_line(last_failure)
                _save_sup("failed_foreign_port")
                return 1

            cmd = build_uvicorn_command(cfg)
            env = build_child_env(cfg, instance_id)
            sup_log.write_line(f"Spawning backend: {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd,
                cwd=str(cfg["code_dir"]),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            active_proc = proc
            pump = threading.Thread(target=_pump_stream, args=(proc.stdout, back_log), daemon=True)
            pump.start()

            child_ident = get_process_identity(proc.pid) or {"lstart": "", "pgid": "", "command": ""}
            active_state = {
                "pid": proc.pid,
                "pgid": int(child_ident["pgid"]) if str(child_ident["pgid"]).isdigit() else None,
                "lstart": child_ident["lstart"],
                "cwd": str(cfg["code_dir"]),
                "instance_id": instance_id,
                "port": cfg["port"],
                "status": "running",
                "started_at": _utc_now(),
            }
            _atomic_write_json(paths["backend"], active_state)
            _save_sup("running")

            while True:
                rc = proc.poll()
                if rc is not None:
                    break
                if stop_requested or _check_stop_intent(paths["stop_intent"], instance_id):
                    _kill_verified_process(
                        proc.pid,
                        active_state,
                        expected_cwd=cfg["code_dir"],
                        required_tokens=_backend_tokens(cfg),
                        timeout=cfg["stop_timeout_seconds"],
                    )
                    rc = proc.wait(timeout=2.0)
                    break
                time.sleep(0.2)

            pump.join(timeout=2.0)
            active_proc = None
            last_exit_code = rc
            _atomic_write_json(
                paths["backend"],
                {**active_state, "status": "stopped", "last_exit_code": rc, "stopped_at": _utc_now()},
            )

            if stop_requested or _check_stop_intent(paths["stop_intent"], instance_id):
                sup_log.write_line(f"Backend stopped cleanly on operator request (exit={rc}).")
                _save_sup("stopped")
                return 0

            now = time.monotonic()
            recent_crashes = [t for t in recent_crashes if (now - t) <= cfg["restart_window_seconds"]]
            recent_crashes.append(now)
            sup_log.write_line(
                f"Backend exited unexpectedly with code {rc} (crash #{len(recent_crashes)} in {cfg['restart_window_seconds']}s window)."
            )

            if len(recent_crashes) > cfg["max_restarts"]:
                last_failure = (
                    f"Backend exceeded max_restarts ({cfg['max_restarts']}) within "
                    f"{cfg['restart_window_seconds']}s; last exit code {rc}."
                )
                sup_log.write_line(last_failure)
                _save_sup("failed")
                return 1

            restarts += 1
            delay = min(
                cfg["restart_backoff_seconds"] * (2 ** (len(recent_crashes) - 1)),
                cfg["max_restart_backoff_seconds"],
            )
            last_failure = f"Unexpected exit code {rc}; restarting (attempt {restarts})"
            _save_sup("backoff")
            sup_log.write_line(f"Waiting {delay:.2f}s before restart #{restarts}.")

            sleep_until = time.monotonic() + delay
            while time.monotonic() < sleep_until:
                if stop_requested or _check_stop_intent(paths["stop_intent"], instance_id):
                    sup_log.write_line("Stop requested during restart backoff; cancelling restart.")
                    _save_sup("stopped")
                    return 0
                time.sleep(0.1)

        _save_sup("stopped")
        return 0
    finally:
        sup_log.close()
        back_log.close()


def _wait_for_port_release(cfg: Dict[str, Any], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_port_listeners(cfg["port"]) and not is_port_accepting(cfg["host"], cfg["port"]):
            return
        time.sleep(0.1)


def start_service(cfg: Dict[str, Any]) -> Dict[str, Any]:
    paths = _state_paths(cfg)
    ensure_controller_dirs(cfg)
    with _acquire_control_lock(paths["lock"]):
        sup_state = _read_json_safe(paths["supervisor"])
        back_state = _read_json_safe(paths["backend"])
        sup_ok, _, sup_ident = verify_managed_process(
            sup_state, expected_cwd=cfg["code_dir"], required_cmd_tokens=_sup_tokens(cfg, sup_state)
        )
        back_ok, _, back_ident = verify_managed_process(
            back_state, expected_cwd=cfg["code_dir"], required_cmd_tokens=_backend_tokens(cfg)
        )

        verified_back_pid = int(back_ident["pid"]) if back_ok and back_ident else None
        foreign = detect_foreign_port_owner(cfg, verified_back_pid)
        if foreign is not None:
            raise ServiceError(
                f"Port {cfg['port']} is already held by a foreign process (pid={foreign.get('pid')}, cmd={foreign.get('command')}). "
                "Refusing to terminate foreign listeners.",
                exit_code=2,
                details={"foreign_port_owner": foreign},
            )

        if sup_ok and back_ok:
            health = fetch_health_snapshot(cfg, timeout=3.0)
            if health["reachable"]:
                status = get_status(cfg)
                status["action"] = "already_running"
                return status

        if sup_ok and not back_ok:
            _kill_verified_process(
                int(sup_ident["pid"]),  # type: ignore[index]
                sup_state,  # type: ignore[arg-type]
                expected_cwd=cfg["code_dir"],
                required_tokens=_sup_tokens(cfg, sup_state),
                timeout=cfg["stop_timeout_seconds"],
            )

        if paths["stop_intent"].exists():
            paths["stop_intent"].unlink()

        instance_id = uuid.uuid4().hex
        boot_log_path = paths["supervisor_log"]
        boot_log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--config",
            str(cfg["config_path"]),
            "_supervise",
            "--instance-id",
            instance_id,
        ]
        with boot_log_path.open("ab") as boot_fh:
            sup_proc = subprocess.Popen(
                cmd,
                cwd=str(cfg["code_dir"]),
                stdin=subprocess.DEVNULL,
                stdout=boot_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )

        deadline = time.monotonic() + cfg["startup_timeout_seconds"]
        while time.monotonic() < deadline:
            cur_sup = _read_json_safe(paths["supervisor"])
            cur_back = _read_json_safe(paths["backend"])
            if cur_sup and cur_sup.get("instance_id") == instance_id and cur_sup.get("status") in ("failed", "failed_foreign_port"):
                raise ServiceError(
                    cur_sup.get("last_failure") or "Backend supervisor failed during startup.",
                    details={"status": cur_sup.get("status"), "supervisor_log": str(paths["supervisor_log"])},
                )
            if sup_proc.poll() is not None and not (cur_sup and cur_sup.get("instance_id") == instance_id and cur_sup.get("status") == "stopped"):
                tail = read_log_tail(paths["supervisor_log"], 20)
                raise ServiceError(
                    f"Supervisor process exited prematurely during startup (code {sup_proc.returncode}).\n{tail}",
                )

            cur_back_ok, _, _ = verify_managed_process(
                cur_back, expected_cwd=cfg["code_dir"], required_cmd_tokens=_backend_tokens(cfg)
            )
            if cur_back_ok:
                health = fetch_health_snapshot(cfg, timeout=1.5)
                if health["reachable"]:
                    status = get_status(cfg)
                    status["action"] = "started"
                    return status
            time.sleep(0.25)

        stop_service(cfg, lock_held=True)
        tail = read_log_tail(paths["backend_log"], 25) or read_log_tail(paths["supervisor_log"], 25)
        raise ServiceError(
            f"Timed out after {cfg['startup_timeout_seconds']}s waiting for {cfg['public_url']}/api/health.\n{tail}"
        )


def stop_service(cfg: Dict[str, Any], *, lock_held: bool = False) -> Dict[str, Any]:
    paths = _state_paths(cfg)
    ctx = _null_ctx() if lock_held else _acquire_control_lock(paths["lock"])
    with ctx:
        sup_state = _read_json_safe(paths["supervisor"])
        back_state = _read_json_safe(paths["backend"])
        inst_id = (sup_state or {}).get("instance_id") or (back_state or {}).get("instance_id") or "*"
        if cfg["state_dir"].is_dir():
            _atomic_write_json(paths["stop_intent"], {"instance_id": inst_id, "requested_at": _utc_now()})

        sup_ok, sup_reason, sup_ident = verify_managed_process(
            sup_state, expected_cwd=cfg["code_dir"], required_cmd_tokens=_sup_tokens(cfg, sup_state)
        )
        back_ok, back_reason, back_ident = verify_managed_process(
            back_state, expected_cwd=cfg["code_dir"], required_cmd_tokens=_backend_tokens(cfg)
        )

        foreign = detect_foreign_port_owner(cfg, int(back_ident["pid"]) if back_ok and back_ident else None)
        if not sup_ok and not back_ok and foreign is not None:
            raise ServiceError(
                f"Port {cfg['port']} is held by a foreign listener (pid={foreign.get('pid')}, cmd={foreign.get('command')}); "
                "not owned by this controller and left untouched.",
                exit_code=2,
            )

        for label, ok, reason, state in (
            ("supervisor", sup_ok, sup_reason, sup_state),
            ("backend", back_ok, back_reason, back_state),
        ):
            if state and not ok and reason not in ("not_running", "zombie", "missing_state", "invalid_pid"):
                if reason == "lstart_mismatch" or "mismatch" in reason or "missing:" in reason:
                    raise ServiceError(
                        f"Refusing to kill {label} PID {state.get('pid')} due to identity mismatch ({reason}).",
                        exit_code=2,
                    )

        stopped_any = False
        if sup_ok and sup_state and sup_ident:
            _kill_verified_process(
                int(sup_ident["pid"]),
                sup_state,
                expected_cwd=cfg["code_dir"],
                required_tokens=_sup_tokens(cfg, sup_state),
                timeout=cfg["stop_timeout_seconds"],
            )
            stopped_any = True

        back_ok_after, _, back_ident_after = verify_managed_process(
            back_state, expected_cwd=cfg["code_dir"], required_cmd_tokens=_backend_tokens(cfg)
        )
        if back_ok_after and back_state and back_ident_after:
            _kill_verified_process(
                int(back_ident_after["pid"]),
                back_state,
                expected_cwd=cfg["code_dir"],
                required_tokens=_backend_tokens(cfg),
                timeout=cfg["stop_timeout_seconds"],
            )
            stopped_any = True

        if stopped_any:
            _wait_for_port_release(cfg, timeout=3.0)

        if sup_state and sup_state.get("status") != "stopped":
            _atomic_write_json(paths["supervisor"], {**sup_state, "status": "stopped", "stopped_at": _utc_now()})
        if back_state and back_state.get("status") != "stopped":
            _atomic_write_json(paths["backend"], {**back_state, "status": "stopped", "stopped_at": _utc_now()})

        status = get_status(cfg)
        status["action"] = "stopped" if stopped_any else "already_stopped"
        return status


@contextmanager
def _null_ctx() -> Iterator[None]:
    yield


def restart_service(cfg: Dict[str, Any]) -> Dict[str, Any]:
    paths = _state_paths(cfg)
    with _acquire_control_lock(paths["lock"]):
        stop_service(cfg, lock_held=True)
    result = start_service(cfg)
    result["action"] = "restarted"
    return result


def open_service_url(cfg: Dict[str, Any], *, no_open: bool = False) -> Dict[str, Any]:
    url = cfg["public_url"]
    health = fetch_health_snapshot(cfg, timeout=2.0)
    opened = False
    if not no_open:
        subprocess.run(["open", url], timeout=10, check=True)
        opened = True
    return {
        "action": "open",
        "url": url,
        "opened": opened,
        "web_ready": health["reachable"],
    }


def read_log_tail(path: Path, lines: int) -> str:
    if not path.is_file():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def format_status_text(data: Dict[str, Any]) -> str:
    proc = data["process"]
    rec = data["recovery"]
    chk = data["checkout"]
    hlth = data["health"]
    corp = data["corpus"]
    pths = data["paths"]
    infra = data.get("infrastructure") or {}
    disk_free_mb = int(infra.get("disk_free_bytes") or 0) // (1024 * 1024)
    lines = [
        f"Service: {data['service']} ({data.get('action', 'status')})",
        f"URL: {data['url']} (bind: {data['listen']})",
        f"Checkout: {chk['code_dir']} | branch={chk['git_branch']} | head={chk['git_head']} | dirty={chk['git_dirty_files']}",
        f"Processes: supervisor_pid={proc['supervisor_pid']} ({proc['supervisor_reason']}), backend_pid={proc['backend_pid']} ({proc['backend_reason']})",
        f"Readiness: web_ready={data['web_ready']}, model_qa_ready={data['model_qa_ready']}, corpus_ready={data['corpus_ready']}",
        f"Health API: http={hlth['http_status']} state={hlth['service_state']} code={hlth['service_code']} inference_ready={hlth['inference_ready']} gpu={hlth['gpu_state']}",
        f"Infrastructure: disk_healthy={infra.get('disk_healthy')} free_mb={disk_free_mb} accounts_writable={infra.get('accounts_dir_writable')} accounts_required={infra.get('study_accounts_required')} secure_cookies={infra.get('study_secure_cookies')}",
        f"Corpus: docs={corp['documents']} chunks={corp['chunks']} src_files={corp['src_files']} src_dir={corp['src_dir']}",
        f"Recovery: supervisor_status={rec['supervisor_status']} restarts={rec['restarts']} last_exit={rec['last_exit_code']} last_failure={rec['last_failure']}",
        f"Paths: config={pths['config_path']} gpu_runtime={pths['study_gpu_runtime_dir']} backend_log={pths['backend_log']} supervisor_log={pths['supervisor_log']}",
    ]
    for warn in infra.get("warnings") or ():
        lines.append(f"WARNING: {warn}")
    if proc.get("foreign_port_owner"):
        lines.append(f"CONFLICT: Foreign port listener detected: {proc['foreign_port_owner']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Study Agent macOS backend service controller")
    parser.add_argument("--config", help="Path to private JSON service config")
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd_name in ("start", "stop", "restart", "status", "preflight"):
        p = sub.add_parser(cmd_name)
        p.add_argument("--json", action="store_true", help="Emit structured JSON output")

    p_open = sub.add_parser("open")
    p_open.add_argument("--no-open", action="store_true", help="Print configured URL without invoking macOS 'open'")
    p_open.add_argument("--json", action="store_true", help="Emit structured JSON output")

    p_logs = sub.add_parser("logs")
    p_logs.add_argument("--lines", type=int, default=80, help="Number of trailing lines per log")
    p_logs.add_argument("--follow", action="store_true", help="Stream new lines cleanly until interrupted")

    p_sup = sub.add_parser("_supervise")
    p_sup.add_argument("--instance-id", required=True)

    args = parser.parse_args(list(argv) if argv is not None else None)
    cfg_path = resolve_config_path(args.config)

    try:
        need_runtime_check = args.command in ("start", "restart", "preflight", "_supervise")
        cfg = load_and_validate_config(cfg_path, check_runtime_paths=need_runtime_check)

        if args.command == "_supervise":
            return run_supervisor(cfg, args.instance_id)
        if args.command == "preflight":
            out = {"action": "preflight", "ok": True, "config_path": str(cfg["config_path"]), "url": cfg["public_url"], "code_dir": str(cfg["code_dir"])}
            print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else f"Preflight OK: {cfg['public_url']} ({cfg['code_dir']})")
            return 0
        if args.command == "start":
            out = start_service(cfg)
            print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else format_status_text(out))
            return 0
        if args.command == "stop":
            out = stop_service(cfg)
            print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else format_status_text(out))
            return 0
        if args.command == "restart":
            out = restart_service(cfg)
            print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else format_status_text(out))
            return 0
        if args.command == "status":
            out = get_status(cfg)
            print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else format_status_text(out))
            return 0
        if args.command == "open":
            out = open_service_url(cfg, no_open=args.no_open)
            print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else f"App URL: {out['url']} (web_ready={out['web_ready']}, opened={out['opened']})")
            return 0
        if args.command == "logs":
            paths = _state_paths(cfg)
            print(f"=== Supervisor Log ({paths['supervisor_log']}) ===")
            print(read_log_tail(paths["supervisor_log"], args.lines) or "(empty)")
            print(f"\n=== Backend Log ({paths['backend_log']}) ===")
            print(read_log_tail(paths["backend_log"], args.lines) or "(empty)")
            if args.follow:
                pos = paths["backend_log"].stat().st_size if paths["backend_log"].is_file() else 0
                try:
                    while True:
                        if paths["backend_log"].is_file():
                            size = paths["backend_log"].stat().st_size
                            if size < pos:
                                pos = 0
                            if size > pos:
                                with paths["backend_log"].open("rb") as fh:
                                    fh.seek(pos)
                                    chunk = fh.read()
                                    pos += len(chunk)
                                    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                                    sys.stdout.flush()
                        time.sleep(0.3)
                except KeyboardInterrupt:
                    return 0
            return 0
    except ServiceError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc), **exc.details}, indent=2, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
