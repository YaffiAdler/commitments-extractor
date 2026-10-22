"""Stage 5b: cross-check printed numbers with Tesseract.

For every numeric field (ID, central and commitment numbers) whose value the
model says is printed (or on a sticker or stamp), the value's box plus
``CROP_PADDING_PX`` is read as a single line (``--psm 7``) with a digits-only
whitelist. The digits are compared with the model's digits:

* ``ocr_agrees`` / ``ocr_disagrees`` — the outcome; the OCR digits and
  their confidence are added as an ``ocr`` signal, the value is never changed;
* ``ocr_empty`` — OCR read no digits (not a disagreement);
* ``ocr_unavailable`` — no check was possible: no OCR engine, no plausible
  box, a crop too small, a value that is not a number, or an OCR error. Not
  a disagreement either.

Handwritten values and missing values are not checked.
"""
from __future__ import annotations

import logging

from app.ocr.base import OcrEngine, OcrError
from app.pipeline.crops import DIGITS, crop_field, digits_of, is_number_text, numeric_fields
from app.pipeline.ingest import PageImage
from app.schemas import Commitment, Field, FieldSignal, FieldSource, SignalSource

logger = logging.getLogger(__name__)

OCR_AGREES = "ocr_agrees"
OCR_DISAGREES = "ocr_disagrees"
OCR_EMPTY = "ocr_empty"
OCR_UNAVAILABLE = "ocr_unavailable"


def check(commitment: Commitment, pages: list[PageImage], ocr: OcrEngine | None) -> None:
    """Add an OCR outcome to every printed numeric field of ``commitment`` (in place)."""
    for field in numeric_fields(commitment):
        if field.value is not None and field.source is not FieldSource.HANDWRITTEN:
            check_field(field, pages, ocr)


def check_field(field: Field[str], pages: list[PageImage], ocr: OcrEngine | None) -> None:
    crop = crop_field(field.bbox, pages, for_ocr=True) if field.bbox else None
    if ocr is None or crop is None or not is_number_text(field.value):
        field.reasons.append(OCR_UNAVAILABLE)
        return
    try:
        line = ocr.read_line(crop, DIGITS)
    except OcrError as exc:
        logger.warning("OCR of one value on page %d failed: %s", field.bbox.page, type(exc).__name__)
        field.reasons.append(OCR_UNAVAILABLE)
        return
    read = digits_of(line.text)
    field.signals.append(FieldSignal(source=SignalSource.OCR, value=read or None, confidence=line.confidence))
    if not read:
        field.reasons.append(OCR_EMPTY)
    else:
        field.reasons.append(OCR_AGREES if read == digits_of(field.value) else OCR_DISAGREES)
