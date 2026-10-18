# AI service interface

`app/ai_interface.py` defines an optional typed boundary for text-generation clients. Existing application flows may still use their current clients directly; the protocol does not mean every route has migrated.

## Function Signatures

`AIServiceInterface` defines three methods:

```python
is_available() -> bool
generate(request: AIRequest) -> AIResponse
generate_stream(request: AIRequest) -> Iterator[AIStreamChunk]
```

`LegacyClientAdapter` wraps an existing local client. `MockAIService` provides deterministic responses for software tests. Neither enables a new provider by itself.

## Inputs

`AIRequest` carries messages, an optional system prompt, an optional model and `GenerationConfig`. Messages use the `system`, `user` or `assistant` role and string content. Validation rejects empty requests, unsupported roles, null/surrogate characters in message content and invalid generation settings.

`GenerationConfig` includes a token limit, temperature, context window, timeout and optional seed. The legacy adapter passes generation settings to its client. It does not currently enforce `timeout_seconds` itself; a consuming integration must enforce its own deadline.

## Outputs

`AIResponse` includes content, finish reason, model, provider and optional latency. `is_truncated` is true for the `length` finish reason. Streaming emits `AIStreamChunk` values with text deltas, followed by completion metadata.

Preserve the distinction between `stop`, `length`, `fallback` and `error`. A completed transport does not prove that an answer is supported by its source. Citation and evidence validation remain the responsibility of the application workflow.

## Errors

All contract exceptions derive from `AIServiceError`:

| Exception | Meaning |
| --- | --- |
| `AIInvalidRequestError` | Invalid request or generation settings |
| `AIConnectionError` | Runtime unavailable or connection failed |
| `AITimeoutError` | The underlying client reports a timeout |
| `AIProviderError` | Provider failure or recognized in-band error |

The adapter translates client exceptions into these types. Callers should handle failures explicitly and retain truncation metadata.

## Fallback

`generate_with_fallback` validates the request, tries an available primary service, then an optional secondary service after an `AIServiceError`. Invalid input is raised immediately. If no service succeeds, it returns the configured fallback text with `finish_reason="fallback"` and `provider="fallback"`.

Treat this response as an availability fallback, not a sourced answer. Keep model changes and provider migration separate from interface adoption, with integration tests and real-model content review.
