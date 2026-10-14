"""Exact tokenizer helper for chat template token counting without model weights or GPU.

Uses .train-venv with transformers.AutoTokenizer (local_files_only) and replicates
the exact apply_chat_template(tokenize=False, add_generation_prompt=True) + encode
used in mlx_infer.
"""

import atexit
import json
import logging
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.config import BASE_DIR, MLX_MODEL_DIR

logger = logging.getLogger(__name__)


def _find_model_dir() -> Optional[Path]:
    candidates = [
        MLX_MODEL_DIR,
        BASE_DIR / "data" / "models" / "qwen2.5-3b-4bit",
    ]
    for p in candidates:
        if p.exists() and (p / "tokenizer.json").exists():
            return p
    return None


def _find_train_python() -> Optional[Path]:
    candidates = [
        BASE_DIR / ".train-venv" / "bin" / "python",
    ]
    for p in candidates:
        if p.exists() and os.access(p, os.X_OK):
            return p
    return None


class TokenizerWorkerClient:
    """Persistent, thread-safe helper client to communicate with tokenizer worker."""

    def __init__(self, model_dir: Path, python_path: Path):
        self.model_dir = model_dir
        self.python_path = python_path
        self.timeout_seconds: float = float(os.getenv("TOKENIZER_WORKER_TIMEOUT", "5.0"))
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._read_buffer: bytes = b""
        atexit.register(self.close)

    def _start_worker(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self.close()
        cmd = [
            str(self.python_path),
            "-m", "app.token_counter",
            "--server",
            "--model-dir", str(self.model_dir),
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            cwd=str(BASE_DIR),
        )
        self._read_buffer = b""
        if sys.platform != "win32":
            os.set_blocking(self._proc.stdin.fileno(), False)

    def _write_req(self, data: bytes, deadline: float) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Tokenizer worker is not running")
        fd = self._proc.stdin.fileno()
        total_written = 0
        while total_written < len(data):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Tokenizer worker stdin write timed out")
            if sys.platform != "win32":
                _, w, _ = select.select([], [fd], [], max(0.0, remaining))
                if not w:
                    raise TimeoutError("Tokenizer worker stdin write timed out")
            try:
                written = os.write(fd, data[total_written:])
            except BlockingIOError:
                continue
            if written == 0:
                raise RuntimeError("Tokenizer worker stdin closed")
            total_written += written

    def _read_line(self, deadline: float) -> str:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("Tokenizer worker is not running")
        fd = self._proc.stdout.fileno()
        while b"\n" not in self._read_buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Tokenizer worker response timed out")
            if sys.platform != "win32":
                r, _, _ = select.select([fd], [], [], max(0.0, remaining))
                if not r:
                    raise TimeoutError("Tokenizer worker response timed out")
            chunk = os.read(fd, 4096)
            if not chunk:
                raise RuntimeError("Tokenizer worker closed unexpectedly")
            self._read_buffer += chunk

        line, self._read_buffer = self._read_buffer.split(b"\n", 1)
        return line.decode("utf-8")

    def _communicate(self, payload: str) -> str:
        timeout = getattr(self, "timeout_seconds", 5.0)
        deadline = time.monotonic() + timeout
        self._start_worker()
        req_bytes = payload.encode("utf-8") + b"\n"
        self._write_req(req_bytes, deadline)
        return self._read_line(deadline)

    def count(self, messages: List[Dict[str, str]]) -> int:
        with self._lock:
            try:
                payload = json.dumps({"messages": messages}, ensure_ascii=False)
                resp_line = self._communicate(payload)
                try:
                    resp = json.loads(resp_line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"Tokenizer worker returned invalid JSON: {resp_line!r}") from exc
                if not isinstance(resp, dict):
                    raise ValueError(f"Tokenizer worker returned non-dict response: {resp!r}")
                if "error" in resp:
                    raise RuntimeError(resp["error"])
                if "count" not in resp:
                    raise ValueError("Missing 'count' in worker response")
                count_val = resp["count"]
                if isinstance(count_val, bool) or not isinstance(count_val, int) or count_val < 0:
                    raise ValueError(f"Invalid count received: {count_val!r}")
                return int(count_val)
            except Exception as exc:
                self.close()
                logger.warning("Tokenizer worker count failed, restarting on next call: %s", exc)
                raise

    def count_batch(self, batch: List[List[Dict[str, str]]]) -> List[int]:
        with self._lock:
            try:
                payload = json.dumps({"batch": batch}, ensure_ascii=False)
                resp_line = self._communicate(payload)
                try:
                    resp = json.loads(resp_line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"Tokenizer worker returned invalid JSON: {resp_line!r}") from exc
                if not isinstance(resp, dict):
                    raise ValueError(f"Tokenizer worker returned non-dict response: {resp!r}")
                if "error" in resp:
                    raise RuntimeError(resp["error"])
                if "counts" not in resp:
                    raise ValueError("Missing 'counts' in worker response")
                counts_val = resp["counts"]
                if not isinstance(counts_val, (list, tuple)) or len(counts_val) != len(batch):
                    raise ValueError(
                        f"Batch cardinality mismatch: expected {len(batch)}, "
                        f"got {len(counts_val) if isinstance(counts_val, (list, tuple)) else 'non-sequence'}"
                    )
                for c in counts_val:
                    if isinstance(c, bool) or not isinstance(c, int) or c < 0:
                        raise ValueError(f"Invalid count in batch response: {c!r}")
                return [int(c) for c in counts_val]
            except Exception as exc:
                self.close()
                logger.warning("Tokenizer worker batch failed: %s", exc)
                raise

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._read_buffer = b""
        if proc is not None:
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                if proc.stdout and not proc.stdout.closed:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=0.2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=0.2)
                except Exception:
                    pass


_worker_client: Optional[TokenizerWorkerClient] = None
_client_lock = threading.Lock()


def get_exact_token_counter() -> Optional[Callable[[List[Dict[str, str]]], int]]:
    """Return exact tokenizer counting callable, or None if unavailable/unsupported."""
    global _worker_client
    if sys.platform == "win32":
        return None

    model_dir = _find_model_dir()
    train_py = _find_train_python()
    if not model_dir or not train_py:
        return None

    with _client_lock:
        if _worker_client is None:
            _worker_client = TokenizerWorkerClient(model_dir, train_py)

    def counter(messages: List[Dict[str, str]]) -> int:
        return _worker_client.count(messages)

    return counter


def _run_server(model_dir: Path) -> None:
    """Run persistent JSON-RPC server on stdin/stdout using transformers.AutoTokenizer."""
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if "messages" in req:
                prompt = tokenizer.apply_chat_template(
                    req["messages"], tokenize=False, add_generation_prompt=True
                )
                count = len(tokenizer.encode(prompt))
                print(json.dumps({"count": count}), flush=True)
            elif "batch" in req:
                counts = []
                for msgs in req["batch"]:
                    prompt = tokenizer.apply_chat_template(
                        msgs, tokenize=False, add_generation_prompt=True
                    )
                    counts.append(len(tokenizer.encode(prompt)))
                print(json.dumps({"counts": counts}), flush=True)
            else:
                print(json.dumps({"error": "Unknown request payload"}), flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Exact tokenizer counter worker")
    parser.add_argument("--server", action="store_true", help="Run as stdio server")
    parser.add_argument("--model-dir", type=Path, default=MLX_MODEL_DIR, help="Base model directory")
    args = parser.parse_args()

    if args.server:
        _run_server(args.model_dir)
    else:
        print("Usage: python -m app.token_counter --server --model-dir <dir>")
