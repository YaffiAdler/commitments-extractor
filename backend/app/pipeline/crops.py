"""The numeric values of a form and their crops, shared by the OCR
cross-check (``ocr_check``), the second look (``reread``) and the name
second look (``reread_name``, with its own padding)."""
from __future__ import annotations

import re

import numpy as np

from app.imaging.crop import crop_box
from app.imaging.geometry import to_gray
from app.pipeline.ingest import PageImage
from app.schemas import BBox, Commitment, Field

DIGITS = "0123456789"
# Padding around the model's box, in ``original`` pixels.
CROP_PADDING_PX = 10
# A crop smaller than this on either side holds no readable digits.
MIN_CROP_EDGE_PX = 8
# Digits with the separators that may sit between digit groups; anything else (letters) is not a number.
_NUMBER_TEXT = re.compile(r"[\d\s./\-]+")
_NON_DIGIT = re.compile(r"\D")


def numeric_fields(commitment: Commitment) -> list[Field[str]]:
    """ID, central and commitment numbers, in that order (the order the checks run in)."""
    return [commitment.id_number, *commitment.central_numbers, *commitment.commitment_numbers]


def digits_of(text: str | None) -> str:
    return _NON_DIGIT.sub("", text or "")


def is_number_text(text: str) -> bool:
    return bool(_NUMBER_TEXT.fullmatch(text)) and bool(digits_of(text))


def crop_field(bbox: BBox, pages: list[PageImage], for_ocr: bool,
               padding_px: int = CROP_PADDING_PX) -> np.ndarray | None:
    """The value's box plus ``padding_px`` from its page: the binarized
    ``ocr_image`` (grayscale ``original`` if there is none) ``for_ocr``, else
    the colour ``original`` (for the model, never binarized). None if the page
    is not in the form, its released original is gone, or the crop is too small to read."""
    page = next((p for p in pages if p.page_no == bbox.page), None)
    if page is None:
        return None
    if for_ocr and page.ocr_image is not None:
        image = page.ocr_image
    else:
        image = page.load_original()
        if image is None:
            return None
        if for_ocr:
            image = to_gray(image)
    return crop_bbox(image, bbox, padding_px)


def crop_bbox(image: np.ndarray, bbox: BBox, padding_px: int = CROP_PADDING_PX) -> np.ndarray | None:
    """``bbox`` plus ``padding_px`` from ``image`` (in ``original``
    coordinates); None if the crop is outside the image or too small to read."""
    try:
        crop = crop_box(image, bbox.x0, bbox.y0, bbox.x1, bbox.y1, padding_px)
    except ValueError:
        return None
    return crop if min(crop.shape[:2]) >= MIN_CROP_EDGE_PX else None
