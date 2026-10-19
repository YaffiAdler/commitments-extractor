"""In-memory registry of jobs with expiry after a TTL.

Jobs hold patient data, so they live in memory only and are dropped together
with their temporary files when deleted or expired.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from app.schemas import Job
from app.storage.tmp import TempStorage


class JobStore:
    def __init__(self, storage: TempStorage, ttl_s: int) -> None:
        self._storage = storage
        self._ttl = timedelta(seconds=ttl_s)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        self.purge_expired()
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
        self._storage.delete_job(job_id)
        return existed

    def purge_expired(self, now: datetime | None = None) -> None:
        """Drop expired jobs and any orphaned job directories on disk."""
        cutoff = (now or datetime.now(timezone.utc)) - self._ttl
        with self._lock:
            expired = [job_id for job_id, job in self._jobs.items() if job.created_at < cutoff]
        for job_id in expired:
            self.delete(job_id)
        self._storage.purge_older_than(self._ttl.total_seconds())
