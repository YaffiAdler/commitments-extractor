"""Stage 1: load an uploaded file (PDF or image) as one image per page.

The file type is detected from its magic bytes, never from the extension.
PDF pages that are a single full-page scan are extracted at their native
resolution; any other PDF page is rendered at ``IngestLimits.render_dpi``.
Images get their EXIF orientation applied. A page over
``IngestLimits.max_image_pixels`` is rejected before it is decoded or rendered. Bad input raises an
``IngestError`` subclass whose message never contains file content.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from app.config import Settings

BYTES_PER_MB = 1024 * 1024
PDF_POINTS_PER_INCH = 72
MAGIC_BYTES_NEEDED = 12
# An image covering at least this fraction of the page area is a full-page scan.
FULL_PAGE_COVERAGE = 0.95
# Tolerance (in PDF points) for the off-diagonal terms of an image transform.
AXIS_ALIGNED_TOLERANCE = 1e-3
RGB_CHANNELS = 3


class FileKind(str, Enum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    WEBP = "webp"


_MAGIC: tuple[tuple[bytes, FileKind], ...] = (
    (b"%PDF-", FileKind.PDF),
    (b"\x89PNG\r\n\x1a\n", FileKind.PNG),
    (b"\xff\xd8\xff", FileKind.JPEG),
    (b"II*\x00", FileKind.TIFF),
    (b"MM\x00*", FileKind.TIFF),
)
_WEBP_RIFF, _WEBP_TAG, _WEBP_TAG_OFFSET = b"RIFF", b"WEBP", 8

FILE_SUFFIXES = {
    FileKind.PDF: ".pdf",
    FileKind.PNG: ".png",
    FileKind.JPEG: ".jpg",
    FileKind.TIFF: ".tif",
    FileKind.WEBP: ".webp",
}


class IngestError(ValueError):
    """The file cannot be loaded. The message never contains file content."""


class UnsupportedFileTypeError(IngestError):
    pass


class FileTooLargeError(IngestError):
    pass


class TooManyPagesError(IngestError):
    pass


class EncryptedPdfError(IngestError):
    pass


class CorruptFileError(IngestError):
    pass


@dataclass(frozen=True)
class IngestLimits:
    """Limits for one file; built from ``Settings`` (no defaults of its own)."""

    max_file_bytes: int
    max_pages: int
    render_dpi: int
    max_image_pixels: int

    @classmethod
    def from_settings(cls, settings: Settings) -> IngestLimits:
        return cls(
            max_file_bytes=settings.max_upload_mb * BYTES_PER_MB,
            max_pages=settings.max_pages,
            render_dpi=settings.render_dpi,
            max_image_pixels=settings.max_image_pixels,
        )


@dataclass(frozen=True, eq=False)
class PageImage:
    """One page as an image. Holds patient data — never log the pixels.

    Images are BGR ``uint8`` arrays (OpenCV convention). Straight out of
    ``ingest`` ``image`` and ``original`` are the same array; ``preprocess``
    replaces ``image`` with the cleaned, downscaled copy that is sent to the
    VLM, keeps the geometry-corrected full-resolution ``original`` for crops,
    and adds a binarized ``ocr_image`` (same geometry as ``original``) for
    Tesseract.
    """

    file_id: str  # stored file name (generated, not the user's path)
    page_no: int  # 0-based
    image: np.ndarray
    original_path: Path
    original: np.ndarray
    dpi: float | None = None  # of ``original``; None for photos without DPI metadata
    ocr_image: np.ndarray | None = None
    rotation: int = 0  # clockwise quarter turns (0/90/180/270) applied by preprocess
    skew_deg: float = 0.0  # small-angle correction applied by preprocess
    flags: tuple[str, ...] = field(default=())  # preprocessing signal codes, e.g. "orientation_uncertain"

    @property
    def image_scale(self) -> float:
        """``image`` pixels per ``original`` pixel (to map boxes back for crops)."""
        return self.image.shape[1] / self.original.shape[1]


def detect_file_kind(head: bytes) -> FileKind | None:
    """Identify the file from its first bytes; None if unsupported."""
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    if head.startswith(_WEBP_RIFF) and head[_WEBP_TAG_OFFSET:_WEBP_TAG_OFFSET + len(_WEBP_TAG)] == _WEBP_TAG:
        return FileKind.WEBP
    return None


def load_file(path: Path, limits: IngestLimits) -> list[PageImage]:
    """Load every page of ``path`` as a ``PageImage``."""
    if path.stat().st_size > limits.max_file_bytes:
        raise FileTooLargeError("file exceeds the size limit")
    with path.open("rb") as f:
        kind = detect_file_kind(f.read(MAGIC_BYTES_NEEDED))
    if kind is None:
        raise UnsupportedFileTypeError("unsupported file type")
    if kind is FileKind.PDF:
        return _load_pdf(path, limits)
    return _load_image(path, limits)


def _check_pixels(pixels: int, limits: IngestLimits) -> None:
    if pixels > limits.max_image_pixels:
        raise FileTooLargeError("page exceeds the pixel limit")


# --- PDF ---------------------------------------------------------------------


def _load_pdf(path: Path, limits: IngestLimits) -> list[PageImage]:
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        raise CorruptFileError("PDF cannot be opened") from exc
    with doc:
        if doc.needs_pass:
            raise EncryptedPdfError("PDF is password protected")
        if doc.page_count > limits.max_pages:
            raise TooManyPagesError("PDF exceeds the page limit")
        try:
            return [_pdf_page(doc, page, path, limits) for page in doc]
        except IngestError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise CorruptFileError("PDF page cannot be read") from exc


def _pdf_page(doc: pymupdf.Document, page: pymupdf.Page, path: Path, limits: IngestLimits) -> PageImage:
    scan = _full_page_scan(page)
    if scan is None:
        scale = limits.render_dpi / PDF_POINTS_PER_INCH
        _check_pixels(round(page.rect.width * scale) * round(page.rect.height * scale), limits)
        pixmap, dpi = page.get_pixmap(dpi=limits.render_dpi), float(limits.render_dpi)
    else:
        _check_pixels(scan["width"] * scan["height"], limits)
        pixmap = pymupdf.Pixmap(doc, scan["xref"])
        dpi = pixmap.width / (page.rect.width / PDF_POINTS_PER_INCH)
    image = _pixmap_to_bgr(pixmap)
    return PageImage(file_id=path.name, page_no=page.number, image=image,
                     original_path=path, original=image, dpi=dpi)


def _full_page_scan(page: pymupdf.Page) -> dict | None:
    """Image info (``xref``, ``width``, ``height``, ...) of the page's only image
    if the page is just that image placed upright over (almost) the whole page;
    otherwise None (render instead)."""
    if page.rotation or page.get_text().strip() or page.get_drawings() or page.first_annot:
        return None
    infos = page.get_image_info(xrefs=True)
    if len(infos) != 1:
        return None
    info = infos[0]
    a, b, c, d = info["transform"][:4]
    upright = a > 0 and d > 0 and abs(b) < AXIS_ALIGNED_TOLERANCE and abs(c) < AXIS_ALIGNED_TOLERANCE
    coverage = (pymupdf.Rect(info["bbox"]) & page.rect).get_area() / page.rect.get_area()
    if not upright or coverage < FULL_PAGE_COVERAGE or info["has-mask"] or info["xref"] <= 0:
        return None
    return info


def _pixmap_to_bgr(pixmap: pymupdf.Pixmap) -> np.ndarray:
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    if pixmap.n != RGB_CHANNELS:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, RGB_CHANNELS)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


# --- Images ------------------------------------------------------------------


def _load_image(path: Path, limits: IngestLimits) -> list[PageImage]:
    try:
        with Image.open(path) as img:
            if getattr(img, "n_frames", 1) > limits.max_pages:
                raise TooManyPagesError("image exceeds the page limit")
            dpi = _image_dpi(img)
            return [_image_page(frame, page_no, path, dpi, limits)
                    for page_no, frame in enumerate(ImageSequence.Iterator(img))]
    except IngestError:
        raise
    except Image.DecompressionBombError as exc:
        raise FileTooLargeError("image exceeds the pixel limit") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise CorruptFileError("image cannot be decoded") from exc


def _image_page(frame: Image.Image, page_no: int, path: Path, dpi: float | None,
                limits: IngestLimits) -> PageImage:
    _check_pixels(frame.width * frame.height, limits)
    rgb = np.asarray(ImageOps.exif_transpose(frame).convert("RGB"))
    image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return PageImage(file_id=path.name, page_no=page_no, image=image,
                     original_path=path, original=image, dpi=dpi)


def _image_dpi(img: Image.Image) -> float | None:
    dpi = img.info.get("dpi")
    return float(dpi[0]) if dpi and dpi[0] else None
