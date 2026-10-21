"""SQLite-specific behaviour: real persistence, schema, cascade and restart."""
import sqlite3
from contextlib import closing

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import fake
from app.providers.fake import FakeProvider
from app.schemas import SPLIT_UNCERTAIN, FormGroup, IrrelevantPage, Job, JobStatus
from app.storage.base import INTERRUPTED_ERROR
from app.storage.sqlite import SqliteJobStore, connect, init_db
from app.storage.tmp import TempStorage

TTL_S = 3600


def _job() -> Job:
    job = Job(status=JobStatus.DONE, files=["b" * 32 + ".png"])
    job.groups = [FormGroup(source_file=job.files[0], pages=[0])]
    return job


def test_persists_across_connections(tmp_path):
    db_path = tmp_path / "app.db"
    job = _job()
    writer = SqliteJobStore(db_path, TempStorage(tmp_path), TTL_S)
    writer.init()
    writer.create(job)
    del writer  # every operation closes its own connection; nothing stays open

    reader = SqliteJobStore(db_path, TempStorage(tmp_path), TTL_S)
    assert reader.get(job.id) == job
    assert reader.list_commitments(job.id) == [job.groups[0].commitment]


def test_init_db_creates_file_and_tables(tmp_path):
    db_path = tmp_path / "nested" / "app.db"
    init_db(db_path)
    init_db(db_path)
    with closing(connect(db_path)) as connection:
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"jobs", "commitments"} <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_split_results_persist(tmp_path):
    job = _job()
    job.groups[0].split_reasons = [SPLIT_UNCERTAIN]
    job.irrelevant_pages = [IrrelevantPage(source_file=job.files[0], page=1)]
    job.split_vlm_calls = 3
    store = SqliteJobStore(tmp_path / "app.db", TempStorage(tmp_path), TTL_S)
    store.create(job)
    assert store.get(job.id) == job


def test_init_adds_columns_to_an_older_database(tmp_path):
    db_path = tmp_path / "app.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,"
                           " source_file TEXT, form_type TEXT, sender TEXT, error TEXT,"
                           " needs_review INTEGER NOT NULL DEFAULT 0)")
        connection.commit()
    store = SqliteJobStore(db_path, TempStorage(tmp_path), TTL_S)
    store.init()
    job = _job()
    store.create(job)
    assert store.get(job.id) == job


def test_filter_columns_and_cascade(tmp_path):
    db_path = tmp_path / "app.db"
    store = SqliteJobStore(db_path, TempStorage(tmp_path), TTL_S)
    job = _job()
    store.create(job)
    with closing(connect(db_path)) as connection:
        row = connection.execute("SELECT status, source_file, needs_review, error FROM jobs").fetchone()
        assert row == ("done", job.files[0], 1, None)
        assert connection.execute("SELECT COUNT(*) FROM commitments").fetchone() == (1,)
        with connection:
            connection.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
        assert connection.execute("SELECT COUNT(*) FROM commitments").fetchone() == (0,)


def test_rejects_orphan_commitment(tmp_path):
    db_path = tmp_path / "app.db"
    init_db(db_path)
    with closing(connect(db_path)) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO commitments VALUES ('x', 'missing-job', 0, '{}', 0)")


def test_rejects_job_with_several_files(tmp_path):
    store = SqliteJobStore(tmp_path / "app.db", TempStorage(tmp_path), TTL_S)
    with pytest.raises(ValueError):
        store.create(Job(files=["a.pdf", "b.pdf"]))


def _use_sqlite_app(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CE_STORAGE", "sqlite")
    monkeypatch.setenv("CE_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CE_TMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("CE_RENDER_DPI", "72")


def test_job_cut_off_by_restart_is_failed_at_startup(tmp_path, monkeypatch):
    _use_sqlite_app(tmp_path, monkeypatch)
    job = Job(status=JobStatus.RUNNING)
    SqliteJobStore(tmp_path / "app.db", TempStorage(tmp_path / "tmp"), TTL_S).create(job)

    with TestClient(create_app(Settings(), FakeProvider())) as client:
        loaded = client.get(f"/jobs/{job.id}").json()
    assert (loaded["status"], loaded["error"]) == ("failed", INTERRUPTED_ERROR)


def test_job_survives_app_restart(tmp_path, monkeypatch):
    _use_sqlite_app(tmp_path, monkeypatch)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "dummy form")
        pdf = doc.tobytes()

    with TestClient(create_app(Settings(), FakeProvider())) as first:
        job_id = first.post("/jobs", files={"file": ("form.pdf", pdf, "application/pdf")}).json()["id"]
        before = first.get(f"/jobs/{job_id}").json()
    assert (tmp_path / "app.db").is_file()

    with TestClient(create_app(Settings(), FakeProvider())) as second:
        after = second.get(f"/jobs/{job_id}").json()
        assert after == before
        assert after["groups"][0]["commitment"]["id_number"]["value"] == fake.FAKE_ID_NUMBER
        assert second.post(f"/jobs/{job_id}/export").status_code == 200
