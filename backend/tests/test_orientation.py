"""Orientation logic with fake OCR engines (no Tesseract needed)."""
import numpy as np
import pytest

from app.imaging.orientation import (
    FLAG_OSD_FAILED,
    FLAG_UNAVAILABLE,
    FLAG_UNCERTAIN,
    OrientationResult,
    detect_orientation,
)
from app.ocr.base import OcrError, OrientationGuess

MIN_CONFIDENCE = 2.0
# A portrait page; the fake engines tell rotations apart by the image shape
# and one marked corner.
PAGE = np.full((120, 80), 255, np.uint8)
PAGE[:10, :10] = 0


def _marked_corner(image: np.ndarray) -> str:
    height, width = image.shape[:2]
    corners = {"tl": image[:5, :5], "tr": image[:5, width - 5:], "br": image[height - 5:, width - 5:], "bl": image[height - 5:, :5]}
    return min(corners, key=lambda k: corners[k].mean())


class Engine:
    def __init__(self, osd=None, osd_error=False, scores=None, score_error=False):
        self.osd, self.osd_error, self.scores, self.score_error = osd, osd_error, scores or {}, score_error
        self.trials = 0

    def detect_orientation(self, image):
        if self.osd_error:
            raise OcrError("osd failed")
        return self.osd

    def text_confidence(self, image):
        self.trials += 1
        if self.score_error:
            raise OcrError("ocr failed")
        # The marked corner moves clockwise tl -> tr -> br -> bl as the page turns.
        return self.scores.get(_marked_corner(image), 0.0)


def test_no_engine_is_flagged():
    assert detect_orientation(PAGE, None, MIN_CONFIDENCE) == OrientationResult(0, (FLAG_UNAVAILABLE,))


def test_confident_osd_is_used():
    engine = Engine(osd=OrientationGuess(90, confidence=5.0))
    assert detect_orientation(PAGE, engine, MIN_CONFIDENCE) == OrientationResult(90)
    assert engine.trials == 0


@pytest.mark.parametrize("osd", [OrientationGuess(90, 0.5), OrientationGuess(45, 9.0)], ids=["low_conf", "not_quarter"])
def test_doubtful_osd_falls_back_to_trials(osd):
    engine = Engine(osd=osd, scores={"br": 10.0, "tl": 2.0})
    assert detect_orientation(PAGE, engine, MIN_CONFIDENCE) == OrientationResult(180)
    assert engine.trials == 4


def test_osd_error_falls_back_and_is_flagged():
    engine = Engine(osd_error=True, scores={"tr": 10.0})
    assert detect_orientation(PAGE, engine, MIN_CONFIDENCE) == OrientationResult(90, (FLAG_OSD_FAILED,))


def test_trial_without_clear_winner_is_not_rotated():
    engine = Engine(osd=OrientationGuess(0, 0.1), scores={"tr": 10.0, "bl": 9.0})
    assert detect_orientation(PAGE, engine, MIN_CONFIDENCE) == OrientationResult(0, (FLAG_UNCERTAIN,))


def test_trial_with_no_text_is_not_rotated():
    engine = Engine(osd=OrientationGuess(0, 0.1))
    assert detect_orientation(PAGE, engine, MIN_CONFIDENCE) == OrientationResult(0, (FLAG_UNCERTAIN,))


def test_trial_ocr_error_is_flagged_not_raised():
    engine = Engine(osd_error=True, score_error=True)
    result = detect_orientation(PAGE, engine, MIN_CONFIDENCE)
    assert result == OrientationResult(0, (FLAG_OSD_FAILED, FLAG_UNCERTAIN))
