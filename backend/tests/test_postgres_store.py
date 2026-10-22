"""Postgres-specific behaviour. Needs ``TEST_DATABASE_URL`` (skipped otherwise);
each test works in its own schema (``pg_conninfo``)."""
import logging
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from app.schemas import Field, FormGroup, Job, JobStatus
from app.storage.base import JobExistsError, JobStoreError
from app.storage.postgres import PostgresJobStore
from app.storage.sql import load_migrations
from app.storage.tmp import TempStorage

TTL_S = 3600
DUMMY_ID = "000000018"
# A password that must never surface in an error or a log line.
SECRET = "dummy-secret-password"


def _store(conninfo: str, tmp_path) -> PostgresJobStore:
    return PostgresJobStore(conninfo, TempStorage(tmp_path), TTL_S, pool_size=2, timeout_s=5)


def test_init_records_every_version_once(pg_conninfo, tmp_path):
    store = _store(pg_conninfo, tmp_path)
    store.init()
    store.init()
    with psycopg.connect(pg_conninfo) as connection:
        versions = [v for (v,) in connection.execute("SELECT version FROM schema_version ORDER BY version")]
    assert versions == [m.version for m in load_migrations()]


def test_concurrent_startups_migrate_once(pg_conninfo, tmp_path):
    stores = [_store(pg_conninfo, tmp_path) for _ in range(4)]
    with ThreadPoolExecutor(len(stores)) as pool:
        list(pool.map(lambda store: store.init(), stores))
    with psycopg.connect(pg_conninfo) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == len(load_migrations())


def test_duplicate_create_leaves_the_stored_job(pg_conninfo, tmp_path):
    store = _store(pg_conninfo, tmp_path)
    job = Job(status=JobStatus.DONE, files=["c" * 32 + ".pdf"])
    job.groups = [FormGroup(source_file=job.files[0], pages=[0])]
    store.create(job)
    with pytest.raises(JobExistsError):
        store.create(job)
    assert store.get(job.id) == job


def test_unreachable_database_is_reported_by_type_only(tmp_path, caplog):
    # Port 1 on localhost: refused at once, no server needed.
    store = PostgresJobStore(f"postgresql://ce:{SECRET}@127.0.0.1:1/none", TempStorage(tmp_path), TTL_S,
                             pool_size=1, timeout_s=1)
    with caplog.at_level(logging.DEBUG), pytest.raises(JobStoreError) as raised:
        store.init()
    store.close()
    assert str(raised.value) in {"OperationalError", "PoolTimeout"}
    assert raised.value.__suppress_context__
    assert SECRET not in caplog.text


def test_failed_query_hides_values(pg_conninfo, tmp_path, monkeypatch):
    """A statement that fails on a field value: the error names the driver type
    only, never the value or the query parameters."""
    store = _store(pg_conninfo, tmp_path)
    store.init()
    job = Job(status=JobStatus.DONE)
    job.groups = [FormGroup(source_file="d" * 32 + ".pdf", pages=[0])]
    job.groups[0].commitment.id_number = Field(value=DUMMY_ID, raw_value=DUMMY_ID)
    monkeypatch.setattr("app.storage.sql.JOB_COLUMNS", ("id", "no_such_column"))
    monkeypatch.setattr("app.storage.sql._job_row", lambda j: (j.id, DUMMY_ID))
    with pytest.raises(JobStoreError) as raised:
        store.create(job)
    assert str(raised.value) == "UndefinedColumn"
    assert DUMMY_ID not in str(raised.value)
