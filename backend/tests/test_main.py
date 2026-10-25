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
from app.field_crops import FIELD_NOT_FOUND, IMAGE_GONE, NO_BOX, PAGE_NOT_IN_FORM
from app.providers import fake
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
    response = client.post("/jobs", files={"files": ("x.pdf", b"not a pdf", "application/pdf")})
    assert response.status_code == 415


def test_rejects_too_large(client):
    response = client.post("/jobs", files={"files": ("x.pdf", b"%PDF-" + b"0" * 1024 * 1024, "application/pdf")})
    assert response.status_code == 413


@pytest.mark.parametrize("job_id", ["0" * 32, "not-an-id"])
def test_unknown_job_is_404(client, job_id):
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.post(f"/jobs/{job_id}/export").status_code == 404
    assert client.delete(f"/jobs/{job_id}").status_code == 404


def test_failed_job_records_type_only(client):
    # A truncated PDF passes the magic check but fails to open.
    job_id = client.post("/jobs", files={"files": ("x.pdf", b"%PDF-1.4 broken", "application/pdf")}).json()["id"]
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
    created = client.post("/jobs", files={"files": ("scan.pdf", _png(), "application/pdf")})
    assert created.status_code == 201
    job = client.get(f"/jobs/{created.json()['id']}").json()
    assert job["status"] == "done"
    assert job["files"][0].endswith(".png")


@pytest.mark.parametrize("content", [b"\x89PNG\r\n\x1a\n truncated", b"%PDF-1.4 broken"], ids=["png", "pdf"])
def test_corrupt_upload_fails_the_job_not_the_server(client, content):
    job_id = client.post("/jobs", files={"files": ("x", content, "application/octet-stream")}).json()["id"]
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


DEMO_TOKEN = "dummy-demo-token-0123456789"
AUTH = {"Authorization": f"Bearer {DEMO_TOKEN}"}


@pytest.fixture
def demo_client(tmp_path, monkeypatch) -> TestClient:
    """The API as deployed for the demo: access token, CORS for one origin, rate limits on."""
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("CE_ALLOWED_ORIGINS", ALLOWED_ORIGIN)
    monkeypatch.setenv("CE_DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    monkeypatch.setenv("CE_REQUIRE_ACCESS_TOKEN", "1")
    return TestClient(create_app(Settings(), FakeProvider()))


def _demo_job(client: TestClient) -> str:
    created = client.post("/jobs", files={"files": ("scan.png", _png(), "image/png")}, headers=AUTH)
    assert created.status_code == 201
    return created.json()["id"]


def test_demo_cors_refuses_foreign_origin(demo_client):
    preflight = demo_client.options("/jobs", headers={"Origin": FOREIGN_ORIGIN, "Access-Control-Request-Method": "POST",
                                                      "Access-Control-Request-Headers": "authorization"})
    assert preflight.status_code == 400
    assert ALLOW_ORIGIN_HEADER not in preflight.headers
    # Even with the right token, a foreign page gets no CORS header, so the browser hides the answer.
    assert ALLOW_ORIGIN_HEADER not in demo_client.get("/senders", headers={**AUTH, "Origin": FOREIGN_ORIGIN}).headers


def test_demo_cors_allows_the_frontend_with_the_token_header(demo_client):
    preflight = demo_client.options("/jobs", headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST",
                                                      "Access-Control-Request-Headers": "authorization"})
    assert preflight.status_code == 200
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize("method, path", [
    ("GET", "/senders"),
    ("POST", "/jobs"),
    ("GET", "/jobs/{job}"),
    ("GET", "/jobs/{job}/events"),
    ("POST", "/jobs/{job}/export"),
    ("GET", "/jobs/{job}/crop/{group}/id_number"),
    ("GET", "/jobs/{job}/pages/{group}/0"),
    ("PATCH", "/jobs/{job}/commitments/{group}/fields/id_number"),
    ("DELETE", "/jobs/{job}"),
])
def test_demo_without_token_is_401(demo_client, method, path):
    job_id = _demo_job(demo_client)
    group_id = demo_client.get(f"/jobs/{job_id}", headers=AUTH).json()["groups"][0]["id"]
    url = path.format(job=job_id, group=group_id)
    for headers in ({}, {"Authorization": "Bearer wrong-token-0123456789"}):
        response = demo_client.request(method, url, headers={**headers, "Origin": ALLOWED_ORIGIN}, json={"value": "1"})
        assert response.status_code == 401
        # The refusal carries CORS headers, so the frontend can read the 401 and ask for the code again.
        assert response.headers[ALLOW_ORIGIN_HEADER] == ALLOWED_ORIGIN
    assert demo_client.get(f"/jobs/{job_id}", headers=AUTH).status_code == 200


