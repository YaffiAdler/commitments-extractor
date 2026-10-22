"""Read one form: classify (stage 4) -> extract (5) -> OCR cross-check (5b)
-> second look (5c) -> normalize and validate (6). Provider calls go through
``ProviderCalls`` (concurrency, retries, timeout)."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Callable

from app.ocr.base import OcrEngine
from app.pipeline import classify, extract, normalize, ocr_check, reread
from app.pipeline.calls import ProviderCalls
from app.pipeline.ingest import PageImage
from app.providers.base import ExtractionProvider
from app.schemas import FormGroup, FormType


class FormReader:
    def __init__(self, provider: ExtractionProvider, form_types: dict[str, FormType], generic_form_type: FormType,
                 sender_matcher: classify.SenderMatcher, ocr: OcrEngine | None,
                 today: Callable[[], date] = date.today) -> None:
        self._provider = provider
        self._form_types = form_types
        self._generic_form_type = generic_form_type
        self._sender_matcher = sender_matcher
        self._ocr = ocr
        self._today = today

    async def read(self, group: FormGroup, pages: list[PageImage], calls: ProviderCalls) -> None:
        """Fill ``group.commitment`` and ``group.form_reasons``. The sender read
        by ``extract_form`` is replaced by the one matched in classification."""
        found = await calls.run(classify.classify, pages, self._form_types, self._generic_form_type,
                                self._sender_matcher, self._provider)
        reading = await calls.run(self._provider.extract_form, pages, found.form_type)
        commitment = extract.to_commitment(reading, pages, found.form_type)
        commitment.sender = found.sender.to_field()
        await asyncio.to_thread(ocr_check.check, commitment, pages, self._ocr)
        await reread.second_look(commitment, pages, self._provider, calls)
        group.commitment = normalize.normalize(commitment, found.form_type, self._today())
        group.form_reasons.extend(found.form_reasons)
