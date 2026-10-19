"""Run the pipeline stages over the files of one job.

Wired so far: ingest -> preprocess -> extract. Each file is treated as a
single form; segment, classify, normalize and confidence are not wired in
yet, so every field stays at confidence 0 / needs review.
"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.ocr.base import OcrEngine
from app.pipeline import extract, ingest, preprocess
from app.providers.base import ExtractionProvider
from app.schemas import FormGroup, FormType, Job, JobStatus, Sender


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        provider: ExtractionProvider,
        form_types: dict[str, FormType],
        senders: list[Sender],
        ocr: OcrEngine | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._form_types = form_types
        self._senders = senders
        self._ocr = ocr
        self._ingest_limits = ingest.IngestLimits(
            max_file_bytes=settings.max_upload_mb * ingest.BYTES_PER_MB,
            max_pages=settings.max_pages,
            render_dpi=settings.render_dpi,
        )

    def run(self, job: Job, stored_files: list[Path]) -> Job:
        """Process ``stored_files`` and fill ``job.groups``.

        On failure ``job.error`` holds the exception type only — never field values.
        """
        job.status = JobStatus.RUNNING
        try:
            for path in stored_files:
                job.groups.extend(self._process_file(path))
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = type(exc).__name__
            raise
        job.status = JobStatus.DONE
        return job

    def _process_file(self, path: Path) -> list[FormGroup]:
        pages = [
            preprocess.preprocess(page, self._settings.preprocess, self._ocr)
            for page in ingest.load_file(path, self._ingest_limits)
        ]
        if not pages:
            return []
        return [self._process_form(pages)]

    def _process_form(self, pages: list[ingest.PageImage]) -> FormGroup:
        commitment = extract.extract(pages, None, self._provider)
        return FormGroup(
            source_file=pages[0].file_id,
            pages=[p.page_no for p in pages],
            rotation=pages[0].rotation,
            commitment=commitment,
        )
