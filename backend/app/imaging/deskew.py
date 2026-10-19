"""Small-angle skew correction.

The skew is the angle of the page's lines in image coordinates (y down), so a
positive angle means the lines fall to the right. Two estimators:

1. Hough: long straight lines (form tables and borders), horizontal or vertical.
2. minAreaRect: words merged into text-line blobs, for pages without ruling.

Each line or blob votes with its length; the length-weighted median is used.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from app.imaging.geometry import downscale_to_max_edge, rotate_by_angle, to_gray

ANALYSIS_MAX_EDGE = 1200
RIGHT_ANGLE_DEG = 90.0
HALF_RIGHT_ANGLE_DEG = 45.0

BLUR_KERNEL = (5, 5)
CANNY_LOW, CANNY_HIGH = 50, 150
HOUGH_RHO_PX = 1
HOUGH_THETA_RAD = math.pi / 360  # 0.5 degree; the angle itself comes from the line end points
HOUGH_VOTES = 100
HOUGH_MIN_LENGTH_FRACTION = 0.25  # of the shorter image side
HOUGH_MAX_GAP_FRACTION = 0.01
MIN_HOUGH_LINES = 3

# Words are merged into line blobs by a wide, flat closing.
TEXT_LINE_KERNEL_FRACTION = 0.03  # kernel width relative to the image width
TEXT_LINE_KERNEL_HEIGHT = 3
TEXT_BLOB_MIN_LENGTH_FRACTION = 0.08
TEXT_BLOB_MIN_ELONGATION = 5.0
MIN_TEXT_BLOBS = 5


def deskew(image: np.ndarray, min_angle_deg: float, max_angle_deg: float) -> tuple[np.ndarray, float]:
    """Straighten ``image`` when its skew is in (min, max]; return the image
    and the counter-clockwise rotation applied (0.0 if untouched)."""
    angle = estimate_skew(image, max_angle_deg)
    if angle is None or abs(angle) <= min_angle_deg:
        return image, 0.0
    return rotate_by_angle(image, angle), angle


def estimate_skew(image: np.ndarray, max_angle_deg: float) -> float | None:
    """Skew in degrees (positive = lines fall to the right), or None if the
    page has no usable lines within ``max_angle_deg``."""
    gray = downscale_to_max_edge(to_gray(image), ANALYSIS_MAX_EDGE)
    for estimator in (_hough_votes, _text_blob_votes):
        angles, weights = estimator(gray, max_angle_deg)
        if angles.size:
            return _weighted_median(angles, weights)
    return None


def _fold_to_skew(angle_deg: np.ndarray) -> np.ndarray:
    """Map any line direction to its deviation from the nearest axis, in [-45, 45)."""
    return (angle_deg + HALF_RIGHT_ANGLE_DEG) % RIGHT_ANGLE_DEG - HALF_RIGHT_ANGLE_DEG


def _within(angles: np.ndarray, weights: np.ndarray, max_angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    keep = np.abs(angles) <= max_angle_deg
    return angles[keep], weights[keep]


def _hough_votes(gray: np.ndarray, max_angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    edges = cv2.Canny(cv2.GaussianBlur(gray, BLUR_KERNEL, 0), CANNY_LOW, CANNY_HIGH)
    short_side = min(gray.shape)
    lines = cv2.HoughLinesP(edges, HOUGH_RHO_PX, HOUGH_THETA_RAD, HOUGH_VOTES,
                            minLineLength=int(short_side * HOUGH_MIN_LENGTH_FRACTION),
                            maxLineGap=max(1, int(short_side * HOUGH_MAX_GAP_FRACTION)))
    if lines is None or len(lines) < MIN_HOUGH_LINES:
        return np.empty(0), np.empty(0)
    x1, y1, x2, y2 = lines.reshape(-1, 4).astype(float).T
    angles = _fold_to_skew(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
    return _within(angles, np.hypot(x2 - x1, y2 - y1), max_angle_deg)


def _text_blob_votes(gray: np.ndarray, max_angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    kernel_width = max(3, int(gray.shape[1] * TEXT_LINE_KERNEL_FRACTION))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, TEXT_LINE_KERNEL_HEIGHT))
    blobs = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_length = gray.shape[1] * TEXT_BLOB_MIN_LENGTH_FRACTION
    angles, weights = [], []
    for contour in contours:
        corners = cv2.boxPoints(cv2.minAreaRect(contour))
        edge_a, edge_b = corners[1] - corners[0], corners[2] - corners[1]
        long_edge, short_edge = (edge_a, edge_b) if np.hypot(*edge_a) >= np.hypot(*edge_b) else (edge_b, edge_a)
        length, thickness = np.hypot(*long_edge), max(np.hypot(*short_edge), 1.0)
        if length >= min_length and length / thickness >= TEXT_BLOB_MIN_ELONGATION:
            angles.append(math.degrees(math.atan2(long_edge[1], long_edge[0])))
            weights.append(length)
    if len(angles) < MIN_TEXT_BLOBS:
        return np.empty(0), np.empty(0)
    return _within(_fold_to_skew(np.array(angles)), np.array(weights), max_angle_deg)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cumulative, cumulative[-1] / 2)])
