import cv2
import numpy as np
import pytest

from app.imaging.deskew import deskew, estimate_skew

MAX_ANGLE = 10.0


def _turned(image: np.ndarray, ccw_deg: float) -> np.ndarray:
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), ccw_deg, 1.0)
    return cv2.warpAffine(image, matrix, (width, height), borderValue=(255, 255, 255))


@pytest.mark.parametrize("ccw_deg", [-7.0, -2.0, 1.0, 3.0, 5.0])
def test_estimates_skew_of_ruled_form(dummy_form, ccw_deg):
    assert estimate_skew(_turned(dummy_form, ccw_deg), MAX_ANGLE) == pytest.approx(-ccw_deg, abs=0.3)


def test_estimates_skew_of_text_only_page():
    page = np.full((1400, 1000, 3), 255, np.uint8)
    for line in range(20):
        cv2.putText(page, "dummy text line for testing only", (60, 100 + line * 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    assert estimate_skew(_turned(page, 3.0), MAX_ANGLE) == pytest.approx(-3.0, abs=0.5)


def test_small_skew_is_left_alone(dummy_form):
    slightly = _turned(dummy_form, 0.3)
    result, angle = deskew(slightly, min_angle_deg=0.5, max_angle_deg=MAX_ANGLE)
    assert angle == 0.0 and result is slightly


def test_skew_beyond_max_is_not_trusted(dummy_form):
    result, angle = deskew(_turned(dummy_form, 20.0), min_angle_deg=0.5, max_angle_deg=MAX_ANGLE)
    assert angle == 0.0


def test_blank_page_has_no_skew():
    assert estimate_skew(np.full((500, 400), 255, np.uint8), MAX_ANGLE) is None
