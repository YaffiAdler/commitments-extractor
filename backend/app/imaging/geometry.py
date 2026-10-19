"""Basic geometric operations on BGR or grayscale ``uint8`` images."""
from __future__ import annotations

import cv2
import numpy as np

WHITE = 255
QUARTER_TURNS = (0, 90, 180, 270)
_CV_ROTATE_CLOCKWISE = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def to_gray(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def rotate_quarter_turns(image: np.ndarray, clockwise_deg: int) -> np.ndarray:
    """Rotate by 0/90/180/270 degrees clockwise (lossless)."""
    if clockwise_deg not in QUARTER_TURNS:
        raise ValueError("rotation must be one of 0, 90, 180, 270")
    if clockwise_deg == 0:
        return image
    return cv2.rotate(image, _CV_ROTATE_CLOCKWISE[clockwise_deg])


def rotate_by_angle(image: np.ndarray, counterclockwise_deg: float) -> np.ndarray:
    """Rotate by a small angle, enlarging the canvas so no corner is cut off;
    the new area is filled with white (paper)."""
    height, width = image.shape[:2]
    center = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(center, counterclockwise_deg, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(round(height * sin + width * cos))
    new_height = int(round(height * cos + width * sin))
    matrix[0, 2] += new_width / 2 - center[0]
    matrix[1, 2] += new_height / 2 - center[1]
    fill = (WHITE,) * (1 if image.ndim == 2 else image.shape[2])
    return cv2.warpAffine(image, matrix, (new_width, new_height), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=fill)


def downscale_to_max_edge(image: np.ndarray, max_edge: int) -> np.ndarray:
    """Shrink so the longer side is at most ``max_edge``; never enlarges."""
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_edge:
        return image
    scale = max_edge / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def resize_scale(image: np.ndarray, max_edge: int) -> float:
    """Factor ``downscale_to_max_edge`` applies to ``image`` (1.0 if none)."""
    return min(1.0, max_edge / max(image.shape[:2]))
