import numpy as np
import pytest

from app.imaging.geometry import downscale_to_max_edge, resize_scale, rotate_by_angle, rotate_quarter_turns


def test_quarter_turn_clockwise():
    image = np.zeros((2, 3), np.uint8)
    image[0, 0] = 255  # top-left
    turned = rotate_quarter_turns(image, 90)
    assert turned.shape == (3, 2) and turned[0, 1] == 255  # now top-right


def test_quarter_turn_rejects_other_angles():
    with pytest.raises(ValueError):
        rotate_quarter_turns(np.zeros((2, 2), np.uint8), 45)


def test_rotate_by_angle_keeps_corners_and_fills_white():
    image = np.zeros((100, 200, 3), np.uint8)
    rotated = rotate_by_angle(image, 10)
    assert rotated.shape[0] > 100 and rotated.shape[1] > 200
    assert tuple(rotated[0, 0]) == (255, 255, 255)
    assert (rotated == 0).all(axis=2).sum() == pytest.approx(100 * 200, rel=0.03)


def test_downscale_never_enlarges():
    small = np.zeros((100, 50, 3), np.uint8)
    assert downscale_to_max_edge(small, 2000) is small
    assert resize_scale(small, 2000) == 1.0


def test_downscale_keeps_aspect():
    big = np.zeros((4000, 3000), np.uint8)
    assert downscale_to_max_edge(big, 2000).shape == (2000, 1500)
    assert resize_scale(big, 2000) == 0.5
