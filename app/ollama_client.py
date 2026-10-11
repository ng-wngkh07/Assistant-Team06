import json
import logging
from typing import List, Dict, Any, Optional, Generator
import requests

from app.config import OLLAMA_BASE_URL, DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL

logger = logging.getLogger(__name__)

class OllamaClient:
    def __init__(self, base_url: str = OLLAMA_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._available_models: Optional[List[str]] = None
        self._unavailable_models = set()

    def check_health(self) -> bool:
        """Check if Ollama server is running and accessible."""
        try:
            res = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return res.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """List all models available locally in Ollama."""
        try:
            res = requests.get(f"{self.base_url}/api/tags", timeout=3)
            if res.status_code == 200:
                data = res.json()
                models = data.get("models", [])
                self._available_models = [m.get("name") for m in models]
                return models
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
            res = requests.get(f"{self.base_url}/api/ps", timeout=3)
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
                    check = requests.get(f"{self.base_url}/api/ps", timeout=3)
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
        if not text.strip() or model in self._unavailable_models:
            return None

        # Try newer /api/embed endpoint first
        try:
            res = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": text},
                timeout=30
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
            res = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=30
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
        if not texts:
            return []
        if model in self._unavailable_models:
            return [None] * len(texts)
        try:
            response = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": model, "input": texts, "truncate": False},
                timeout=180,
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
        self.last_done_reason = None
        payload = {
            "model": chosen_model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            }
        }
        if system_prompt:
            payload["messages"] = [{"role": "system", "content": system_prompt}] + messages

        try:
            res = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                stream=True,
                timeout=120
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
        payload = {
            "model": chosen_model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            }
        }
        if system_prompt:
            payload["messages"] = [{"role": "system", "content": system_prompt}] + messages

        try:
            res = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=120
            )
            if res.status_code == 200:
                return res.json()
            return {"message": {"content": ""}, "error": res.text}
        except Exception as e:
            logger.warning("Lỗi trong chat_sync: %s", e)
            return {"message": {"content": ""}, "error": str(e)}
