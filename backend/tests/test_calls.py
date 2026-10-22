"""ProviderCalls: semaphore, retries with backoff, timeout per call."""
import asyncio
import threading
import time

import pytest

from app.config import ExtractionSettings
from app.pipeline.calls import ProviderCalls
from app.providers.base import ProviderError, RetryableProviderError


def _settings(**overrides) -> ExtractionSettings:
    values = {"concurrency": 2, "call_timeout_s": 1.0, "max_attempts": 3, "backoff_initial_s": 0.0,
              "backoff_max_s": 0.0, **overrides}
    return ExtractionSettings(**values)


def _run(coro):
    return asyncio.run(coro)


def test_retryable_error_is_retried_and_every_attempt_is_counted():
    outcomes = [RetryableProviderError("busy"), RetryableProviderError("busy"), "ok"]

    def call():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def main():
        calls = ProviderCalls(asyncio.Semaphore(1), _settings())
        return await calls.run(call), calls.count

    assert _run(main()) == ("ok", 3)


def test_retries_stop_after_max_attempts():
    async def main():
        calls = ProviderCalls(asyncio.Semaphore(1), _settings(max_attempts=2))
        with pytest.raises(RetryableProviderError):
            await calls.run(lambda: (_ for _ in ()).throw(RetryableProviderError("busy")))
        return calls.count

    assert _run(main()) == 2


def test_other_errors_are_not_retried():
    async def main():
        calls = ProviderCalls(asyncio.Semaphore(1), _settings())
        with pytest.raises(ProviderError):
            await calls.run(lambda: (_ for _ in ()).throw(ProviderError("bad request")))
        return calls.count

    assert _run(main()) == 1


def test_slow_call_times_out_and_is_retried():
    durations = [0.5, 0.0]

    def call():
        time.sleep(durations.pop(0))
        return "ok"

    async def main():
        calls = ProviderCalls(asyncio.Semaphore(1), _settings(call_timeout_s=0.2))
        return await calls.run(call), calls.count

    assert _run(main()) == ("ok", 2)


def test_semaphore_bounds_calls_in_flight_across_forms():
    lock, in_flight, peak = threading.Lock(), [0], [0]

    def call():
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        time.sleep(0.05)
        with lock:
            in_flight[0] -= 1

    async def main():
        semaphore = asyncio.Semaphore(2)
        forms = [ProviderCalls(semaphore, _settings()) for _ in range(3)]
        await asyncio.gather(*(form.run(call) for form in forms for _ in range(2)))
        return [form.count for form in forms]

    assert _run(main()) == [2, 2, 2]
    assert peak[0] == 2


def test_invalid_settings_are_rejected():
    with pytest.raises(ValueError):
        _settings(concurrency=0)
    with pytest.raises(ValueError):
        _settings(backoff_initial_s=5.0, backoff_max_s=1.0)
