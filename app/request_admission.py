"""Early ASGI request-body admission, rate limiting, and security header middleware."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
import math
import os
import threading
import time
from typing import Optional

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_POST_BODY_BYTES = 128 * 1024  # 128 KiB limit for chat and learning endpoints
MAX_API_MUTATION_BODY_BYTES = 256 * 1024  # 256 KiB limit for all other API mutation endpoints
MAX_UPLOAD_BODY_BYTES = 28 * 1024 * 1024  # 28 MiB limit for encoded upload envelope

CHAT_BODY_LIMIT_DETAIL = "Dung lượng yêu cầu vượt quá giới hạn 128 KiB."
UPLOAD_BODY_LIMIT_DETAIL = "Dung lượng yêu cầu tải lên vượt quá giới hạn 28 MiB."


@dataclass(frozen=True)
class BodyLimitPolicy:
    max_bytes: int
    detail: str


_CHAT_POLICY = BodyLimitPolicy(max_bytes=MAX_POST_BODY_BYTES, detail=CHAT_BODY_LIMIT_DETAIL)
_API_MUTATION_POLICY = BodyLimitPolicy(
    max_bytes=MAX_API_MUTATION_BODY_BYTES,
    detail=f"Kích thước dữ liệu gửi lên vượt quá giới hạn cho phép ({MAX_API_MUTATION_BODY_BYTES // 1024} KiB).",
)
_UPLOAD_POLICY = BodyLimitPolicy(max_bytes=MAX_UPLOAD_BODY_BYTES, detail=UPLOAD_BODY_LIMIT_DETAIL)
RATE_LIMIT_DETAIL = "Quá nhiều yêu cầu trong thời gian ngắn. Vui lòng chờ giây lát rồi thử lại."


def resolve_body_limit(method: str, path: str) -> BodyLimitPolicy | None:
    """Return the byte cap and error detail for admitted mutation endpoints, or None."""
    if method.upper() not in ("POST", "PUT", "PATCH"):
        return None
    normalized = path.rstrip("/") or "/"
    if normalized in ("/api/materials/upload", "/api/timetable/extract"):
        return _UPLOAD_POLICY
    if path.startswith("/api/chat") or path.startswith("/api/learning"):
        return _CHAT_POLICY
    if path.startswith("/api/"):
        return _API_MUTATION_POLICY
    return None


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value >= 1 else default
    except ValueError:
        return default


def _env_positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        return value if value >= 1.0 else default
    except ValueError:
        return default


def _extract_header(scope: Scope, name: bytes) -> str:
    target = name.lower()
    for hdr_name, hdr_val in scope.get("headers") or ():
        if hdr_name.lower() == target:
            return hdr_val.decode("latin-1").strip()
    return ""


def _extract_cookie(cookie_header: str, cookie_name: str) -> str:
    if not cookie_header:
        return ""
    for part in cookie_header.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        if k.strip() == cookie_name:
            return v.strip()
    return ""


def _trusted_proxy_hosts() -> set[str]:
    raw = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1,::1").strip()
    hosts = {"127.0.0.1", "::1"}
    for token in raw.split(","):
        cleaned = token.strip()
        if cleaned and cleaned != "*":
            hosts.add(cleaned)
    return hosts


def _is_https_request(scope: Scope) -> bool:
    if os.getenv("STUDY_SECURE_COOKIES", "").strip() == "1":
        return True
    if scope.get("scheme") == "https":
        return True
    client = scope.get("client")
    client_host = str(client[0]) if client and client[0] else ""
    if client_host in _trusted_proxy_hosts():
        forwarded_proto = _extract_header(scope, b"x-forwarded-proto").split(",")[0].strip().lower()
        if forwarded_proto == "https":
            return True
    return False


def _security_header_pairs(scope: Scope) -> list[tuple[bytes, bytes]]:
    headers: list[tuple[bytes, bytes]] = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"SAMEORIGIN"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]
    if _is_https_request(scope):
        headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
    return headers


class SlidingWindowRateLimiter:
    """Thread-safe bounded sliding-window rate limiter keyed by account token or client IP."""

    def __init__(self, max_buckets: int = 4096) -> None:
        self._max_buckets = max(64, int(max_buckets))
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._rejected_429 = 0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._rejected_429 = 0

    def check_many(
        self,
        bucket_limits: list[tuple[str, int]],
        window_seconds: float,
    ) -> tuple[bool, int]:
        if not bucket_limits:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            for key, limit in bucket_limits:
                q = self._buckets.get(key)
                if q is not None:
                    while q and q[0] <= cutoff:
                        q.popleft()
                    if len(q) >= limit:
                        retry_after = max(1, int(math.ceil(q[0] + window_seconds - now)))
                        self._rejected_429 += 1
                        return False, retry_after

            for key, _limit in bucket_limits:
                q = self._buckets.get(key)
                if q is None:
                    if len(self._buckets) >= self._max_buckets:
                        self._buckets.popitem(last=False)
                    q = deque()
                    self._buckets[key] = q
                else:
                    self._buckets.move_to_end(key)
                q.append(now)
            return True, 0

    def get_metrics(self) -> dict[str, object]:
        with self._lock:
            active_buckets = len(self._buckets)
            rejected = self._rejected_429
        return {
            "enabled": _env_flag("STUDY_RATE_LIMIT_ENABLED", default=True),
            "active_buckets": active_buckets,
            "max_buckets": self._max_buckets,
            "rejected_429_total": rejected,
            "api_limit_per_minute": _env_positive_int("STUDY_API_RATE_LIMIT_PER_MINUTE", 300),
            "inference_limit_per_minute": _env_positive_int("STUDY_INFERENCE_RATE_LIMIT_PER_MINUTE", 30),
        }


rate_limiter = SlidingWindowRateLimiter(max_buckets=4096)


def _is_heavy_endpoint(method: str, path: str) -> bool:
    if method.upper() not in ("POST", "PUT", "PATCH"):
        return False
    normalized = path.rstrip("/") or "/"
    if (
        path.startswith("/api/chat")
        or path.startswith("/api/learning")
        or path.startswith("/api/practice")
    ):
        return True
    if normalized in (
        "/api/materials/upload",
        "/api/materials/generate-from-library",
        "/api/materials/inspect-library",
        "/api/timetable/extract",
        "/api/timetable/planner/generate",
        "/api/index",
        "/api/ocr/run-page",
    ):
        return True
    if path.startswith("/api/sessions/") and normalized.endswith("/summarize"):
        return True
    if path.startswith("/api/timetable/planner/") and normalized.endswith("/exercise/generate"):
        return True
    return False


def _admit_rate_limit(scope: Scope) -> tuple[bool, int]:
    path = scope.get("path", "")
    if not path.startswith("/api/"):
        return True, 0
    if not _env_flag("STUDY_RATE_LIMIT_ENABLED", default=True):
        return True, 0

    method = scope.get("method", "")
    heavy = _is_heavy_endpoint(method, path)
    tier = "inf" if heavy else "api"
    limit = (
        _env_positive_int("STUDY_INFERENCE_RATE_LIMIT_PER_MINUTE", 30)
        if heavy
        else _env_positive_int("STUDY_API_RATE_LIMIT_PER_MINUTE", 300)
    )
    window_seconds = _env_positive_float("STUDY_RATE_LIMIT_WINDOW_SECONDS", 60.0)

    client = scope.get("client")
    client_host = str(client[0]) if client and client[0] else "unknown"
    session_token = _extract_cookie(_extract_header(scope, b"cookie"), "study_session")

    if session_token:
        tok_hash = hashlib.sha256(session_token.encode("utf-8", "ignore")).hexdigest()[:16]
        buckets = [
            (f"{tier}:tok:{tok_hash}", limit),
            (f"{tier}:ipagg:{client_host}", max(limit * 25, 250)),
        ]
    else:
        buckets = [(f"{tier}:ip:{client_host}", limit)]

    return rate_limiter.check_many(buckets, window_seconds=window_seconds)


def _declared_content_lengths(headers: list[tuple[bytes, bytes]]) -> list[int]:
    lengths: list[int] = []
    for name, value in headers:
        if name.lower() != b"content-length":
            continue
        raw = value.decode("latin-1")
        for token in raw.split(","):
            part = token.strip()
            if part.isascii() and part.isdigit():
                lengths.append(int(part))
    return lengths


def _reject_413(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"detail": detail},
        headers={"Cache-Control": "no-store"},
    )


class RequestBodyLimitMiddleware:
    """Pure ASGI middleware enforcing security headers, rate limits, and early body byte caps."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        extra_headers = _security_header_pairs(scope)
        orig_send = send

        async def secured_send(message: dict) -> None:
            if message.get("type") == "http.response.start":
                raw_headers = list(message.get("headers") or [])
                existing_names = {k.lower() for k, _ in raw_headers}
                for hdr_name, hdr_value in extra_headers:
                    if hdr_name not in existing_names:
                        raw_headers.append((hdr_name, hdr_value))
                message = {**message, "headers": raw_headers}
            await orig_send(message)

        send = secured_send

        admitted, retry_after = _admit_rate_limit(scope)
        if not admitted:
            resp = JSONResponse(
                {"detail": RATE_LIMIT_DETAIL},
                status_code=429,
                headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
            )
            await resp(scope, receive, send)
            return

        policy = resolve_body_limit(scope.get("method", ""), scope.get("path", ""))
        if policy is None:
            await self.app(scope, receive, send)
            return

        for declared in _declared_content_lengths(scope.get("headers") or []):
            if declared > policy.max_bytes:
                await _reject_413(policy.detail)(scope, receive, send)
                return

        chunks: list[bytes] = []
        total_received = 0
        try:
            while True:
                message = await receive()
                msg_type = message.get("type")
                if msg_type == "http.request":
                    chunk = message.get("body", b"")
                    if chunk:
                        total_received += len(chunk)
                        if total_received > policy.max_bytes:
                            chunks.clear()
                            await _reject_413(policy.detail)(scope, receive, send)
                            return
                        chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
                elif msg_type == "http.disconnect":
                    chunks.clear()
                    return
            payload = b"".join(chunks)
        finally:
            chunks.clear()

        delivered = False

        async def replayed_receive():
            nonlocal delivered, payload
            if not delivered:
                delivered = True
                body = payload
                payload = b""
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        try:
            await self.app(scope, replayed_receive, send)
        finally:
            payload = b""
