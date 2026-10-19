"""OCR engines behind the ``OcrEngine`` interface."""
from __future__ import annotations

import logging

from app.config import Settings
from app.ocr.base import OcrEngine
from app.ocr.tesseract import TesseractEngine, TesseractUnavailableError

logger = logging.getLogger(__name__)


def create_ocr_engine(settings: Settings) -> OcrEngine | None:
    """Tesseract if it is installed, otherwise None (orientation detection is
    then skipped and every page is flagged ``orientation_unavailable``)."""
    try:
        return TesseractEngine.create(settings.tesseract_cmd, settings.ocr_languages, settings.ocr_timeout_s)
    except TesseractUnavailableError as exc:
        logger.warning("OCR disabled: %s", exc)
        return None
