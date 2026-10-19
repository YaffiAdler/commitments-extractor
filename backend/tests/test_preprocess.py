"""The preprocessing chain on the six synthetic variants (dummy form only)."""
from dataclasses import replace

import numpy as np
import pytest

from app.config import PreprocessSettings
from app.imaging.orientation import FLAG_UNAVAILABLE
from app.ocr.base import OrientationGuess
from app.pipeline.preprocess import FLAG_PERSPECTIVE_CORRECTED, preprocess
from image_checks import best_quarter_turn, rule_tilt_deg
from synthetic import page_of, variants

MAX_RESIDUAL_TILT_DEG = 0.5
SETTINGS = PreprocessSettings()
ALL_OFF = PreprocessSettings(fix_perspective=False, deskew=False, fix_orientation=False,
                             normalize_illumination=False, resize_for_vlm=False, binarize_for_ocr=False)


class FixedOsd:
    """Fake OCR engine: OSD returns a fixed answer (the real engine is
    Tesseract, which these tests must not depend on)."""

    def __init__(self, rotation: int) -> None:
        self.rotation = rotation

    def detect_orientation(self, image: np.ndarray) -> OrientationGuess:
        return OrientationGuess(self.rotation, confidence=10.0)

    def text_confidence(self, image: np.ndarray) -> float:
        raise AssertionError("not expected")


# variant -> clockwise correction a correct OSD reports for it
VARIANT_CORRECTION = {"rotate_90": 270, "rotate_180": 180, "skew_5": 0, "dark": 0, "perspective": 0, "noise": 0}


@pytest.mark.parametrize("name", list(variants.VARIANTS))
def test_variant_comes_out_upright_and_level(dummy_form, name):
    distorted = variants.VARIANTS[name](dummy_form)
    result = preprocess(page_of(distorted), SETTINGS, FixedOsd(VARIANT_CORRECTION[name]))
    assert best_quarter_turn(result.original, dummy_form) == 0
    assert abs(rule_tilt_deg(result.image)) <= MAX_RESIDUAL_TILT_DEG
    assert result.rotation == VARIANT_CORRECTION[name]


def test_skew_is_measured(dummy_form):
    result = preprocess(page_of(variants.skew_5(dummy_form)), SETTINGS, None)
    # The variant turns the content 5° counter-clockwise; undoing it is clockwise (negative).
    assert result.skew_deg == pytest.approx(-variants.SKEW_DEG, abs=0.3)


def test_perspective_photo_is_flattened_to_the_page(dummy_form):
    photo = variants.perspective(dummy_form)
    result = preprocess(page_of(photo), SETTINGS, None)
    assert FLAG_PERSPECTIVE_CORRECTED in result.flags
    page_aspect = dummy_form.shape[0] / dummy_form.shape[1]
    assert result.original.shape[0] / result.original.shape[1] == pytest.approx(page_aspect, rel=0.08)
    # The dark table around the page is gone.
    assert result.original.shape[0] < photo.shape[0] * 0.9


@pytest.mark.parametrize("name", ["dark", "noise", "skew_5"])
def test_scan_without_page_outline_is_not_warped(dummy_form, name):
    result = preprocess(page_of(variants.VARIANTS[name](dummy_form)), SETTINGS, None)
    assert FLAG_PERSPECTIVE_CORRECTED not in result.flags


def test_dark_page_is_brightened(dummy_form):
    dark = variants.dark(dummy_form)
    result = preprocess(page_of(dark), SETTINGS, None)
    assert np.median(result.image) > np.median(dark) + 80


def test_vlm_image_is_not_binarized_but_ocr_copy_is(dummy_form):
    page = page_of(variants.dark(dummy_form))
    result = preprocess(page, SETTINGS, None)
    assert result.image.ndim == 3 and len(np.unique(result.image)) > 50
    assert set(np.unique(result.ocr_image)) <= {0, 255}
    assert result.ocr_image.shape == result.original.shape[:2]


def test_vlm_image_is_downscaled_and_original_kept(dummy_form):
    big = np.repeat(np.repeat(dummy_form, 2, axis=0), 2, axis=1)  # 3510 x 2480
    result = preprocess(page_of(big), replace(SETTINGS, vlm_max_edge_px=2000), None)
    assert max(result.image.shape[:2]) == 2000
    assert result.original.shape == big.shape
    assert result.image_scale == pytest.approx(2000 / big.shape[0], rel=0.01)


def test_every_step_can_be_switched_off(dummy_form):
    distorted = variants.skew_5(dummy_form)
    result = preprocess(page_of(distorted), ALL_OFF, FixedOsd(90))
    assert result.image is distorted and result.original is distorted
    assert result.ocr_image is None and result.rotation == 0 and result.skew_deg == 0.0
    assert result.flags == ()


def test_without_ocr_engine_orientation_is_flagged_not_guessed(dummy_form):
    result = preprocess(page_of(variants.rotate_90(dummy_form)), SETTINGS, None)
    assert result.rotation == 0
    assert FLAG_UNAVAILABLE in result.flags


def test_page_identity_is_kept(dummy_form):
    page = replace(page_of(dummy_form), page_no=3, dpi=150.0)
    result = preprocess(page, SETTINGS, None)
    assert (result.file_id, result.page_no, result.original_path, result.dpi) == (
        page.file_id, page.page_no, page.original_path, page.dpi)


@pytest.mark.parametrize("image", [
    np.full((400, 300, 3), 255, np.uint8),           # blank page
    np.zeros((400, 300, 3), np.uint8),               # black page
    np.full((2, 2, 3), 128, np.uint8),               # tiny
    np.random.default_rng(1).integers(0, 256, (300, 300, 3), dtype=np.uint8),  # pure noise
], ids=["blank", "black", "tiny", "noise"])
def test_degenerate_pages_do_not_crash(image):
    result = preprocess(page_of(image), SETTINGS, None)
    assert result.image.ndim == 3 and result.ocr_image is not None
