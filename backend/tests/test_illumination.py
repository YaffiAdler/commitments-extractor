import cv2
import numpy as np

from app.imaging.illumination import (CLAHE_CLIP_LIMIT, CLAHE_TILES, FLOAT_STRIP_ROWS, FULL_SCALE,
                                      MIN_BACKGROUND_LEVEL, PAPER_WHITE_POINT, binarize, estimate_background,
                                      normalize_illumination)

from synthetic import variants


def test_uneven_lighting_is_flattened(dummy_form):
    dark = variants.dark(dummy_form)
    result = normalize_illumination(dark)
    height, width = dark.shape[:2]
    corner = (slice(0, height // 8), slice(0, width // 8))
    opposite = (slice(-height // 8, None), slice(-width // 8, None))
    gap_before = abs(np.median(dark[opposite]) - np.median(dark[corner]))
    gap_after = abs(np.median(result[opposite]) - np.median(result[corner]))
    assert gap_after < gap_before / 4


def test_colour_is_kept(dummy_form):
    page = dummy_form.copy()
    page[100:200, 100:200] = (0, 200, 255)  # a yellow highlighter mark (BGR)
    result = normalize_illumination(page)
    assert result.shape == page.shape
    blue, green, red = result[150, 150].astype(int)
    assert red > blue + 80 and green > blue + 50


def test_gray_input_stays_gray():
    gray = np.full((100, 80), 120, np.uint8)
    assert normalize_illumination(gray).shape == gray.shape


def test_binarize_gives_black_text_on_white(dummy_form):
    binary = binarize(variants.dark(dummy_form))
    assert binary.ndim == 2 and set(np.unique(binary)) <= {0, 255}
    assert (binary == 255).mean() > 0.85  # mostly paper


def test_dark_paper_comes_out_clean_without_bands(dummy_form):
    paper = (dummy_form == 255).all(axis=2)
    result = normalize_illumination(variants.dark(dummy_form))
    assert result[paper].std() < 1.0


def test_division_by_strips_gives_the_whole_page_result(dummy_form):
    """The row strips must give the same pixels as one float32 division over the whole page."""
    lightness = cv2.cvtColor(variants.dark(dummy_form), cv2.COLOR_BGR2GRAY)
    assert lightness.shape[0] > FLOAT_STRIP_ROWS
    background = np.maximum(estimate_background(lightness), MIN_BACKGROUND_LEVEL).astype(np.float32)
    flat = np.clip(lightness.astype(np.float32) / (background * PAPER_WHITE_POINT) * FULL_SCALE,
                   0, FULL_SCALE).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILES)
    assert np.array_equal(normalize_illumination(lightness), clahe.apply(flat))
