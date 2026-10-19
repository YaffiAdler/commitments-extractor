"""Lighting normalization and binarization.

``normalize_illumination`` works on the lightness channel only (LAB), so the
colours of stamps and stickers survive for the VLM. ``binarize`` is for the
separate Tesseract copy only — the VLM image is never binarized.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.imaging.geometry import to_gray

# The background (paper brightness) is estimated on a reduced image: a max
# filter removes the dark ink, a median blur smooths what is left.
BACKGROUND_SCALE = 8
BACKGROUND_MAX_FILTER = 7
BACKGROUND_MEDIAN = 15
# Caps the gain (255 / floor) so near-black areas are not blown out to white.
MIN_BACKGROUND_LEVEL = 64
FULL_SCALE = 255.0
# Pixels at least this bright relative to the local background are paper.
# Without it the 8-bit steps of a dark photo become visible bands after the
# division and CLAHE amplifies them. Kept high so faint pencil survives.
PAPER_WHITE_POINT = 0.95

CLAHE_CLIP_LIMIT = 2.0
CLAHE_TILES = (8, 8)

BINARIZE_BLOCK_SIZE = 31  # pixels, odd
BINARIZE_OFFSET = 15


def normalize_illumination(image: np.ndarray) -> np.ndarray:
    """Flatten uneven lighting (background subtraction) and boost local
    contrast (CLAHE). Returns an image of the same shape and type."""
    if image.ndim == 2:
        return _normalize_lightness(image)
    lightness, a, b = cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2LAB))
    return cv2.cvtColor(cv2.merge((_normalize_lightness(lightness), a, b)), cv2.COLOR_LAB2BGR)


def binarize(image: np.ndarray) -> np.ndarray:
    """Black text on white (0/255, single channel), for Tesseract only."""
    gray = to_gray(image)
    block = min(BINARIZE_BLOCK_SIZE, _largest_odd_at_most(min(gray.shape)))
    if block < 3:
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        return binary
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
                                 block, BINARIZE_OFFSET)


def estimate_background(lightness: np.ndarray) -> np.ndarray:
    height, width = lightness.shape
    small_size = (max(1, width // BACKGROUND_SCALE), max(1, height // BACKGROUND_SCALE))
    small = cv2.resize(lightness, small_size, interpolation=cv2.INTER_AREA)
    small = cv2.dilate(small, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (BACKGROUND_MAX_FILTER,) * 2))
    small = cv2.medianBlur(small, BACKGROUND_MEDIAN)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def _normalize_lightness(lightness: np.ndarray) -> np.ndarray:
    background = np.maximum(estimate_background(lightness), MIN_BACKGROUND_LEVEL).astype(np.float32)
    paper_level = background * PAPER_WHITE_POINT
    flat = np.clip(lightness.astype(np.float32) / paper_level * FULL_SCALE, 0, FULL_SCALE).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=CLAHE_TILES)
    return clahe.apply(flat)


def _largest_odd_at_most(value: int) -> int:
    return value if value % 2 else value - 1
