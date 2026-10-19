"""Stage 2: straighten and clean a page image before reading.

Chain (each step can be switched off in ``PreprocessSettings``):

1. perspective — flatten a phone photo of a page (untouched if no clear page outline)
2. deskew      — small-angle rotation from table lines / text lines
3. orientation — quarter turns, from the image (PDF ``/Rotate`` is always 0)
4. illumination — background subtraction + CLAHE on the lightness channel
5. resize      — longer side ~2000 px for the VLM
6. binarize    — a separate copy for Tesseract only

Geometry runs first, on the full-resolution image, so ``PageImage.original``
(kept for crops) and ``ocr_image`` share one coordinate system and ``image``
(sent to the VLM) is the same picture scaled by ``PageImage.image_scale``.
Deskew comes before orientation because OSD reads straight lines better and
the skew estimate does not depend on the quarter turn.
Iron rule: the VLM image is never binarized.
"""
from __future__ import annotations

from dataclasses import replace

from app.config import PreprocessSettings
from app.imaging.deskew import deskew
from app.imaging.geometry import downscale_to_max_edge, rotate_quarter_turns
from app.imaging.illumination import binarize, normalize_illumination
from app.imaging.orientation import detect_orientation
from app.imaging.perspective import correct_perspective
from app.ocr.base import OcrEngine
from app.pipeline.ingest import PageImage

FLAG_PERSPECTIVE_CORRECTED = "perspective_corrected"


def preprocess(page: PageImage, settings: PreprocessSettings, ocr: OcrEngine | None) -> PageImage:
    image, flags = page.original, list(page.flags)

    if settings.fix_perspective:
        image, corrected = correct_perspective(image)
        if corrected:
            flags.append(FLAG_PERSPECTIVE_CORRECTED)

    skew_deg = 0.0
    if settings.deskew:
        image, skew_deg = deskew(image, settings.deskew_min_angle_deg, settings.deskew_max_angle_deg)

    rotation = 0
    if settings.fix_orientation:
        orientation = detect_orientation(image, ocr, settings.osd_min_confidence)
        image, rotation = rotate_quarter_turns(image, orientation.rotation), orientation.rotation
        flags.extend(orientation.flags)

    cleaned = normalize_illumination(image) if settings.normalize_illumination else image
    for_vlm = downscale_to_max_edge(cleaned, settings.vlm_max_edge_px) if settings.resize_for_vlm else cleaned
    for_ocr = binarize(cleaned) if settings.binarize_for_ocr else None

    return replace(page, image=for_vlm, original=image, ocr_image=for_ocr,
                   rotation=rotation, skew_deg=skew_deg, flags=tuple(flags))
