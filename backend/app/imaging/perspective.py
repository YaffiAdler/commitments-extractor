"""Perspective correction for phone photos of a page.

The page outline is looked for as a large convex quadrilateral of edges
(Canny + findContours + approxPolyDP). It is only accepted when the paper
inside is clearly brighter than what lies outside — this keeps a scan whose
table border is a large rectangle on white paper untouched. If no clear page
is found the image is returned as is.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.imaging.geometry import downscale_to_max_edge, resize_scale, to_gray

ANALYSIS_MAX_EDGE = 1000
BLUR_KERNEL = (5, 5)
CANNY_LOW, CANNY_HIGH = 50, 150
EDGE_DILATE_KERNEL = np.ones((3, 3), np.uint8)
CANDIDATE_CONTOURS = 5
POLY_APPROX_EPSILON = 0.02  # fraction of the contour perimeter
QUAD_CORNERS = 4
MIN_PAGE_AREA_FRACTION = 0.2
# A quad this close to the full frame means the image already is the page.
MAX_PAGE_AREA_FRACTION = 0.97
MIN_PAPER_CONTRAST = 40  # gray levels, paper mean minus surroundings mean


@dataclass(frozen=True)
class PageQuad:
    """Page corners in image pixels, ordered top-left, top-right, bottom-right, bottom-left."""

    corners: np.ndarray  # shape (4, 2), float32


def correct_perspective(image: np.ndarray) -> tuple[np.ndarray, bool]:
    """Warp the page to a flat rectangle; return the image and whether it changed."""
    quad = find_page_quad(image)
    if quad is None:
        return image, False
    return warp_to_rectangle(image, quad), True


def find_page_quad(image: np.ndarray) -> PageQuad | None:
    gray = downscale_to_max_edge(to_gray(image), ANALYSIS_MAX_EDGE)
    for contour in _largest_contours(gray):
        polygon = cv2.approxPolyDP(contour, POLY_APPROX_EPSILON * cv2.arcLength(contour, True), True)
        if len(polygon) != QUAD_CORNERS or not cv2.isContourConvex(polygon):
            continue
        area_fraction = cv2.contourArea(polygon) / gray.size
        if MIN_PAGE_AREA_FRACTION <= area_fraction <= MAX_PAGE_AREA_FRACTION and _is_paper(gray, polygon):
            scale = resize_scale(image, ANALYSIS_MAX_EDGE)
            return PageQuad(_order_corners(polygon.reshape(QUAD_CORNERS, 2).astype(np.float32) / scale))
    return None


def warp_to_rectangle(image: np.ndarray, quad: PageQuad) -> np.ndarray:
    top_left, top_right, bottom_right, bottom_left = quad.corners
    width = int(round(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left))))
    height = int(round(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right))))
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad.corners, target)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def _largest_contours(gray: np.ndarray) -> list[np.ndarray]:
    edges = cv2.Canny(cv2.GaussianBlur(gray, BLUR_KERNEL, 0), CANNY_LOW, CANNY_HIGH)
    edges = cv2.dilate(edges, EDGE_DILATE_KERNEL)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return sorted(contours, key=cv2.contourArea, reverse=True)[:CANDIDATE_CONTOURS]


def _is_paper(gray: np.ndarray, polygon: np.ndarray) -> bool:
    inside = np.zeros_like(gray)
    cv2.fillConvexPoly(inside, polygon, 255)
    outside = inside == 0
    if not outside.any():
        return False
    return float(gray[inside > 0].mean()) - float(gray[outside].mean()) >= MIN_PAPER_CONTRAST


def _order_corners(points: np.ndarray) -> np.ndarray:
    sums, diffs = points.sum(axis=1), np.diff(points, axis=1).ravel()  # diff = y - x
    return np.array([points[np.argmin(sums)], points[np.argmin(diffs)],
                     points[np.argmax(sums)], points[np.argmax(diffs)]], dtype=np.float32)
