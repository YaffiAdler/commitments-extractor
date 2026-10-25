"""Stage 1: load an uploaded file (PDF or image) as one image per page.

The file type is detected from its magic bytes, never from the extension.
PDF pages that are a single full-page scan are extracted at their native
resolution, or scaled down to ``IngestLimits.render_dpi`` when
``downscale_scans`` is set and the scan is finer; any other PDF page is
rendered at ``IngestLimits.render_dpi``. ``iter_pages`` yields one page at a
time, so a caller can process and drop each page before the next is decoded;
MuPDF's cache of decoded images is emptied after each page.
Images get their EXIF orientation applied (and, with ``downscale_scans``, are
scaled down to ``render_dpi`` when their DPI metadata says they are finer).
A page over ``IngestLimits.max_image_pixels``, or a file whose loaded pages
together exceed ``IngestLimits.max_file_pixels``, is rejected before any page
is decoded or rendered. Bad input raises an
``IngestError`` subclass whose message never contains file content.
"""
from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
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
# ``TOOLS.store_shrink`` percentage that empties MuPDF's store (decoded images it keeps for reuse).
EMPTY_STORE_PERCENT = 100


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


class PixelBudgetError(IngestError):
    """The pages of the file together exceed ``IngestLimits.max_file_pixels`` (too big for the server's memory)."""


