"""Focused tests for macOS backend recovery controller, shared GPU dir, and hosted UI copy."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import unicodedata

import pytest

from app import runtime_health
from scripts import backend_service as bs


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "launchers" / "macos" / "backend-quick-start"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_until(predicate, timeout: float = 15.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _create_disposable_store(base_dir: Path, *, with_kb_tables: bool = True) -> dict[str, Path]:
    src_dir = base_dir / "src"
    data_dir = base_dir / "data"
    accounts_dir = base_dir / "accounts"
    timetable_images_dir = base_dir / "timetable_images"
    mlx_dir = base_dir / "models" / "qwen3.5-9b-4bit"
    bge_dir = base_dir / "models" / "bge-m3-mlx-fp16"
    gpu_dir = base_dir / "gpu_runtime"
    state_dir = base_dir / "ctrl_state"
    log_dir = base_dir / "ctrl_logs"
    for d in (src_dir, data_dir, accounts_dir, timetable_images_dir, mlx_dir, bge_dir, gpu_dir.parent, state_dir, log_dir):
        d.mkdir(parents=True, exist_ok=True)

    (src_dir / "lesson.txt").write_text("Noi dung hoc tap mau.", encoding="utf-8")
    (mlx_dir / "config.json").write_text("{}", encoding="utf-8")
    (bge_dir / "config.json").write_text("{}", encoding="utf-8")

    kb_db = data_dir / "knowledge_base.db"
    if with_kb_tables:
        from app.indexer import KnowledgeIndexer
        KnowledgeIndexer(db_path=kb_db, src_dir=src_dir, ocr_cache_dir=data_dir / "ocr_cache")
        conn = sqlite3.connect(str(kb_db))
        try:
            conn.execute(
                "INSERT INTO documents (id, filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, is_scanned, status) "
                "VALUES (1, 'lesson.txt', 'Lesson', ?, 22, 'abc123', 1, 1, 0, 'indexed')",
                (str(src_dir / "lesson.txt"),),
            )
            conn.execute(
                "INSERT INTO chunks (id, doc_id, book_title, filename, page_num, chunk_index, text) "
                "VALUES (1, 1, 'Lesson', 'lesson.txt', 1, 0, 'sample chunk')"
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "src_dir": src_dir,
        "data_dir": data_dir,
        "kb_db": kb_db,
        "accounts_dir": accounts_dir,
        "history_db": base_dir / "history.db",
        "timetable_db": base_dir / "timetable.db",
        "timetable_images_dir": timetable_images_dir,
        "mlx_dir": mlx_dir,
        "bge_dir": bge_dir,
        "gpu_dir": gpu_dir,
        "state_dir": state_dir,
        "log_dir": log_dir,
    }


def _write_config(
    cfg_path: Path,
    store: dict[str, Path],
    port: int,
    *,
    server_app: str = "app.server:app",
    extra: dict | None = None,
) -> Path:
    payload = {
        "configured": True,
        "code_dir": str(REPO_ROOT),
        "python_bin": sys.executable,
        "train_python": str(REPO_ROOT / ".train-venv" / "bin" / "python")
        if (REPO_ROOT / ".train-venv" / "bin" / "python").is_file()
        else sys.executable,
        "host": "127.0.0.1",
        "port": port,
        "public_url": f"http://127.0.0.1:{port}",
        "forwarded_allow_ips": "127.0.0.1",
        "server_app": server_app,
        "agent_src_dir": str(store["src_dir"]),
        "agent_data_dir": str(store["data_dir"]),
        "knowledge_base_db": str(store["kb_db"]),
        "study_accounts_dir": str(store["accounts_dir"]),
        "history_db_path": str(store["history_db"]),
        "timetable_db_path": str(store["timetable_db"]),
        "timetable_images_dir": str(store["timetable_images_dir"]),
        "mlx_model_dir": str(store["mlx_dir"]),
        "mlx_9b_model_dir": str(store["mlx_dir"]),
        "mlx_bge_model_dir": str(store["bge_dir"]),
        "study_gpu_runtime_dir": str(store["gpu_dir"]),
        "state_dir": str(store["state_dir"]),
        "log_dir": str(store["log_dir"]),
        "study_src_auto_watch": False,
        "study_accounts_required": True,
        "study_allow_registration": False,
        "study_secure_cookies": False,
        "max_log_bytes": 4096,
        "max_log_backups": 2,
        "restart_backoff_seconds": 0.15,
        "max_restart_backoff_seconds": 0.6,
        "max_restarts": 2,
        "restart_window_seconds": 30.0,
        "startup_timeout_seconds": 20.0,
        "stop_timeout_seconds": 5.0,
    }
    if extra:
        payload.update(extra)
    cfg_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return cfg_path


def _write_test_asgi_module(mod_dir: Path, name: str, *, crash_immediately: bool = False, noisy_logs: bool = False) -> str:
    mod_file = mod_dir / f"{name}.py"
    if crash_immediately:
        mod_file.write_text(
            "import sys\nsys.stderr.write('simulated startup crash\\n')\nsys.exit(17)\n",
            encoding="utf-8",
        )
    else:
        noise = (
            "    for i in range(40):\n"
            "        print(f'noise-line-{i}-' + ('x' * 80), flush=True)\n"
            if noisy_logs
            else ""
        )
        mod_file.write_text(
            "import json, os\n"
            "async def app(scope, receive, send):\n"
            "    if scope['type'] != 'http':\n"
            "        return\n"
            f"{noise}"
            "    body = json.dumps({'status': 'ok', 'service_state': 'degraded', 'inference_ready': False,\n"
            "                       'env_gpu': os.environ.get('STUDY_GPU_RUNTIME_DIR'),\n"
            "                       'env_watch': os.environ.get('STUDY_SRC_AUTO_WATCH'),\n"
            "                       'pid': os.getpid()}).encode('utf-8')\n"
            "    await send({'type': 'http.response.start', 'status': 200,\n"
            "                'headers': [(b'content-type', b'application/json')]})\n"
            "    await send({'type': 'http.response.body', 'body': body})\n",
            encoding="utf-8",
        )
    return f"{name}:app"


def _run_cli(cfg_path: Path, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "backend_service.py"), "--config", str(cfg_path), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="macOS/POSIX supervisor and .command launcher tests")
def test_launcher_templates_syntax_executable_and_sanitized():
    scripts = [TEMPLATE_DIR / "_run_action.sh", *sorted(TEMPLATE_DIR.glob("*.command"))]
    expected_commands = {
        "Start Backend.command",
        "Check Status.command",
        "Restart Backend.command",
        "Stop Backend.command",
        "Open App.command",
        "View Logs.command",
    }
    assert {p.name for p in TEMPLATE_DIR.glob("*.command")} == expected_commands

    for script in scripts:
        assert os.access(script, os.X_OK), f"Launcher missing +x: {script}"
        syntax = subprocess.run(["zsh", "-n", str(script)], capture_output=True, text=True, check=False)
        assert syntax.returncode == 0, f"zsh -n failed for {script}: {syntax.stderr}"
        text = script.read_text(encoding="utf-8")
        assert "/Users/" not in text
        assert "eval " not in text

    example = json.loads((TEMPLATE_DIR / "config.example.json").read_text(encoding="utf-8"))
    assert example["configured"] is False
    assert example["host"] == "127.0.0.1"
    assert example["forwarded_allow_ips"] == "127.0.0.1"
    assert example["study_accounts_required"] is True
    assert example["study_allow_registration"] is False
    assert "/Users/" not in (TEMPLATE_DIR / "README.md").read_text(encoding="utf-8")


def test_preflight_unconfigured_and_missing_paths_fail_closed(tmp_path):
    store = _create_disposable_store(tmp_path / "store")
    port = _free_port()

    unconfigured = tmp_path / "unconfigured.json"
    shutil.copy2(TEMPLATE_DIR / "config.example.json", unconfigured)
    with pytest.raises(bs.ServiceError, match="unconfigured|placeholder"):
        bs.load_and_validate_config(unconfigured)

    missing_kb_path = store["kb_db"]
    missing_kb_path.unlink()
    cfg_missing_db = _write_config(tmp_path / "missing_db.json", store, port)
    with pytest.raises(bs.ServiceError, match="knowledge_base.db"):
        bs.load_and_validate_config(cfg_missing_db)
    assert not missing_kb_path.exists(), "Controller must never implicitly initialize a missing database"

    cfg_bad_bind = _write_config(
        tmp_path / "bad_bind.json", store, port, extra={"host": "0.0.0.0"}
    )
    with pytest.raises(bs.ServiceError, match="non-loopback"):
        bs.load_and_validate_config(cfg_bad_bind)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS/POSIX supervisor and .command launcher tests")
def test_real_server_start_status_persistence_and_stop(tmp_path, monkeypatch):
    import base64
    import http.cookiejar
    import secrets
    import urllib.error
    import urllib.request
    import uuid

    fitz = pytest.importorskip("fitz")

    store = _create_disposable_store(tmp_path / "real_store")
    port = _free_port()
    cfg_path = _write_config(
        tmp_path / "real.config.json",
        store,
        port,
        extra={"study_src_auto_watch": False},
    )
    base_url = f"http://127.0.0.1:{port}"

    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(store["accounts_dir"]))
    from app import accounts as acct_mod
    identity = uuid.uuid4().hex
    salt = secrets.token_hex(16)
    encoded = acct_mod.password_hash("StrongPassword!123", salt)
    with acct_mod.connection() as db:
        db.execute("INSERT INTO accounts VALUES(?,?,?,?)", (identity, "learner1", salt, encoded))

    def _http_json(method: str, path: str, body: dict | None = None, *, headers: dict | None = None, opener=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req_headers = {"Accept": "application/json", "Origin": base_url}
        if data is not None:
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(f"{base_url}{path}", data=data, headers=req_headers, method=method)
        fn = opener.open if opener else urllib.request.urlopen
        with fn(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    try:
        start_res = _run_cli(cfg_path, "start", "--json")
        assert start_res.returncode == 0, f"stderr={start_res.stderr}\nstdout={start_res.stdout}"
        data = json.loads(start_res.stdout)
        assert data["action"] == "started"
        assert data["web_ready"] is True
        assert data["corpus_ready"] is True
        assert data["corpus"]["documents"] == 1
        assert data["checkout"]["git_head"] != "unknown"
        assert data["paths"]["study_gpu_runtime_dir"] == str(store["gpu_dir"])
        sup_pid = data["process"]["supervisor_pid"]
        back_pid = data["process"]["backend_pid"]
        assert isinstance(sup_pid, int) and isinstance(back_pid, int)

        ps_cmd = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(back_pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        assert "uvicorn" in ps_cmd
        assert "--host 127.0.0.1" in ps_cmd
        assert f"--port {port}" in ps_cmd
        assert "--workers 1" in ps_cmd
        assert "--proxy-headers" in ps_cmd
        assert "--forwarded-allow-ips 127.0.0.1" in ps_cmd
        assert "--reload" not in ps_cmd

        # Unauthenticated private route stays closed under STUDY_ACCOUNTS_REQUIRED=1
        with pytest.raises(urllib.error.HTTPError) as unauth_exc:
            _http_json("GET", "/api/storage")
        assert unauth_exc.value.code == 401

        # Proxied remote registration stays closed when STUDY_ALLOW_REGISTRATION=0
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _http_json(
                "POST",
                "/api/account/register",
                {"username": "remote1", "password": "StrongPassword!123"},
                headers={"X-Forwarded-For": "203.0.113.10"},
            )
        assert exc_info.value.code == 403

        # Authenticate as learner1, upload an isolated PDF material without running models
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        login_code, login_data = _http_json(
            "POST",
            "/api/account/login",
            {"username": "learner1", "password": "StrongPassword!123"},
            opener=opener,
        )
        assert login_code == 200
        assert login_data["account"]["username"] == "learner1"

        with fitz.open() as document:
            document.new_page().insert_text((72, 72), "Disposable material persistence check across restart.")
            pdf_raw = document.tobytes()

        up_code, up_data = _http_json(
            "POST",
            "/api/materials/upload",
            {
                "filename": "disposable-restart.pdf",
                "file_base64": base64.b64encode(pdf_raw).decode(),
                "generate_study_set": False,
            },
            opener=opener,
        )
        assert up_code == 200
        material_id = up_data["material_id"]

        restart_res = _run_cli(cfg_path, "restart", "--json")
        assert restart_res.returncode == 0, restart_res.stderr
        restarted = json.loads(restart_res.stdout)
        assert restarted["action"] == "restarted"
        assert restarted["process"]["backend_pid"] != back_pid
        assert not _pid_alive(back_pid)

        # Fresh login and uploaded account material persist across full backend restart
        cj2 = http.cookiejar.CookieJar()
        opener2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj2))
        login2_code, login2_data = _http_json(
            "POST",
            "/api/account/login",
            {"username": "learner1", "password": "StrongPassword!123"},
            opener=opener2,
        )
        assert login2_code == 200
        assert login2_data["account"]["username"] == "learner1"
        mat_code, mat_data = _http_json("GET", f"/api/materials/{material_id}", opener=opener2)
        assert mat_code == 200
        assert mat_data["filename"] == "disposable-restart.pdf"
    finally:
        stop_res = _run_cli(cfg_path, "stop", "--json")
        assert stop_res.returncode == 0, stop_res.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="macOS/POSIX supervisor and .command launcher tests")
def test_moved_nfd_folder_terminal_exit_survival_idempotent_crash_recovery_and_stop(tmp_path):
    nfd_name = unicodedata.normalize("NFD", "Thư mục kiểm thử NFD")
    moved_dir = tmp_path / nfd_name / "Backend Quick Start"
    moved_dir.mkdir(parents=True)
    for item in TEMPLATE_DIR.iterdir():
        if item.is_file():
            shutil.copy2(item, moved_dir / item.name)
            if item.suffix in (".command", ".sh"):
                os.chmod(moved_dir / item.name, 0o755)

    store = _create_disposable_store(tmp_path / "fast_store")
    mod_dir = tmp_path / "asgi_mods"
    mod_dir.mkdir()
    app_target = _write_test_asgi_module(mod_dir, "fast_app", noisy_logs=True)
    port = _free_port()
    cfg_path = _write_config(
        moved_dir / "backend.config.json",
        store,
        port,
        server_app=app_target,
        extra={"max_log_bytes": 1500, "max_log_backups": 2},
    )
    env = {**os.environ, "PYTHONPATH": f"{mod_dir}:{REPO_ROOT}"}

    try:
        # Start from an ephemeral parent shell that exits immediately after Start Backend.command
        shell_cmd = subprocess.run(
            ["zsh", "-c", f'"{moved_dir / "Start Backend.command"}" --json && exit 0'],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            check=False,
        )
        assert shell_cmd.returncode == 0, f"stderr={shell_cmd.stderr}\nstdout={shell_cmd.stdout}"
        first = json.loads(shell_cmd.stdout)
        sup_pid = first["process"]["supervisor_pid"]
        back_pid = first["process"]["backend_pid"]
        assert _pid_alive(sup_pid) and _pid_alive(back_pid)

        # Idempotent double-start keeps exact PIDs
        second_cmd = subprocess.run(
            [str(moved_dir / "Start Backend.command"), "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            check=False,
        )
        assert second_cmd.returncode == 0, second_cmd.stderr
        second = json.loads(second_cmd.stdout)
        assert second["action"] == "already_running"
        assert second["process"]["supervisor_pid"] == sup_pid
        assert second["process"]["backend_pid"] == back_pid

        # Open --no-open verifies configured URL without opening browser
        open_cmd = subprocess.run(
            [str(moved_dir / "Open App.command"), "--no-open", "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
            check=False,
        )
        assert open_cmd.returncode == 0
        assert json.loads(open_cmd.stdout)["url"] == f"http://127.0.0.1:{port}"

        # Bounded log rotation verified
        log_files = list(store["log_dir"].glob("backend.log*"))
        assert len(log_files) >= 2 and len(log_files) <= 3
        for lf in log_files:
            assert lf.stat().st_size <= 4096

        # Simulate backend child crash -> supervisor must recover cleanly
        os.kill(back_pid, signal.SIGKILL)

        recovered_data = {}

        def _is_recovered() -> bool:
            nonlocal recovered_data
            st = _run_cli(cfg_path, "status", "--json", env_extra=env)
            if st.returncode != 0:
                return False
            parsed = json.loads(st.stdout)
            if parsed["web_ready"] and parsed["process"]["backend_pid"] not in (None, back_pid):
                recovered_data = parsed
                return True
            return False

        assert _wait_until(_is_recovered, timeout=15.0)
        new_back_pid = recovered_data["process"]["backend_pid"]
        assert new_back_pid != back_pid
        assert recovered_data["recovery"]["restarts"] >= 1
        assert recovered_data["process"]["supervisor_pid"] == sup_pid

        # View logs works cleanly
        logs_res = subprocess.run(
            [str(moved_dir / "View Logs.command"), "--lines", "15"],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
            check=False,
        )
        assert logs_res.returncode == 0
        assert "Supervisor Log" in logs_res.stdout

        # Controlled stop halts supervisor and backend and prevents restart
        stop_cmd = subprocess.run(
            [str(moved_dir / "Stop Backend.command"), "--json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            check=False,
        )
        assert stop_cmd.returncode == 0, stop_cmd.stderr
        stopped = json.loads(stop_cmd.stdout)
        assert stopped["web_ready"] is False
        assert _wait_until(lambda: not _pid_alive(sup_pid) and not _pid_alive(new_back_pid), timeout=5.0)
        time.sleep(0.5)
        assert not _pid_alive(sup_pid) and not _pid_alive(new_back_pid)
    finally:
        _run_cli(cfg_path, "stop", "--json", env_extra=env)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS/POSIX supervisor and .command launcher tests")
def test_crash_loop_respects_max_restarts_cap(tmp_path):
    store = _create_disposable_store(tmp_path / "crash_store")
    mod_dir = tmp_path / "crash_mods"
    mod_dir.mkdir()
    app_target = _write_test_asgi_module(mod_dir, "crashing_app", crash_immediately=True)
    port = _free_port()
    cfg_path = _write_config(
        tmp_path / "crash.json",
        store,
        port,
        server_app=app_target,
        extra={"max_restarts": 2, "restart_backoff_seconds": 0.05, "restart_window_seconds": 30.0},
    )
    env = {**os.environ, "PYTHONPATH": f"{mod_dir}:{REPO_ROOT}"}

    try:
        res = _run_cli(cfg_path, "start", "--json", env_extra=env)
        assert res.returncode != 0
        assert _wait_until(lambda: json.loads(_run_cli(cfg_path, "status", "--json", env_extra=env).stdout)["recovery"]["supervisor_status"] == "failed", timeout=10.0)
        st = json.loads(_run_cli(cfg_path, "status", "--json", env_extra=env).stdout)
        assert st["recovery"]["restarts"] == 2
        assert "max_restarts" in (st["recovery"]["last_failure"] or "")
        assert not _pid_alive(st["process"]["supervisor_pid"] or -1)
    finally:
        _run_cli(cfg_path, "stop", "--json", env_extra=env)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS/POSIX supervisor and .command launcher tests")
def test_foreign_port_fail_closed_and_stale_tampered_pid_untouched(tmp_path):
    store = _create_disposable_store(tmp_path / "foreign_store")
    port = _free_port()
    cfg_path = _write_config(tmp_path / "foreign.json", store, port)

    foreign_proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_until(lambda: bs.is_port_accepting("127.0.0.1", port), timeout=5.0)
        start_res = _run_cli(cfg_path, "start", "--json")
        assert start_res.returncode == 2
        assert "foreign" in start_res.stdout.lower()
        assert foreign_proc.poll() is None, "Foreign port listener must NEVER be killed"

        stop_res = _run_cli(cfg_path, "stop", "--json")
        assert stop_res.returncode == 2
        assert foreign_proc.poll() is None, "Stop must NEVER kill foreign port listener"
    finally:
        foreign_proc.terminate()
        foreign_proc.wait(timeout=5)

    # Stale / tampered PID identity mismatch must fail closed without signaling unrelated process
    unrelated = subprocess.Popen(["sleep", "30"], cwd=str(tmp_path))
    try:
        cfg = bs.load_and_validate_config(cfg_path)
        bs.ensure_controller_dirs(cfg)
        (store["state_dir"] / "backend.json").write_text(
            json.dumps({"pid": unrelated.pid, "lstart": "Mon Jan 01 00:00:00 2001", "cwd": str(REPO_ROOT)}),
            encoding="utf-8",
        )
        stop_tampered = _run_cli(cfg_path, "stop", "--json")
        assert stop_tampered.returncode == 2
        assert "mismatch" in stop_tampered.stdout.lower()
        assert unrelated.poll() is None, "Unrelated PID must NEVER be signaled"
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_hosted_runtime_failure_messages_omit_local_launcher_instructions():
    cases = {
        "incomplete": ("finish_reason done_reason=length", ("chưa trọn vẹn",)),
        "sqlite": ("sqlite3.OperationalError: disk I/O error", ("cơ sở dữ liệu",)),
        "metal": ("nanobind metal initialization failed", ("GPU Metal", "người vận hành")),
        "generic": ("model artifact missing", ("người vận hành", "thử lại")),
    }
    messages = {}
    for key, (err, expected_substrings) in cases.items():
        msg = runtime_health.runtime_failure_message(err)
        messages[key] = msg
        for needle in expected_substrings:
            assert needle.lower() in msg.lower()
        for forbidden in ("Start Study Agent.command", "Start Study Agent.bat", "Finder", "Terminal"):
            assert forbidden not in msg, f"Hosted UI error leaked '{forbidden}': {msg}"
    assert len(set(messages.values())) == 4


def test_gpu_runtime_dir_override_keeps_watcher_and_accounts_separate(tmp_path, monkeypatch):
    import app.config as cfg
    import app.gpu_lock as gpu_lock
    from app.src_watcher import SrcWatcher

    shared_gpu = tmp_path / "shared_gpu"
    data_a = tmp_path / "data_a"
    accounts_dir = tmp_path / "accounts"
    src_dir = tmp_path / "src"
    data_a.mkdir()
    accounts_dir.mkdir()
    src_dir.mkdir()

    monkeypatch.setenv("STUDY_GPU_RUNTIME_DIR", str(shared_gpu))
    monkeypatch.setattr(cfg, "DATA_DIR", data_a)
    importlib.reload(gpu_lock)
    try:
        mgr = gpu_lock.GPULockManager()
        assert mgr.lock_path.parent == shared_gpu
        watcher = SrcWatcher(src_dir=src_dir, data_dir=data_a, enabled_override=False)
        assert watcher.state_path.parent == data_a / "runtime"
        assert watcher.state_path.parent != shared_gpu
    finally:
        monkeypatch.delenv("STUDY_GPU_RUNTIME_DIR", raising=False)
        importlib.reload(gpu_lock)


def test_launcher_template_files_exist_and_infra_posture_warnings(tmp_path, monkeypatch):
    """Cross-platform check for tracked launcher templates and infrastructure/security posture."""
    expected_commands = {
        "Start Backend.command",
        "Stop Backend.command",
        "Restart Backend.command",
        "Check Status.command",
        "View Logs.command",
        "Open App.command",
    }
    assert TEMPLATE_DIR.is_dir(), f"Missing tracked template directory: {TEMPLATE_DIR}"
    assert {p.name for p in TEMPLATE_DIR.glob("*.command")} == expected_commands
    for fname in ("_run_action.sh", "config.example.json", "README.md"):
        assert (TEMPLATE_DIR / fname).is_file()
        assert "/Users/" not in (TEMPLATE_DIR / fname).read_text(encoding="utf-8")

    infra_root = tmp_path / "infra"
    src_dir = infra_root / "src"
    data_dir = infra_root / "data"
    accounts_dir = infra_root / "accounts"
    model_9b_dir = infra_root / "models" / "9b"
    model_bge_dir = infra_root / "models" / "bge"
    timetable_images_dir = data_dir / "timetable_images"
    gpu_runtime_dir = data_dir / "runtime"
    for d in (src_dir, data_dir, accounts_dir, model_9b_dir, model_bge_dir, timetable_images_dir, gpu_runtime_dir):
        d.mkdir(parents=True, exist_ok=True)
    (model_9b_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_bge_dir / "config.json").write_text("{}", encoding="utf-8")
    kb_db = data_dir / "knowledge_base.db"
    with sqlite3.connect(kb_db) as conn:
        conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY)")
        conn.commit()

    cfg = {
        "config_path": tmp_path / "backend-service.json",
        "code_dir": REPO_ROOT,
        "python_bin": Path(sys.executable),
        "train_python": Path(sys.executable),
        "agent_src_dir": src_dir,
        "agent_data_dir": data_dir,
        "knowledge_base_db": kb_db,
        "study_accounts_dir": accounts_dir,
        "history_db_path": data_dir / "history.db",
        "timetable_db_path": data_dir / "timetable.db",
        "timetable_images_dir": timetable_images_dir,
        "mlx_model_dir": model_9b_dir,
        "mlx_9b_model_dir": model_9b_dir,
        "mlx_bge_model_dir": model_bge_dir,
        "study_gpu_runtime_dir": gpu_runtime_dir,
        "state_dir": tmp_path / "state",
        "log_dir": tmp_path / "logs",
        "host": "127.0.0.1",
        "port": 8000,
        "forwarded_allow_ips": "127.0.0.1,::1",
        "server_app": bs.DEFAULT_SERVER_APP,
        "public_url": "https://study-pilot.trycloudflare.com",
        "study_src_auto_watch": False,
        "study_accounts_required": False,
        "study_allow_registration": True,
        "study_secure_cookies": False,
        "require_nonempty_src": False,
        "max_log_bytes": 1024 * 1024,
        "max_log_backups": 2,
        "restart_backoff_seconds": 0.5,
        "max_restart_backoff_seconds": 2.0,
        "max_restarts": 2,
        "restart_window_seconds": 60.0,
        "startup_timeout_seconds": 10.0,
        "stop_timeout_seconds": 5.0,
        "min_free_disk_mb": 128,
    }

    bs.validate_runtime_paths(cfg)
    posture = bs.inspect_infrastructure_posture(cfg)
    assert posture["https_public_url"] is True
    assert posture["disk_healthy"] is True
    assert any("study_secure_cookies=False" in w for w in posture["warnings"])
    assert any("study_accounts_required=False" in w for w in posture["warnings"])
    assert any("study_allow_registration=True" in w for w in posture["warnings"])

    FakeDisk = type("FakeDisk", (), {"total": 10 * 1024 * 1024 * 1024, "used": 9 * 1024 * 1024 * 1024, "free": 32 * 1024 * 1024})
    monkeypatch.setattr(bs.shutil, "disk_usage", lambda _path: FakeDisk())
    low_posture = bs.inspect_infrastructure_posture(cfg)
    assert low_posture["disk_healthy"] is False
    assert any("Low free disk space" in w for w in low_posture["warnings"])
    with pytest.raises(bs.ServiceError, match="Insufficient free disk space"):
        bs.validate_runtime_paths(cfg)
