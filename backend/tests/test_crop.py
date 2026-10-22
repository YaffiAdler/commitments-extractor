import numpy as np
import pytest

from app.imaging.crop import crop_box, enlarge


def test_crop_adds_padding_and_clips_to_the_image():
    image = np.zeros((100, 200), np.uint8)
    assert crop_box(image, 50, 40, 70, 50, padding_px=10).shape == (30, 40)
    assert crop_box(image, 0, 0, 20, 10, padding_px=10).shape == (20, 30)  # clipped at the corner


def test_crop_outside_the_image_is_an_error():
    with pytest.raises(ValueError):
        crop_box(np.zeros((100, 200), np.uint8), 300, 10, 320, 20, padding_px=10)


def test_enlarge_by_factor_capped_by_max_edge():
    image = np.zeros((20, 100, 3), np.uint8)
    assert enlarge(image, 2.5, 1000).shape == (50, 250, 3)
    assert enlarge(image, 3, 200).shape == (40, 200, 3)
    assert enlarge(image, 3, 50) is image  # already larger than the cap: unchanged


def test_empty_box_is_an_error():
    with pytest.raises(ValueError):
        crop_box(np.zeros((100, 200), np.uint8), 10, 10, 10, 20, padding_px=10)
