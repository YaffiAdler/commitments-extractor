"""One contract suite for every JobStore implementation (memory, SQLite and,
with ``TEST_DATABASE_URL``, Postgres)."""
import os
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import DATABASE_URL_ENV, Settings, StorageName
from app.schemas import (
    MANUAL_CORRECTION,
    BBox,
    ClassificationResult,
    Commitment,
    Correction,
    Field,
    FieldSignal,
    FieldSource,
    FormGroup,
    IrrelevantPage,
    Job,
    JobStatus,
    SenderCandidate,
    SignalSource,
)
from app.storage import create_job_store
from app.storage.base import INTERRUPTED_ERROR, JobExistsError, JobStore
from app.storage.memory import MemoryJobStore
from app.storage.sqlite import SqliteJobStore, connect
from app.storage.tmp import TempStorage
from conftest import store_params

TTL_S = 60
JOB_TABLES = ("jobs", "job_files", "commitments", "corrections")


def _ok(value):
    return Field(value=value, raw_value=str(value), confidence=1.0, needs_review=False, source=FieldSource.PRINTED)


def _commitment(complete: bool) -> Commitment:
    commitment = Commitment(
        form_type_id="clalit_form17",
        first_name=_ok("יוסף"),
        last_name=_ok("כהן"),
        id_number=_ok("000000018"),
        central_numbers=[_ok("0012345")],
        received_date=_ok(date(2026, 1, 4)),
        sender=_ok("כללית"),
        commitment_numbers=[_ok("0098765"), _ok("0098766")],
    )
    if not complete:
        commitment.id_number = Field.missing()
    return commitment


def _done_job(*complete: bool, created_at: datetime | None = None) -> Job:
    job = Job(status=JobStatus.DONE, files=["a" * 32 + ".pdf"])
    if created_at is not None:
        job.created_at = created_at
    job.groups = [
        FormGroup(source_file=job.files[0], pages=[seq], rotation=90, commitment=_commitment(flag))
        for seq, flag in enumerate(complete)
    ]
    return job


def _full_job() -> Job:
    """A job with every kind of data the pipeline produces (dummy values)."""
    now = datetime.now(timezone.utc)
    job = _done_job(True, False)
    job.files.append("b" * 32 + ".png")
    job.started_at, job.finished_at = now, now + timedelta(seconds=3)
    job.irrelevant_pages = [IrrelevantPage(source_file=job.files[1], page=2)]
    job.split_vlm_calls = 2
    first, second = job.groups
    first.split_confidence, first.split_reasons = 0.7, ["split_uncertain", "vlm_same_form"]
    first.form_reasons = ["central_duplicate"]
    first.vlm_calls, first.read_ms, first.finished = 3, 1234, True
    first.classification = ClassificationResult(
        form_type_id="clalit_form17", known=True, sender_raw="שירותי בריאות כללית", sender_status="matched",
        sender_score=95.0, sender_candidates=[SenderCandidate(name="כללית", score=95.0),
                                              SenderCandidate(name="מכבי", score=40.0)])
    first.commitment.id_number = Field(
        value="000000018", raw_value="00000001 8", confidence=0.65, needs_review=True,
        reasons=["ocr_disagrees", "handwritten"], explanations=["טקסט"], source=FieldSource.HANDWRITTEN,
        bbox=BBox(page=0, x0=10, y0=20, x1=110, y1=60),
        signals=[FieldSignal(source=SignalSource.OCR, value="000000019", confidence=0.8),
                 FieldSignal(source=SignalSource.REREAD, value="000000018", legible=True)])
    second.error, second.form_reasons = "TimeoutError", ["extraction_failed"]
    job.corrections = [Correction(group_id=first.id, field="central_numbers", item=0,
                                  previous_raw_value="0012345", previous_source=FieldSource.STICKER)]
    return job


@dataclass
class StoreEnv:
    store: JobStore
    storage: TempStorage
    # A new store instance on the same database (a server restart); None for memory.
    reopen: Callable[[], JobStore] | None
    # Rows of a job per table; None for memory.
    count_rows: Callable[[str], dict[str, int]] | None


