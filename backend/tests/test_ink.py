import cv2
import numpy as np

from app.imaging.ink import separate_ink

WHITE = 255
BLUE = (170, 50, 30)    # BGR ballpoint blue
BLACK = (20, 20, 20)
SHAPE = (120, 400, 3)
STROKE_PX = 3
# A few strokes shaped roughly like handwritten letters.
STROKES = [((150, 40), (200, 40)), ((200, 40), (200, 75)), ((170, 45), (165, 75)), ((215, 40), (240, 75))]


def _crop() -> np.ndarray:
    crop = np.full(SHAPE, WHITE, np.uint8)
    crop[100:103, :] = BLACK              # ruled line across the crop
    crop[10:110, 5:8] = BLACK             # box edge
    return crop


def _write(crop: np.ndarray, colour) -> np.ndarray:
    for start, end in STROKES:
        cv2.line(crop, start, end, colour, STROKE_PX)
    return crop


def _near(image: np.ndarray, colour) -> int:
    return int(np.all(np.abs(image.astype(int) - colour) < 40, axis=2).sum())


def test_lines_are_removed_and_coloured_handwriting_kept_without_print():
    crop = _write(_crop(), BLUE)
    cv2.putText(crop, "AB", (300, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, BLACK, 2)  # printed text
    result = separate_ink(crop)
    assert result.shape == crop.shape and result.dtype == np.uint8
    assert _near(result, BLUE) >= 0.9 * _near(crop, BLUE)
    assert _near(result, BLACK) == 0


def test_black_handwriting_is_kept_when_there_is_no_coloured_ink():
    crop = _write(_crop(), BLACK)
    result = separate_ink(crop)
    assert result[100:103, 50:140].min() == WHITE       # the line is gone
    assert result[10:110, 5:8].min() == WHITE          # the box edge too
    assert _near(result[35:80, 140:250], BLACK) >= 0.9 * _near(crop[35:80, 140:250], BLACK)


def test_blank_crop_stays_blank():
    blank = np.full(SHAPE, WHITE, np.uint8)
    assert (separate_ink(blank) == WHITE).all()
