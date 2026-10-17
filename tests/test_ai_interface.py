"""Issue #24: AI service interface contract, validation, mock, adapter, and fallback tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pytest

from app.ai_interface import (
    AIConnectionError,
    AIInvalidRequestError,
    AIProviderError,
    AIRequest,
    AIResponse,
    AIServiceError,
    AIServiceInterface,
    AIStreamChunk,
    AITimeoutError,
    ChatMessage,
    GenerationConfig,
    LegacyClientAdapter,
    MockAIService,
    generate_with_fallback,
)

ROOT = Path(__file__).resolve().parents[1]


def test_ai_interface_doc_exists_and_covers_required_sections():
    """Issue #24 acceptance: docs/ai-interface.md documents signature, inputs, outputs, errors, fallback."""
    doc_path = ROOT / "docs" / "ai-interface.md"
    assert doc_path.is_file(), "docs/ai-interface.md must exist"
    text = doc_path.read_text(encoding="utf-8")
    for required_term in (
        "AIServiceInterface",
        "Function Signatures",
        "Inputs",
        "Outputs",
        "Errors",
        "Fallback",
        "AIRequest",
        "AIResponse",
        "AIServiceError",
    ):
        assert required_term in text, f"Missing required section/term in docs/ai-interface.md: {required_term}"


def test_mock_ai_service_satisfies_protocol_and_returns_structured_output():
    mock = MockAIService(response_text="Working memory holds information temporarily. [S1]", model="mock-qwen")
    assert isinstance(mock, AIServiceInterface)
    assert mock.is_available() is True

    req = AIRequest(
        system_prompt="Answer strictly from sources.",
        messages=[ChatMessage(role="user", content="What is working memory?")],
        config=GenerationConfig(max_tokens=256, temperature=0.1),
    )
    resp = mock.generate(req)
    assert isinstance(resp, AIResponse)
    assert resp.content == "Working memory holds information temporarily. [S1]"
    assert resp.finish_reason == "stop"
    assert resp.is_truncated is False
    assert resp.provider == "mock"
    assert resp.model == "mock-qwen"
    assert len(mock.requests) == 1

    chunks = list(mock.generate_stream(req))
    assert all(isinstance(c, AIStreamChunk) for c in chunks)
    assert "".join(c.delta for c in chunks) == resp.content
    assert chunks[-1].done is True
    assert chunks[-1].finish_reason == "stop"


@pytest.mark.parametrize(
    "invalid_kwargs",
    [
        {"messages": []},
        {"messages": [ChatMessage(role="user", content="   ")]},
        {"messages": [{"role": "invalid_role", "content": "Hello"}]},
        {"messages": [ChatMessage(role="user", content="bad\x00char")]},
        {"messages": [ChatMessage(role="user", content="Hi")], "config": GenerationConfig(max_tokens=0)},
        {"messages": [ChatMessage(role="user", content="Hi")], "config": GenerationConfig(temperature=2.5)},
        {"messages": [ChatMessage(role="user", content="Hi")], "config": GenerationConfig(context_window=64)},
        {"messages": [ChatMessage(role="user", content="Hi")], "config": GenerationConfig(timeout_seconds=0)},
    ],
)
def test_invalid_request_raises_ai_invalid_request_error(invalid_kwargs):
    mock = MockAIService()
    req = AIRequest(**invalid_kwargs)
    with pytest.raises(AIInvalidRequestError):
        mock.generate(req)


def test_swapping_implementations_and_fallback_behavior():
    """Business callers can swap implementations or fall back without changing business logic."""
    req = AIRequest(messages=[ChatMessage(role="user", content="Explain LIFO in stacks.")])

    failing_primary = MockAIService(error=AIConnectionError("Primary MLX offline"))
    working_secondary = MockAIService(response_text="Stack uses Last-In, First-Out (LIFO). [S1]", model="backup-ollama")

    resp = generate_with_fallback(req, primary=failing_primary, secondary=working_secondary)
    assert resp.content == "Stack uses Last-In, First-Out (LIFO). [S1]"
    assert resp.finish_reason == "stop"
    assert resp.model == "backup-ollama"

    # When both primary and secondary fail, deterministic fallback is returned safely
    failing_secondary = MockAIService(error=AITimeoutError("Secondary timed out"))
    fb_resp = generate_with_fallback(
        req,
        primary=failing_primary,
        secondary=failing_secondary,
        fallback_content="Dịch vụ AI tạm thời gián đoạn.",
    )
    assert fb_resp.finish_reason == "fallback"
    assert fb_resp.provider == "fallback"
    assert fb_resp.content == "Dịch vụ AI tạm thời gián đoạn."


def test_legacy_client_adapter_normalizes_done_reason_and_inband_errors():
    """LegacyClientAdapter wraps existing OllamaClient/TrainedModelClient style objects cleanly."""

    class FakeLegacyClient:
        def __init__(self, pieces, done_reason="stop", healthy=True):
            self.pieces = pieces
            self.last_done_reason = done_reason
            self.healthy = healthy

        def check_health(self):
            return self.healthy

        def chat_stream(self, **kwargs):
            for p in self.pieces:
                yield p

    req = AIRequest(messages=[ChatMessage(role="user", content="Test adapter")])

    # 1. Truncated stream (last_done_reason == 'length') -> finish_reason == 'length'
    truncated_adapter = LegacyClientAdapter(
        FakeLegacyClient(["Câu trả lời bị cắt giữa "], done_reason="length"),
        provider_name="ollama",
        default_model="qwen2.5:3b",
    )
    assert isinstance(truncated_adapter, AIServiceInterface)
    res = truncated_adapter.generate(req)
    assert res.is_truncated is True
    assert res.finish_reason == "length"

    # 2. In-band error string ('❌ Không kết nối được...') -> raises AIConnectionError
    inband_conn_err = LegacyClientAdapter(
        FakeLegacyClient(["❌ Không kết nối được tới Ollama. Hãy chắc chắn Ollama đang chạy."]),
        provider_name="ollama",
    )
    with pytest.raises(AIConnectionError):
        inband_conn_err.generate(req)

    # 3. In-band timeout string -> raises AITimeoutError
    inband_timeout = LegacyClientAdapter(
        FakeLegacyClient(["❌ Yêu cầu quá thời gian chờ (timeout)."]),
        provider_name="ollama",
    )
    with pytest.raises(AITimeoutError):
        inband_timeout.generate(req)

    # 4. In-band provider error -> raises AIProviderError
    inband_provider = LegacyClientAdapter(
        FakeLegacyClient(["Lỗi từ Ollama: HTTP 500"]),
        provider_name="ollama",
    )
    with pytest.raises(AIProviderError):
        inband_provider.generate(req)
