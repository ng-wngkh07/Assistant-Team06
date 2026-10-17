"""Standardized AI Service Interface contract, data types, errors, adapters, and mock (Issue #24).

See `docs/ai-interface.md` for full architectural specification.
No new external LLM provider is called in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Iterator, Literal, Mapping, Optional, Protocol, Sequence, runtime_checkable

FinishReason = Literal["stop", "length", "fallback", "error"]
RoleType = Literal["system", "user", "assistant"]

VALID_ROLES: frozenset[str] = frozenset({"system", "user", "assistant"})
VALID_FINISH_REASONS: frozenset[str] = frozenset({"stop", "length", "fallback", "error"})


class AIServiceError(Exception):
    """Base exception for all AI service contract errors."""


class AIInvalidRequestError(AIServiceError):
    """Raised when an AIRequest or GenerationConfig violates input validation rules."""


class AIConnectionError(AIServiceError):
    """Raised when the AI runtime is unreachable or offline."""


class AITimeoutError(AIServiceError):
    """Raised when an AI generation request exceeds its configured timeout."""


class AIProviderError(AIServiceError):
    """Raised when the underlying AI runtime fails internally or returns malformed output."""


@dataclass(frozen=True)
class ChatMessage:
    """Single message turn in an AI conversation request."""

    role: RoleType
    content: str

    def validate(self) -> None:
        if self.role not in VALID_ROLES:
            raise AIInvalidRequestError(f"Invalid message role: {self.role!r}")
        if not isinstance(self.content, str):
            raise AIInvalidRequestError("Message content must be a string.")
        if "\x00" in self.content or any(0xD800 <= ord(ch) <= 0xDFFF for ch in self.content):
            raise AIInvalidRequestError("Message content contains invalid null or surrogate characters.")

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class GenerationConfig:
    """Provider-agnostic generation hyperparameters."""

    max_tokens: int = 512
    temperature: float = 0.2
    context_window: int = 4096
    timeout_seconds: float = 30.0
    seed: Optional[int] = None

    def validate(self) -> None:
        if not isinstance(self.max_tokens, int) or isinstance(self.max_tokens, bool) or self.max_tokens <= 0:
            raise AIInvalidRequestError("max_tokens must be a positive integer.")
        if not isinstance(self.temperature, (int, float)) or isinstance(self.temperature, bool):
            raise AIInvalidRequestError("temperature must be a number.")
        if not (0.0 <= float(self.temperature) <= 2.0):
            raise AIInvalidRequestError("temperature must be between 0.0 and 2.0.")
        if not isinstance(self.context_window, int) or isinstance(self.context_window, bool) or self.context_window < 256:
            raise AIInvalidRequestError("context_window must be an integer >= 256.")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or float(self.timeout_seconds) <= 0.0
        ):
            raise AIInvalidRequestError("timeout_seconds must be > 0.")
        if self.seed is not None and (not isinstance(self.seed, int) or isinstance(self.seed, bool)):
            raise AIInvalidRequestError("seed must be an integer when provided.")


@dataclass(frozen=True)
class AIRequest:
    """Standardized input payload for AI text generation."""

    messages: Sequence[ChatMessage | Mapping[str, Any]]
    system_prompt: Optional[str] = None
    config: GenerationConfig = field(default_factory=GenerationConfig)
    model: Optional[str] = None

    def normalized_messages(self) -> list[ChatMessage]:
        if not self.messages:
            raise AIInvalidRequestError("messages must not be empty.")
        normalized: list[ChatMessage] = []
        for item in self.messages:
            if isinstance(item, ChatMessage):
                msg = item
            elif isinstance(item, Mapping):
                role = item.get("role")
                content = item.get("content")
                msg = ChatMessage(role=role, content=content)  # type: ignore[arg-type]
            else:
                raise AIInvalidRequestError(f"Unsupported message type: {type(item)!r}")
            msg.validate()
            normalized.append(msg)
        if all(not m.content.strip() for m in normalized):
            raise AIInvalidRequestError("At least one message must contain non-whitespace content.")
        return normalized

    def validate(self) -> list[ChatMessage]:
        if self.system_prompt is not None and not isinstance(self.system_prompt, str):
            raise AIInvalidRequestError("system_prompt must be a string when provided.")
        if not isinstance(self.config, GenerationConfig):
            raise AIInvalidRequestError("config must be a GenerationConfig instance.")
        self.config.validate()
        return self.normalized_messages()


@dataclass(frozen=True)
class AIResponse:
    """Standardized synchronous output from an AI text generation service."""

    content: str
    finish_reason: FinishReason = "stop"
    model: str = "unknown"
    provider: str = "unknown"
    latency_ms: Optional[float] = None

    @property
    def is_truncated(self) -> bool:
        return self.finish_reason == "length"


@dataclass(frozen=True)
class AIStreamChunk:
    """Single chunk emitted by a streaming AI text generation call."""

    delta: str
    done: bool = False
    finish_reason: Optional[FinishReason] = None


@runtime_checkable
class AIServiceInterface(Protocol):
    """Protocol that all current and future LLM text generation clients must satisfy."""

    def is_available(self) -> bool:
        """Return True if the underlying LLM runtime is ready to accept requests."""
        ...

    def generate(self, request: AIRequest) -> AIResponse:
        """Generate a complete response for the given AIRequest."""
        ...

    def generate_stream(self, request: AIRequest) -> Iterator[AIStreamChunk]:
        """Stream response chunks for the given AIRequest."""
        ...


def _normalize_finish_reason(raw_reason: Optional[str]) -> FinishReason:
    if not raw_reason:
        return "stop"
    lowered = str(raw_reason).strip().lower()
    if lowered in {"length", "max_tokens"}:
        return "length"
    if lowered in {"fallback"}:
        return "fallback"
    if lowered in {"error"}:
        return "error"
    return "stop"


def _raise_if_inband_error(text: str) -> None:
    stripped = text.strip()
    if stripped.startswith("❌") or stripped.startswith("Lỗi từ Ollama"):
        lower = stripped.lower()
        if "không kết nối được" in lower or "connection" in lower:
            raise AIConnectionError(stripped)
        if "quá thời gian" in lower or "timeout" in lower:
            raise AITimeoutError(stripped)
        raise AIProviderError(stripped)


class LegacyClientAdapter:
    """Adapter wrapping existing `OllamaClient` or `TrainedModelClient` into `AIServiceInterface`."""

    def __init__(self, client: Any, *, provider_name: str = "legacy", default_model: str = "default") -> None:
        self._client = client
        self._provider_name = provider_name
        self._default_model = default_model

    def is_available(self) -> bool:
        if hasattr(self._client, "available") and callable(self._client.available):
            return bool(self._client.available())
        if hasattr(self._client, "check_health") and callable(self._client.check_health):
            return bool(self._client.check_health())
        return True

    def generate(self, request: AIRequest) -> AIResponse:
        messages = [m.to_dict() for m in request.validate()]
        if not self.is_available():
            raise AIConnectionError(f"Provider '{self._provider_name}' is not available.")

        start = time.perf_counter()
        chunks: list[str] = []
        finish_reason: FinishReason = "stop"
        for chunk in self.generate_stream(request):
            if chunk.delta:
                chunks.append(chunk.delta)
            if chunk.done and chunk.finish_reason:
                finish_reason = chunk.finish_reason

        content = "".join(chunks)
        _raise_if_inband_error(content)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return AIResponse(
            content=content,
            finish_reason=finish_reason,
            model=request.model or self._default_model,
            provider=self._provider_name,
            latency_ms=round(elapsed_ms, 2),
        )

    def generate_stream(self, request: AIRequest) -> Iterator[AIStreamChunk]:
        messages = [m.to_dict() for m in request.validate()]
        if not self.is_available():
            raise AIConnectionError(f"Provider '{self._provider_name}' is not available.")

        kwargs: dict[str, Any] = {
            "messages": messages,
            "system_prompt": request.system_prompt,
            "temperature": request.config.temperature,
            "num_predict": request.config.max_tokens,
            "max_tokens": request.config.max_tokens,
            "num_ctx": request.config.context_window,
        }
        if request.model is not None:
            kwargs["model"] = request.model
        if request.config.seed is not None:
            kwargs["seed"] = request.config.seed

        try:
            stream = self._client.chat_stream(**kwargs)
            first_piece = True
            for token in stream:
                token_str = str(token)
                if first_piece:
                    _raise_if_inband_error(token_str)
                    first_piece = False
                yield AIStreamChunk(delta=token_str, done=False)
        except AIServiceError:
            raise
        except TimeoutError as exc:
            raise AITimeoutError(str(exc)) from exc
        except ConnectionError as exc:
            raise AIConnectionError(str(exc)) from exc
        except Exception as exc:
            raise AIProviderError(f"Provider '{self._provider_name}' stream failed: {exc}") from exc

        raw_reason = getattr(self._client, "last_done_reason", "stop")
        yield AIStreamChunk(delta="", done=True, finish_reason=_normalize_finish_reason(raw_reason))


class MockAIService:
    """Deterministic in-memory implementation of `AIServiceInterface` for unit tests."""

    def __init__(
        self,
        response_text: str = "Mock AI response.",
        *,
        finish_reason: FinishReason = "stop",
        model: str = "mock-llm",
        available: bool = True,
        error: Optional[AIServiceError] = None,
    ) -> None:
        self.response_text = response_text
        self.finish_reason: FinishReason = finish_reason
        self.model = model
        self.available = available
        self.error = error
        self.requests: list[AIRequest] = []

    def is_available(self) -> bool:
        return self.available

    def generate(self, request: AIRequest) -> AIResponse:
        request.validate()
        self.requests.append(request)
        if not self.available:
            raise AIConnectionError("MockAIService is configured as unavailable.")
        if self.error is not None:
            raise self.error
        return AIResponse(
            content=self.response_text,
            finish_reason=self.finish_reason,
            model=request.model or self.model,
            provider="mock",
            latency_ms=0.5,
        )

    def generate_stream(self, request: AIRequest) -> Iterator[AIStreamChunk]:
        resp = self.generate(request)
        words = resp.content.split(" ")
        for idx, word in enumerate(words):
            piece = word if idx == len(words) - 1 else f"{word} "
            yield AIStreamChunk(delta=piece, done=False)
        yield AIStreamChunk(delta="", done=True, finish_reason=resp.finish_reason)


def generate_with_fallback(
    request: AIRequest,
    primary: AIServiceInterface,
    *,
    secondary: Optional[AIServiceInterface] = None,
    fallback_content: str = "Hệ thống mô hình ngôn ngữ hiện chưa sẵn sàng. Vui lòng thử lại sau.",
) -> AIResponse:
    """Execute an AIRequest through `primary` (and optional `secondary`), falling back safely on runtime errors.

    Note: `AIInvalidRequestError` is never masked by fallback and is raised immediately.
    """
    request.validate()

    for service in (primary, secondary):
        if service is None:
            continue
        try:
            if not service.is_available():
                continue
            return service.generate(request)
        except AIInvalidRequestError:
            raise
        except AIServiceError:
            continue

    return AIResponse(
        content=fallback_content,
        finish_reason="fallback",
        model="deterministic-fallback",
        provider="fallback",
        latency_ms=0.0,
    )
