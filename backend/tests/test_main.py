import io
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import app, create_app
from app.pipeline import ingest
from app.field_crops import FIELD_NOT_FOUND, IMAGE_GONE, NO_BOX
from app.providers.fake import FakeProvider
from app.schemas import BBox, Job
from app.storage.base import JobStoreError
from app.storage.page_images import PAGES_DIR


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


# --- Review: manual corrections, field crops, deletion, store errors ----------

DUMMY_ID_NUMBER = "000000026"
BOX = {"page": 0, "x0": 10.0, "y0": 10.0, "x1": 60.0, "y1": 40.0}


def _done_job_id(client: TestClient) -> str:
    created = client.post("/jobs", files={"file": ("scan.png", _png(), "image/png")})
    job = client.get(f"/jobs/{created.json()['id']}").json()
    assert job["status"] == "done"
    return job["id"]


def _field_url(job_id: str, group_id: str, field: str) -> str:
    return f"/jobs/{job_id}/groups/{group_id}/fields/{field}"


def test_patch_sets_a_manual_value_and_keeps_it(client):
    job_id = _done_job_id(client)
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]

    response = client.patch(_field_url(job_id, group_id, "id_number"), json={"value": DUMMY_ID_NUMBER})
    assert response.status_code == 200
    field = response.json()["groups"][0]["commitment"]["id_number"]
    assert (field["value"], field["source"], field["needs_review"], field["confidence"]) == (
        DUMMY_ID_NUMBER, "manual", False, 1.0)
    assert field["reasons"] == ["manual_correction"]

    stored = client.get(f"/jobs/{job_id}").json()
    assert stored["groups"][0]["commitment"]["id_number"] == field
    assert [(c["group_id"], c["field"], c["item"]) for c in stored["corrections"]] == [(group_id, "id_number", None)]


def test_patch_list_item_and_new_item(client):
    job_id = _done_job_id(client)
    group = client.get(f"/jobs/{job_id}").json()["groups"][0]
    count = len(group["commitment"]["central_numbers"])
    url = _field_url(job_id, group["id"], "central_numbers")
    assert client.patch(url, json={"value": "0011111", "item": count}).status_code == 200
    assert client.patch(url, json={"value": "0022222", "item": 0}).status_code == 200
    values = [f["value"] for f in client.get(f"/jobs/{job_id}").json()["groups"][0]["commitment"]["central_numbers"]]
    assert values[0] == "0022222" and values[-1] == "0011111" and len(values) == count + 1


def test_patch_date_and_sender(client):
    from app.pipeline.normalize import load_senders

    job_id = _done_job_id(client)
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]
    sender = load_senders(Settings().senders_path)[0].canonical
    assert client.patch(_field_url(job_id, group_id, "received_date"), json={"value": "2026-01-04"}).status_code == 200
    assert client.patch(_field_url(job_id, group_id, "sender"), json={"value": sender}).status_code == 200
    commitment = client.get(f"/jobs/{job_id}").json()["groups"][0]["commitment"]
    assert (commitment["received_date"]["value"], commitment["sender"]["value"]) == ("2026-01-04", sender)


@pytest.mark.parametrize(("field", "body"), [
    ("received_date", {"value": "4/1/26"}),
    ("sender", {"value": "not a listed sender"}),
    ("no_such_field", {"value": "x"}),
    ("id_number", {"value": "   "}),
    ("id_number", {"value": DUMMY_ID_NUMBER, "item": 0}),
    ("central_numbers", {"value": "1"}),
    ("central_numbers", {"value": "1", "item": 99}),
])
def test_patch_rejects_invalid_corrections(client, field, body):
    job_id = _done_job_id(client)
    before = client.get(f"/jobs/{job_id}").json()
    response = client.patch(_field_url(job_id, before["groups"][0]["id"], field), json=body)
    assert response.status_code == 422
    assert body["value"].strip() == "" or body["value"] not in response.text
    assert client.get(f"/jobs/{job_id}").json() == before


def test_patch_unknown_form_or_unfinished_job(client):
    job_id = _done_job_id(client)
    assert client.patch(_field_url(job_id, "0" * 32, "id_number"), json={"value": "1"}).status_code == 404
    pending = Job()
    client.app.state.jobs.create(pending)
    assert client.patch(_field_url(pending.id, "0" * 32, "id_number"), json={"value": "1"}).status_code == 409


def _with_box(client: TestClient, job_id: str) -> str:
    """Give the ID field a box (the fake provider reads none); return the form id."""
    store = client.app.state.jobs
    job = store.get(job_id)
    job.groups[0].commitment.id_number.bbox = BBox(**BOX)
    store.update(job)
    return job.groups[0].id


def test_crop_of_a_field_is_a_png(client):
    job_id = _done_job_id(client)
    group_id = _with_box(client, job_id)
    response = client.get(_field_url(job_id, group_id, "id_number") + "/crop")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    # The box plus 10 px padding: x 0..70 clipped to the 60 px wide page, y 0..50.
    assert Image.open(io.BytesIO(response.content)).size == (60, 50)


def test_crop_after_the_page_image_is_gone_is_a_clear_404(client, tmp_path):
    job_id = _done_job_id(client)
    group_id = _with_box(client, job_id)
    shutil.rmtree(tmp_path / job_id / PAGES_DIR)  # as the file TTL or a restart on an ephemeral disk would
    response = client.get(_field_url(job_id, group_id, "id_number") + "/crop")
    assert (response.status_code, response.json()["detail"]) == (404, IMAGE_GONE)
    assert client.get(f"/jobs/{job_id}").status_code == 200  # the job itself remains


def test_crop_without_a_box_or_field(client):
    job_id = _done_job_id(client)
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]
    assert client.get(_field_url(job_id, group_id, "id_number") + "/crop").json()["detail"] == NO_BOX
    assert client.get(_field_url(job_id, group_id, "nope") + "/crop").json()["detail"] == FIELD_NOT_FOUND
    assert client.get(_field_url(job_id, "0" * 32, "id_number") + "/crop").status_code == 404


def test_delete_removes_the_job_and_its_files(client, tmp_path):
    job_id = _done_job_id(client)
    assert (tmp_path / job_id / PAGES_DIR).is_dir()
    assert client.delete(f"/jobs/{job_id}").status_code == 204
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.app.state.jobs.list_commitments(job_id) == []
    assert not (tmp_path / job_id).exists()


def test_store_failure_is_503_and_logs_the_type_only(client, monkeypatch, caplog):
    job_id = _done_job_id(client)

    def broken(_job_id):
        raise JobStoreError("OperationalError")

    monkeypatch.setattr(client.app.state.jobs, "get", broken)
    with caplog.at_level(logging.INFO):
        response = client.get(f"/jobs/{job_id}")
    assert (response.status_code, response.json()["detail"]) == (503, "job store unavailable")
    assert [r.getMessage() for r in caplog.records if r.name == "app.main"] == ["job store failed: OperationalError"]
