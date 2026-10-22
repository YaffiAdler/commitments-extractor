"""In-memory job store (``CE_STORAGE=memory``): lost on restart."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from app.schemas import Commitment, Job, JobStatus
from app.storage.base import INTERRUPTED_ERROR, UNFINISHED_STATUSES, JobExistsError
from app.storage.tmp import TempStorage


class MemoryJobStore:
    def __init__(self, storage: TempStorage, ttl_s: int, file_ttl_s: int | None = None) -> None:
        self._storage = storage
        self._ttl = timedelta(seconds=ttl_s)
        self._file_ttl_s = ttl_s if file_ttl_s is None else file_ttl_s
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def init(self) -> None:
        """Nothing to prepare."""

    def close(self) -> None:
        """Nothing to release."""

    def create(self, job: Job) -> None:
        with self._lock:
            if job.id in self._jobs:
                raise JobExistsError("job already exists")
            self._jobs[job.id] = job.model_copy(deep=True)

    def get(self, job_id: str) -> Job | None:
        self.purge_expired()
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else job.model_copy(deep=True)

    def update(self, job: Job) -> bool:
        with self._lock:
            if job.id not in self._jobs:
                return False
            self._jobs[job.id] = job.model_copy(deep=True)
            return True

    def delete(self, job_id: str) -> bool:
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
        self._storage.delete_job(job_id)
        return existed

    def list_commitments(self, job_id: str | None = None, needs_review: bool | None = None) -> list[Commitment]:
        self.purge_expired()
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at)
            return [
                group.commitment.model_copy(deep=True)
                for job in jobs
                if job_id is None or job.id == job_id
                for group in job.groups
                if needs_review is None or group.needs_review == needs_review
            ]

    def fail_interrupted(self) -> int:
        with self._lock:
            interrupted = [job for job in self._jobs.values() if job.status in UNFINISHED_STATUSES]
            for job in interrupted:
                job.status, job.error = JobStatus.FAILED, INTERRUPTED_ERROR
            return len(interrupted)

    def purge_expired(self, now: datetime | None = None) -> None:
        cutoff = (now or datetime.now(timezone.utc)) - self._ttl
        with self._lock:
            expired = [job_id for job_id, job in self._jobs.items() if job.created_at < cutoff]
        for job_id in expired:
            self.delete(job_id)
        self._storage.purge_older_than(self._file_ttl_s)
