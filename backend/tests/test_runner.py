"""JobRunner: the job slots (CE_MAX_CONCURRENT_JOBS) and what a failed job logs."""
import asyncio
import logging
import threading

import pytest

from app.runner import JobRunner
from app.schemas import Job, JobStatus
from app.storage.memory import MemoryJobStore
from app.storage.tmp import TempStorage

TTL_S = 3600
# A dummy value the pipeline might quote in an exception message.
FIELD_VALUE = "000000018"
WAIT_S = 5


class BlockingPipeline:
    """Holds every job until released; records how many ran at once."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.running = 0
        self.max_running = 0
        self._lock = threading.Lock()

    def run(self, job, stored_files, on_progress=None):
        with self._lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        assert self.release.wait(WAIT_S)
        with self._lock:
            self.running -= 1
        job.status = JobStatus.DONE
        return job


class FailingPipeline:
    def run(self, job, stored_files, on_progress=None):
        job.status, job.error = JobStatus.FAILED, "ValueError"
        raise ValueError(f"cannot parse id {FIELD_VALUE}")


@pytest.fixture
def store(tmp_path) -> MemoryJobStore:
    return MemoryJobStore(TempStorage(tmp_path), TTL_S)


def _stored_job(store: MemoryJobStore) -> Job:
    job = Job()
    store.create(job)
    return job


def test_jobs_beyond_the_limit_wait_as_pending(store, tmp_path):
    pipeline = BlockingPipeline()
    jobs = [_stored_job(store) for _ in range(3)]
    runner = JobRunner(store, pipeline, max_concurrent_jobs=2)

    async def scenario() -> None:
        tasks = [asyncio.create_task(runner.submit(job, tmp_path / "f.pdf")) for job in jobs]
        while pipeline.running < 2:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)  # time for a third job to start, if the limit were broken
        statuses = [store.get(job.id).status for job in jobs]
        assert sorted(statuses) == sorted([JobStatus.RUNNING, JobStatus.RUNNING, JobStatus.PENDING])
        pipeline.release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert pipeline.max_running == 2
    assert [store.get(job.id).status for job in jobs] == [JobStatus.DONE] * 3


def test_failed_job_logs_type_and_id_only(store, tmp_path, caplog, capsys):
    job = _stored_job(store)
    with caplog.at_level(logging.DEBUG):
        JobRunner(store, FailingPipeline(), max_concurrent_jobs=1).run(job, tmp_path / "f.pdf")
    assert FIELD_VALUE not in caplog.text
    assert all(record.exc_info is None and not record.exc_text for record in caplog.records)
    failure = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert failure == [f"job {job.id} failed: ValueError"]
    captured = capsys.readouterr()
    assert FIELD_VALUE not in captured.out + captured.err
    stored = store.get(job.id)
    assert (stored.status, stored.error) == (JobStatus.FAILED, "ValueError")


def test_unexpected_failure_is_recorded_on_the_job(store, tmp_path):
    class BrokenPipeline:
        def run(self, job, stored_files, on_progress=None):
            raise RuntimeError(f"dummy {FIELD_VALUE}")

    job = _stored_job(store)
    JobRunner(store, BrokenPipeline(), max_concurrent_jobs=1).run(job, tmp_path / "f.pdf")
    stored = store.get(job.id)
    assert (stored.status, stored.error) == (JobStatus.FAILED, "RuntimeError")


def test_progress_is_saved_to_the_store(store, tmp_path):
    class ProgressPipeline:
        def run(self, job, stored_files, on_progress=None):
            job.split_vlm_calls = 7
            on_progress(job)
            assert store.get(job.id).split_vlm_calls == 7
            job.status = JobStatus.DONE
            return job

    job = _stored_job(store)
    JobRunner(store, ProgressPipeline(), max_concurrent_jobs=1).run(job, tmp_path / "f.pdf")
    assert store.get(job.id).status is JobStatus.DONE
