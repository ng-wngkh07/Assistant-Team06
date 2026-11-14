"""Direct native MLX dense embedding client."""

import atexit
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, List, Optional, Tuple

import app.config as _app_config
import app.model_resolver as _model_resolver
from app.config import BASE_DIR
from app.gpu_lock import IndexingCancelledError, gpu_coordinator
from app.model_resolver import MLX_BGE_DIR

_INITIAL_CFG_DATA_DIR = Path(_app_config.DATA_DIR)
_INITIAL_MR_MLX_BGE_DIR = Path(_model_resolver.MLX_BGE_DIR)
_INITIAL_NE_MLX_BGE_DIR = Path(MLX_BGE_DIR)

_ACTIVE_WORKERS_LOCK = threading.Lock()
_ACTIVE_WORKERS: set[subprocess.Popen] = set()


def _terminate_owned_worker(proc: subprocess.Popen, grace_seconds: float = 0.5) -> None:
    """Terminate and reap ONLY the owned child worker (and its dedicated session group)."""
    if proc.poll() is not None:
        return
    kill_pg = False
    if os.name == "posix":
        try:
            kill_pg = os.getpgid(proc.pid) == proc.pid
        except OSError:
            kill_pg = False
    try:
        if kill_pg:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except OSError:
        pass

    deadline = time.monotonic() + max(0.1, float(grace_seconds))
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)

    if proc.poll() is None:
        try:
            if kill_pg:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=2.0)
    except Exception:
        pass


def _cleanup_active_workers() -> None:
    with _ACTIVE_WORKERS_LOCK:
        procs = list(_ACTIVE_WORKERS)
    for proc in procs:
        _terminate_owned_worker(proc, grace_seconds=0.3)


atexit.register(_cleanup_active_workers)

BGE_NATIVE_IDENTITY = "bge-m3-mlx-fp16@a37eddded9a6a1273a87fb8b0da0d1cdbd98aeec"

