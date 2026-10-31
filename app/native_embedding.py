"""Direct native MLX dense embedding client."""

import hashlib
import json
import math
import os
import subprocess
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, List, Optional

from app.config import BASE_DIR
from app.gpu_lock import gpu_coordinator
from app.model_resolver import MLX_BGE_DIR

BGE_NATIVE_IDENTITY = "bge-m3-mlx-fp16@a37eddded9a6a1273a87fb8b0da0d1cdbd98aeec"

# Pinned metadata for bge-m3-mlx-fp16 artifact verification
PINNED_BGE_FILES = {
    "config.json": {"bytes": 715, "sha256": "2f0b6a5c00cf8653f67d93dd611b119f57e9199f876e137de63090dab4dfb426"},
    "tokenizer.json": {"bytes": 17098085, "sha256": "5df1f55d60c9705a501ab9a75550728625740741fe4be308dac4806c16b7d51d"},
    "tokenizer_config.json": {"bytes": 379, "sha256": "c2ef99124628ae6f79a847ad67e5d3f5b016d0387de90c9fbaf17c45a98933af"},
    "special_tokens_map.json": {"bytes": 964, "sha256": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835"},
    "model.safetensors": {"bytes": 1135554209, "sha256": "e8028acb2e77e0010d35ef75832d823edb5851c95682deef2bde86a45bd4441d"},
}


class NativeEmbeddingClient:
    """Direct local MLX embedding client using pinned BGE-M3 MLX artifact."""

    # Query vectors only: bounded process memory, never source chunks or answers.
    _query_cache = OrderedDict()
    _query_cache_lock = threading.Lock()
    _query_cache_limit = 128

    def __init__(self, model_dir: Optional[Path] = None):
        self.model_dir = Path(model_dir or MLX_BGE_DIR)

    @classmethod
    def verify_identity(cls, model_dir: Path) -> str:
        """Establish content-bound weight/config/tokenizer identity.

        Never claims pinned BGE_NATIVE_IDENTITY unless actual file bytes/hashes match.
        """
        resolved = model_dir.resolve()

        # Verify against pinned BGE files
        all_match = True
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
            return BGE_NATIVE_IDENTITY

        # If bytes do not match pinned revision, compute explicit content fingerprint.
        # Identities are never cached by mtime, so same-size/same-mtime replacements are detected.
        tracked_fnames = (
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "sentence_bert_config.json",
            "modules.json",
            "model.safetensors.index.json",
            "model.safetensors",
        )
        content_hasher = hashlib.sha256()
        found_any = False
        for fname in sorted(tracked_fnames):
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
            return f"bge-m3-custom@{cfg_hash}"
        return "bge-m3-unknown@unverified"

    def get_identity(self) -> str:
        return self.verify_identity(self.model_dir)

    @classmethod
    def available(cls, model_dir: Optional[Path] = None) -> bool:
        if sys.platform != "darwin":
            return False
        md = Path(model_dir or MLX_BGE_DIR)
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
            # An artifact replaced during generation cannot populate the old key.
            if self.get_identity() != identity:
                raise RuntimeError("Mô hình embedding đã thay đổi trong khi xử lý câu hỏi.")
            with self._query_cache_lock:
                self._query_cache[key] = tuple(vector)
                self._query_cache.move_to_end(key)
                while len(self._query_cache) > self._query_cache_limit:
                    self._query_cache.popitem(last=False)
        return vector

    def get_batch_embeddings(
        self, texts: List[str], max_length: int = 512
    ) -> List[Optional[List[float]]]:
        if not texts:
            return []
        if not self.available(self.model_dir):
            raise RuntimeError(f"Mô hình MLX BGE chưa sẵn sàng tại {self.model_dir}")

        training_python = BASE_DIR / ".train-venv" / "bin" / "python"
        request = {
            "model_path": str(self.model_dir),
            "texts": texts,
            "max_length": max_length,
        }

        with gpu_coordinator.acquire_for_inference() as lock_fds:
            kwargs_run = {
                "cwd": BASE_DIR,
                "input": json.dumps(request, ensure_ascii=False),
                "capture_output": True,
                "text": True,
                "timeout": 120,
                "check": False,
                "env": {
                    **os.environ,
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "HF_DATASETS_OFFLINE": "1",
                },
            }
            if sys.platform != "win32" and lock_fds and isinstance(lock_fds, (tuple, list)):
                kwargs_run["pass_fds"] = tuple(fd for fd in lock_fds if isinstance(fd, int))

            completed = subprocess.run(
                [str(training_python), "-m", "app.mlx_embedding"],
                **kwargs_run,
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

        return vectors
