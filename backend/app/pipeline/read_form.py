"""Read one form: classify (stage 4) -> extract (5) -> OCR cross-check (5b)
-> second look (5c) -> name second look (5d, opt-in) -> normalize and
validate (6) -> confidence (7).
Provider calls go through ``ProviderCalls`` (concurrency, retries, timeout)."""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Callable

from app.ocr.base import OcrEngine
from app.config import ConfidenceSettings, NameRereadSettings
from app.pipeline import classify, confidence, extract, normalize, ocr_check, reread, reread_name
from app.pipeline.calls import ProviderCalls
from app.pipeline.ingest import PageImage
from app.providers.base import ExtractionProvider
from app.schemas import FormGroup, FormType


class FormReader:
    def __init__(self, provider: ExtractionProvider, form_types: dict[str, FormType], generic_form_type: FormType,
                 sender_matcher: classify.SenderMatcher, ocr: OcrEngine | None,
                 today: Callable[[], date] = date.today,
                 confidence_settings: ConfidenceSettings | None = None,
                 name_reread: NameRereadSettings | None = None) -> None:
        self._provider = provider
        self._form_types = form_types
        self._generic_form_type = generic_form_type
        self._sender_matcher = sender_matcher
        self._ocr = ocr
        self._today = today
        self._confidence = confidence_settings or ConfidenceSettings()
        self._name_reread = name_reread or NameRereadSettings()

    async def read(self, group: FormGroup, pages: list[PageImage], calls: ProviderCalls) -> None:
        """Fill ``group.commitment`` and ``group.form_reasons``. The sender read
        by ``extract_form`` is replaced by the one matched in classification
        and kept as its second reading."""
        found = await calls.run(classify.classify, pages, self._form_types, self._generic_form_type,
                                self._sender_matcher, self._provider)
        reading = await calls.run(self._provider.extract_form, pages, found.form_type)
        commitment = extract.to_commitment(reading, pages, found.form_type)
        commitment.sender = found.sender.to_field()
        classify.compare_sender_readings(commitment.sender, self._sender_matcher.match(reading.sender.value),
                                         reading.sender.value)
        await asyncio.to_thread(ocr_check.check, commitment, pages, self._ocr)
        await reread.second_look(commitment, pages, self._provider, calls)
        if self._name_reread.enabled:
            await reread_name.second_look_names(commitment, pages, self._provider, calls, self._name_reread)
        normalized = normalize.normalize(commitment, found.form_type, self._today())
        group.commitment = confidence.score_fields(normalized, found.form_type, self._confidence)
        group.form_reasons.extend(found.form_reasons)
        group.classification = found.result()
