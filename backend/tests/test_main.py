import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, create_app
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
