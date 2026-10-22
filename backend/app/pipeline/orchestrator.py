"""Run the pipeline stages over the files of one job.

Wired so far: ingest -> preprocess -> segment -> (per form, concurrently)
classify -> extract -> OCR cross-check -> second look. normalize and
confidence are not wired in yet, so every field except a matched sender stays
at confidence 0 / needs review.

A form that fails is marked (``FormGroup.error``, ``extraction_failed``) and
the other forms are still read; the job fails only if every form failed.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import Settings
from app.ocr.base import OcrEngine
from app.pipeline import classify, ingest, preprocess, segment
from app.pipeline.calls import ProviderCalls
from app.pipeline.ingest import PageImage
from app.pipeline.read_form import FormReader
from app.providers.base import ExtractionProvider
from app.schemas import EXTRACTION_FAILED, FormGroup, FormType, Job, JobStatus, Segmentation, Sender

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        provider: ExtractionProvider,
        form_types: dict[str, FormType],
        generic_form_type: FormType,
        senders: list[Sender],
        ocr: OcrEngine | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._form_types = form_types
        self._reader = FormReader(provider, form_types, generic_form_type, classify.SenderMatcher(senders), ocr)
        self._ocr = ocr
        self._ingest_limits = ingest.IngestLimits(
            max_file_bytes=settings.max_upload_mb * ingest.BYTES_PER_MB,
            max_pages=settings.max_pages,
            render_dpi=settings.render_dpi,
        )

    def run(self, job: Job, stored_files: list[Path]) -> Job:
        """Process ``stored_files`` and fill ``job.groups``. Must not be called
        from a running event loop (it runs its own for the provider calls).

        On failure ``job.error`` holds the exception type only — never field values.
        """
        job.status = JobStatus.RUNNING
        try:
            for path in stored_files:
                result = self._process_file(path)
                job.groups.extend(result.groups)
                job.irrelevant_pages.extend(result.irrelevant_pages)
                job.split_vlm_calls += result.vlm_calls
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = type(exc).__name__
            raise
        if job.groups and all(group.error for group in job.groups):
            job.status, job.error = JobStatus.FAILED, job.groups[0].error
            return job
        job.status = JobStatus.DONE
        return job

    def _process_file(self, path: Path) -> Segmentation:
        pages = [
            preprocess.preprocess(page, self._settings.preprocess, self._ocr)
            for page in ingest.load_file(path, self._ingest_limits)
        ]
        result = segment.segment(pages, self._ocr, self._form_types, self._provider,
                                 self._settings.review_threshold)
        by_page_no = {page.page_no: page for page in pages}
        asyncio.run(self._read_forms([(group, [by_page_no[n] for n in group.pages]) for group in result.groups]))
        return result

    async def _read_forms(self, forms: list[tuple[FormGroup, list[PageImage]]]) -> None:
        semaphore = asyncio.Semaphore(self._settings.extraction.concurrency)
        await asyncio.gather(*(self._read_form(group, pages, semaphore) for group, pages in forms))

    async def _read_form(self, group: FormGroup, pages: list[PageImage], semaphore: asyncio.Semaphore) -> None:
        calls = ProviderCalls(semaphore, self._settings.extraction)
        try:
            await self._reader.read(group, pages, calls)
        except Exception as exc:  # one failed form must not stop the others; it is recorded on the form
            group.error = type(exc).__name__
            group.form_reasons.append(EXTRACTION_FAILED)
            logger.warning("form %s (file %s, %d pages) failed: %s", group.id, group.source_file,
                           len(group.pages), group.error)
        finally:
            group.vlm_calls = calls.count
