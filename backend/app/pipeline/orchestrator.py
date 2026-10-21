"""Run the pipeline stages over the files of one job.

Wired so far: ingest -> preprocess -> segment -> classify -> extract.
normalize and confidence are not wired in yet, so every field except a
matched sender stays at confidence 0 / needs review.
"""
from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.ocr.base import OcrEngine
from app.pipeline.ingest import PageImage
from app.pipeline import classify, extract, ingest, preprocess, segment
from app.providers.base import ExtractionProvider
from app.schemas import FormGroup, FormType, Job, JobStatus, Segmentation, Sender


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
        self._generic_form_type = generic_form_type
        self._sender_matcher = classify.SenderMatcher(senders)
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
                result = self._process_file(path)
                job.groups.extend(result.groups)
                job.irrelevant_pages.extend(result.irrelevant_pages)
                job.split_vlm_calls += result.vlm_calls
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = type(exc).__name__
            raise
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
        for group in result.groups:
            self._read_form(group, [by_page_no[n] for n in group.pages])
        return result

    def _read_form(self, group: FormGroup, pages: list[PageImage]) -> None:
        """Classify the form, extract it with the chosen layout and set the
        matched sender (the sender read by ``extract`` is not used)."""
        found = classify.classify(pages, self._form_types, self._generic_form_type,
                                  self._sender_matcher, self._provider)
        group.commitment = extract.extract(pages, found.form_type, self._provider)
        group.commitment.sender = found.sender.to_field()
        group.form_reasons.extend(found.form_reasons)
