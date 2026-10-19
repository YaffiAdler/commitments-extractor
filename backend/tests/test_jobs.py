from datetime import datetime, timedelta, timezone

from app.jobs import JobStore
from app.schemas import Job
from app.storage.tmp import TempStorage

TTL_S = 60


def test_add_get_delete(tmp_path):
    storage = TempStorage(tmp_path)
    store = JobStore(storage, TTL_S)
    job = Job()
    storage.save_upload(job.id, b"%PDF-")
    store.add(job)
    assert store.get(job.id) is job
    assert store.delete(job.id)
    assert store.get(job.id) is None
    assert not storage.job_dir(job.id).exists()


def test_expired_jobs_are_purged(tmp_path):
    storage = TempStorage(tmp_path)
    store = JobStore(storage, TTL_S)
    job = Job()
    storage.save_upload(job.id, b"%PDF-")
    store.add(job)
    store.purge_expired(now=datetime.now(timezone.utc) + timedelta(seconds=TTL_S + 1))
    assert store.get(job.id) is None
    assert not storage.job_dir(job.id).exists()
