"""HTTP security controls: headers, rate limiting, authentication, request IDs.

Defence in depth: each of these is also configured at the edge in the deploy
templates (nginx, Cloud Armor, ingress annotations), and the app still enforces
them itself so a misconfigured proxy does not leave it open.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
import uuid
from collections import OrderedDict

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from .config import Settings

log = logging.getLogger("stocklens.access")

#: No inline script or style anywhere in the UI, so the policy can forbid both.
#: `connect-src 'self'` means the page can only talk to this API — a script
#: that did get in could not post data anywhere else.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'; upgrade-insecure-requests"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The left-most entry is the client as seen by the first proxy.
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Token bucket per key, bounded memory (LRU eviction).

    In-process, so the limit is per replica. Behind several replicas the edge
    limit (nginx `limit_req`, Cloud Armor, WAF) is the real ceiling and this
    is the backstop; a Redis-backed bucket is the drop-in upgrade.
    """

    def __init__(self, per_minute: int, max_keys: int = 10_000) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(per_minute)
        self.max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.pop(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            allowed = tokens >= 1
            if allowed:
                tokens -= 1
            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
        retry_after = 0.0 if allowed else (1 - tokens) / self.rate
        return allowed, retry_after


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.limiter = RateLimiter(settings.rate_limit_per_minute)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id", "")
        if not (0 < len(request_id) <= 64 and request_id.replace("-", "").isalnum()):
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        ip = client_ip(request, self.settings.trust_proxy_headers)
        request.state.client_ip = ip
        started = time.monotonic()

        if request.url.path.startswith("/api/"):
            allowed, retry = self.limiter.allow(ip)
            if not allowed:
                response: Response = JSONResponse(
                    {"detail": "rate limit exceeded"}, status_code=429,
                    headers={"Retry-After": str(int(retry) + 1)})
                return self._finish(request, response, started)

        try:
            response = await call_next(request)
        except Exception:
            # Never leak a stack trace to the client; it is in the log.
            log.exception("unhandled error", extra={"request_id": request_id})
            response = JSONResponse({"detail": "internal error", "request_id": request_id},
                                    status_code=500)
        return self._finish(request, response, started)

    def _finish(self, request: Request, response: Response, started: float) -> Response:
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        log.info("request", extra={
            "request_id": request.state.request_id, "method": request.method,
            "path": request.url.path, "status": response.status_code,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "client_ip": request.state.client_ip,
        })
        return response


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token else None


def has_valid_key(request: Request, settings: Settings) -> bool:
    if settings.api_key is None:
        return False
    token = _bearer(request)
    # Constant-time comparison: response timing must not reveal a prefix match.
    return token is not None and hmac.compare_digest(
        token.encode(), settings.api_key.get_secret_value().encode())


def require_api_key_if_configured(request: Request, settings: Settings) -> None:
    if settings.api_key is not None and not has_valid_key(request, settings):
        raise HTTPException(status_code=401, detail="missing or invalid API key",
                            headers={"WWW-Authenticate": "Bearer"})
