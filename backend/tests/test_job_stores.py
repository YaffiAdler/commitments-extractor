"""One contract suite for every JobStore implementation."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import Settings, StorageName
from app.schemas import Commitment, Field, FieldSource, FormGroup, Job, JobStatus
from app.storage import create_job_store
from app.storage.base import INTERRUPTED_ERROR, JobExistsError, JobStore
from app.storage.memory import MemoryJobStore
from app.storage.sqlite import SqliteJobStore
from app.storage.tmp import TempStorage

TTL_S = 60


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


@pytest.fixture(params=[name.value for name in StorageName])
def env(request, tmp_path):
    storage = TempStorage(tmp_path / "tmp")
    if request.param == StorageName.SQLITE.value:
        store = SqliteJobStore(tmp_path / "app.db", storage, TTL_S)
    else:
        store = MemoryJobStore(storage, TTL_S)
    store.init()
    return store, storage


def test_implements_protocol(env):
    store, _ = env
    assert isinstance(store, JobStore)


def test_create_get_roundtrip(env):
    store, _ = env
    job = _done_job(True, False)
    store.create(job)
    assert store.get(job.id) == job


def test_get_unknown_is_none(env):
    store, _ = env
    assert store.get("0" * 32) is None


def test_create_twice_is_rejected(env):
    store, _ = env
    job = Job()
    store.create(job)
    with pytest.raises(JobExistsError):
        store.create(job)


def test_get_returns_a_copy(env):
    store, _ = env
    job = Job()
    store.create(job)
    job.status = JobStatus.DONE
    loaded = store.get(job.id)
    assert loaded.status is JobStatus.PENDING
    loaded.status = JobStatus.FAILED
    assert store.get(job.id).status is JobStatus.PENDING


def test_update_replaces_job_and_groups(env):
    store, _ = env
    job = _done_job(True, True)
    store.create(job)
    job.groups = job.groups[:1]
    job.status, job.error = JobStatus.FAILED, "CorruptFileError"
    assert store.update(job)
    assert store.get(job.id) == job


def test_update_unknown_returns_false(env):
    store, _ = env
    assert not store.update(Job())


def test_delete_removes_job_and_files(env):
    store, storage = env
    job = _done_job(True)
    storage.save_upload(job.id, b"%PDF-")
    store.create(job)
    assert store.delete(job.id)
    assert store.get(job.id) is None
    assert store.list_commitments(job.id) == []
    assert not storage.job_dir(job.id).exists()
    assert not store.delete(job.id)


def test_list_commitments_order_and_filters(env):
    store, _ = env
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
    store, storage = env
    job = Job()
    storage.save_upload(job.id, b"%PDF-")
    store.create(job)
    store.purge_expired(now=datetime.now(timezone.utc) + timedelta(seconds=TTL_S + 1))
    assert store.get(job.id) is None
    assert not storage.job_dir(job.id).exists()


def test_fail_interrupted_marks_unfinished_jobs_only(env):
    store, _ = env
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
    store, _ = env
    job = Job()
    store.create(job)
    store.init()
    assert store.get(job.id) == job


@pytest.mark.parametrize(("name", "cls"), [("sqlite", SqliteJobStore), ("memory", MemoryJobStore)])
def test_factory_follows_setting(monkeypatch, tmp_path, name, cls):
    monkeypatch.setenv("CE_STORAGE", name)
    assert isinstance(create_job_store(Settings(), TempStorage(tmp_path)), cls)


def test_default_storage_is_memory(monkeypatch):
    monkeypatch.delenv("CE_STORAGE")
    assert Settings().storage is StorageName.MEMORY
