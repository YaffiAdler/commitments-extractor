"""Blank pages and a scripted OCR engine for the extraction tests (no Tesseract, no network)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.ocr.base import OcrError, OcrLine
from app.pipeline.ingest import PageImage
from app.schemas import RelativeBox

PAGE_WIDTH, PAGE_HEIGHT = 1000, 800


def blank_page(page_no: int = 0, with_ocr_image: bool = True) -> PageImage:
    original = np.full((PAGE_HEIGHT, PAGE_WIDTH, 3), 255, np.uint8)
    ocr_image = np.full((PAGE_HEIGHT, PAGE_WIDTH), 255, np.uint8) if with_ocr_image else None
    return PageImage(file_id="f.pdf", page_no=page_no, image=original[::2, ::2], original_path=Path("f.pdf"),
                     original=original, ocr_image=ocr_image)


def value_box(page: int = 0, top: float = 0.1) -> RelativeBox:
    """A plausible box for one number."""
    return RelativeBox(page=page, x0=0.1, y0=top, x1=0.3, y1=top + 0.05)


class LineOcr:
    """Answers ``read_line`` with the given texts in order ("" once used up);
    ``None`` in the list raises ``OcrError``."""

    def __init__(self, *texts: str | None, confidence: float = 0.9) -> None:
        self._texts = list(texts)
        self._confidence = confidence
        self.crops: list[np.ndarray] = []
        self.allowed: list[str] = []

    def read_line(self, image: np.ndarray, allowed_chars: str) -> OcrLine:
        self.crops.append(image)
        self.allowed.append(allowed_chars)
        text = self._texts.pop(0) if self._texts else ""
        if text is None:
            raise OcrError("scripted failure")
        return OcrLine(text=text, confidence=self._confidence if text else 0.0)

    def detect_orientation(self, image):
        raise OcrError("not scripted")

    def text_confidence(self, image):
        raise OcrError("not scripted")

    def read_words(self, image):
        raise OcrError("not scripted")
