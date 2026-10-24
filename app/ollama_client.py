import json
import logging
from typing import List, Dict, Any, Optional, Generator
from urllib.parse import urlsplit
import requests

from app.config import OLLAMA_BASE_URL, DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL

logger = logging.getLogger(__name__)


def validate_local_endpoint(endpoint: str) -> str:
    """Ensure endpoint strictly targets local loopback without proxy or cloud traversal."""
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError("Mã định danh endpoint không hợp lệ hoặc rỗng.")
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme != "http":
        raise ValueError(f"Chỉ cho phép giao thức http cục bộ, nhận: {parsed.scheme!r}")
    if parsed.username or parsed.password:
        raise ValueError("Endpoint không được chứa thông tin xác thực người dùng.")
    if parsed.path and parsed.path not in ("", "/"):
        raise ValueError(f"Endpoint không được chứa đường dẫn con (path), nhận: {parsed.path!r}")
    if parsed.query or parsed.fragment:
        raise ValueError("Endpoint không được chứa query hoặc fragment.")
    host = parsed.hostname
    if not host:
        raise ValueError("Endpoint thiếu hostname.")
    host_lower = host.lower()
    if host_lower.startswith("[") and host_lower.endswith("]"):
        host_lower = host_lower[1:-1]
    if host_lower not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"Chỉ cho phép kết nối loopback cục bộ (127.0.0.1, localhost, [::1]), nhận: {host!r}")
    return endpoint.rstrip("/")


