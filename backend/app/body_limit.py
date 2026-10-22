"""Refuse request bodies over a size limit before they are read.

Starlette spools a multipart upload to disk before the endpoint sees it, so the
per-file check in ``POST /jobs`` alone would still let a huge body through. A
request with a body must declare ``Content-Length`` (browsers always do for
``fetch`` with ``FormData`` or JSON); a larger one gets 413, one without a length 411.
"""
from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
TOO_LARGE_DETAIL = "request too large"
LENGTH_REQUIRED_DETAIL = "content length required"


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in BODY_METHODS:
            await self._app(scope, receive, send)
            return
        length = Headers(scope=scope).get("content-length", "")
        if not length.isdigit():
            await JSONResponse({"detail": LENGTH_REQUIRED_DETAIL}, status_code=411)(scope, receive, send)
        elif int(length) > self._max_bytes:
            await JSONResponse({"detail": TOO_LARGE_DETAIL}, status_code=413)(scope, receive, send)
        else:
            # The server (h11 / httptools) stops reading at the declared length.
            await self._app(scope, receive, send)
