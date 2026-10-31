"""Continuous Multi-Round MLX Training Pipeline Orchestrator.
Provides atomic state management with flock serialization, process group supervision,
robust identity verification (PID, start time, cwd, command), checkpointing,
and fail-closed corrupt state recovery.
"""

import argparse
import fcntl
import json
import logging
import os
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any, Optional, List

from app.config import BASE_DIR, DEFAULT_CHAT_MODEL
from app.model_resolver import reject_retired_model
from app.gpu_lock import GPULockManager, gpu_coordinator

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = BASE_DIR / "data" / "runtime" / "continuous_pipeline_state.json"
DEFAULT_STATE_LOCK = BASE_DIR / "data" / "runtime" / "continuous_pipeline.lock"

PIPELINE_STAGES = [
    "batch_discovery",
    "curation",
    "snapshot_freeze",
    "training",
    "evaluation",
    "review",
    "completed"
]


class CorruptStateError(RuntimeError):
    """Raised when pipeline state is corrupted and cannot be safely loaded."""
    pass


class ContinuousPipelineOrchestrator:
    def __init__(
        self,
        state_path: Path = DEFAULT_STATE_PATH,
        lock_path: Path = DEFAULT_STATE_LOCK,
        gpu_mgr: Optional[GPULockManager] = None
    ):
        self.state_path = state_path
        self.lock_path = lock_path
        self.gpu_mgr = gpu_mgr or gpu_coordinator
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _acquire_lock(self):
        """Serialize all pipeline state mutations and lifecycle operations with fcntl.flock."""
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _read_state(self) -> Dict[str, Any]:
        """Read state from disk. Fails closed on corrupted JSON unless a valid backup exists."""
        if not self.state_path.exists():
            return {
                "status": "idle",
                "stage": "idle",
                "round": 0,
                "current_step": 0,
                "total_steps": 0,
                "dataset_version": None,
                "model_name": DEFAULT_CHAT_MODEL,
                "pid": None,
                "pgid": None,
                "start_time": None,
                "lstart": None,
                "command": None,
                "cwd": None,
                "checkpoints": [],
                "history": [],
                "error": None,
            }

        bak_path = self.state_path.with_suffix(".json.bak")
        raw_text = self.state_path.read_text(encoding="utf-8")
        try:
            return json.loads(raw_text)
        except Exception as primary_err:
            logger.error("State file corrupted: %s", primary_err)
            # Attempt recovery from backup
            if bak_path.exists():
                try:
                    recovered = json.loads(bak_path.read_text(encoding="utf-8"))
                    logger.warning("Successfully recovered state from backup %s", bak_path)
                    return recovered
                except Exception as bak_err:
                    logger.error("Backup state file also corrupted: %s", bak_err)
            raise CorruptStateError(
                f"Tệp trạng thái pipeline bị hỏng ({primary_err}). Fail-closed để ngăn chặn khởi động trùng lặp."
            ) from primary_err

    def _write_state(self, state: Dict[str, Any]):
        """Atomic write via unique temporary file + fsync + atomic rename, with zero-gap backup preservation."""
        state["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        bak_path = self.state_path.with_suffix(".json.bak")

        # Create unique temp file in same directory
        temp_fd, temp_path = tempfile.mkstemp(
            prefix="pipeline_state_",
            suffix=".tmp",
            dir=str(self.state_path.parent)
        )
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(temp_fd)

            # Copy existing state to backup BEFORE atomic replace (zero gap!)
            if self.state_path.exists():
                try:
                    bak_path.write_bytes(self.state_path.read_bytes())
                except Exception:
                    pass

            os.replace(temp_path, str(self.state_path))
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def get_process_identity(self, pid: int) -> Optional[Dict[str, str]]:
        """Query macOS ps to retrieve process lstart, stat, and full command."""
        try:
            res = subprocess.run(
                ["ps", "-ww", "-p", str(pid), "-o", "pid,stat,lstart,command"],
                capture_output=True,
                text=True,
                check=False
            )
            if res.returncode != 0:
                return None
            lines = res.stdout.strip().splitlines()
            if len(lines) < 2:
                return None
            line = lines[1].strip()
            parts = line.split(None, 7)  # pid, stat, weekday, month, day, time, year, command
            if len(parts) >= 8:
                stat = parts[1]
                lstart = f"{parts[2]} {parts[3]} {parts[4]} {parts[5]} {parts[6]}"
                command = parts[7]
                return {"stat": stat, "lstart": lstart, "command": command}
        except Exception:
            pass
        return None

    def get_process_cwd(self, pid: int) -> Optional[str]:
        """Query lsof to retrieve process cwd directory."""
        try:
            res = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                capture_output=True,
                text=True,
                check=False
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.startswith("n"):
                        return line[1:]
        except Exception:
            pass
        return None

    def _verify_process_alive(
        self,
        pid: Optional[int],
        expected_cwd: Optional[str] = None,
        expected_command: Optional[List[str]] = None,
        expected_lstart: Optional[str] = None
    ) -> bool:
        """Strictly verify that process is alive AND matches expected identity (cwd, command, start time).

        Fail-closed: returns False if PID does not exist or identity does not match.
        """
        if not pid or pid <= 0:
            return False

        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False

        ident = self.get_process_identity(pid)
        if not ident:
            return False

        # Check for zombie process
        if "Z" in ident["stat"]:
            return False

        # Verify start time if recorded (prevents PID reuse clobbering)
        if expected_lstart and ident["lstart"] != expected_lstart:
            logger.warning("Process %s start time mismatch: expected %s, found %s", pid, expected_lstart, ident["lstart"])
            return False

        # Verify command token matching if expected command provided
        if expected_command:
            cmd_text = ident["command"].lower()
            exe_base = Path(expected_command[0]).name.lower()

            if "python" in exe_base:
                exe_match = "python" in cmd_text
            else:
                exe_match = exe_base in cmd_text

            arg_match = True
            normalized_cmd = cmd_text.replace(r"\012", " ").replace("\012", " ")
            for arg in expected_command[1:]:
                if not arg.startswith("-") and len(arg.strip()) > 3:
                    tokens = [t.lower() for t in arg.split() if len(t) > 2]
                    if tokens and not any(t in normalized_cmd for t in tokens[:3]):
                        arg_match = False
                        break

            if not (exe_match and arg_match):
                logger.warning("Process %s command mismatch: expected %s in %s", pid, expected_command, ident["command"])
                return False

        # Verify cwd if expected_cwd provided - FAIL CLOSED if cwd cannot be retrieved
        if expected_cwd:
            proc_cwd = self.get_process_cwd(pid)
            if not proc_cwd:
                logger.warning("Process %s cwd cannot be determined; failing closed", pid)
                return False
            try:
                if not (proc_cwd == expected_cwd or os.path.samefile(proc_cwd, expected_cwd)):
                    logger.warning("Process %s cwd mismatch: expected %s, found %s", pid, expected_cwd, proc_cwd)
                    return False
            except OSError:
                return False

        return True

    def get_status(self) -> Dict[str, Any]:
        """Query live pipeline status with flock serialization and real-time identity verification."""
        with self._acquire_lock():
            state = self._read_state()
            pid = state.get("pid")
            current_status = state.get("status", "idle")

            if current_status in ("running", "paused"):
                alive = self._verify_process_alive(
                    pid=pid,
                    expected_cwd=state.get("cwd"),
                    expected_command=state.get("command"),
                    expected_lstart=state.get("lstart")
                )
                if not alive:
                    logger.info("Process %s died or identity mismatch, auto-healing pipeline state to stopped", pid)
                    state["status"] = "stopped"
                    state["pid"] = None
                    state["pgid"] = None
                    state["error"] = f"Process {pid} exited or was replaced"
                    self._write_state(state)

            return state

    def start_round(
        self,
        dataset_version: str,
        command: List[str],
        total_steps: int,
        model_name: str = DEFAULT_CHAT_MODEL,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        mock_proc: Optional[subprocess.Popen] = None
    ) -> Dict[str, Any]:
        """Start a new continuous training round under exclusive lock."""
        reject_retired_model(model_name)
        with self._acquire_lock():
            state = self._read_state()
            pid = state.get("pid")

            if state.get("status") in ("running", "paused"):
                if self._verify_process_alive(pid, state.get("cwd"), state.get("command"), state.get("lstart")):
                    raise RuntimeError(f"Pipeline is already running (PID {pid}). Pause or stop before starting.")

            current_round = state.get("round", 0) + 1
            work_dir = cwd or BASE_DIR

            if mock_proc:
                proc = mock_proc
            else:
                proc = subprocess.Popen(
                    command,
                    cwd=str(work_dir),
                    env=env,
                    start_new_session=True
                )

            # Retrieve process group - SAFETY GUARD: only record pgid if proc.pid is its own group leader
            # AND not the caller's process group!
            try:
                actual_pgid = os.getpgid(proc.pid)
                if actual_pgid == proc.pid and actual_pgid != os.getpgrp():
                    pgid = actual_pgid
                else:
                    pgid = None
            except Exception:
                pgid = None

            ident = self.get_process_identity(proc.pid)
            lstart = ident["lstart"] if ident else None

            state["status"] = "running"
            state["stage"] = "training"
            state["round"] = current_round
            state["current_step"] = 0
            state["total_steps"] = total_steps
            state["dataset_version"] = dataset_version
            state["model_name"] = model_name
            state["pid"] = proc.pid
            state["pgid"] = pgid
            state["lstart"] = lstart
            state["command"] = command
            state["cwd"] = str(work_dir)
            state["start_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            state["error"] = None
            self._write_state(state)

            return state

    def pause(self, reason: str = "User requested pause") -> Dict[str, Any]:
        """Pause the running process group with SIGSTOP under exclusive lock."""
        with self._acquire_lock():
            state = self._read_state()
            if state.get("status") != "running":
                raise RuntimeError(f"Cannot pause pipeline in status '{state.get('status')}'")

            pid = state.get("pid")
            pgid = state.get("pgid")
            if not self._verify_process_alive(pid, state.get("cwd"), state.get("command"), state.get("lstart")):
                state["status"] = "stopped"
                state["pid"] = None
                state["pgid"] = None
                self._write_state(state)
                raise RuntimeError(f"Cannot pause: process {pid} is not alive or identity mismatch")

            try:
                if pgid and pgid == pid and pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGSTOP)
                else:
                    os.kill(pid, signal.SIGSTOP)
            except Exception as exc:
                raise RuntimeError(f"Failed to send SIGSTOP to process {pid}: {exc}") from exc

            state["status"] = "paused"
            state["pause_reason"] = reason
            self._write_state(state)
            return state

    def resume(self) -> Dict[str, Any]:
        """Resume the paused process group with SIGCONT under exclusive lock."""
        with self._acquire_lock():
            state = self._read_state()
            if state.get("status") != "paused":
                raise RuntimeError(f"Cannot resume pipeline in status '{state.get('status')}'")

            pid = state.get("pid")
            pgid = state.get("pgid")
            if not self._verify_process_alive(pid, state.get("cwd"), state.get("command"), state.get("lstart")):
                state["status"] = "stopped"
                state["pid"] = None
                state["pgid"] = None
                self._write_state(state)
                raise RuntimeError(f"Cannot resume: process {pid} is not alive or identity mismatch")

            try:
                if pgid and pgid == pid and pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGCONT)
                else:
                    os.kill(pid, signal.SIGCONT)
            except Exception as exc:
                raise RuntimeError(f"Failed to send SIGCONT to process {pid}: {exc}") from exc

            state["status"] = "running"
            state["pause_reason"] = None
            self._write_state(state)
            return state

    def stop(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Gracefully terminate process group (SIGTERM then SIGKILL), waiting for children to die."""
        with self._acquire_lock():
            state = self._read_state()
            pid = state.get("pid")
            pgid = state.get("pgid")

            if pid and self._verify_process_alive(pid, state.get("cwd"), state.get("command"), state.get("lstart")):
                # Find all child processes of the target worker
                child_pids = []
                try:
                    res = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False)
                    if res.returncode == 0:
                        child_pids = [int(p.strip()) for p in res.stdout.splitlines() if p.strip().isdigit()]
                except Exception:
                    pass

                # Wake process if paused before sending terminate
                if state.get("status") == "paused":
                    try:
                        if pgid and pgid == pid and pgid != os.getpgrp():
                            os.killpg(pgid, signal.SIGCONT)
                        else:
                            os.kill(pid, signal.SIGCONT)
                        for cpid in child_pids:
                            try:
                                os.kill(cpid, signal.SIGCONT)
                            except OSError:
                                pass
                    except Exception:
                        pass

                try:
                    if pgid and pgid == pid and pgid != os.getpgrp():
                        os.killpg(pgid, signal.SIGTERM)
                    else:
                        os.kill(pid, signal.SIGTERM)
                    for cpid in child_pids:
                        try:
                            os.kill(cpid, signal.SIGTERM)
                        except OSError:
                            pass
                except Exception as exc:
                    logger.warning("Error sending SIGTERM to process %s: %s", pid, exc)

                # Wait for parent and all children in the group to terminate
                start_wait = time.time()
                while time.time() - start_wait < timeout:
                    parent_dead = not self._verify_process_alive(pid, state.get("cwd"), state.get("command"), state.get("lstart"))
                    children_dead = True
                    for cpid in child_pids:
                        try:
                            os.kill(cpid, 0)
                            children_dead = False
                        except OSError:
                            pass
                    if parent_dead and children_dead:
                        break
                    time.sleep(0.1)

                # If still alive, escalate to SIGKILL
                if self._verify_process_alive(pid, state.get("cwd"), state.get("command"), state.get("lstart")):
                    try:
                        if pgid and pgid == pid and pgid != os.getpgrp():
                            os.killpg(pgid, signal.SIGKILL)
                        else:
                            os.kill(pid, signal.SIGKILL)
                    except Exception as exc:
                        logger.warning("Error sending SIGKILL to process %s: %s", pid, exc)

                for cpid in child_pids:
                    try:
                        os.kill(cpid, signal.SIGKILL)
                    except OSError:
                        pass

                # Final drain wait
                drain_start = time.time()
                while time.time() - drain_start < 2.0:
                    parent_dead = not self._verify_process_alive(pid, state.get("cwd"), state.get("command"), state.get("lstart"))
                    children_dead = True
                    for cpid in child_pids:
                        try:
                            os.kill(cpid, 0)
                            children_dead = False
                        except OSError:
                            pass
                    if parent_dead and children_dead:
                        break
                    time.sleep(0.1)

            state["status"] = "stopped"
            state["stage"] = "stopped"
            state["pid"] = None
            state["pgid"] = None
            self._write_state(state)
            return state

    def record_checkpoint(self, step: int, checkpoint_path: str, metrics: Optional[Dict[str, Any]] = None):
        """Record an intermediate training checkpoint under exclusive lock."""
        with self._acquire_lock():
            state = self._read_state()
            state["current_step"] = step
            checkpoint_entry = {
                "step": step,
                "path": checkpoint_path,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "metrics": metrics or {}
            }
            state["checkpoints"].append(checkpoint_entry)
            self._write_state(state)

    def complete_round(
        self,
        exit_code: int,
        evaluation_evidence: Dict[str, Any],
        adapter_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Mark round as completed. Strictly enforces zero exit code and verified evaluation evidence."""
        with self._acquire_lock():
            state = self._read_state()

            # Rule 1: Worker must exit with 0
            if exit_code != 0:
                raise ValueError(f"Không thể hoàn tất vòng học: tiến trình kết thúc với mã lỗi {exit_code}")

            # Rule 2: Evaluation evidence must be non-empty and verified
            if not evaluation_evidence or not isinstance(evaluation_evidence, dict):
                raise ValueError("Bắt buộc phải có bằng chứng evaluation (metrics/validation loss) để nghiệm thu vòng học")

            # Rule 3: Process must be verified terminated
            pid = state.get("pid")
            if pid and self._verify_process_alive(pid, state.get("cwd"), state.get("command"), state.get("lstart")):
                raise RuntimeError(f"Tiến trình {pid} vẫn đang chạy; không thể đánh dấu completed trước khi tiến trình kết thúc")

            round_num = state.get("round", 1)
            history_entry = {
                "round": round_num,
                "dataset_version": state.get("dataset_version"),
                "model_name": state.get("model_name"),
                "total_steps": state.get("total_steps"),
                "final_step": state.get("current_step", state.get("total_steps")),
                "adapter_path": adapter_path,
                "start_time": state.get("start_time"),
                "end_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "evaluation": evaluation_evidence,
            }
            state["history"].append(history_entry)
            state["status"] = "completed"
            state["stage"] = "completed"
            state["pid"] = None
            state["pgid"] = None
            self._write_state(state)
            return state


def main():
    parser = argparse.ArgumentParser(description="Continuous MLX Training Pipeline Controller")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("status", help="Get current pipeline status")
    subparsers.add_parser("pause", help="Pause running training")
    subparsers.add_parser("resume", help="Resume paused training")
    subparsers.add_parser("stop", help="Stop training process")

    start_p = subparsers.add_parser("start", help="Start continuous training")
    start_p.add_argument("--data-version", type=str, default="v6")
    start_p.add_argument("--steps", type=int, default=80)
    start_p.add_argument("--model", type=str, default=DEFAULT_CHAT_MODEL)

    args = parser.parse_args()
    orch = ContinuousPipelineOrchestrator()

    if args.action == "status":
        print(json.dumps(orch.get_status(), ensure_ascii=False, indent=2))
    elif args.action == "pause":
        print(json.dumps(orch.pause(), ensure_ascii=False, indent=2))
    elif args.action == "resume":
        print(json.dumps(orch.resume(), ensure_ascii=False, indent=2))
    elif args.action == "stop":
        print(json.dumps(orch.stop(), ensure_ascii=False, indent=2))
    elif args.action == "start":
        cmd = ["echo", "Starting training round"]
        print(json.dumps(orch.start_round(args.data_version, cmd, args.steps, args.model), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
