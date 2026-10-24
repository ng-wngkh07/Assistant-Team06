"""Direct native MLX vision inference client."""

import json
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from PIL import Image, ImageOps

from app.config import BASE_DIR
from app.gpu_lock import gpu_coordinator
from app.model_resolver import MLX_9B_DIR, resolve_mlx_model_dir

MAX_INFERENCE_EDGE = 1024


class NativeVisionClient:
    """Direct local MLX vision client using Qwen3.5-9B-4bit via mlx-vlm."""

    def __init__(self, model_dir: Optional[Path] = None):
        self.model_dir = Path(model_dir or resolve_mlx_model_dir("qwen3.5-9b-4bit"))

    def get_identity(self) -> str:
        """Bind cached recognition to actual local weights and processor assets.

        Read file bytes on each request; preserved size/mtime is not proof of identity.
        The prefix denotes a content fingerprint, never an inferred Hub revision.
        """
        from app.trained_client import TrainedModelClient
        if not TrainedModelClient._verify_weights(self.model_dir):
            raise RuntimeError("Incomplete local vision artifact")
        files = set(self.model_dir.glob("*.safetensors"))
        names = ("config.json", "model.safetensors.index.json", "tokenizer.json",
                 "tokenizer_config.json", "special_tokens_map.json", "vocab.json",
                 "merges.txt", "chat_template.jinja", "processor_config.json",
                 "preprocessor_config.json", "video_preprocessor_config.json")
        files.update(self.model_dir / name for name in names)
        digest = hashlib.sha256(b"native-vision-artifact-v1\0")
        for path in sorted(files):
            digest.update(path.name.encode("utf-8") + b"\0")
            if path.is_file():
                with path.open("rb") as stream:
                    digest.update(hashlib.file_digest(stream, "sha256").digest())
            else:
                digest.update(b"missing")
        return "mlx-vision-sha256:" + digest.hexdigest()

    @classmethod
    def available(cls, model_dir: Optional[Path] = None) -> bool:
        md = Path(model_dir or MLX_9B_DIR)
        if not (md / "processor_config.json").is_file() or not (md / "preprocessor_config.json").is_file():
            return False
        if sys.platform == "darwin":
            runtime = BASE_DIR / ".train-venv" / "bin" / "python"
            if not (runtime.is_file() and os.access(runtime, os.X_OK)):
                return False
        from app.trained_client import TrainedModelClient
        return TrainedModelClient._verify_weights(md)

    def generate_from_image(
        self,
        image_bytes: bytes,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        timeout_seconds: int = 420,
    ) -> Dict[str, Any]:
        """Run bounded vision generation using MLX VLM. Original image remains untouched."""
        if not self.available(self.model_dir):
            raise RuntimeError(f"Mô hình MLX Vision chưa sẵn sàng tại {self.model_dir}")

        training_python = BASE_DIR / ".train-venv" / "bin" / "python"

        with tempfile.TemporaryDirectory(prefix="mlx_vision_") as tmp_dir:
            tmp_image_path = Path(tmp_dir) / "input.png"
            import io
            with Image.open(io.BytesIO(image_bytes)) as original:
                prepared = ImageOps.exif_transpose(original).convert("RGB")
                prepared.thumbnail((MAX_INFERENCE_EDGE, MAX_INFERENCE_EDGE), Image.Resampling.LANCZOS)
                prepared.save(tmp_image_path, "PNG")

            request = {
                "model_path": str(self.model_dir),
                "image_path": str(tmp_image_path),
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            with gpu_coordinator.acquire_for_inference() as lock_fds:
                kwargs_run = {
                    "cwd": BASE_DIR,
                    "input": json.dumps(request, ensure_ascii=False),
                    "capture_output": True,
                    "text": True,
                    "timeout": timeout_seconds,
                    "check": False,
                }
                if sys.platform != "win32" and lock_fds and isinstance(lock_fds, (tuple, list)):
                    kwargs_run["pass_fds"] = tuple(fd for fd in lock_fds if isinstance(fd, int))

                completed = subprocess.run(
                    [str(training_python), "-m", "app.mlx_vision"],
                    **kwargs_run,
                )

        if completed.returncode != 0:
            raise RuntimeError(f"Lỗi chạy native MLX vision: {completed.stderr[-1000:]}")

        data = json.loads(completed.stdout)
        if data.get("finish_reason") == "length" or data.get("is_truncated"):
            raise ValueError("Phản hồi của mô hình chạm giới hạn độ dài token; kết quả bị cắt cụt.")

        return data
