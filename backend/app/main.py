"""FastAPI entry point.

Run locally:  uvicorn app.main:app --reload   (from backend/)
"""
from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.events import stream_job_events
from app.export.xlsx import write_xlsx
from app.ocr import create_ocr_engine
from app.pipeline.ingest import BYTES_PER_MB, FILE_SUFFIXES, MAGIC_BYTES_NEEDED, detect_file_kind
from app.pipeline.classify import load_form_types, load_generic_form_type
from app.pipeline.normalize import load_senders
from app.pipeline.orchestrator import Orchestrator
from app.providers import create_provider
from app.providers.base import ExtractionProvider
from app.runner import JobRunner
from app.schemas import Job, JobStatus
from app.storage import create_job_store
from app.storage.tmp import TempStorage

logger = logging.getLogger(__name__)

EXPORT_FILE_NAME = "commitments.xlsx"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# The methods the endpoints below use; the frontend sends no custom headers.
CORS_METHODS = ("GET", "POST", "DELETE")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
EVENTS_MEDIA_TYPE = "text/event-stream"
# no-cache: never store the stream; X-Accel-Buffering: proxies (nginx, Render) must not buffer it.
EVENTS_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
EVENTS_POLL_INTERVAL_S = 0.5
EVENTS_KEEPALIVE_S = 15.0


def configure_logging(level: str) -> None:
    """Send the app's logs to stdout. uvicorn configures only its own loggers,
    so without this ``app.*`` INFO logs are dropped. A no-op if the root logger
    already has handlers (e.g. under pytest)."""
    logging.basicConfig(stream=sys.stdout, level=level, format=LOG_FORMAT)


def configure_process(settings: Settings) -> None:
    """Process-wide settings: logging and Pillow's decompression-bomb limit."""
    configure_logging(settings.log_level)
    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels


def create_app(settings: Settings | None = None, provider: ExtractionProvider | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_process(settings)
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
    runner = JobRunner(jobs, orchestrator, settings.max_concurrent_jobs)

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
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                           allow_methods=list(CORS_METHODS))

    def require_job(job_id: str) -> Job:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return job

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # A plain ``def``: FastAPI runs it in the thread pool, so the file write and
    # the store insert do not block the event loop.
    @app.post("/jobs", status_code=status.HTTP_201_CREATED)
    def create_job(file: UploadFile, background: BackgroundTasks) -> Job:
        data = file.file.read(settings.max_upload_mb * BYTES_PER_MB + 1)
        if len(data) > settings.max_upload_mb * BYTES_PER_MB:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
        kind = detect_file_kind(data[:MAGIC_BYTES_NEEDED])
        if kind is None:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "only PDF, PNG, JPEG, TIFF or WEBP files are supported")
        job = Job()
        stored = storage.save_upload(job.id, data, FILE_SUFFIXES[kind])
        job.files.append(stored.name)
        jobs.create(job)
        # Waits as ``pending`` while CE_MAX_CONCURRENT_JOBS jobs are running.
        background.add_task(runner.submit, job, stored)
        return job

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> Job:
        return require_job(job_id)

    @app.get("/jobs/{job_id}/events")
    def job_events(job_id: str) -> StreamingResponse:
        """Progress and finished rows as server-sent events (see ``app/events.py``)."""
        require_job(job_id)
        stream = stream_job_events(lambda: run_in_threadpool(jobs.get, job_id),
                                   EVENTS_POLL_INTERVAL_S, EVENTS_KEEPALIVE_S)
        return StreamingResponse(stream, media_type=EVENTS_MEDIA_TYPE, headers=EVENTS_HEADERS)

    @app.post("/jobs/{job_id}/export")
    def export_job(job_id: str) -> FileResponse:
        job = require_job(job_id)
        if job.status is not JobStatus.DONE:
            raise HTTPException(status.HTTP_409_CONFLICT, "job is not finished")
        out_path = write_xlsx(job, storage.output_path(job.id, EXPORT_FILE_NAME), settings.export)
        return FileResponse(out_path, media_type=XLSX_MEDIA_TYPE, filename=EXPORT_FILE_NAME)

    @app.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_job(job_id: str) -> None:
        require_job(job_id)
        jobs.delete(job_id)

    return app


app = create_app()
