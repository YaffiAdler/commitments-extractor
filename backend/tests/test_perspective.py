import cv2
import numpy as np
import pytest

from app.imaging.perspective import correct_perspective, find_page_quad

from synthetic import variants


def test_page_corners_are_found_in_order(dummy_form):
    quad = find_page_quad(variants.perspective(dummy_form))
    assert quad is not None
    top_left, top_right, bottom_right, bottom_left = quad.corners
    assert top_left[0] < top_right[0] and bottom_left[0] < bottom_right[0]
    assert top_left[1] < bottom_left[1] and top_right[1] < bottom_right[1]


def test_scan_with_table_border_is_untouched():
    """A big ruled rectangle on white paper is not a page outline."""
    scan = np.full((1400, 1000, 3), 255, np.uint8)
    cv2.rectangle(scan, (80, 80), (920, 1320), (0, 0, 0), 4)
    result, changed = correct_perspective(scan)
    assert not changed and result is scan


@pytest.mark.parametrize("image", [np.full((300, 200, 3), 255, np.uint8), np.zeros((300, 200, 3), np.uint8)],
                         ids=["white", "black"])
def test_no_quad_found(image):
    assert find_page_quad(image) is None