def test_demo_token_in_query_for_event_stream_and_images(demo_client):
    job_id = _demo_job(demo_client)
    group_id = demo_client.get(f"/jobs/{job_id}", headers=AUTH).json()["groups"][0]["id"]
    events = demo_client.get(f"/jobs/{job_id}/events", params={"access_token": DEMO_TOKEN})
    assert events.status_code == 200
    assert _sse_events(events.text)[-1][0] == "end"
    page = demo_client.get(f"/jobs/{job_id}/pages/{group_id}/0", params={"access_token": DEMO_TOKEN})
    assert page.status_code == 200
    # Writes must send the header.
    assert demo_client.delete(f"/jobs/{job_id}", params={"access_token": DEMO_TOKEN}).status_code == 401


def test_demo_health_needs_no_token(demo_client):
    assert demo_client.get("/health").status_code == 200


def test_demo_upload_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("CE_UPLOAD_LIMIT_PER_HOUR", "1")
    client = TestClient(create_app(Settings(), FakeProvider()))
    assert client.post("/jobs", files={"files": ("scan.png", _png(), "image/png")}).status_code == 201
    refused = client.post("/jobs", files={"files": ("scan.png", _png(), "image/png")})
    assert refused.status_code == 429
    assert "retry-after" in refused.headers


def _pdf(pages: int) -> bytes:
    import pymupdf

    with pymupdf.open() as doc:
        for _ in range(pages):
            doc.new_page()
        return doc.tobytes()


def test_too_many_pages_per_file_and_per_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("CE_MAX_PAGES", "3")
    monkeypatch.setenv("CE_MAX_JOB_PAGES", "4")
    client = TestClient(create_app(Settings(), FakeProvider()))
    one_file = client.post("/jobs", files={"files": ("a.pdf", _pdf(4), "application/pdf")})
    assert (one_file.status_code, one_file.json()["detail"]) == (413, "too many pages")
    two_files = client.post("/jobs", files=[("files", ("a.pdf", _pdf(3), "application/pdf")),
                                            ("files", ("b.pdf", _pdf(2), "application/pdf"))])
    assert (two_files.status_code, two_files.json()["detail"]) == (413, "too many pages")
    assert list(tmp_path.iterdir()) == []  # nothing stored


def test_file_over_the_pixel_budget_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("CE_RENDER_DPI", "72")  # an A4 page is 595 x 842 = 500,990 pixels
    monkeypatch.setenv("CE_MAX_FILE_PIXELS", "1000000")
    client = TestClient(create_app(Settings(), FakeProvider()))
    refused = client.post("/jobs", files={"files": ("a.pdf", _pdf(2), "application/pdf")})
    assert (refused.status_code, refused.json()["detail"]) == (413, "file exceeds the memory budget")
    assert list(tmp_path.iterdir()) == []  # nothing stored
    assert client.post("/jobs", files={"files": ("a.pdf", _pdf(1), "application/pdf")}).status_code == 201


def test_request_body_over_the_upload_limits_is_413(client):
    # CE_MAX_UPLOAD_MB=1 and CE_MAX_FILES=20 (default): a body over ~21 MB is refused before it is read.
    response = client.post("/jobs", content=b"x" * (22 * 1024 * 1024), headers={"Content-Type": "multipart/form-data"})
    assert response.status_code == 413


