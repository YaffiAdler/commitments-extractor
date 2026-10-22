import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import UNAUTHORIZED_DETAIL, AccessTokenMiddleware, request_token, token_matches

TOKEN = "dummy-demo-token-0123456789"


def _scope(method: str = "GET", headers: dict[str, str] | None = None, query: str = "") -> dict:
    raw = [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
    return {"type": "http", "method": method, "path": "/jobs", "headers": raw, "query_string": query.encode()}


@pytest.mark.parametrize("scope, expected", [
    (_scope(headers={"Authorization": f"Bearer {TOKEN}"}), TOKEN),
    (_scope(headers={"Authorization": f"bearer  {TOKEN} "}), TOKEN),
    (_scope(query=f"item=1&access_token={TOKEN}"), TOKEN),
    (_scope(headers={"Authorization": "Basic abc"}), None),
    (_scope(headers={"Authorization": "Bearer "}), None),
    (_scope(), None),
    # Only GET takes the query parameter (EventSource); a POST must use the header.
    (_scope(method="POST", query=f"access_token={TOKEN}"), None),
])
def test_request_token(scope, expected):
    assert request_token(scope) == expected


def test_token_matches():
    assert token_matches(TOKEN, TOKEN)
    assert not token_matches(TOKEN + "x", TOKEN)
    assert not token_matches(None, TOKEN)
    assert not token_matches("", TOKEN)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.get("/health")
    def health() -> str:
        return "ok"

    @app.get("/jobs")
    def jobs() -> str:
        return "jobs"

    app.add_middleware(AccessTokenMiddleware, token=TOKEN)
    return TestClient(app)


def test_refuses_without_token(client):
    response = client.get("/jobs")
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED_DETAIL}
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get("/jobs", headers={"Authorization": "Bearer wrong-token-0123456789"}).status_code == 401


def test_accepts_header_and_query(client):
    assert client.get("/jobs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get("/jobs", params={"access_token": TOKEN}).status_code == 200


def test_health_is_open(client):
    assert client.get("/health").status_code == 200


def test_refusal_logs_no_token(client, caplog):
    client.get("/jobs", headers={"Authorization": "Bearer guessed-token-0123456789"})
    assert "guessed-token" not in caplog.text
    assert TOKEN not in caplog.text
