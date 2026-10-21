"""Tesseract through pytesseract. Requires the ``tesseract`` binary with the
``osd`` data (orientation) and the configured languages (default heb+eng)."""
from __future__ import annotations

import numpy as np
import pytesseract
from pytesseract import Output

from app.ocr.base import OcrError, OcrWord, OrientationGuess

OSD_LANGUAGE = "osd"
OSD_CONFIG = "--psm 0"
LANGUAGE_SEPARATOR = "+"
MIN_WORD_LENGTH = 2
PERCENT = 100.0


class TesseractUnavailableError(RuntimeError):
    """The binary or the required language data is missing."""


class TesseractEngine:
    def __init__(self, languages: str, timeout_s: float) -> None:
        self._languages = languages
        self._timeout_s = timeout_s

    @classmethod
    def create(cls, cmd: str, languages: str, timeout_s: float) -> "TesseractEngine":
        """Check the installation and keep only the installed languages."""
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        try:
            installed = set(pytesseract.get_languages(config=""))
        except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as exc:
            raise TesseractUnavailableError("tesseract binary not found") from exc
        wanted = languages.split(LANGUAGE_SEPARATOR)
        available = [lang for lang in wanted if lang in installed]
        if not available:
            raise TesseractUnavailableError(f"none of the languages {wanted} is installed")
        if OSD_LANGUAGE not in installed:
            raise TesseractUnavailableError("osd data is not installed")
        return cls(LANGUAGE_SEPARATOR.join(available), timeout_s)

    def detect_orientation(self, image: np.ndarray) -> OrientationGuess:
        try:
            osd = pytesseract.image_to_osd(image, config=OSD_CONFIG, output_type=Output.DICT,
                                           timeout=self._timeout_s)
        except (pytesseract.TesseractError, RuntimeError) as exc:
            raise OcrError("tesseract orientation detection failed") from exc
        # "rotate" is the clockwise rotation that makes the page upright.
        return OrientationGuess(rotation=int(osd["rotate"]) % 360, confidence=float(osd["orientation_conf"]))

    def text_confidence(self, image: np.ndarray) -> float:
        """Sum of word confidences (0..1) over words of at least 2 characters."""
        data = self._image_to_data(image)
        return sum(
            float(conf) / PERCENT
            for text, conf in zip(data["text"], data["conf"])
            if float(conf) > 0 and len(text.strip()) >= MIN_WORD_LENGTH
        )

    def read_words(self, image: np.ndarray) -> list[OcrWord]:
        """Non-empty word boxes (Tesseract marks non-word boxes with confidence -1)."""
        data = self._image_to_data(image)
        return [
            OcrWord(text=text.strip(), confidence=float(conf) / PERCENT,
                    left=int(left), top=int(top), width=int(width), height=int(height))
            for text, conf, left, top, width, height in zip(
                data["text"], data["conf"], data["left"], data["top"], data["width"], data["height"])
            if float(conf) >= 0 and text.strip()
        ]

    def _image_to_data(self, image: np.ndarray) -> dict[str, list]:
        try:
            return pytesseract.image_to_data(image, lang=self._languages, output_type=Output.DICT,
                                             timeout=self._timeout_s)
        except (pytesseract.TesseractError, RuntimeError) as exc:
            raise OcrError("tesseract recognition failed") from exc
