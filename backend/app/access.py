"""Demo access check: every request needs the shared code ``Settings.demo_access_token``.

The code comes as ``Authorization: Bearer <token>``; on GET requests also as the
query parameter ``access_token``, because the browser's ``EventSource`` cannot
send headers. Paths in ``OPEN_PATHS`` (the host's health check) need no code.
The token is compared in constant time and never logged; the query parameter is
masked in uvicorn's access log by ``log_filters.RedactQueryTokenFilter``.
"""
from __future__ import annotations

import hmac
import logging
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

TOKEN_QUERY_PARAM = "access_token"
BEARER_PREFIX = "bearer "
OPEN_PATHS = frozenset({"/health"})
UNAUTHORIZED_DETAIL = "access token missing or invalid"
# Methods that may carry the token in the query string (EventSource and image URLs only use GET).
QUERY_TOKEN_METHODS = frozenset({"GET"})


def request_token(scope: Scope) -> str | None:
    """The access token the request carries, or None."""
    authorization = Headers(scope=scope).get("authorization", "")
    if authorization.lower().startswith(BEARER_PREFIX):
        return authorization[len(BEARER_PREFIX):].strip() or None
    if scope.get("method") in QUERY_TOKEN_METHODS:
        values = parse_qs(scope.get("query_string", b"").decode("latin-1")).get(TOKEN_QUERY_PARAM)
        if values:
            return values[0]
    return None


def token_matches(given: str | None, expected: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


class AccessTokenMiddleware:
    """401 for any HTTP request outside ``OPEN_PATHS`` without the right token."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] != "http" or scope["path"] in OPEN_PATHS
                or token_matches(request_token(scope), self._token)):
            await self._app(scope, receive, send)
            return
        logger.warning("request refused: no valid access token")
        response = JSONResponse({"detail": UNAUTHORIZED_DETAIL}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        await response(scope, receive, send)