# Pinned metadata for bge-m3-mlx-fp16 artifact verification
PINNED_BGE_FILES = {
    "config.json": {"bytes": 715, "sha256": "2f0b6a5c00cf8653f67d93dd611b119f57e9199f876e137de63090dab4dfb426"},
    "tokenizer.json": {"bytes": 17098085, "sha256": "5df1f55d60c9705a501ab9a75550728625740741fe4be308dac4806c16b7d51d"},
    "tokenizer_config.json": {"bytes": 379, "sha256": "c2ef99124628ae6f79a847ad67e5d3f5b016d0387de90c9fbaf17c45a98933af"},
    "special_tokens_map.json": {"bytes": 964, "sha256": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835"},
    "model.safetensors": {"bytes": 1135554209, "sha256": "e8028acb2e77e0010d35ef75832d823edb5851c95682deef2bde86a45bd4441d"},
}


class _VerifiedBatchResult(list):
    """List-compatible container carrying verified post-generation model identity."""
    def __init__(self, items: list, verified_identity: str):
        super().__init__(items)
        self.verified_identity = verified_identity


class NativeEmbeddingClient:
    """Direct local MLX embedding client using pinned BGE-M3 MLX artifact."""

    # Query vectors only: bounded process memory, never source chunks or answers.
    _query_cache = OrderedDict()
    _query_cache_lock = threading.Lock()
    _query_cache_limit = 128

    # Bounded process-local verified status identity cache keyed by resolved path and
    # stat/pinned-metadata snapshot. Only populated after full byte verification succeeds;
    # query/embedding checks still call verify_identity() directly to rehash actual bytes.
    _status_identity_cache: "OrderedDict[str, Tuple[Tuple[Any, ...], str]]" = OrderedDict()
    _status_identity_lock = threading.Lock()
    _STATUS_IDENTITY_CACHE_MAX = 16
    _TRACKED_IDENTITY_FILES = (
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "sentence_bert_config.json",
        "modules.json",
        "model.safetensors.index.json",
        "model.safetensors",
    )

    @classmethod
    def _resolve_default_model_dir(cls) -> Path:
        env_dir = (
            os.getenv("MLX_BGE_MODEL_DIR") or os.getenv("BGE_MODEL_DIR") or ""
        ).strip()
        if env_dir:
            return Path(env_dir)
        if Path(MLX_BGE_DIR) != _INITIAL_NE_MLX_BGE_DIR:
            return Path(MLX_BGE_DIR)
        mr_dir = Path(getattr(_model_resolver, "MLX_BGE_DIR", _INITIAL_MR_MLX_BGE_DIR))
        if mr_dir != _INITIAL_MR_MLX_BGE_DIR:
            return mr_dir
        cfg_data_dir = Path(getattr(_app_config, "DATA_DIR", _INITIAL_CFG_DATA_DIR))
        if cfg_data_dir != _INITIAL_CFG_DATA_DIR:
            return cfg_data_dir / "models" / "bge-m3-mlx-fp16"
        env_data_dir = (os.getenv("AGENT_DATA_DIR") or "").strip()
        if env_data_dir:
            return Path(env_data_dir) / "models" / "bge-m3-mlx-fp16"
        return cfg_data_dir / "models" / "bge-m3-mlx-fp16"

    @classmethod
    def clear_status_identity_cache(cls) -> None:
        """Clear cached status identities after explicit configuration or test resets."""
        with cls._status_identity_lock:
            cls._status_identity_cache.clear()

    @classmethod
    def _artifact_status_signature(cls, resolved: Path) -> Optional[Tuple[Any, ...]]:
        if not resolved.is_dir():
            return None
        pinned_sig = tuple(
            (fname, int(meta["bytes"]), str(meta["sha256"]))
            for fname, meta in sorted(PINNED_BGE_FILES.items())
        )
        candidate_names = set(cls._TRACKED_IDENTITY_FILES) | set(PINNED_BGE_FILES.keys())
        try:
            for shard in resolved.glob("model-*.safetensors"):
                candidate_names.add(shard.name)
        except OSError:
            return None
        file_stats = []
        found_any = False
        for fname in sorted(candidate_names):
            f = resolved / fname
            try:
                if f.is_file():
                    st = f.stat()
                    found_any = True
                    file_stats.append((
                        fname,
                        True,
                        int(st.st_dev),
                        int(st.st_ino),
                        int(st.st_size),
                        int(st.st_mtime_ns),
                        int(st.st_ctime_ns),
                    ))
                else:
                    file_stats.append((fname, False))
            except OSError:
                return None
        if not found_any:
            return None
        return (pinned_sig, tuple(file_stats))

    def __init__(self, model_dir: Optional[Path] = None):
        self.model_dir = Path(model_dir) if model_dir is not None else self._resolve_default_model_dir()

    @classmethod
    def verify_identity(cls, model_dir: Path) -> str:
        """Establish content-bound weight/config/tokenizer identity.

        Never claims pinned BGE_NATIVE_IDENTITY unless actual file bytes/hashes match.
        """
        resolved = model_dir.resolve()
        pre_sig = cls._artifact_status_signature(resolved)

        # Verify against pinned BGE files
        all_match = bool(PINNED_BGE_FILES)
        for fname, meta in PINNED_BGE_FILES.items():
            f = resolved / fname
            if not f.is_file():
                all_match = False
                break
            st = f.stat()
            if st.st_size != meta["bytes"]:
                all_match = False
                break
            with f.open("rb") as stream:
                h = hashlib.file_digest(stream, "sha256").hexdigest()
            if h != meta["sha256"]:
                all_match = False
                break

        if all_match:
            post_sig = cls._artifact_status_signature(resolved)
            with cls._status_identity_lock:
                if pre_sig is not None and post_sig == pre_sig:
                    cls._status_identity_cache[str(resolved)] = (post_sig, BGE_NATIVE_IDENTITY)
                    cls._status_identity_cache.move_to_end(str(resolved))
                    while len(cls._status_identity_cache) > cls._STATUS_IDENTITY_CACHE_MAX:
                        cls._status_identity_cache.popitem(last=False)
                else:
                    cls._status_identity_cache.pop(str(resolved), None)
            return BGE_NATIVE_IDENTITY

        # If bytes do not match pinned revision, compute explicit content fingerprint.
        # Identities are never cached by mtime, so same-size/same-mtime replacements are detected.
        content_hasher = hashlib.sha256()
        found_any = False
        for fname in sorted(cls._TRACKED_IDENTITY_FILES):
            f = resolved / fname
            if f.is_file():
                found_any = True
                content_hasher.update(fname.encode())
                with f.open("rb") as stream:
                    content_hasher.update(hashlib.file_digest(stream, "sha256").digest())
        for f in sorted(resolved.glob("model-*.safetensors")):
            found_any = True
            content_hasher.update(f.name.encode())
            with f.open("rb") as stream:
                content_hasher.update(hashlib.file_digest(stream, "sha256").digest())

        if found_any:
            cfg_hash = content_hasher.hexdigest()[:16]
            custom_id = f"bge-m3-custom@{cfg_hash}"
            post_sig = cls._artifact_status_signature(resolved)
            with cls._status_identity_lock:
                if pre_sig is not None and post_sig == pre_sig:
                    cls._status_identity_cache[str(resolved)] = (post_sig, custom_id)
                    cls._status_identity_cache.move_to_end(str(resolved))
                    while len(cls._status_identity_cache) > cls._STATUS_IDENTITY_CACHE_MAX:
                        cls._status_identity_cache.popitem(last=False)
                else:
                    cls._status_identity_cache.pop(str(resolved), None)
            return custom_id
        with cls._status_identity_lock:
            cls._status_identity_cache.pop(str(resolved), None)
        return "bge-m3-unknown@unverified"

    def get_identity(self) -> str:
        return self.verify_identity(self.model_dir)

    def get_status_identity(self) -> str:
        """Return verified status identity without rehashing unchanged verified artifacts."""
        try:
            resolved = self.model_dir.resolve()
        except OSError:
            return "bge-m3-unknown@unverified"
        sig = self._artifact_status_signature(resolved)
        if sig is not None:
            with self._status_identity_lock:
                cached = self._status_identity_cache.get(str(resolved))
                if cached is not None and cached[0] == sig:
                    self._status_identity_cache.move_to_end(str(resolved))
                    return cached[1]
        return self.get_identity()

    @classmethod
    def available(cls, model_dir: Optional[Path] = None) -> bool:
        if sys.platform != "darwin":
            return False
        md = Path(model_dir) if model_dir is not None else cls._resolve_default_model_dir()
        if not md.is_dir():
            return False

        # 1. Config file must exist and be valid non-empty JSON
        cfg = md / "config.json"
        if not (cfg.is_file() and cfg.stat().st_size > 0):
            return False
        try:
            cfg_data = json.loads(cfg.read_text(encoding="utf-8"))
            if not isinstance(cfg_data, dict):
                return False
        except Exception:
            return False

        # 2. Tokenizer files: requires tokenizer.json or (tokenizer_config.json with vocab)
        tok_json = md / "tokenizer.json"
        tok_cfg = md / "tokenizer_config.json"
        if not ((tok_json.is_file() and tok_json.stat().st_size > 0) or (tok_cfg.is_file() and tok_cfg.stat().st_size > 0)):
            return False

        # 3. Weights: validate complete referenced shards or single safetensors
        shard_index = md / "model.safetensors.index.json"
        single_weights = md / "model.safetensors"

        if shard_index.is_file() and shard_index.stat().st_size > 0:
            try:
                data = json.loads(shard_index.read_text(encoding="utf-8"))
                weight_map = data.get("weight_map")
                if not isinstance(weight_map, dict) or not weight_map:
                    return False
                resolved_base = md.resolve()
                shard_files = set()
                for sf in weight_map.values():
                    if not isinstance(sf, str) or not sf.strip():
                        return False
                    shard_files.add(sf)
                for sf in shard_files:
                    sf_path = (md / sf).resolve()
                    if not sf_path.is_relative_to(resolved_base):
                        return False
                    if not sf_path.is_file() or sf_path.stat().st_size == 0:
                        return False
            except (OSError, ValueError, KeyError, TypeError):
                return False
        elif single_weights.is_file() and single_weights.stat().st_size > 0:
            pass
        else:
            return False

        # 4. Runtime check on macOS: training python runtime must exist
        if sys.platform == "darwin":
            runtime = BASE_DIR / ".train-venv" / "bin" / "python"
            if not (runtime.is_file() and os.access(runtime, os.X_OK)):
                return False

        return True

    def get_embedding(self, text: str, max_length: int = 512,
                      expected_identity: Optional[str] = None) -> Optional[List[float]]:
        if not self.available(self.model_dir):
            raise RuntimeError(f"Mô hình MLX BGE chưa sẵn sàng tại {self.model_dir}")
        # Rehash actual artifact bytes on every lookup, including cache hits.
        identity = self.get_identity()
        # A cached or newly generated query must use the same basis as the
        # stored vectors selected by the caller. Reuse the existing byte hash.
        if expected_identity is not None and identity != expected_identity:
            return None
        key = (str(self.model_dir.resolve()), identity, max_length,
               hashlib.sha256(text.encode("utf-8")).digest())
        with self._query_cache_lock:
            cached = self._query_cache.get(key)
            if cached is not None:
                self._query_cache.move_to_end(key)
                return list(cached)
        results = self.get_batch_embeddings([text], max_length=max_length)
        vector = results[0] if results else None
        if vector is not None:
            # Reuse actual verified post-generation identity from batch embedding when available,
            # retaining byte rehash for custom overrides returning plain lists.
            batch_identity = getattr(results, "verified_identity", None)
            if batch_identity is not None:
                if batch_identity != identity:
                    raise RuntimeError("Mô hình embedding đã thay đổi trong khi xử lý câu hỏi.")
            else:
                if self.get_identity() != identity:
                    raise RuntimeError("Mô hình embedding đã thay đổi trong khi xử lý câu hỏi.")
            with self._query_cache_lock:
                self._query_cache[key] = tuple(vector)
                self._query_cache.move_to_end(key)
                while len(self._query_cache) > self._query_cache_limit:
                    self._query_cache.popitem(last=False)
        return vector

    @classmethod
    def _worker_command(cls) -> List[str]:
        training_python = BASE_DIR / ".train-venv" / "bin" / "python"
        return [str(training_python), "-m", "app.mlx_embedding"]

    @staticmethod
    def _run_cancellable_popen(
        cmd: List[str],
        *,
        input_text: str,
        cwd: Path,
        env: dict,
        timeout: float = 120.0,
        cancel_event: Optional[threading.Event] = None,
        pass_fds: tuple = (),
    ) -> subprocess.CompletedProcess:
        popen_kwargs: dict = {
            "cwd": cwd,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
            if pass_fds:
                popen_kwargs["pass_fds"] = pass_fds

        proc = subprocess.Popen(cmd, **popen_kwargs)
        with _ACTIVE_WORKERS_LOCK:
            _ACTIVE_WORKERS.add(proc)

        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []

        def _feed_stdin() -> None:
            try:
                if proc.stdin is not None:
                    proc.stdin.write(input_text)
                    proc.stdin.flush()
            except OSError:
                pass
            finally:
                try:
                    if proc.stdin is not None:
                        proc.stdin.close()
                except OSError:
                    pass

        def _drain_stream(stream, target: List[str]) -> None:
            try:
                if stream is not None:
                    target.append(stream.read())
            except OSError:
                pass
            finally:
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass

        t_in = threading.Thread(target=_feed_stdin, daemon=True)
        t_out = threading.Thread(target=_drain_stream, args=(proc.stdout, stdout_chunks), daemon=True)
        t_err = threading.Thread(target=_drain_stream, args=(proc.stderr, stderr_chunks), daemon=True)
        t_in.start()
        t_out.start()
        t_err.start()

        deadline = time.monotonic() + float(timeout)
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    _terminate_owned_worker(proc)
                    raise IndexingCancelledError("Đã hủy tiến trình embedding cục bộ theo yêu cầu dừng.")
                rc = proc.poll()
                if rc is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    _terminate_owned_worker(proc)
                    raise subprocess.TimeoutExpired(cmd, timeout)
                wait_slice = min(0.04, remaining)
                if cancel_event is not None:
                    cancel_event.wait(timeout=wait_slice)
                else:
                    time.sleep(wait_slice)

            t_out.join(timeout=1.0)
            t_err.join(timeout=1.0)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=rc,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
            )
        finally:
            if proc.poll() is None:
                _terminate_owned_worker(proc)
            t_in.join(timeout=0.2)
            t_out.join(timeout=0.2)
            t_err.join(timeout=0.2)
            with _ACTIVE_WORKERS_LOCK:
                _ACTIVE_WORKERS.discard(proc)

    def get_batch_embeddings(
        self,
        texts: List[str],
        max_length: int = 512,
        cancel_event: Optional[threading.Event] = None,
    ) -> List[Optional[List[float]]]:
        if not texts:
            return []
        if cancel_event is not None and cancel_event.is_set():
            raise IndexingCancelledError("Đã hủy nhúng vector cục bộ theo yêu cầu dừng.")
        if not self.available(self.model_dir):
            raise RuntimeError(f"Mô hình MLX BGE chưa sẵn sàng tại {self.model_dir}")

        cmd = self._worker_command()
        request = {
            "model_path": str(self.model_dir),
            "texts": texts,
            "max_length": max_length,
        }
        payload_text = json.dumps(request, ensure_ascii=False)
        worker_env = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_DATASETS_OFFLINE": "1",
        }

        acq_ctx = (
            gpu_coordinator.acquire_for_inference(cancel_event=cancel_event)
            if cancel_event is not None
            else gpu_coordinator.acquire_for_inference()
        )
        with acq_ctx as lock_fds:
            if cancel_event is not None and cancel_event.is_set():
                raise IndexingCancelledError("Đã hủy nhúng vector cục bộ theo yêu cầu dừng.")
            raw_fds = getattr(lock_fds, "fds", lock_fds)
            if sys.platform != "win32" and raw_fds is not None:
                if isinstance(raw_fds, int) and not isinstance(raw_fds, bool):
                    pass_fds = (raw_fds,)
                elif isinstance(raw_fds, (tuple, list)):
                    pass_fds = tuple(fd for fd in raw_fds if isinstance(fd, int) and not isinstance(fd, bool))
                else:
                    pass_fds = ()
            else:
                pass_fds = ()
            completed = self._run_cancellable_popen(
                cmd,
                input_text=payload_text,
                cwd=BASE_DIR,
                env=worker_env,
                timeout=120.0,
                cancel_event=cancel_event,
                pass_fds=pass_fds,
            )

        if completed.returncode != 0:
            raise RuntimeError(f"Lỗi chạy native MLX embedding: {completed.stderr[-1000:]}")

        data = json.loads(completed.stdout)
        vectors = data.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RuntimeError(f"Native MLX embedding worker trả về số lượng vector ({len(vectors) if isinstance(vectors, list) else None}) không khớp số lượng yêu cầu ({len(texts)})")

        expected_identity = self.get_identity()
        reported_identity = data.get("model_identity")
        if reported_identity != expected_identity:
            raise RuntimeError(f"Worker báo cáo identity ({reported_identity}) không khớp identity đã xác minh ({expected_identity})")

        for idx, vec in enumerate(vectors):
            if not isinstance(vec, list) or len(vec) != 1024:
                raise RuntimeError(f"Vector {idx} có kích thước {len(vec) if isinstance(vec, list) else None}, yêu cầu đúng 1024 chiều")
            if not all(math.isfinite(x) for x in vec):
                raise RuntimeError(f"Vector {idx} chứa giá trị không hữu hạn (NaN hoặc Inf)")
            norm = math.sqrt(sum(x * x for x in vec))
            if norm <= 0 or abs(norm - 1.0) > 1e-3:
                raise RuntimeError(f"Vector {idx} chưa được chuẩn hóa L2 hợp lệ (norm = {norm})")

        return _VerifiedBatchResult(vectors, verified_identity=expected_identity)
