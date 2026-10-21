"""The interface the pipeline uses for OCR, so engines can be swapped and mocked."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


class OcrError(RuntimeError):
    """An OCR call failed. The message never contains recognized text."""


@dataclass(frozen=True)
class OrientationGuess:
    rotation: int  # clockwise degrees (0/90/180/270) that make the page upright
    confidence: float  # engine-specific scale


@dataclass(frozen=True)
class OcrWord:
    """One recognized word. Holds patient data — never log ``text``."""

    text: str
    confidence: float  # 0..1
    left: int
    top: int
    width: int
    height: int


@runtime_checkable
class OcrEngine(Protocol):
    def detect_orientation(self, image: np.ndarray) -> OrientationGuess:
        """Orientation of a grayscale page; raises ``OcrError`` if undecidable."""
        ...

    def text_confidence(self, image: np.ndarray) -> float:
        """How well text reads on this (binarized) image; higher is better.
        Used to compare the four rotations of one page."""
        ...

    def read_words(self, image: np.ndarray) -> list[OcrWord]:
        """Every word on a (binarized) page, in reading order, with its box
        in ``image`` pixels; raises ``OcrError`` if recognition fails."""
        ...
