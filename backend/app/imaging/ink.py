"""Separate handwriting from the printed form on a small colour crop (the
optional ``ink`` variant of the name second look).

On the dark ("ink") pixels found by an adaptive threshold:

1. long straight horizontal and vertical runs — ruled lines and box edges —
   are removed (a morphological opening with a kernel spanning
   ``HORIZONTAL_LINE_FRACTION`` of the width / ``VERTICAL_LINE_FRACTION`` of
   the height);
2. when at least ``COLOURED_SHARE_MIN`` of the remaining ink is coloured
   (saturation above ``SATURATION_MIN``: blue or red ballpoint), only the
   coloured ink is kept, since printed text is black or grey. Black
   handwriting is kept with the print.

Kept pixels keep their colour; everything else becomes white. Strokes that
lie on a ruled line lose the part covered by it. The result goes to the model
only; the OCR copy is never made from it.
"""
from __future__ import annotations

import cv2
import numpy as np

ADAPTIVE_BLOCK_PX = 31  # odd: neighbourhood of the adaptive threshold
ADAPTIVE_OFFSET = 15    # how much darker than its neighbourhood a pixel must be to count as ink
HORIZONTAL_LINE_FRACTION = 0.35
VERTICAL_LINE_FRACTION = 0.6
MIN_LINE_PX = 15
SATURATION_MIN = 60
COLOURED_SHARE_MIN = 0.25
EDGE_KERNEL = np.ones((3, 3), np.uint8)  # grows a mask by one pixel (anti-aliased stroke edges)
WHITE = 255
BINARY_ON = 255


def ink_mask(image: np.ndarray) -> np.ndarray:
    """Pixels darker than their neighbourhood (ink and print), as a 0/255 mask."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.adaptiveThreshold(gray, BINARY_ON, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV,
                                 ADAPTIVE_BLOCK_PX, ADAPTIVE_OFFSET)


def line_mask(ink: np.ndarray) -> np.ndarray:
    """The long horizontal and vertical runs of ``ink`` (form lines), grown by one pixel."""
    height, width = ink.shape
    horizontal = cv2.getStructuringElement(cv2.MORPH_RECT, (max(MIN_LINE_PX, int(width * HORIZONTAL_LINE_FRACTION)), 1))
    vertical = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(MIN_LINE_PX, int(height * VERTICAL_LINE_FRACTION))))
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, horizontal) | cv2.morphologyEx(ink, cv2.MORPH_OPEN, vertical)
    return cv2.dilate(lines, EDGE_KERNEL)


def separate_ink(image: np.ndarray) -> np.ndarray:
    """``image`` (BGR) with only the handwriting left on white (see the module docstring)."""
    ink = ink_mask(image)
    ink[line_mask(ink) > 0] = 0
    saturation = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[..., 1]
    coloured = (saturation >= SATURATION_MIN) & (ink > 0)
    kept = ink > 0
    if kept.any() and coloured.sum() >= COLOURED_SHARE_MIN * kept.sum():
        kept &= cv2.dilate(coloured.astype(np.uint8), EDGE_KERNEL) > 0
    result = np.full_like(image, WHITE)
    result[kept] = image[kept]
    return result
