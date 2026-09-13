"""Small, dependency-free security helpers for the single-process hosted beta."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading
import time
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.responses import RedirectResponse

from .beta_config import BetaConfig
from .search_public import PUBLIC_DISCOVERY, PUBLIC_ORIGIN


@dataclass
class FailedAttemptLimiter:
    """Bound failed authentication attempts per client key inside one process."""

    max_failures: int
    window_seconds: int
    max_keys: int = 10_000
    clock: Callable[[], float] = time.monotonic
    _events: dict[str, deque[float]] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            self._events.pop(key, None)
            events = deque()
        return events

    def check(self, key: str) -> tuple[bool, int]:
        now = self.clock()
        with self._lock:
            events = self._prune(key, now)
            if len(events) < self.max_failures:
                return True, 0
            retry_after = max(1, int(self.window_seconds - (now - events[0])))
            return False, retry_after

    def record_failure(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            if key not in self._events and len(self._events) >= self.max_keys:
                oldest_key = min(
                    self._events,
                    key=lambda candidate: self._events[candidate][0] if self._events[candidate] else now,
                )
                self._events.pop(oldest_key, None)
            events = self._prune(key, now)
            if key not in self._events:
                self._events[key] = events
            events.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


def client_key(request: Request) -> str:
    """Return Cloudflare's client IP when available, otherwise the socket peer."""

    cloudflare_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cloudflare_ip and len(cloudflare_ip) <= 64:
        return cloudflare_ip
    if request.client and request.client.host:
        return request.client.host[:64]
    return "unknown"


def normalized_host(request: Request) -> str:
    host = request.headers.get("host", "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):
        closing = host.find("]")
        return host[1:closing] if closing > 0 else host
    return host.split(":", 1)[0]


class HostedBetaSecurityMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth boundary for the public Cloudflare-hosted beta.

    The application remains bound to loopback. This layer adds host/origin
    validation, failed-login throttling, API cache suppression, and hides
    framework documentation endpoints from the public beta surface.
    """

    _hidden_paths = {"/docs", "/redoc", "/openapi.json"}
    _invite_login_path = "/api/beta/session"
    _admin_login_path = "/api/beta/admin/session"

    def __init__(self, app, *, config: BetaConfig) -> None:
        super().__init__(app)
        self.config = config
        self.invite_limiter = FailedAttemptLimiter(
            config.invite_login_failures,
            config.login_window_seconds,
        )
        self.admin_limiter = FailedAttemptLimiter(
            config.admin_login_failures,
            config.login_window_seconds,
        )

    @property
    def hosted(self) -> bool:
        return self.config.enabled or self.config.configured

    def _finish(self, response: Response, path: str, *, public: bool = False) -> Response:
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if not (public and response.status_code == 200):
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        if path.startswith("/api/beta/") or path in {"/ready", "/health"}:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not self.hosted:
            return await call_next(request)

        if path in self._hidden_paths:
            return self._finish(JSONResponse({"detail": "not found"}, status_code=404), path)

        if self.config.allowed_hosts:
            host = normalized_host(request)
            if host not in self.config.allowed_hosts:
                return self._finish(JSONResponse({"detail": "invalid host"}, status_code=400), path)

        if (
            self.config.public_access
            and path in PUBLIC_DISCOVERY
            and request.method in {"GET", "HEAD"}
            and not request.url.query
            and request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower() == "http"
        ):
            return self._finish(RedirectResponse(PUBLIC_ORIGIN + path, status_code=308), path)

        if (
            self.config.public_origin
            and path.startswith("/api/beta/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        ):
            origin = request.headers.get("origin", "").rstrip("/")
            if origin and origin != self.config.public_origin.rstrip("/"):
                return self._finish(JSONResponse({"detail": "cross-origin request rejected"}, status_code=403), path)

        limiter = None
        if request.method == "POST" and path == self._invite_login_path:
            limiter = self.invite_limiter
        elif request.method == "POST" and path == self._admin_login_path:
            limiter = self.admin_limiter

        key = client_key(request)
        if limiter is not None:
            allowed, retry_after = limiter.check(key)
            if not allowed:
                response = JSONResponse(
                    {"detail": "too many failed authentication attempts"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                return self._finish(response, path)

        response = await call_next(request)

        if limiter is not None:
            if response.status_code == 401:
                limiter.record_failure(key)
            elif 200 <= response.status_code < 300:
                limiter.reset(key)

        public = (
            self.config.public_access
            and path in PUBLIC_DISCOVERY
            and request.method in {"GET", "HEAD"}
            and not request.url.query
        )
        if request.url.query:
            response.headers["Cache-Control"] = "no-store"
        return self._finish(response, path, public=public)
