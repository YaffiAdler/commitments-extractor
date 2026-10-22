"""Provider calls while reading forms: bounded concurrency, retries with
backoff and a timeout per call.

Providers are blocking (``httpx.Client``), so each call runs in a worker
thread. All forms of a job share one ``asyncio.Semaphore``, held only while
a call is in flight (not while waiting to retry). A ``RetryableProviderError``
or a timeout is retried up to ``max_attempts`` with exponential backoff plus
jitter; any other error is raised at once. A call that times out is
abandoned, but its thread runs until the provider's own HTTP timeout ends it.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from app.config import ExtractionSettings
from app.providers.base import RetryableProviderError
from app.providers.diagnostics import exception_chain

logger = logging.getLogger(__name__)

T = TypeVar("T")
RETRYABLE_ERRORS = (RetryableProviderError, TimeoutError)


def _log_retry(state: RetryCallState) -> None:
    error = state.outcome.exception() if state.outcome else None
    logger.warning("provider call attempt %d failed (%s), retrying", state.attempt_number,
                   exception_chain(error) if error else None)


class ProviderCalls:
    """Runs the provider calls of one form and counts them (retries included)."""

    def __init__(self, semaphore: asyncio.Semaphore, settings: ExtractionSettings) -> None:
        self._semaphore = semaphore
        self._settings = settings
        self.count = 0

    async def run(self, call: Callable[..., T], *args: Any) -> T:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._settings.max_attempts),
            wait=wait_exponential(multiplier=self._settings.backoff_initial_s, max=self._settings.backoff_max_s)
            + wait_random(0, self._settings.backoff_initial_s),
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            before_sleep=_log_retry,
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                async with self._semaphore:
                    self.count += 1
                    return await asyncio.wait_for(asyncio.to_thread(call, *args), self._settings.call_timeout_s)
        raise AssertionError("unreachable: tenacity re-raises the last error")
