"""Independent measurements of test results (they do not use app.imaging)."""
import math

import cv2
import numpy as np

# The dummy form's table spans 10%-90% of the page width; these bands cross
# its horizontal rules but avoid its vertical ones and the sticker.
LEFT_BAND, RIGHT_BAND = 0.2, 0.6
BAND_WIDTH = 0.08  # wide enough that no letter stroke fills it
RULE_DARK_FRACTION = 0.7
COMPARE_SIZE = (60, 85)


def _ink(image: np.ndarray) -> np.ndarray:
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return ink


def _first_rule_row(ink: np.ndarray, centre: float) -> tuple[float, int]:
    width = ink.shape[1]
    x0, x1 = int(width * (centre - BAND_WIDTH / 2)), int(width * (centre + BAND_WIDTH / 2))
    dark = ink[:, x0:x1].mean(axis=1)
    rows = np.flatnonzero(dark >= RULE_DARK_FRACTION)
    if not rows.size:
        raise AssertionError("no horizontal rule found")
    return (x0 + x1) / 2, int(rows[0])


def rule_tilt_deg(image: np.ndarray) -> float:
    """Tilt of the dummy form's top table rule (0 = level)."""
    ink = _ink(image)
    (x_left, y_left), (x_right, y_right) = (_first_rule_row(ink, c) for c in (LEFT_BAND, RIGHT_BAND))
    return math.degrees(math.atan2(y_right - y_left, x_right - x_left))


def best_quarter_turn(result: np.ndarray, reference: np.ndarray) -> int:
    """The clockwise quarter turn of ``result`` that best matches ``reference``
    (0 means ``result`` is upright like the reference)."""
    target = cv2.resize(_ink(reference).astype(np.float32), COMPARE_SIZE, interpolation=cv2.INTER_AREA)
    turns = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    scores = {}
    for turn, code in turns.items():
        candidate = _ink(result) if code is None else cv2.rotate(_ink(result), code)
        small = cv2.resize(candidate.astype(np.float32), COMPARE_SIZE, interpolation=cv2.INTER_AREA)
        scores[turn] = float(np.corrcoef(small.ravel(), target.ravel())[0, 1])
    return max(scores, key=scores.__getitem__)
