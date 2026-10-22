"""Crops of a single value, for the OCR cross-check and the second look."""
from __future__ import annotations

import cv2
import numpy as np


def crop_box(image: np.ndarray, x0: float, y0: float, x1: float, y1: float, padding_px: int) -> np.ndarray:
    """The box (rounded to whole pixels) grown by ``padding_px`` on every side
    and clipped to the image. Raises ``ValueError`` for an empty box or one
    that lies outside the image."""
    if x1 <= x0 or y1 <= y0:
        raise ValueError("box is empty")
    height, width = image.shape[:2]
    left, top = max(0, round(x0) - padding_px), max(0, round(y0) - padding_px)
    right, bottom = min(width, round(x1) + padding_px), min(height, round(y1) + padding_px)
    if right <= left or bottom <= top:
        raise ValueError("box lies outside the image")
    return image[top:bottom, left:right].copy()


def enlarge(image: np.ndarray, factor: float, max_edge_px: int) -> np.ndarray:
    """Scale up by ``factor`` (bicubic), but never past ``max_edge_px`` on the
    longer side; an image already larger than that is left as is."""
    height, width = image.shape[:2]
    scale = min(factor, max_edge_px / max(height, width))
    if scale <= 1:
        return image
    return cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_CUBIC)
