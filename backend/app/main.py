"""FastAPI entry point.

Run locally:  uvicorn app.main:app --reload   (from backend/)
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.config import Settings
from app.export.xlsx import write_xlsx
from app.ocr import create_ocr_engine
from app.pipeline.ingest import BYTES_PER_MB, FILE_SUFFIXES, MAGIC_BYTES_NEEDED, detect_file_kind
from app.pipeline.classify import load_form_types, load_generic_form_type
from app.pipeline.normalize import load_senders
from app.pipeline.orchestrator import Orchestrator
from app.providers import create_provider
from app.providers.base import ExtractionProvider
from app.schemas import Job, JobStatus
from app.storage import create_job_store
from app.storage.tmp import TempStorage

logger = logging.getLogger(__name__)

EXPORT_FILE_NAME = "commitments.xlsx"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def create_app(settings: Settings | None = None, provider: ExtractionProvider | None = None) -> FastAPI:
    settings = settings or Settings()
    storage = TempStorage(settings.tmp_dir)
    jobs = create_job_store(settings, storage)
    orchestrator = Orchestrator(
        settings=settings,
        provider=provider or create_provider(settings),
        form_types=load_form_types(settings.form_types_dir),
        generic_form_type=load_generic_form_type(settings.form_types_dir),
        senders=load_senders(settings.senders_path),
        ocr=create_ocr_engine(settings),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        jobs.init()
        # Jobs a previous run did not finish would otherwise stay "running" forever.
        interrupted = jobs.fail_interrupted()
        if interrupted:
            logger.warning("%d unfinished jobs from a previous run marked as failed", interrupted)
        jobs.purge_expired()  # leftovers from a previous run
        yield

    app = FastAPI(title="commitments-extractor", lifespan=lifespan)

    def require_job(job_id: str) -> Job:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return job

    def run_job(job: Job, stored: Path) -> None:
        # The orchestrator changes ``job`` in place; persist the start and the outcome.
        job.status = JobStatus.RUNNING
        if not jobs.update(job):
            logger.info("job %s was deleted before it started", job.id)
            return
        try:
            orchestrator.run(job, [stored])
        except Exception:
            # job.error already holds the exception type; log the id only.
            logger.exception("job %s failed", job.id)
        if not jobs.update(job):
            logger.info("job %s was deleted while running", job.id)
            return
        logger.info("job %s finished: %s, %d forms", job.id, job.status.value, len(job.groups))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/jobs", status_code=status.HTTP_201_CREATED)
    async def create_job(file: UploadFile, background: BackgroundTasks) -> Job:
        data = await file.read(settings.max_upload_mb * BYTES_PER_MB + 1)
        if len(data) > settings.max_upload_mb * BYTES_PER_MB:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
        kind = detect_file_kind(data[:MAGIC_BYTES_NEEDED])
        if kind is None:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "only PDF, PNG, JPEG, TIFF or WEBP files are supported")
        job = Job()
        stored = storage.save_upload(job.id, data, FILE_SUFFIXES[kind])
        job.files.append(stored.name)
        jobs.create(job)
        background.add_task(run_job, job, stored)
        return job

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> Job:
        return require_job(job_id)

    @app.post("/jobs/{job_id}/export")
    def export_job(job_id: str) -> FileResponse:
        job = require_job(job_id)
        if job.status is not JobStatus.DONE:
            raise HTTPException(status.HTTP_409_CONFLICT, "job is not finished")
        out_path = write_xlsx(job, storage.output_path(job.id, EXPORT_FILE_NAME))
        return FileResponse(out_path, media_type=XLSX_MEDIA_TYPE, filename=EXPORT_FILE_NAME)

    @app.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_job(job_id: str) -> None:
        require_job(job_id)
        jobs.delete(job_id)

    return app


app = create_app()
