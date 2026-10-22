"""FastAPI entry point.

Run locally:  uvicorn app.main:app --reload   (from backend/)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.access import OPEN_PATHS, AccessTokenMiddleware
from app.body_limit import BodySizeLimitMiddleware
from app.config import Settings
from app.corrections import CorrectionError, apply_correction
from app.events import stream_job_events
from app.export.xlsx import write_xlsx
from app.field_crops import CropUnavailableError, field_crop_png, form_page_jpeg
from app.ocr import create_ocr_engine
from app.log_filters import install_token_redaction
from app.pipeline.ingest import (BYTES_PER_MB, FILE_SUFFIXES, MAGIC_BYTES_NEEDED, FileTooLargeError, IngestError,
                                 count_pages, detect_file_kind)
from app.pipeline.classify import load_form_types, load_generic_form_type
from app.pipeline.normalize import load_senders
from app.pipeline.orchestrator import Orchestrator
from app.providers import create_provider
from app.providers.base import ExtractionProvider
from app.rate_limit import HOUR_S, MINUTE_S, RateLimiter, RateLimitMiddleware, RateRule
from app.runner import JobRunner
from app.schemas import FormGroup, Job, JobStatus
from app.storage import create_job_store
from app.storage.base import JobStore, JobStoreError
from app.storage.page_images import PageImageStore
from app.storage.tmp import TempStorage

logger = logging.getLogger(__name__)

EXPORT_FILE_NAME = "commitments.xlsx"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# The methods the endpoints below use; the only non-simple header the frontend sends is the access token.
CORS_METHODS = ("GET", "POST", "PATCH", "DELETE")
CORS_HEADERS = ("Authorization",)
# Room for the multipart boundaries and part headers around the files of one upload.
MULTIPART_OVERHEAD_BYTES = BYTES_PER_MB
UPLOAD_PATH = "/jobs"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
EVENTS_MEDIA_TYPE = "text/event-stream"
# no-cache: never store the stream; X-Accel-Buffering: proxies (nginx, Render) must not buffer it.
EVENTS_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
EVENTS_POLL_INTERVAL_S = 0.5
EVENTS_KEEPALIVE_S = 15.0
PNG_MEDIA_TYPE = "image/png"
JPEG_MEDIA_TYPE = "image/jpeg"
# Crops and pages are patient data: the browser must not keep them.
CROP_HEADERS = {"Cache-Control": "no-store"}
STORE_ERROR_DETAIL = "job store unavailable"
TOO_MANY_PAGES_DETAIL = "too many pages"


class FieldCorrectionRequest(BaseModel):
    """Body of ``PATCH /jobs/{id}/commitments/{cid}/fields/{field}``: the reviewer's value; ``item`` is
    the list index for central / commitment numbers (the list length adds one)."""

    value: str
    item: int | None = None


def configure_logging(level: str) -> None:
    """Send the app's logs to stdout. uvicorn configures only its own loggers,
    so without this ``app.*`` INFO logs are dropped. A no-op if the root logger
    already has handlers (e.g. under pytest)."""
    logging.basicConfig(stream=sys.stdout, level=level, format=LOG_FORMAT)
    install_token_redaction()


def configure_process(settings: Settings) -> None:
    """Process-wide settings: logging and Pillow's decompression-bomb limit."""
    configure_logging(settings.log_level)
    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels


def add_middleware(app: FastAPI, settings: Settings) -> None:
    """Demo protection around every endpoint. Starlette runs the last added middleware first, so a
    request passes: CORS (outermost, so refusals carry CORS headers and the browser can read them) ->
    body size -> rate limit (before the token check, so guessing is limited too) -> access token."""
    if settings.demo_access_token:
        app.add_middleware(AccessTokenMiddleware, token=settings.demo_access_token)
    rules = []
    if settings.rate_limit_per_minute:
        rules.append(RateRule(RateLimiter(settings.rate_limit_per_minute, MINUTE_S)))
    if settings.upload_limit_per_hour:
        rules.append(RateRule(RateLimiter(settings.upload_limit_per_hour, HOUR_S),
                              methods=frozenset({"POST"}), path=UPLOAD_PATH))
    if rules:
        app.add_middleware(RateLimitMiddleware, rules=rules, trusted_proxy_hops=settings.trusted_proxy_hops,
                           exempt=OPEN_PATHS)
    app.add_middleware(BodySizeLimitMiddleware,
                       max_bytes=settings.max_files * settings.max_upload_mb * BYTES_PER_MB + MULTIPART_OVERHEAD_BYTES)
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                           allow_methods=list(CORS_METHODS), allow_headers=list(CORS_HEADERS))