class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE_URL):
        self.base_url = validate_local_endpoint(base_url)
        self.session = requests.Session()
        self.session.trust_env = False
        self._available_models: Optional[List[str]] = None
        self._unavailable_models = set()
        self._verified_local_models = set()
        self.last_usage: Dict[str, Any] = {}

    def _verify_model_locality(self, model_name: str) -> None:
        """Inspect model metadata via /api/show to ensure it is not a remote or cloud model."""
        if not model_name:
            return
        if not hasattr(self, "_verified_local_models"):
            self._verified_local_models = set()
        if model_name in self._verified_local_models:
            return
        if ":cloud" in model_name or model_name.endswith(":cloud"):
            raise ValueError(f"Không cho phép sử dụng mô hình cloud: {model_name}")

        try:
            resp = self.session.post(
                f"{self.base_url}/api/show",
                json={"name": model_name},
                timeout=10,
                allow_redirects=False,
            )
        except Exception as e:
            if isinstance(e, (ValueError, RuntimeError)):
                raise
            raise RuntimeError(f"Không thể kết nối để kiểm tra metadata cho '{model_name}': {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(
                f"Không thể xác minh tính cục bộ của mô hình '{model_name}': "
                f"máy chủ trả về mã trạng thái {resp.status_code}"
            )

        try:
            data = resp.json()
        except Exception as e:
            raise ValueError(f"Metadata /api/show của mô hình '{model_name}' không phải JSON hợp lệ") from e

        if not isinstance(data, dict) or not data:
            raise ValueError(f"Không có metadata /api/show hợp lệ cho mô hình '{model_name}'")

        if (
            data.get("remote_host")
            or data.get("remote_model")
            or "remote" in str(data.get("remote_host", "")).lower()
            or "cloud" in str(data.get("remote_model", "")).lower()
            or "cloud" in str(data.get("details", {}).get("family", "")).lower()
        ):
            raise ValueError(
                f"Mô hình '{model_name}' được định tuyến tới máy chủ từ xa/cloud. Chỉ cho phép mô hình cục bộ."
            )
        self._verified_local_models.add(model_name)

    def check_health(self) -> bool:
        """Check if Ollama server is running and accessible."""
        try:
            res = self.session.get(f"{self.base_url}/api/tags", timeout=2, allow_redirects=False)
            return res.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """List all models available locally in Ollama."""
        try:
            res = self.session.get(f"{self.base_url}/api/tags", timeout=3, allow_redirects=False)
            if res.status_code == 200:
                data = res.json()
                models = data.get("models", [])
                filtered = []
                for m in models:
                    name = str(m.get("name", ""))
                    details = m.get("details", {}) or {}
                    family = str(details.get("family", "")).lower()
                    families = [str(f).lower() for f in (details.get("families", []) or [])]
                    if ":cloud" in name or name.endswith(":cloud") or family in ("cloud", "remote") or any(f in ("cloud", "remote") for f in families):
                        continue
                    filtered.append(m)
                self._available_models = [m.get("name") for m in filtered]
                return filtered
            return []
        except Exception as e:
            logger.warning(f"Không thể lấy danh sách mô hình từ Ollama: {e}")
            return []

    def unload_resident_models(self, timeout: float = 8.0) -> List[str]:
        """Evict any models currently loaded in Ollama VRAM/RAM to prevent GPU memory pressure.

        Verifies HTTP success for unload requests and polls /api/ps until models list is confirmed empty.
        Raises RuntimeError if Ollama models cannot be verified unloaded.
        """
        import time

        try:
            res = requests.get(f"{self.base_url}/api/ps", timeout=3, allow_redirects=False)
            if res.status_code != 200:
                raise RuntimeError(f"Không thể kiểm tra trạng thái Ollama /api/ps (mã HTTP {res.status_code}): {res.text[:100]}")
            try:
                data = res.json()
            except Exception as json_err:
                raise RuntimeError(f"Phản hồi /api/ps từ Ollama không phải JSON hợp lệ: {json_err}") from json_err

            if not isinstance(data, dict) or not isinstance(data.get("models"), list):
                raise RuntimeError("Phản hồi /api/ps từ Ollama có cấu trúc không hợp lệ")

            loaded_models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
            if not loaded_models:
                return []

            unloaded = []
            for model_name in loaded_models:
                resp = requests.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model_name, "keep_alive": 0},
                    timeout=5,
                    allow_redirects=False,
                )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Yêu cầu giải phóng mô hình '{model_name}' thất bại (mã {resp.status_code}): {resp.text}"
                    )
                unloaded.append(model_name)

            # Poll /api/ps until verified empty or timeout
            start_poll = time.time()
            verified_empty = False
            last_reason = "Chưa nhận được phản hồi xác nhận"

            while time.time() - start_poll < timeout:
                try:
                    check = requests.get(f"{self.base_url}/api/ps", timeout=3, allow_redirects=False)
                    if check.status_code == 200:
                        try:
                            check_data = check.json()
                            if isinstance(check_data, dict) and isinstance(check_data.get("models"), list):
                                remaining = [
                                    m.get("name") for m in check_data.get("models", [])
                                    if isinstance(m, dict) and m.get("name")
                                ]
                                if not remaining:
                                    verified_empty = True
                                    break
                                last_reason = f"Vẫn còn mô hình cư trú trong GPU: {remaining}"
                            else:
                                last_reason = "Phản hồi /api/ps có cấu trúc không hợp lệ"
                        except Exception as json_err:
                            last_reason = f"Phản hồi /api/ps không parse được JSON: {json_err}"
                    else:
                        last_reason = f"Mã HTTP {check.status_code} từ /api/ps: {check.text[:80]}"
                except requests.RequestException as req_err:
                    last_reason = f"Lỗi yêu cầu /api/ps: {req_err}"

                time.sleep(0.3)

            if not verified_empty:
                raise RuntimeError(f"Không thể xác nhận GPU rỗng từ Ollama sau {timeout}s ({last_reason})")

            return unloaded

        except requests.exceptions.ConnectionError:
            # If Ollama is not running, no model resides in GPU memory
            return []
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Lỗi khi giải phóng mô hình Ollama khỏi GPU: {e}") from e

    def get_available_model_names(self) -> List[str]:
        """Get list of available model names with caching."""
        if self._available_models is None:
            self.list_models()
        return self._available_models or []

    def find_best_chat_model(self, preferred: Optional[str] = None) -> str:
        """Find best available chat model locally."""
        avail = self.get_available_model_names()
        if preferred:
            if preferred in avail:
                return preferred
            if ":" not in preferred and f"{preferred}:latest" in avail:
                return f"{preferred}:latest"
            return preferred
        # Look for common models
        for cand in ["qwen2.5:7b", "qwen2.5:3b", "qwen3:4b", "llama3.2:3b", "mistral", "gemma2"]:
            for m in avail:
                if cand in m:
                    return m
        if avail:
            return avail[0]
        return DEFAULT_CHAT_MODEL

    def find_best_embed_model(self, preferred: Optional[str] = None) -> Optional[str]:
        """Find best available embedding model locally."""
        avail = self.get_available_model_names()
        if preferred:
            if preferred in avail:
                return preferred
            if ":" not in preferred and f"{preferred}:latest" in avail:
                return f"{preferred}:latest"
            return None
        for cand in ["bge-m3", "nomic-embed-text", "all-minilm", "mxbai-embed-large"]:
            for m in avail:
                if cand in m:
                    return m
        return None

    def get_embedding(self, text: str, model: str = DEFAULT_EMBED_MODEL) -> Optional[List[float]]:
        """
        Generate embedding vector for a given text.
        """
        self._verify_model_locality(model)
        if not text.strip() or model in self._unavailable_models:
            return None

        # Try newer /api/embed endpoint first
        try:
            res = self.session.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": text},
                timeout=30,
                allow_redirects=False,
            )
            if res.status_code == 200:
                data = res.json()
                embeddings = data.get("embeddings", [])
                if embeddings and len(embeddings) > 0:
                    return embeddings[0]
            elif res.status_code == 404:
                self._unavailable_models.add(model)
                return None
        except Exception:
            pass

        # Fallback to classic /api/embeddings
        try:
            res = self.session.post(
                f"{self.base_url}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=30,
                allow_redirects=False,
            )
            if res.status_code == 200:
                data = res.json()
                return data.get("embedding")
            elif res.status_code == 404:
                self._unavailable_models.add(model)
                return None
        except Exception as e:
            self._unavailable_models.add(model)
            logger.debug(f"Không thể tạo embedding qua Ollama ({model}): {e}")
            
        return None

    def get_batch_embeddings(self, texts: List[str], model: str = DEFAULT_EMBED_MODEL) -> List[Optional[List[float]]]:
        """Embed a batch with one local Ollama request, preserving input order."""
        self._verify_model_locality(model)
        if not texts:
            return []
        if model in self._unavailable_models:
            return [None] * len(texts)
        try:
            response = self.session.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": texts, "truncate": False},
                timeout=180,
                allow_redirects=False,
            )
            if response.status_code == 404:
                self._unavailable_models.add(model)
            response.raise_for_status()
            vectors = response.json().get("embeddings", [])
            if len(vectors) == len(texts):
                return vectors
            logger.warning("Ollama returned %s vectors for %s texts", len(vectors), len(texts))
        except requests.RequestException as exc:
            logger.warning("Batch embedding failed: %s", exc)
        return [None] * len(texts)

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
        num_predict: int = 640,
        num_ctx: int = 8192,
        **kwargs
    ) -> Generator[str, None, None]:
        """
        Stream chat response from Ollama.
        """
        chosen_model = self.find_best_chat_model(model)
        self._verify_model_locality(chosen_model)
        self.last_done_reason = None
        self.last_usage = {}
        payload = {
            "model": chosen_model,
            "messages": messages,
            "stream": True,
            "think": kwargs.get("think", False),
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            }
        }
        if kwargs.get("seed") is not None:
            payload["options"]["seed"] = kwargs["seed"]
        if system_prompt:
            payload["messages"] = [{"role": "system", "content": system_prompt}] + messages

        try:
            res = self.session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                stream=True,
                timeout=120,
                allow_redirects=False,
            )
            if res.status_code != 200:
                yield f"Lỗi từ Ollama (Mã {res.status_code}): {res.text}"
                return

            for line in res.iter_lines():
                if line:
                    chunk = json.loads(line.decode("utf-8"))
                    message = chunk.get("message", {})
                    content = message.get("content", "")
                    if content:
                        yield content
                    if chunk.get("done", False):
                        self.last_done_reason = chunk.get("done_reason")
                        self.last_usage = {
                            key: chunk[key] for key in (
                                "total_duration", "load_duration", "prompt_eval_count",
                                "prompt_eval_duration", "eval_count", "eval_duration",
                            ) if key in chunk
                        }
                        break
        except requests.exceptions.ConnectionError:
            yield "❌ Không thể kết nối tới máy chủ Ollama tại http://localhost:11434. Vui lòng đảm bảo ứng dụng Ollama đã được khởi chạy."
        except Exception as e:
            yield f"❌ Lỗi trong quá trình tạo câu trả lời: {str(e)}"

    def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = DEFAULT_CHAT_MODEL,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
        num_predict: int = 640,
        num_ctx: int = 8192,
        **kwargs
    ) -> str:
        """
        Generate complete chat response synchronously.
        """
        full_text = []
        for chunk in self.chat_stream(
            messages,
            model=model,
            temperature=temperature,
            system_prompt=system_prompt,
            num_predict=num_predict,
            num_ctx=num_ctx,
            **kwargs
        ):
            full_text.append(chunk)
        return "".join(full_text)

    def chat_sync(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        system_prompt: Optional[str] = None,
        num_predict: int = 640,
        num_ctx: int = 8192,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate complete chat response synchronously via Ollama /api/chat with stream=False."""
        chosen_model = self.find_best_chat_model(model)
        self._verify_model_locality(chosen_model)
        payload = {
            "model": chosen_model,
            "messages": messages,
            "stream": False,
            "think": kwargs.get("think", False),
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            }
        }
        if kwargs.get("seed") is not None:
            payload["options"]["seed"] = kwargs["seed"]
        if system_prompt:
            payload["messages"] = [{"role": "system", "content": system_prompt}] + messages

        try:
            res = self.session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=120,
                allow_redirects=False,
            )
            if res.status_code == 200:
                result = res.json()
                if isinstance(result.get("message"), dict):
                    result["message"].pop("thinking", None)
                return result
            return {"message": {"content": ""}, "error": res.text}
        except Exception as e:
            logger.warning("Lỗi trong chat_sync: %s", e)
            return {"message": {"content": ""}, "error": str(e)}