@dataclass(frozen=True)
class IngestLimits:
    """Limits for one file; built from ``Settings`` (no defaults of its own)."""

    max_file_bytes: int
    max_pages: int
    render_dpi: int
    max_image_pixels: int
    max_file_pixels: int
    downscale_scans: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> IngestLimits:
        return cls(
            max_file_bytes=settings.max_upload_mb * BYTES_PER_MB,
            max_pages=settings.max_pages,
            render_dpi=settings.render_dpi,
            max_image_pixels=settings.max_image_pixels,
            max_file_pixels=settings.max_file_pixels,
            downscale_scans=settings.downscale_scans,
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
    # None once released to disk (``without_original``); read it through ``load_original``.
    original: np.ndarray | None
    dpi: float | None = None  # of ``original``; None for photos without DPI metadata
    ocr_image: np.ndarray | None = None
    rotation: int = 0  # clockwise quarter turns (0/90/180/270) applied by preprocess
    skew_deg: float = 0.0  # small-angle correction applied by preprocess
    flags: tuple[str, ...] = field(default=())  # preprocessing signal codes, e.g. "orientation_uncertain"
    original_loader: Callable[[], np.ndarray | None] | None = field(default=None, repr=False)

    @property
    def original_size(self) -> tuple[int, int]:
        """(height, width) of ``original``; ``ocr_image`` has the same geometry."""
        source = self.original if self.original is not None else self.ocr_image
        if source is None:
            raise ValueError("page has neither original nor ocr_image")
        return source.shape[0], source.shape[1]

    def load_original(self) -> np.ndarray | None:
        """``original``, read back through ``original_loader`` once released; None if it is gone."""
        if self.original is not None:
            return self.original
        return self.original_loader() if self.original_loader is not None else None

    def without_original(self, loader: Callable[[], np.ndarray | None]) -> PageImage:
        """This page with ``original`` dropped from memory and read through ``loader`` when needed.
        Needs ``ocr_image``, which keeps the geometry (``original_size``)."""
        if self.ocr_image is None:
            raise ValueError("original can only be released when ocr_image is set")
        return replace(self, original=None, original_loader=loader)

    @property
    def image_scale(self) -> float:
        """``image`` pixels per ``original`` pixel (to map boxes back for crops)."""
        return self.image.shape[1] / self.original_size[1]


def detect_file_kind(head: bytes) -> FileKind | None:
    """Identify the file from its first bytes; None if unsupported."""
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    if head.startswith(_WEBP_RIFF) and head[_WEBP_TAG_OFFSET:_WEBP_TAG_OFFSET + len(_WEBP_TAG)] == _WEBP_TAG:
        return FileKind.WEBP
    return None


def count_pages(data: bytes, kind: FileKind) -> int:
    """Pages (PDF) or frames (images) in an uploaded file, without rendering or decoding them.
    ``CorruptFileError`` if the file cannot be opened."""
    try:
        if kind is FileKind.PDF:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                return doc.page_count
        with Image.open(io.BytesIO(data)) as img:
            return getattr(img, "n_frames", 1)
    except Image.DecompressionBombError as exc:
        raise FileTooLargeError("image exceeds the pixel limit") from exc
    except (RuntimeError, ValueError, UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise CorruptFileError("file cannot be opened to count its pages") from exc


def count_pixels(data: bytes, kind: FileKind, limits: IngestLimits) -> int:
    """Pixels of all pages of an uploaded file once loaded (``downscale_scans`` applied; for a PDF page
    an upper bound), from the page geometry without rendering or decoding; 0 for a password-protected
    PDF (ingest rejects it).
    ``CorruptFileError`` if the file cannot be read."""
    try:
        if kind is FileKind.PDF:
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                return 0 if doc.needs_pass else _pdf_pixels(doc, limits)
        with Image.open(io.BytesIO(data)) as img:
            return _image_pixels(img, _image_dpi(img), limits)
    except IngestError:
        raise
    except Image.DecompressionBombError as exc:
        raise FileTooLargeError("image exceeds the pixel limit") from exc
    except (RuntimeError, ValueError, UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise CorruptFileError("file cannot be opened to count its pixels") from exc


def load_file(path: Path, limits: IngestLimits) -> list[PageImage]:
    """Load every page of ``path`` as a ``PageImage``."""
    return list(iter_pages(path, limits))


def iter_pages(path: Path, limits: IngestLimits) -> Iterator[PageImage]:
    """The pages of ``path`` one at a time; errors are raised when the page that causes them is reached."""
    if path.stat().st_size > limits.max_file_bytes:
        raise FileTooLargeError("file exceeds the size limit")
    with path.open("rb") as f:
        kind = detect_file_kind(f.read(MAGIC_BYTES_NEEDED))
    if kind is None:
        raise UnsupportedFileTypeError("unsupported file type")
    if kind is FileKind.PDF:
        yield from _load_pdf(path, limits)
    else:
        yield from _load_image(path, limits)


def _check_pixels(pixels: int, limits: IngestLimits) -> None:
    if pixels > limits.max_image_pixels:
        raise FileTooLargeError("page exceeds the pixel limit")


def _check_budget(pixels: int, limits: IngestLimits) -> None:
    if pixels > limits.max_file_pixels:
        raise PixelBudgetError("file exceeds the pixel budget")


def _scales_down(dpi: float | None, limits: IngestLimits) -> bool:
    return limits.downscale_scans and dpi is not None and dpi > limits.render_dpi


def _scaled_size(width: int, height: int, dpi: float, target_dpi: int) -> tuple[int, int]:
    factor = target_dpi / dpi
    return max(1, round(width * factor)), max(1, round(height * factor))


def _scale_to_dpi(image: np.ndarray, dpi: float, target_dpi: int) -> np.ndarray:
    size = _scaled_size(image.shape[1], image.shape[0], dpi, target_dpi)
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def _loaded_pixels(width: int, height: int, dpi: float | None, limits: IngestLimits) -> int:
    """Pixels of a ``width`` x ``height`` page once loaded (the page limit is checked when it is decoded)."""
    if _scales_down(dpi, limits):
        width, height = _scaled_size(width, height, dpi, limits.render_dpi)
    return width * height


# --- PDF ---------------------------------------------------------------------


def _load_pdf(path: Path, limits: IngestLimits) -> Iterator[PageImage]:
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
            _check_budget(_pdf_pixels(doc, limits), limits)
            for page in doc:
                yield _pdf_page(doc, page, path, limits)
        except IngestError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise CorruptFileError("PDF page cannot be read") from exc


def _pdf_pixels(doc: pymupdf.Document, limits: IngestLimits) -> int:
    return sum(_pdf_page_pixels(page, limits) for page in doc)


def _pdf_page_pixels(page: pymupdf.Page, limits: IngestLimits) -> int:
    """Upper bound of the page's loaded pixels: it is loaded either rendered or as one of its images
    (a full-page scan). From the sizes written in the PDF: ``get_image_info`` would decode every image."""
    rendered = _loaded_pixels(*_rendered_size(page, limits), None, limits)
    as_scan = [_loaded_pixels(width, height, _scan_dpi(width, page), limits)
               for _, _, width, height, *_ in page.get_images(full=True)]
    return max([rendered, *as_scan])


def _rendered_size(page: pymupdf.Page, limits: IngestLimits) -> tuple[int, int]:
    scale = limits.render_dpi / PDF_POINTS_PER_INCH
    return round(page.rect.width * scale), round(page.rect.height * scale)


def _scan_dpi(scan_width: int, page: pymupdf.Page) -> float:
    return scan_width / (page.rect.width / PDF_POINTS_PER_INCH)


def _pdf_page(doc: pymupdf.Document, page: pymupdf.Page, path: Path, limits: IngestLimits) -> PageImage:
    scan = _full_page_scan(page)
    if scan is None:
        width, height = _rendered_size(page, limits)
        _check_pixels(width * height, limits)
        pixmap, dpi = page.get_pixmap(dpi=limits.render_dpi), float(limits.render_dpi)
    else:
        _check_pixels(scan["width"] * scan["height"], limits)
        pixmap = pymupdf.Pixmap(doc, scan["xref"])
        dpi = _scan_dpi(pixmap.width, page)
    image, dpi = _pixmap_to_bgr(pixmap, dpi, limits)  # a rendered page already is at render_dpi
    del pixmap
    pymupdf.TOOLS.store_shrink(EMPTY_STORE_PERCENT)
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


def _pixmap_to_bgr(pixmap: pymupdf.Pixmap, dpi: float, limits: IngestLimits) -> tuple[np.ndarray, float]:
    """A BGR copy of ``pixmap`` and its DPI, read in place (``samples_mv``, not a ``samples`` copy)."""
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    if pixmap.n != RGB_CHANNELS:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    rgb = np.frombuffer(pixmap.samples_mv, dtype=np.uint8).reshape(pixmap.height, pixmap.width, RGB_CHANNELS)
    return _to_bgr_at_dpi(rgb, dpi, limits)


def _to_bgr_at_dpi(rgb: np.ndarray, dpi: float | None, limits: IngestLimits) -> tuple[np.ndarray, float | None]:
    """``rgb`` as BGR, scaled down to ``render_dpi`` when ``_scales_down``. The scaling comes first, so
    no full-size BGR copy is made (resizing works per channel: the order does not change the pixels)."""
    if _scales_down(dpi, limits):
        rgb, dpi = _scale_to_dpi(rgb, dpi, limits.render_dpi), float(limits.render_dpi)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), dpi


# --- Images ------------------------------------------------------------------


def _load_image(path: Path, limits: IngestLimits) -> Iterator[PageImage]:
    try:
        with Image.open(path) as img:
            if getattr(img, "n_frames", 1) > limits.max_pages:
                raise TooManyPagesError("image exceeds the page limit")
            dpi = _image_dpi(img)
            _check_budget(_image_pixels(img, dpi, limits), limits)
            for page_no, frame in enumerate(ImageSequence.Iterator(img)):
                yield _image_page(frame, page_no, path, dpi, limits)
    except IngestError:
        raise
    except Image.DecompressionBombError as exc:
        raise FileTooLargeError("image exceeds the pixel limit") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise CorruptFileError("image cannot be decoded") from exc


def _image_page(frame: Image.Image, page_no: int, path: Path, dpi: float | None,
                limits: IngestLimits) -> PageImage:
    _check_pixels(frame.width * frame.height, limits)
    image, dpi = _to_bgr_at_dpi(np.asarray(ImageOps.exif_transpose(frame).convert("RGB")), dpi, limits)
    return PageImage(file_id=path.name, page_no=page_no, image=image,
                     original_path=path, original=image, dpi=dpi)


def _image_pixels(img: Image.Image, dpi: float | None, limits: IngestLimits) -> int:
    """Loaded pixels of every frame, from the frame headers (no frame is decoded)."""
    return sum(_loaded_pixels(frame.width, frame.height, dpi, limits) for frame in ImageSequence.Iterator(img))


def _image_dpi(img: Image.Image) -> float | None:
    dpi = img.info.get("dpi")
    return float(dpi[0]) if dpi and dpi[0] else None