async def purge_periodically(jobs: JobStore, interval_s: float) -> None:
    """Delete expired jobs and files every ``interval_s`` seconds, also while no request comes in."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await run_in_threadpool(jobs.purge_expired)
        except (JobStoreError, OSError) as exc:  # logged by type; the next round tries again
            logger.error("periodic purge failed: %s", type(exc).__name__)


def create_app(settings: Settings | None = None, provider: ExtractionProvider | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_process(settings)
    storage = TempStorage(settings.tmp_dir)
    jobs = create_job_store(settings, storage)
    page_images = PageImageStore(storage)
    senders = load_senders(settings.senders_path)
    sender_names = frozenset(sender.canonical for sender in senders)
    orchestrator = Orchestrator(
        settings=settings,
        provider=provider or create_provider(settings),
        form_types=load_form_types(settings.form_types_dir),
        generic_form_type=load_generic_form_type(settings.form_types_dir),
        senders=senders,
        ocr=create_ocr_engine(settings),
        page_images=page_images,
    )
    runner = JobRunner(jobs, orchestrator, settings.max_concurrent_jobs)

    def clear_files() -> None:
        if settings.clear_tmp_on_restart:
            logger.info("temporary files cleared: %d job directories", len(storage.clear()))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        jobs.init()
        # Jobs a previous run did not finish would otherwise stay "running" forever.
        interrupted = jobs.fail_interrupted()
        if interrupted:
            logger.warning("%d unfinished jobs from a previous run marked as failed", interrupted)
        clear_files()
        jobs.purge_expired()  # leftovers from a previous run
        purger = asyncio.create_task(purge_periodically(jobs, settings.purge_interval_s))
        try:
            yield
        finally:
            purger.cancel()
            clear_files()
            jobs.close()

    app = FastAPI(title="commitments-extractor", lifespan=lifespan)
    app.state.jobs = jobs  # for tests and scripts that inspect the store behind the API
    add_middleware(app, settings)

    @app.exception_handler(JobStoreError)
    async def job_store_failed(_: Request, exc: JobStoreError) -> JSONResponse:
        # The message is the driver's exception type only (see JobStoreError).
        logger.error("job store failed: %s", exc)
        return JSONResponse({"detail": STORE_ERROR_DETAIL}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    def require_job(job_id: str) -> Job:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        return job

    # A commitment is identified by its form's id (``FormGroup.id``): one form holds one commitment.
    def require_group(job: Job, group_id: str) -> FormGroup:
        group = job.group(group_id)
        if group is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "commitment not found")
        return group

    def read_upload(upload: UploadFile) -> tuple[bytes, str, int]:
        """The file's content, stored suffix and page count; 413 / 415 for a file the job cannot take."""
        data = upload.file.read(settings.max_upload_mb * BYTES_PER_MB + 1)
        if len(data) > settings.max_upload_mb * BYTES_PER_MB:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
        kind = detect_file_kind(data[:MAGIC_BYTES_NEEDED])
        if kind is None:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                                "only PDF, PNG, JPEG, TIFF or WEBP files are supported")
        try:
            pages = count_pages(data, kind)
        except FileTooLargeError:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large") from None
        except IngestError:
            # Not refused here: ingest fails the job with the specific error type, as before.
            pages = 0
        if pages > settings.max_pages:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_MANY_PAGES_DETAIL)
        return data, FILE_SUFFIXES[kind], pages

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/senders")
    def list_senders() -> list[str]:
        """The canonical sender names a corrected ``sender`` must be one of."""
        return sorted(sender_names)

    # A plain ``def``: FastAPI runs it in the thread pool, so the file write and
    # the store insert do not block the event loop.
    @app.post("/jobs", status_code=status.HTTP_201_CREATED)
    def create_job(background: BackgroundTasks, files: list[UploadFile] = File()) -> Job:
        """One job over all ``files`` (read in the order sent). Every file is
        checked before any is stored, so a refused upload leaves nothing behind."""
        if len(files) > settings.max_files:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "too many files")
        uploads = [read_upload(upload) for upload in files]
        if sum(pages for _, _, pages in uploads) > settings.max_job_pages:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_MANY_PAGES_DETAIL)
        job = Job()
        stored = [storage.save_upload(job.id, data, suffix) for data, suffix, _ in uploads]
        job.files.extend(path.name for path in stored)
        jobs.create(job)
        logger.info("job %s created: %d files", job.id, len(stored))
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

    @app.patch("/jobs/{job_id}/commitments/{commitment_id}/fields/{field}")
    def correct_field(job_id: str, commitment_id: str, field: str, body: FieldCorrectionRequest) -> Job:
        """A reviewer's value for one field (see ``app/corrections.py``); only on a finished job."""
        job = require_job(job_id)
        if job.status is not JobStatus.DONE:
            raise HTTPException(status.HTTP_409_CONFLICT, "job is not finished")
        require_group(job, commitment_id)
        try:
            apply_correction(job, commitment_id, field, body.value, body.item, sender_names)
        except CorrectionError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        if not jobs.update(job):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
        logger.info("job %s form %s: field %s corrected", job.id, commitment_id, field)
        return job

    @app.get("/jobs/{job_id}/crop/{commitment_id}/{field}")
    def field_crop(job_id: str, commitment_id: str, field: str, item: int | None = None) -> Response:
        """The field's box on its page as PNG. 404 ``image_gone`` once the page
        image was deleted (file TTL, server restart); the job itself may remain."""
        job = require_job(job_id)
        group = require_group(job, commitment_id)
        try:
            png = field_crop_png(page_images, job.id, group, field, item)
        except CropUnavailableError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
        return Response(png, media_type=PNG_MEDIA_TYPE, headers=CROP_HEADERS)

    @app.get("/jobs/{job_id}/pages/{commitment_id}/{page}")
    def form_page(job_id: str, commitment_id: str, page: int) -> Response:
        """One page of the form as JPEG, at the resolution of ``Field.bbox``
        (``page`` is 0-based in the source file, as in ``FormGroup.pages``)."""
        job = require_job(job_id)
        group = require_group(job, commitment_id)
        try:
            jpeg = form_page_jpeg(page_images, job.id, group, page)
        except CropUnavailableError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
        return Response(jpeg, media_type=JPEG_MEDIA_TYPE, headers=CROP_HEADERS)

    @app.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_job(job_id: str) -> None:
        """Deletes the job's rows in every table and its temporary files."""
        require_job(job_id)
        jobs.delete(job_id)

    return app


app = create_app()