def test_temp_files_cleared_at_startup_and_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    leftover = tmp_path / ("0" * 32)
    leftover.mkdir()
    with TestClient(create_app(Settings(), FakeProvider())) as client:
        assert not leftover.exists()
        job_id = client.post("/jobs", files={"files": ("scan.png", _png(), "image/png")}).json()["id"]
        assert (tmp_path / job_id).is_dir()
    assert not (tmp_path / job_id).exists()


def _sse_events(text: str) -> list[tuple[str, dict | list]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_events_stream_progress_rows_and_end(client):
    job_id = client.post("/jobs", files={"files": ("scan.png", _png(), "image/png")}).json()["id"]
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

    monkeypatch.setattr(ingest, "iter_pages", failing_load)
    with caplog.at_level(logging.DEBUG):
        job_id = client.post("/jobs", files={"files": ("scan.png", _png(), "image/png")}).json()["id"]
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
    created = client.post("/jobs", files={"files": ("scan.png", _png(), "image/png")})
    job = client.get(f"/jobs/{created.json()['id']}").json()
    assert job["status"] == "done"
    return job["id"]


def _field_url(job_id: str, group_id: str, field: str) -> str:
    return f"/jobs/{job_id}/commitments/{group_id}/fields/{field}"


def _crop_url(job_id: str, group_id: str, field: str) -> str:
    return f"/jobs/{job_id}/crop/{group_id}/{field}"


def test_patch_sets_a_manual_value_and_keeps_it(client, caplog):
    job_id = _done_job_id(client)
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]
    raw_before = client.get(f"/jobs/{job_id}").json()["groups"][0]["commitment"]["id_number"]["raw_value"]

    with caplog.at_level(logging.DEBUG):
        response = client.patch(_field_url(job_id, group_id, "id_number"), json={"value": DUMMY_ID_NUMBER})
    assert response.status_code == 200
    field = response.json()["groups"][0]["commitment"]["id_number"]
    assert (field["value"], field["source"], field["needs_review"], field["confidence"]) == (
        DUMMY_ID_NUMBER, "manual", False, 1.0)
    assert field["reasons"] == ["manual_correction"]
    assert field["raw_value"] == raw_before == fake.FAKE_ID_NUMBER  # the reading is kept
    assert DUMMY_ID_NUMBER not in caplog.text and fake.FAKE_ID_NUMBER not in caplog.text

    stored = client.get(f"/jobs/{job_id}").json()
    assert stored["groups"][0]["commitment"]["id_number"] == field
    assert [(c["group_id"], c["field"], c["item"]) for c in stored["corrections"]] == [(group_id, "id_number", None)]


def test_senders_are_the_canonical_names(client):
    from app.pipeline.normalize import load_senders

    names = client.get("/senders").json()
    assert names == sorted(sender.canonical for sender in load_senders(Settings().senders_path))
    assert fake.FAKE_SENDER in names


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
    response = client.get(_crop_url(job_id, group_id, "id_number"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    # The box plus 10 px padding: x 0..70 clipped to the 60 px wide page, y 0..50.
    assert Image.open(io.BytesIO(response.content)).size == (60, 50)


def test_crop_after_the_page_image_is_gone_is_a_clear_404(client, tmp_path):
    job_id = _done_job_id(client)
    group_id = _with_box(client, job_id)
    shutil.rmtree(tmp_path / job_id / PAGES_DIR)  # as the file TTL or a restart on an ephemeral disk would
    response = client.get(_crop_url(job_id, group_id, "id_number"))
    assert (response.status_code, response.json()["detail"]) == (404, IMAGE_GONE)
    assert client.get(f"/jobs/{job_id}").status_code == 200  # the job itself remains


def test_crop_without_a_box_or_field(client):
    job_id = _done_job_id(client)
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]
    assert client.get(_crop_url(job_id, group_id, "id_number")).json()["detail"] == NO_BOX
    assert client.get(_crop_url(job_id, group_id, "nope")).json()["detail"] == FIELD_NOT_FOUND
    assert client.get(_crop_url(job_id, "0" * 32, "id_number")).status_code == 404


def _page_url(job_id: str, group_id: str, page: int) -> str:
    return f"/jobs/{job_id}/pages/{group_id}/{page}"


def test_page_of_a_form_is_the_stored_jpeg(client, tmp_path):
    job_id = _done_job_id(client)
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]
    response = client.get(_page_url(job_id, group_id, 0))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert Image.open(io.BytesIO(response.content)).size == (60, 80)  # full resolution, the bbox coordinates


