"""A simple per-client-IP rate limit for the demo, kept in memory.

Fixed windows: at most ``limit`` requests per ``window_s`` seconds per key. The
counters live in this process only (lost on restart, not shared between
workers), which is enough for one small demo instance. Client IPs are used as
keys only, never logged.
"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

RATE_LIMITED_DETAIL = "rate limit exceeded"
MINUTE_S = 60.0
HOUR_S = 3600.0
# Above this many tracked clients, windows that have ended are dropped (bounds the memory).
MAX_TRACKED_KEYS = 10_000
UNKNOWN_CLIENT = "unknown"


@dataclass
class _Window:
    start: float
    count: int


class RateLimiter:
    """``hit(key)`` counts one request and says how long to wait (0 = allowed)."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._windows: dict[str, _Window] = {}

    def hit(self, key: str) -> float:
        """0 if the request is allowed, else the seconds until the key's window ends."""
        now = self._clock()
        window = self._windows.get(key)
        if window is None or now - window.start >= self._window_s:
            self._drop_ended(now)
            self._windows[key] = _Window(now, 1)
            return 0.0
        if window.count >= self._limit:
            return window.start + self._window_s - now
        window.count += 1
        return 0.0

    def _drop_ended(self, now: float) -> None:
        if len(self._windows) < MAX_TRACKED_KEYS:
            return
        self._windows = {key: w for key, w in self._windows.items() if now - w.start < self._window_s}


def client_ip(scope: Scope, trusted_proxy_hops: int) -> str:
    """The client address: the socket peer, or the ``trusted_proxy_hops``-th entry from the
    right of ``X-Forwarded-For`` (each proxy appends the address it saw, so those entries
    cannot be forged by the client; entries further left can)."""
    peer = scope.get("client")
    if trusted_proxy_hops == 0:
        return peer[0] if peer else UNKNOWN_CLIENT
    forwarded = [part.strip() for part in Headers(scope=scope).get("x-forwarded-for", "").split(",") if part.strip()]
    if len(forwarded) >= trusted_proxy_hops:
        return forwarded[-trusted_proxy_hops]
    return peer[0] if peer else UNKNOWN_CLIENT


@dataclass(frozen=True)
class RateRule:
    """A limiter and the requests it counts (``methods`` × ``path``; ``path`` None = every path)."""

    limiter: RateLimiter
    methods: frozenset[str] | None = None
    path: str | None = None

    def applies(self, scope: Scope) -> bool:
        return ((self.methods is None or scope["method"] in self.methods)
                and (self.path is None or scope["path"] == self.path))


class RateLimitMiddleware:
    """429 with ``Retry-After`` once any matching rule's limit is reached; ``exempt`` paths are not counted."""

    def __init__(self, app: ASGIApp, rules: list[RateRule], trusted_proxy_hops: int,
                 exempt: frozenset[str] = frozenset()) -> None:
        self._app = app
        self._rules = rules
        self._hops = trusted_proxy_hops
        self._exempt = exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._exempt:
            await self._app(scope, receive, send)
            return
        key = client_ip(scope, self._hops)
        wait_s = max((rule.limiter.hit(key) for rule in self._rules if rule.applies(scope)), default=0.0)
        if wait_s <= 0:
            await self._app(scope, receive, send)
            return
        logger.warning("request refused: rate limit")
        response = JSONResponse({"detail": RATE_LIMITED_DETAIL}, status_code=429,
                                headers={"Retry-After": str(math.ceil(wait_s))})
        await response(scope, receive, send)
