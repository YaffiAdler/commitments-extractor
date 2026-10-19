"""Page orientation (0/90/180/270) from the image itself.

The PDF ``/Rotate`` flag cannot be trusted (it is 0 on every sample page).
First Tesseract OSD; if it fails or is not confident, every quarter turn is
OCR'd and the one whose text reads best wins — but only by a clear margin.
Otherwise the page is left as is and flagged, never rotated on a guess.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.imaging.geometry import QUARTER_TURNS, downscale_to_max_edge, rotate_quarter_turns, to_gray
from app.imaging.illumination import binarize
from app.ocr.base import OcrEngine, OcrError

OSD_MAX_EDGE = 2000
TRIAL_MAX_EDGE = 1200
# The best rotation must read this much better than the runner-up.
TRIAL_MIN_MARGIN = 1.25

FLAG_UNAVAILABLE = "orientation_unavailable"
FLAG_OSD_FAILED = "orientation_osd_failed"
FLAG_UNCERTAIN = "orientation_uncertain"


@dataclass(frozen=True)
class OrientationResult:
    rotation: int  # clockwise degrees to apply
    flags: tuple[str, ...] = ()


def detect_orientation(image: np.ndarray, engine: OcrEngine | None, min_osd_confidence: float) -> OrientationResult:
    if engine is None:
        return OrientationResult(0, (FLAG_UNAVAILABLE,))
    gray = to_gray(image)
    try:
        guess = engine.detect_orientation(downscale_to_max_edge(gray, OSD_MAX_EDGE))
    except OcrError:
        return _trial_rotations(gray, engine, (FLAG_OSD_FAILED,))
    if guess.confidence >= min_osd_confidence and guess.rotation in QUARTER_TURNS:
        return OrientationResult(guess.rotation)
    return _trial_rotations(gray, engine, ())


def _trial_rotations(gray: np.ndarray, engine: OcrEngine, flags: tuple[str, ...]) -> OrientationResult:
    small = binarize(downscale_to_max_edge(gray, TRIAL_MAX_EDGE))
    try:
        scores = {r: engine.text_confidence(rotate_quarter_turns(small, r)) for r in QUARTER_TURNS}
    except OcrError:
        return OrientationResult(0, (*flags, FLAG_UNCERTAIN))
    ranked = sorted(scores, key=scores.__getitem__, reverse=True)
    best, runner_up = scores[ranked[0]], scores[ranked[1]]
    if best <= 0 or best < runner_up * TRIAL_MIN_MARGIN:
        return OrientationResult(0, (*flags, FLAG_UNCERTAIN))
    return OrientationResult(ranked[0], flags)
