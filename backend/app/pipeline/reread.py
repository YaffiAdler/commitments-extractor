"""Stage 5c: one second look at numbers that are not confirmed.

A numeric field (ID, central or commitment number) gets a second look when it
is illegible (even if OCR agreed with the partial value), or when OCR did not
confirm it: OCR disagreed, read nothing or was unavailable, the value is
handwritten, or the value is missing but has a box. A missing value without a
box and not marked illegible is simply not on the form and is left alone. The colour crop (never binarized) plus ``CROP_PADDING_PX`` is enlarged
``ENLARGE_FACTOR`` times and sent alone with the narrow prompt
(``ExtractionProvider.read_number``). Each field is looked at once; at most
``MAX_REREADS_PER_FORM`` per form.

The answer is one more signal (``reread``), never written over the value —
even when the value is missing and the second look finds digits
(``reread_found_value``); deciding belongs to the confidence stage or a
reviewer. Outcomes: ``reread_agrees``, ``reread_disagrees``, ``reread_null``,
``reread_found_value``; ``reread_unavailable`` (no usable box),
``reread_failed`` (the call failed after retries), ``reread_limit`` (over the
per-form cap).
"""
from __future__ import annotations

import logging

from app.imaging.crop import enlarge
from app.pipeline.calls import ProviderCalls
from app.pipeline.crops import crop_field, digits_of, numeric_fields
from app.pipeline.extract import ILLEGIBLE
from app.pipeline.ingest import PageImage
from app.pipeline.ocr_check import OCR_AGREES
from app.providers.base import ExtractionProvider, ProviderError
from app.schemas import Commitment, Field, FieldSignal, SignalSource

logger = logging.getLogger(__name__)

ENLARGE_FACTOR = 2.5
MAX_CROP_EDGE_PX = 1600
MAX_REREADS_PER_FORM = 8

REREAD_AGREES = "reread_agrees"
REREAD_DISAGREES = "reread_disagrees"
REREAD_NULL = "reread_null"
REREAD_FOUND_VALUE = "reread_found_value"
REREAD_UNAVAILABLE = "reread_unavailable"
REREAD_FAILED = "reread_failed"
REREAD_LIMIT = "reread_limit"


def needs_second_look(field: Field[str]) -> bool:
    if ILLEGIBLE in field.reasons:
        return True  # OCR agreeing with a partial value does not make it whole
    if OCR_AGREES in field.reasons:
        return False
    return field.value is not None or field.bbox is not None


async def second_look(commitment: Commitment, pages: list[PageImage], provider: ExtractionProvider,
                      calls: ProviderCalls) -> None:
    """Look again at every unconfirmed numeric field (in place), one at a time."""
    targets = [field for field in numeric_fields(commitment) if needs_second_look(field)]
    for field in targets[MAX_REREADS_PER_FORM:]:
        field.reasons.append(REREAD_LIMIT)
    for field in targets[:MAX_REREADS_PER_FORM]:
        await _look_again(field, pages, provider, calls)


async def _look_again(field: Field[str], pages: list[PageImage], provider: ExtractionProvider,
                      calls: ProviderCalls) -> None:
    crop = crop_field(field.bbox, pages, for_ocr=False) if field.bbox else None
    if crop is None:
        field.reasons.append(REREAD_UNAVAILABLE)
        return
    try:
        answer = await calls.run(provider.read_number, enlarge(crop, ENLARGE_FACTOR, MAX_CROP_EDGE_PX))
    except (ProviderError, TimeoutError) as exc:
        logger.warning("second look at one value on page %d failed: %s", field.bbox.page, type(exc).__name__)
        field.reasons.append(REREAD_FAILED)
        return
    read = digits_of(answer.value)
    field.signals.append(FieldSignal(source=SignalSource.REREAD, value=read or None))
    field.reasons.append(_outcome(read, field.value))


def _outcome(read: str, value: str | None) -> str:
    if not read:
        return REREAD_NULL
    if value is None:
        return REREAD_FOUND_VALUE
    return REREAD_AGREES if read == digits_of(value) else REREAD_DISAGREES
