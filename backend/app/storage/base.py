"""The interface every job store implements.

The API depends only on this interface, so the store is chosen by
configuration (``CE_STORAGE`` / ``DATABASE_URL``) and every implementation
shares one test suite. Stores hold patient data: never log what they return,
log ids/counts only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.schemas import Commitment, Job, JobStatus

# ``Job.error`` of a job whose processing was cut off by a server stop.
INTERRUPTED_ERROR = "Interrupted"
UNFINISHED_STATUSES = (JobStatus.PENDING, JobStatus.RUNNING)


class JobExistsError(ValueError):
    """``create`` was called with the id of a stored job."""


class JobStoreError(RuntimeError):
    """The database failed. The message is the driver's exception type only:
    driver messages and query parameters can quote field values."""


@runtime_checkable
class JobStore(Protocol):
    def init(self) -> None:
        """Prepare the store (create or migrate the schema); safe to call more
        than once and from several processes (called at startup)."""
        ...

    def close(self) -> None:
        """Release connections (called at shutdown); the store is not used after."""
        ...

    def create(self, job: Job) -> None:
        """Store a new job. Raises ``JobExistsError`` if the id is taken."""
        ...

    def get(self, job_id: str) -> Job | None:
        """A copy of the stored job (None if unknown or expired).

        Changing the copy does not change the store; call ``update``.
        """
        ...

    def update(self, job: Job) -> bool:
        """Replace the stored job; False if it was deleted meanwhile."""
        ...

    def delete(self, job_id: str) -> bool:
        """Delete the job and its temporary files; False if it was unknown."""
        ...

    def list_commitments(self, job_id: str | None = None, needs_review: bool | None = None) -> list[Commitment]:
        """Commitments in job creation order, then form order, optionally filtered."""
        ...

    def fail_interrupted(self) -> int:
        """Mark every pending/running job as failed with ``INTERRUPTED_ERROR``;
        return how many. Call at startup only: no job can be running then."""
        ...

    def purge_expired(self, now: datetime | None = None) -> None:
        """Delete jobs older than the job TTL, and job directories on disk not
        written for the file TTL (orphans included)."""
        ...
