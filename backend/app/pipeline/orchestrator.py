"""Run the pipeline stages over the files of one job.

Wired so far: ingest -> preprocess -> segment -> (per form, concurrently)
classify -> extract -> OCR cross-check -> second look -> normalize ->
confidence; then the forms of the whole job are compared for duplicate
central numbers.

A form that fails is marked (``FormGroup.error``, ``extraction_failed``) and
the other forms are still read; the job fails only if every form failed.

Pages are loaded and preprocessed one at a time. When a ``PageImageStore`` is
given, each preprocessed page is saved to the job's temporary directory (for
the crop endpoint; never to the database) and its full-resolution ``original``
is dropped from memory: the colour crops of the second look read it back from
that JPEG. Only the model and OCR copies of the pages stay in memory.

Progress: the groups of a file are added to ``job.groups`` as soon as it is
segmented, each gets ``finished=True`` when its reading ends, and the optional
``on_progress`` callback is called with the job after both steps.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import numpy as np

from app.config import Settings
from app.ocr.base import OcrEngine
from app.pipeline import classify, ingest, normalize, preprocess, segment
from app.pipeline.calls import ProviderCalls
from app.pipeline.ingest import PageImage
from app.pipeline.read_form import FormReader
from app.providers.base import ExtractionProvider
from app.providers.diagnostics import exception_chain
from app.schemas import EXTRACTION_FAILED, FormGroup, FormType, Job, JobStatus, Sender
from app.storage.page_images import PageImageError, PageImageStore

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[], None]
MS_PER_S = 1000


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        provider: ExtractionProvider,
        form_types: dict[str, FormType],
        generic_form_type: FormType,
        senders: list[Sender],
        ocr: OcrEngine | None = None,
        page_images: PageImageStore | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._form_types = form_types
        self._reader = FormReader(provider, form_types, generic_form_type, classify.SenderMatcher(senders), ocr,
                                  confidence_settings=settings.confidence, name_reread=settings.name_reread)
        self._ocr = ocr
        self._page_images = page_images
        self._ingest_limits = ingest.IngestLimits.from_settings(settings)

    def run(self, job: Job, stored_files: list[Path], on_progress: Callable[[Job], None] | None = None) -> Job:
        """Process ``stored_files`` and fill ``job.groups``. Must not be called
        from a running event loop (it runs its own for the provider calls).

        ``on_progress(job)`` is called after each file is segmented and after
        each form is read (see the module docstring).
        On failure ``job.error`` holds the exception type only — never field values.
        """
        job.status = JobStatus.RUNNING

        def notify() -> None:
            if on_progress is not None:
                on_progress(job)

        try:
            for path in stored_files:
                self._process_file(path, job, notify)
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = type(exc).__name__
            raise
        normalize.flag_duplicate_centrals(job.groups)
        if job.groups and all(group.error for group in job.groups):
            job.status, job.error = JobStatus.FAILED, job.groups[0].error
            return job
        job.status = JobStatus.DONE
        return job

    def _process_file(self, path: Path, job: Job, notify: ProgressCallback) -> None:
        pages = [self._prepare_page(job.id, page) for page in ingest.iter_pages(path, self._ingest_limits)]
        result = segment.segment(pages, self._ocr, self._form_types, self._provider,
                                 self._settings.review_threshold, self._settings.segment)
        job.groups.extend(result.groups)
        job.irrelevant_pages.extend(result.irrelevant_pages)
        job.split_vlm_calls += result.vlm_calls
        notify()
        by_page_no = {page.page_no: page for page in pages}
        forms = [(group, [by_page_no[n] for n in group.pages]) for group in result.groups]
        asyncio.run(self._read_forms(forms, notify))

    def _prepare_page(self, job_id: str, page: PageImage) -> PageImage:
        """Preprocess ``page``; with a page store, save it and keep only the copies the later stages read."""
        page = preprocess.preprocess(page, self._settings.preprocess, self._ocr)
        if self._page_images is None or page.ocr_image is None:
            return page
        self._page_images.save(job_id, page)
        return page.without_original(partial(self._stored_original, job_id, page.file_id, page.page_no))

    def _stored_original(self, job_id: str, source_file: str, page_no: int) -> np.ndarray | None:
        try:
            return self._page_images.load(job_id, source_file, page_no)
        except PageImageError as exc:  # the crop is skipped; the second look records it as unavailable
            logger.warning("page %d of file %s cannot be read back: %s", page_no, source_file, type(exc).__name__)
            return None

    async def _read_forms(self, forms: list[tuple[FormGroup, list[PageImage]]], notify: ProgressCallback) -> None:
        semaphore = asyncio.Semaphore(self._settings.extraction.concurrency)
        await asyncio.gather(*(self._read_form(group, pages, semaphore, notify) for group, pages in forms))

    async def _read_form(self, group: FormGroup, pages: list[PageImage], semaphore: asyncio.Semaphore,
                         notify: ProgressCallback) -> None:
        calls = ProviderCalls(semaphore, self._settings.extraction)
        started = time.perf_counter()
        try:
            await self._reader.read(group, pages, calls)
        except Exception as exc:  # one failed form must not stop the others; it is recorded on the form
            group.error = type(exc).__name__
            group.form_reasons.append(EXTRACTION_FAILED)
            logger.warning("form %s (file %s, %d pages) failed after %d calls: %s", group.id, group.source_file,
                           len(group.pages), calls.count, exception_chain(exc))
        finally:
            group.vlm_calls = calls.count
            group.read_ms = round((time.perf_counter() - started) * MS_PER_S)
            group.finished = True
            notify()