def test_page_outside_the_form_or_gone_is_a_clear_404(client, tmp_path):
    job_id = _done_job_id(client)
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]
    response = client.get(_page_url(job_id, group_id, 1))
    assert (response.status_code, response.json()["detail"]) == (404, PAGE_NOT_IN_FORM)
    shutil.rmtree(tmp_path / job_id / PAGES_DIR)
    response = client.get(_page_url(job_id, group_id, 0))
    assert (response.status_code, response.json()["detail"]) == (404, IMAGE_GONE)


UNKNOWN_ID = "0" * 32


@pytest.mark.parametrize(("method", "path"), [
    ("get", "/jobs/{job}"),
    ("get", "/jobs/{job}/events"),
    ("post", "/jobs/{job}/export"),
    ("patch", "/jobs/{job}/commitments/{cid}/fields/id_number"),
    ("get", "/jobs/{job}/crop/{cid}/id_number"),
    ("get", "/jobs/{job}/pages/{cid}/0"),
    ("delete", "/jobs/{job}"),
])
def test_every_job_endpoint_is_404_for_an_unknown_job(client, method, path):
    url = path.format(job=UNKNOWN_ID, cid=UNKNOWN_ID)
    kwargs = {"json": {"value": "1"}} if method == "patch" else {}
    response = client.request(method.upper(), url, **kwargs)
    assert (response.status_code, response.json()["detail"]) == (404, "job not found")


@pytest.mark.parametrize(("method", "path"), [
    ("patch", "/jobs/{job}/commitments/{cid}/fields/id_number"),
    ("get", "/jobs/{job}/crop/{cid}/id_number"),
    ("get", "/jobs/{job}/pages/{cid}/0"),
])
def test_every_commitment_endpoint_is_404_for_an_unknown_commitment(client, method, path):
    job_id = _done_job_id(client)
    kwargs = {"json": {"value": "1"}} if method == "patch" else {}
    response = client.request(method.upper(), path.format(job=job_id, cid=UNKNOWN_ID), **kwargs)
    assert (response.status_code, response.json()["detail"]) == (404, "commitment not found")


# --- Several files per job, a form that fails ------------------------------------

def _png_upload(name: str) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, _png(), "image/png"))


def test_several_files_make_one_job(client):
    created = client.post("/jobs", files=[_png_upload("a.png"), _png_upload("b.png")])
    assert created.status_code == 201
    job = client.get(f"/jobs/{created.json()['id']}").json()
    assert job["status"] == "done" and len(job["files"]) == 2
    assert [group["source_file"] for group in job["groups"]] == job["files"]  # file order is kept


def test_a_refused_file_refuses_the_whole_upload(client, tmp_path):
    response = client.post("/jobs", files=[_png_upload("a.png"), ("files", ("b.pdf", b"not a pdf", "application/pdf"))])
    assert response.status_code == 415
    assert list(tmp_path.iterdir()) == []  # nothing stored, not even the good file


def test_too_many_files_is_413(tmp_path, monkeypatch):
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("CE_MAX_FILES", "1")
    client = TestClient(create_app(Settings(), FakeProvider()))
    response = client.post("/jobs", files=[_png_upload("a.png"), _png_upload("b.png")])
    assert (response.status_code, response.json()["detail"]) == (413, "too many files")
    assert list(tmp_path.iterdir()) == []


def test_upload_without_files_is_422(client):
    assert client.post("/jobs").status_code == 422


class FirstFormFailsProvider(FakeProvider):
    """Raises on the first form's extraction, with a dummy value in the message."""

    SECRET = "000000034"

    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def extract_form(self, pages, form_type):
        with self._lock:
            fail, self._failed = not self._failed, True
        if fail:
            raise ValueError(f"cannot read {self.SECRET}")
        return super().extract_form(pages, form_type)


