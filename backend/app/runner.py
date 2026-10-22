"""Run uploaded jobs in the background, a limited number at a time.

``submit`` waits for one of ``max_concurrent_jobs`` slots on the event loop
(a waiting job stays ``pending`` and holds no worker thread), then runs the
pipeline in the thread pool. Progress is saved to the job store as the
orchestrator reports it, so ``GET /jobs/{id}`` and the event stream see it.

Logs carry the job id, status, counts and exception *type* only: never the
exception message or traceback, which may quote field values.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from starlette.concurrency import run_in_threadpool

from app.schemas import Job, JobStatus
from app.storage.base import JobStore

logger = logging.getLogger(__name__)


class JobPipeline(Protocol):
    """What the runner needs from ``Orchestrator``."""

    def run(self, job: Job, stored_files: list[Path],
            on_progress: Callable[[Job], None] | None = None) -> Job: ...


class JobRunner:
    def __init__(self, jobs: JobStore, pipeline: JobPipeline, max_concurrent_jobs: int) -> None:
        self._jobs = jobs
        self._pipeline = pipeline
        # Created unbound; asyncio binds it to the server's loop on first wait.
        self._slots = asyncio.Semaphore(max_concurrent_jobs)

    async def submit(self, job: Job, stored: list[Path]) -> None:
        """Wait for a free slot, then run the job (its ``stored`` files, in order) in the thread pool."""
        async with self._slots:
            await run_in_threadpool(self.run, job, stored)

    def run(self, job: Job, stored: list[Path]) -> None:
        """Run one job now and persist its start, progress and outcome."""
        # The orchestrator changes ``job`` in place.
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        if not self._jobs.update(job):
            logger.info("job %s was deleted before it started", job.id)
            return
        try:
            self._pipeline.run(job, stored, on_progress=self._jobs.update)
        except Exception as exc:  # recorded on the job (status, error type); the server keeps serving
            if job.status is not JobStatus.FAILED:
                job.status, job.error = JobStatus.FAILED, type(exc).__name__
            logger.error("job %s failed: %s", job.id, type(exc).__name__)
        job.finished_at = datetime.now(timezone.utc)
        if not self._jobs.update(job):
            logger.info("job %s was deleted while running", job.id)
            return
        logger.info("job %s finished: %s, %d forms", job.id, job.status.value, len(job.groups))