def _sqlite_counts(db_path):
    def count(job_id: str) -> dict[str, int]:
        with closing(connect(db_path)) as connection:
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table} WHERE "
                                              f"{'id' if table == 'jobs' else 'job_id'} = ?", (job_id,)).fetchone()[0]
                    for table in JOB_TABLES}
    return count


def _postgres_counts(conninfo):
    import psycopg

    def count(job_id: str) -> dict[str, int]:
        with psycopg.connect(conninfo) as connection:
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table} WHERE "
                                              f"{'id' if table == 'jobs' else 'job_id'} = %s", (job_id,)).fetchone()[0]
                    for table in JOB_TABLES}
    return count


@pytest.fixture(params=store_params())
def env(request, tmp_path) -> StoreEnv:
    storage = TempStorage(tmp_path / "tmp")
    if request.param == StorageName.MEMORY.value:
        env = StoreEnv(MemoryJobStore(storage, TTL_S), storage, None, None)
    elif request.param == StorageName.SQLITE.value:
        db_path = tmp_path / "app.db"
        env = StoreEnv(SqliteJobStore(db_path, storage, TTL_S), storage,
                       lambda: SqliteJobStore(db_path, storage, TTL_S), _sqlite_counts(db_path))
    else:
        from app.storage.postgres import PostgresJobStore

        conninfo = request.getfixturevalue("pg_conninfo")

        def make() -> PostgresJobStore:
            return PostgresJobStore(conninfo, storage, TTL_S, pool_size=2, timeout_s=10)

        env = StoreEnv(make(), storage, make, _postgres_counts(conninfo))
    env.store.init()
    return env


def _persistent(env: StoreEnv) -> None:
    if env.reopen is None:
        pytest.skip("the memory store keeps nothing across instances")


def test_implements_protocol(env):
    assert isinstance(env.store, JobStore)


def test_create_get_roundtrip(env):
    job = _done_job(True, False)
    env.store.create(job)
    assert env.store.get(job.id) == job


def test_full_job_roundtrip(env):
    job = _full_job()
    env.store.create(job)
    assert env.store.get(job.id) == job


def test_get_unknown_is_none(env):
    assert env.store.get("0" * 32) is None


def test_create_twice_is_rejected(env):
    job = Job()
    env.store.create(job)
    with pytest.raises(JobExistsError):
        env.store.create(job)
    assert env.store.get(job.id) == job


def test_get_returns_a_copy(env):
    job = Job()
    env.store.create(job)
    job.status = JobStatus.DONE
    loaded = env.store.get(job.id)
    assert loaded.status is JobStatus.PENDING
    loaded.status = JobStatus.FAILED
    assert env.store.get(job.id).status is JobStatus.PENDING


def test_update_replaces_job_and_groups(env):
    job = _done_job(True, True)
    env.store.create(job)
    job.groups = job.groups[:1]
    job.status, job.error = JobStatus.FAILED, "CorruptFileError"
    assert env.store.update(job)
    assert env.store.get(job.id) == job


def test_manual_correction_persists(env):
    job = _full_job()
    env.store.create(job)
    group = job.groups[0]
    group.commitment.id_number = Field(value="000000026", raw_value="000000026", confidence=1.0, needs_review=False,
                                       reasons=[MANUAL_CORRECTION], source=FieldSource.MANUAL)
    job.corrections.append(Correction(group_id=group.id, field="id_number", previous_raw_value="00000001 8",
                                      previous_source=FieldSource.HANDWRITTEN))
    assert env.store.update(job)
    loaded = env.store.get(job.id)
    assert loaded == job
    assert loaded.groups[0].commitment.id_number.source is FieldSource.MANUAL
    assert [c.field for c in loaded.corrections] == ["central_numbers", "id_number"]


def test_update_unknown_returns_false(env):
    assert not env.store.update(Job())


def test_delete_removes_job_and_files(env):
    job = _full_job()
    env.storage.save_upload(job.id, b"%PDF-")
    env.store.create(job)
    assert env.store.delete(job.id)
    assert env.store.get(job.id) is None
    assert env.store.list_commitments(job.id) == []
    assert not env.storage.job_dir(job.id).exists()
    assert not env.store.delete(job.id)


