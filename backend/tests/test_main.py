import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import app, create_app
from app.pipeline import ingest
from app.providers.fake import FakeProvider


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("CE_MAX_UPLOAD_MB", "1")
    return TestClient(create_app(Settings(), FakeProvider()))


def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_rejects_non_pdf(client):
    response = client.post("/jobs", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert response.status_code == 415


def test_rejects_too_large(client):
    response = client.post("/jobs", files={"file": ("x.pdf", b"%PDF-" + b"0" * 1024 * 1024, "application/pdf")})
    assert response.status_code == 413


@pytest.mark.parametrize("job_id", ["0" * 32, "not-an-id"])
def test_unknown_job_is_404(client, job_id):
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.post(f"/jobs/{job_id}/export").status_code == 404
    assert client.delete(f"/jobs/{job_id}").status_code == 404


def test_failed_job_records_type_only(client):
    # A truncated PDF passes the magic check but fails to open.
    job_id = client.post("/jobs", files={"file": ("x.pdf", b"%PDF-1.4 broken", "application/pdf")}).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error"].isidentifier()
    assert client.post(f"/jobs/{job_id}/export").status_code == 409


def _png() -> bytes:
    import io
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (60, 80), "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_accepts_image_by_content_not_name(client):
    # A PNG uploaded under a .pdf name is detected as PNG and processed.
    created = client.post("/jobs", files={"file": ("scan.pdf", _png(), "application/pdf")})
    assert created.status_code == 201
    job = client.get(f"/jobs/{created.json()['id']}").json()
    assert job["status"] == "done"
    assert job["files"][0].endswith(".png")


@pytest.mark.parametrize("content", [b"\x89PNG\r\n\x1a\n truncated", b"%PDF-1.4 broken"], ids=["png", "pdf"])
def test_corrupt_upload_fails_the_job_not_the_server(client, content):
    job_id = client.post("/jobs", files={"file": ("x", content, "application/octet-stream")}).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["error"] == "CorruptFileError"
    assert client.get("/health").status_code == 200


ALLOWED_ORIGIN = "https://frontend.example.org"
FOREIGN_ORIGIN = "https://other.example.org"
ALLOW_ORIGIN_HEADER = "access-control-allow-origin"


def _cors_client(tmp_path, monkeypatch, origins: str) -> TestClient:
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("CE_ALLOWED_ORIGINS", origins)
    return TestClient(create_app(Settings(), FakeProvider()))


def _preflight(client: TestClient, origin: str):
    return client.options("/jobs", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})


def test_cors_allowed_origin_gets_headers(tmp_path, monkeypatch):
    client = _cors_client(tmp_path, monkeypatch, ALLOWED_ORIGIN)
    response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert response.headers[ALLOW_ORIGIN_HEADER] == ALLOWED_ORIGIN
    preflight = _preflight(client, ALLOWED_ORIGIN)
    assert preflight.status_code == 200
    assert preflight.headers[ALLOW_ORIGIN_HEADER] == ALLOWED_ORIGIN
    assert "POST" in preflight.headers["access-control-allow-methods"]


def test_cors_foreign_origin_gets_no_headers(tmp_path, monkeypatch):
    client = _cors_client(tmp_path, monkeypatch, ALLOWED_ORIGIN)
    assert ALLOW_ORIGIN_HEADER not in client.get("/health", headers={"Origin": FOREIGN_ORIGIN}).headers
    preflight = _preflight(client, FOREIGN_ORIGIN)
    assert preflight.status_code == 400
    assert ALLOW_ORIGIN_HEADER not in preflight.headers


def test_no_cors_when_origins_empty(tmp_path, monkeypatch):
    client = _cors_client(tmp_path, monkeypatch, "")
    assert ALLOW_ORIGIN_HEADER not in client.get("/health", headers={"Origin": ALLOWED_ORIGIN}).headers
    assert ALLOW_ORIGIN_HEADER not in _preflight(client, ALLOWED_ORIGIN).headers


def _sse_events(text: str) -> list[tuple[str, dict | list]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_events_stream_progress_rows_and_end(client):
    job_id = client.post("/jobs", files={"file": ("scan.png", _png(), "image/png")}).json()["id"]
    response = client.get(f"/jobs/{job_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = _sse_events(response.text)
    assert [name for name, _ in events] == ["progress", "rows", "end"]
    assert events[0][1] == {"status": "done", "forms_total": 1, "forms_done": 1}
    assert len(events[1][1]) == 1 and events[1][1][0]["finished"]
    assert events[2][1]["id"] == job_id and events[2][1]["status"] == "done"


def test_events_of_unknown_job_is_404(client):
    assert client.get(f"/jobs/{'0' * 32}/events").status_code == 404


def test_failed_job_logs_no_field_value(client, monkeypatch, caplog):
    secret = "000000018"  # dummy ID number quoted by a (hypothetical) library error

    def failing_load(path, limits):
        raise ValueError(f"bad id {secret}")

    monkeypatch.setattr(ingest, "load_file", failing_load)
    with caplog.at_level(logging.DEBUG):
        job_id = client.post("/jobs", files={"file": ("scan.png", _png(), "image/png")}).json()["id"]
    assert client.get(f"/jobs/{job_id}").json()["error"] == "ValueError"
    assert secret not in caplog.text
    assert f"job {job_id} failed: ValueError" in caplog.text


def test_max_image_pixels_is_set_on_pillow(tmp_path, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", Image.MAX_IMAGE_PIXELS)  # restored after the test
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("CE_MAX_IMAGE_PIXELS", "1234567")
    create_app(Settings(), FakeProvider())
    assert Image.MAX_IMAGE_PIXELS == 1234567


def test_logs_go_to_stdout(tmp_path):
    # A fresh interpreter: under pytest the root logger already has handlers.
    code = ("import logging; from app.main import configure_logging; configure_logging('INFO'); "
            "logging.getLogger('app.check').info('log-line-marker'); "
            "logging.getLogger('app.check').debug('debug-line-marker')")
    env = {**os.environ, "CE_PROVIDER": "fake", "CE_TMP_DIR": str(tmp_path), "CE_LOG_LEVEL": "INFO"}
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "log-line-marker" in result.stdout and "log-line-marker" not in result.stderr
    assert "debug-line-marker" not in result.stdout