def test_one_failed_form_leaves_the_others_usable(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path))
    client = TestClient(create_app(Settings(), FirstFormFailsProvider()))
    with caplog.at_level(logging.DEBUG):
        job_id = client.post("/jobs", files=[_png_upload("a.png"), _png_upload("b.png")]).json()["id"]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done" and job["error"] is None
    failed, read = job["groups"]
    assert failed["error"] == "ValueError" and "extraction_failed" in failed["form_reasons"]
    assert failed["commitment"]["id_number"]["value"] is None and failed["finished"]
    assert read["error"] is None and read["commitment"]["id_number"]["value"] == fake.FAKE_ID_NUMBER

    events = _sse_events(client.get(f"/jobs/{job_id}/events").text)
    assert [name for name, _ in events] == ["progress", "rows", "end"]
    assert {row["id"] for row in events[1][1]} == {failed["id"], read["id"]}

    assert client.patch(_field_url(job_id, failed["id"], "id_number"), json={"value": DUMMY_ID_NUMBER}).status_code == 200
    assert client.post(f"/jobs/{job_id}/export").status_code == 200
    assert FirstFormFailsProvider.SECRET not in caplog.text and DUMMY_ID_NUMBER not in caplog.text


def test_delete_removes_the_job_and_its_files(client, tmp_path):
    job_id = _done_job_id(client)
    other_id = _done_job_id(client)
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]
    client.patch(_field_url(job_id, group_id, "id_number"), json={"value": DUMMY_ID_NUMBER})
    assert client.post(f"/jobs/{job_id}/export").status_code == 200
    job_dir = tmp_path / job_id
    assert (job_dir / PAGES_DIR).is_dir() and any(job_dir.glob("*.png")) and any(job_dir.glob("*.xlsx"))

    assert client.delete(f"/jobs/{job_id}").status_code == 204

    assert not job_dir.exists()  # upload, page images and export
    store = client.app.state.jobs
    assert store.get(job_id) is None and store.list_commitments(job_id) == []
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.get(f"/jobs/{job_id}/events").status_code == 404
    assert client.get(_page_url(job_id, group_id, 0)).status_code == 404
    assert client.delete(f"/jobs/{job_id}").status_code == 404
    # Another job is untouched.
    assert client.get(f"/jobs/{other_id}").status_code == 200 and (tmp_path / other_id / PAGES_DIR).is_dir()


def test_store_failure_is_503_and_logs_the_type_only(client, monkeypatch, caplog):
    job_id = _done_job_id(client)

    def broken(_job_id):
        raise JobStoreError("OperationalError")

    monkeypatch.setattr(client.app.state.jobs, "get", broken)
    with caplog.at_level(logging.INFO):
        response = client.get(f"/jobs/{job_id}")
    assert (response.status_code, response.json()["detail"]) == (503, "job store unavailable")
    assert [r.getMessage() for r in caplog.records if r.name == "app.main"] == ["job store failed: OperationalError"]


def test_lab_match_with_fake_provider_and_demo_lab(client, caplog):
    job_id = _done_job_id(client)
    with caplog.at_level(logging.INFO):
        response = client.get(f"/jobs/{job_id}/lab-match")
    assert response.status_code == 200
    result = response.json()
    group_id = client.get(f"/jobs/{job_id}").json()["groups"][0]["id"]
    assert [(m["commitment_id"], m["central_number"], m["date_differs"]) for m in result["matched"]] == [
        (group_id, fake.FAKE_CENTRAL_NUMBER, False)]
    # Only samples near the job's received date (01.01.2025) are asked for; the March ones are out of range.
    assert sorted(s["central_number"] for s in result["samples_without_commitment"]) == ["345678", "456789", "567890"]
    assert result["commitments_without_sample"] == []
    assert fake.FAKE_CENTRAL_NUMBER not in caplog.text  # counts only in the log
    assert client.get(f"/jobs/{'0' * 32}/lab-match").status_code == 404
