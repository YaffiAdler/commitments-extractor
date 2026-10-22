"""SQLite-specific behaviour: real persistence, schema versions, migration of
older databases, cascade, hidden driver errors and restart through the API."""
import sqlite3
from contextlib import closing

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import fake
from app.providers.fake import FakeProvider
from app.schemas import FormGroup, Job, JobStatus
from app.storage.base import INTERRUPTED_ERROR, JobStoreError
from app.storage.sql import load_migrations
from app.storage.sqlite import SqliteJobStore, connect
from app.storage.tmp import TempStorage

TTL_S = 3600
# The schema of version 1 as it was before schema versions existed (old schema.sql).
VERSION_1_JOBS = ("CREATE TABLE jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,"
                  " source_file TEXT, form_type TEXT, sender TEXT, error TEXT, needs_review INTEGER NOT NULL DEFAULT 0")
VERSION_1_COMMITMENTS = ("CREATE TABLE commitments (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs (id)"
                         " ON DELETE CASCADE, seq INTEGER NOT NULL, data_json TEXT NOT NULL,"
                         " needs_review INTEGER NOT NULL, UNIQUE (job_id, seq))")


def _job() -> Job:
    job = Job(status=JobStatus.DONE, files=["b" * 32 + ".png"])
    job.groups = [FormGroup(source_file=job.files[0], pages=[0])]
    return job


def _store(tmp_path, name: str = "app.db") -> SqliteJobStore:
    return SqliteJobStore(tmp_path / name, TempStorage(tmp_path), TTL_S)


def _tables(db_path) -> set[str]:
    with closing(connect(db_path)) as connection:
        return {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _versions(db_path) -> list[int]:
    with closing(connect(db_path)) as connection:
        return [v for (v,) in connection.execute("SELECT version FROM schema_version ORDER BY version")]


def test_persists_across_connections(tmp_path):
    job = _job()
    writer = _store(tmp_path)
    writer.init()
    writer.create(job)
    del writer  # every operation closes its own connection; nothing stays open

    reader = _store(tmp_path)
    assert reader.get(job.id) == job
    assert reader.list_commitments(job.id) == [job.groups[0].commitment]


def test_init_creates_file_tables_and_versions(tmp_path):
    db_path = tmp_path / "nested" / "app.db"
    store = SqliteJobStore(db_path, TempStorage(tmp_path), TTL_S)
    store.init()
    store.init()
    assert {"jobs", "job_files", "commitments", "corrections", "schema_version"} <= _tables(db_path)
    assert _versions(db_path) == [m.version for m in load_migrations()]
    with closing(connect(db_path)) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)


def test_migrates_a_version_1_database_without_losing_jobs(tmp_path):
    """A file made by the old schema.sql (no schema_version table) keeps its
    jobs; the single ``source_file`` column moves to ``job_files``."""
    db_path = tmp_path / "app.db"
    job = _job()
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(VERSION_1_JOBS + ", irrelevant_pages_json TEXT NOT NULL DEFAULT '[]',"
                                            " split_vlm_calls INTEGER NOT NULL DEFAULT 0)")
        connection.execute(VERSION_1_COMMITMENTS)
        connection.execute("INSERT INTO jobs (id, created_at, status, source_file) VALUES (?, ?, ?, ?)",
                           (job.id, job.created_at.isoformat(timespec="microseconds"), "done", job.files[0]))
        connection.execute("INSERT INTO commitments VALUES (?, ?, 0, ?, 1)",
                           (job.groups[0].id, job.id, job.groups[0].model_dump_json()))
    store = SqliteJobStore(db_path, TempStorage(tmp_path), TTL_S)
    store.init()
    assert store.get(job.id) == job
    with closing(connect(db_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    assert "source_file" not in columns and {"started_at", "finished_at"} <= columns


def test_init_adds_columns_to_an_older_database(tmp_path):
    db_path = tmp_path / "app.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(VERSION_1_JOBS + ")")
    store = SqliteJobStore(db_path, TempStorage(tmp_path), TTL_S)
    store.init()
    job = _job()
    store.create(job)
    assert store.get(job.id) == job


def test_failed_migration_is_rolled_back(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    migrations = load_migrations()
    broken = [*migrations[:1], type(migrations[1])(2, ("CREATE TABLE half_done (x INTEGER)", "NOT SQL"))]
    monkeypatch.setattr("app.storage.sql.load_migrations", lambda: broken)
    with pytest.raises(JobStoreError):
        SqliteJobStore(db_path, TempStorage(tmp_path), TTL_S).init()
    assert "half_done" not in _tables(db_path) and "jobs" not in _tables(db_path)


def test_filter_columns_and_cascade(tmp_path):
    db_path = tmp_path / "app.db"
    store = _store(tmp_path)
    job = _job()
    store.create(job)
    with closing(connect(db_path)) as connection:
        row = connection.execute("SELECT status, needs_review, error FROM jobs").fetchone()
        assert row == ("done", 1, None)
        assert connection.execute("SELECT stored_name FROM job_files").fetchall() == [(job.files[0],)]
        assert connection.execute("SELECT COUNT(*) FROM commitments").fetchone() == (1,)
        with connection:
            connection.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
        assert connection.execute("SELECT COUNT(*) FROM commitments").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM job_files").fetchone() == (0,)


def test_rejects_orphan_commitment(tmp_path):
    store = _store(tmp_path)
    store.init()
    with closing(connect(tmp_path / "app.db")) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO commitments VALUES ('x', 'missing-job', 0, '{}', 0)")


def test_driver_error_is_hidden_behind_its_type(tmp_path):
    """A database that cannot be opened: the error carries the driver's type only."""
    (tmp_path / "is_a_directory.db").mkdir()
    store = SqliteJobStore(tmp_path / "is_a_directory.db", TempStorage(tmp_path), TTL_S)
    with pytest.raises(JobStoreError) as raised:
        store.init()
    assert str(raised.value) == "OperationalError"
    assert raised.value.__suppress_context__


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
