import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rate_limit import (MAX_TRACKED_KEYS, RATE_LIMITED_DETAIL, RateLimiter, RateLimitMiddleware, RateRule,
                            client_ip)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limit_per_window():
    clock = FakeClock()
    limiter = RateLimiter(2, 60, clock)
    assert limiter.hit("a") == 0
    assert limiter.hit("a") == 0
    clock.now += 10
    assert limiter.hit("a") == pytest.approx(50)
    assert limiter.hit("b") == 0  # per key
    clock.now += 50
    assert limiter.hit("a") == 0  # a new window


def test_ended_windows_are_dropped_when_many_keys():
    clock = FakeClock()
    limiter = RateLimiter(1, 60, clock)
    for n in range(MAX_TRACKED_KEYS):
        limiter.hit(f"k{n}")
    clock.now += 61
    limiter.hit("new")
    assert len(limiter._windows) == 1


def _scope(client: tuple[str, int] | None, forwarded: str | None = None) -> dict:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return {"type": "http", "client": client, "headers": headers}


@pytest.mark.parametrize("scope, hops, expected", [
    (_scope(("10.0.0.1", 5)), 0, "10.0.0.1"),
    # The client may send its own X-Forwarded-For; without trusted proxies it is ignored.
    (_scope(("10.0.0.1", 5), "1.2.3.4"), 0, "10.0.0.1"),
    # One proxy appended the address it saw: the rightmost entry, not the forged leftmost one.
    (_scope(("10.0.0.1", 5), "6.6.6.6, 203.0.113.7"), 1, "203.0.113.7"),
    (_scope(("10.0.0.1", 5), "6.6.6.6, 203.0.113.7, 10.0.0.9"), 2, "203.0.113.7"),
    (_scope(("10.0.0.1", 5), ""), 1, "10.0.0.1"),
    (_scope(None), 0, "unknown"),
])
def test_client_ip(scope, hops, expected):
    assert client_ip(scope, hops) == expected


def _client(rules: list[RateRule]) -> TestClient:
    app = FastAPI()

    @app.get("/health")
    def health() -> str:
        return "ok"

    @app.get("/jobs")
    def list_jobs() -> str:
        return "jobs"

    @app.post("/jobs")
    def create_job() -> str:
        return "created"

    app.add_middleware(RateLimitMiddleware, rules=rules, trusted_proxy_hops=0, exempt=frozenset({"/health"}))
    return TestClient(app)


def test_middleware_answers_429_with_retry_after():
    client = _client([RateRule(RateLimiter(2, 60))])
    assert [client.get("/jobs").status_code for _ in range(2)] == [200, 200]
    refused = client.get("/jobs")
    assert refused.status_code == 429
    assert refused.json() == {"detail": RATE_LIMITED_DETAIL}
    assert 0 < int(refused.headers["retry-after"]) <= 60
    assert client.get("/health").status_code == 200  # exempt


def test_rule_counts_only_its_method_and_path():
    client = _client([RateRule(RateLimiter(1, 3600), methods=frozenset({"POST"}), path="/jobs")])
    assert client.post("/jobs").status_code == 200
    assert client.post("/jobs").status_code == 429
    assert client.get("/jobs").status_code == 200
