from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.body_limit import LENGTH_REQUIRED_DETAIL, TOO_LARGE_DETAIL, BodySizeLimitMiddleware

MAX_BYTES = 10


def _client() -> TestClient:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> int:
        return len(await request.body())

    @app.get("/echo")
    def get() -> str:
        return "ok"

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BYTES)
    return TestClient(app)


def test_body_within_limit_passes():
    assert _client().post("/echo", content=b"x" * MAX_BYTES).json() == MAX_BYTES


def test_body_over_limit_is_413():
    response = _client().post("/echo", content=b"x" * (MAX_BYTES + 1))
    assert response.status_code == 413
    assert response.json() == {"detail": TOO_LARGE_DETAIL}


def test_body_without_length_is_411():
    # A generator body is sent chunked, without Content-Length.
    response = _client().post("/echo", content=iter([b"x"]))
    assert response.status_code == 411
    assert response.json() == {"detail": LENGTH_REQUIRED_DETAIL}


def test_get_is_not_checked():
    assert _client().get("/echo").status_code == 200
