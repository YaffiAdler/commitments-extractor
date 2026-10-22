"""Logging filters that keep secrets out of the logs."""
from __future__ import annotations

import logging
import re

from app.access import TOKEN_QUERY_PARAM

# uvicorn's access log line holds the request path with its query string.
ACCESS_LOGGER = "uvicorn.access"
REDACTED = "***"
_TOKEN_IN_QUERY = re.compile(rf"({TOKEN_QUERY_PARAM}=)[^&\s\"]*")


def redact_token(text: str) -> str:
    return _TOKEN_IN_QUERY.sub(rf"\g<1>{REDACTED}", text)


class RedactQueryTokenFilter(logging.Filter):
    """Masks ``access_token=...`` in a record's message and string arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_token(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact_token(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True


def install_token_redaction() -> None:
    """Attach the filter to uvicorn's access logger once (it is configured before the app is imported)."""
    access = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(existing, RedactQueryTokenFilter) for existing in access.filters):
        access.addFilter(RedactQueryTokenFilter())