def test_delete_removes_every_row(env):
    if env.count_rows is None:
        pytest.skip("the memory store has no tables")
    job, other = _full_job(), _full_job()
    env.store.create(job)
    env.store.create(other)
    assert all(env.count_rows(job.id).values())
    env.store.delete(job.id)
    assert env.count_rows(job.id) == dict.fromkeys(JOB_TABLES, 0)
    assert env.store.get(other.id) == other


def test_job_survives_restart(env):
    _persistent(env)
    job = _full_job()
    env.store.create(job)
    env.store.close()
    reopened = env.reopen()
    reopened.init()
    assert reopened.get(job.id) == job
    assert reopened.list_commitments(job.id) == [g.commitment for g in job.groups]
    reopened.close()


def test_list_commitments_order_and_filters(env):
    store = env.store
    now = datetime.now(timezone.utc)
    older = _done_job(True, False, created_at=now - timedelta(seconds=2))
    newer = _done_job(False, created_at=now - timedelta(seconds=1))
    store.create(newer)
    store.create(older)
    assert store.list_commitments() == [g.commitment for g in older.groups + newer.groups]
    assert store.list_commitments(job_id=newer.id) == [newer.groups[0].commitment]
    assert store.list_commitments(needs_review=False) == [older.groups[0].commitment]
    assert store.list_commitments(needs_review=True) == [older.groups[1].commitment, newer.groups[0].commitment]
    assert store.list_commitments(job_id=older.id, needs_review=True) == [older.groups[1].commitment]


def test_expired_jobs_are_purged(env):
    job = Job()
    env.storage.save_upload(job.id, b"%PDF-")
    env.store.create(job)
    env.store.purge_expired(now=datetime.now(timezone.utc) + timedelta(seconds=TTL_S + 1))
    assert env.store.get(job.id) is None
    assert not env.storage.job_dir(job.id).exists()


def test_fail_interrupted_marks_unfinished_jobs_only(env):
    store = env.store
    pending, running, done = Job(), Job(status=JobStatus.RUNNING), _done_job(True)
    for job in (pending, running, done):
        store.create(job)
    assert store.fail_interrupted() == 2
    for job in (pending, running):
        loaded = store.get(job.id)
        assert (loaded.status, loaded.error) == (JobStatus.FAILED, INTERRUPTED_ERROR)
    assert store.get(done.id) == done
    assert store.fail_interrupted() == 0


def test_init_is_idempotent(env):
    job = _full_job()
    env.store.create(job)
    env.store.init()
    env.store.init()
    assert env.store.get(job.id) == job


def test_files_are_kept_apart_from_the_job_ttl(tmp_path):
    """A long job TTL (database rows) does not keep uploads longer than the file TTL."""
    storage = TempStorage(tmp_path)
    store = MemoryJobStore(storage, ttl_s=10 * TTL_S, file_ttl_s=TTL_S)
    job = Job()
    storage.save_upload(job.id, b"%PDF-")
    store.create(job)
    old = datetime.now(timezone.utc).timestamp() - TTL_S - 1
    os.utime(storage.job_dir(job.id), (old, old))
    store.purge_expired()
    assert store.get(job.id) == job
    assert not storage.job_dir(job.id).exists()


@pytest.mark.parametrize(("name", "cls"), [("sqlite", SqliteJobStore), ("memory", MemoryJobStore)])
def test_factory_follows_setting(monkeypatch, tmp_path, name, cls):
    monkeypatch.setenv("CE_STORAGE", name)
    assert isinstance(create_job_store(Settings(), TempStorage(tmp_path)), cls)


def test_factory_picks_postgres_from_database_url(monkeypatch, tmp_path):
    from app.storage.postgres import PostgresJobStore

    monkeypatch.delenv("CE_STORAGE")
    # Never connected: the pool opens on first use.
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://user@localhost:1/none")
    store = create_job_store(Settings(), TempStorage(tmp_path))
    assert isinstance(store, PostgresJobStore)
    store.close()
