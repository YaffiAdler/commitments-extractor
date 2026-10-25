import io
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image

from app.config import Settings
from app.pipeline.ingest import (
    PageImage,
    PixelBudgetError,
    CorruptFileError,
    EncryptedPdfError,
    count_pages,
    count_pixels,
    FileKind,
    FileTooLargeError,
    IngestLimits,
    TooManyPagesError,
    UnsupportedFileTypeError,
    detect_file_kind,
    iter_pages,
    load_file,
)

A4_POINTS = (595, 842)
EXIF_ORIENTATION_TAG = 0x0112
EXIF_ROTATE_90_CW = 6


def _limits(**overrides) -> IngestLimits:
    return replace(IngestLimits.from_settings(Settings()), **overrides)


def _png_bytes(width: int, height: int, color=(200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def _vector_pdf(path, pages: int = 2) -> None:
    with pymupdf.open() as doc:
        for _ in range(pages):
            doc.new_page(width=A4_POINTS[0], height=A4_POINTS[1]).insert_text((72, 72), "dummy")
        doc.save(path)


def _scan_pdf(path, image_size=(1000, 1414), coverage: float = 1.0) -> None:
    with pymupdf.open() as doc:
        page = doc.new_page(width=A4_POINTS[0], height=A4_POINTS[1])
        rect = pymupdf.Rect(0, 0, A4_POINTS[0] * coverage, A4_POINTS[1] * coverage)
        page.insert_image(rect, stream=_png_bytes(*image_size))
        doc.save(path)


@pytest.mark.parametrize("head, kind", [
    (b"%PDF-1.7", FileKind.PDF),
    (b"\x89PNG\r\n\x1a\n\x00\x00", FileKind.PNG),
    (b"\xff\xd8\xff\xe0", FileKind.JPEG),
    (b"II*\x00", FileKind.TIFF),
    (b"MM\x00*", FileKind.TIFF),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", FileKind.WEBP),
    (b"RIFF\x00\x00\x00\x00WAVE", None),
    (b"PK\x03\x04", None),
    (b"", None),
])
def test_detect_file_kind(head, kind):
    assert detect_file_kind(head) is kind


def test_vector_pdf_pages_are_rendered_at_render_dpi(tmp_path):
    path = tmp_path / "vector.pdf"
    _vector_pdf(path)
    pages = load_file(path, _limits(render_dpi=72))
    assert [p.page_no for p in pages] == [0, 1]
    assert all(p.file_id == "vector.pdf" and p.original_path == path for p in pages)
    assert pages[0].image.shape == (A4_POINTS[1], A4_POINTS[0], 3)
    assert pages[0].dpi == 72
    assert pages[0].image is pages[0].original


def test_full_page_scan_is_extracted_at_native_resolution(tmp_path):
    path = tmp_path / "scan.pdf"
    _scan_pdf(path, image_size=(1000, 1414))
    page = load_file(path, _limits(render_dpi=72))[0]
    assert page.image.shape == (1414, 1000, 3)
    assert page.dpi == pytest.approx(1000 / (A4_POINTS[0] / 72))
    # The PNG was red; BGR order puts red last.
    assert tuple(page.image[0, 0]) == (30, 30, 200)


def test_partial_image_page_is_rendered(tmp_path):
    path = tmp_path / "partial.pdf"
    _scan_pdf(path, coverage=0.5)
    page = load_file(path, _limits(render_dpi=72))[0]
    assert page.image.shape == (A4_POINTS[1], A4_POINTS[0], 3)


def test_image_type_comes_from_magic_bytes_not_extension(tmp_path):
    path = tmp_path / "looks_like.pdf"
    path.write_bytes(_png_bytes(30, 20))
    pages = load_file(path, _limits())
    assert len(pages) == 1 and pages[0].image.shape == (20, 30, 3)
    assert pages[0].dpi is None


def test_jpeg_exif_orientation_is_applied(tmp_path):
    path = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[EXIF_ORIENTATION_TAG] = EXIF_ROTATE_90_CW
    Image.new("RGB", (40, 20), "white").save(path, "JPEG", exif=exif)
    assert load_file(path, _limits())[0].image.shape == (40, 20, 3)


def test_multi_page_tiff(tmp_path):
    path = tmp_path / "scan.tif"
    frames = [Image.new("L", (30, 40), level) for level in (0, 128, 255)]
    frames[0].save(path, save_all=True, append_images=frames[1:], dpi=(200, 200))
    pages = load_file(path, _limits())
    assert [p.page_no for p in pages] == [0, 1, 2]
    assert all(p.image.shape == (40, 30, 3) and p.dpi == 200 for p in pages)


def test_encrypted_pdf(tmp_path):
    path = tmp_path / "locked.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    with pytest.raises(EncryptedPdfError):
        load_file(path, _limits())


@pytest.mark.parametrize("content", [b"%PDF-1.4 broken", b"%PDF-" + bytes(range(256)) * 4])
def test_corrupt_pdf(tmp_path, content):
    path = tmp_path / "bad.pdf"
    path.write_bytes(content)
    with pytest.raises(CorruptFileError):
        load_file(path, _limits())


def test_truncated_png(tmp_path):
    path = tmp_path / "cut.png"
    path.write_bytes(_png_bytes(200, 200)[:60])
    with pytest.raises(CorruptFileError):
        load_file(path, _limits())


@pytest.mark.parametrize("content", [b"", b"hello world", b"PK\x03\x04zipfile"])
def test_unsupported_file(tmp_path, content):
    path = tmp_path / "x.pdf"
    path.write_bytes(content)
    with pytest.raises(UnsupportedFileTypeError):
        load_file(path, _limits())


def test_file_size_limit(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(_png_bytes(100, 100))
    with pytest.raises(FileTooLargeError):
        load_file(path, _limits(max_file_bytes=10))


def test_pdf_page_limit(tmp_path):
    path = tmp_path / "long.pdf"
    _vector_pdf(path, pages=3)
    with pytest.raises(TooManyPagesError):
        load_file(path, _limits(max_pages=2))


def test_tiff_page_limit(tmp_path):
    path = tmp_path / "long.tif"
    frames = [Image.new("L", (10, 10)) for _ in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    with pytest.raises(TooManyPagesError):
        load_file(path, _limits(max_pages=2))


def test_error_messages_carry_no_content(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4 SECRET-VALUE")
    with pytest.raises(CorruptFileError) as info:
        load_file(path, _limits())
    assert "SECRET" not in str(info.value)


def test_page_image_scale_defaults_to_one(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(_png_bytes(10, 10))
    page = load_file(path, _limits())[0]
    assert page.image_scale == 1.0
    assert np.array_equal(page.image, page.original)


def test_limits_come_from_settings(monkeypatch):
    monkeypatch.setenv("CE_MAX_UPLOAD_MB", "3")
    monkeypatch.setenv("CE_MAX_PAGES", "7")
    monkeypatch.setenv("CE_RENDER_DPI", "150")
    monkeypatch.setenv("CE_MAX_IMAGE_PIXELS", "1000")
    monkeypatch.setenv("CE_MAX_FILE_PIXELS", "5000")
    monkeypatch.setenv("CE_DOWNSCALE_SCANS", "true")
    assert IngestLimits.from_settings(Settings()) == IngestLimits(
        max_file_bytes=3 * 1024 * 1024, max_pages=7, render_dpi=150, max_image_pixels=1000,
        max_file_pixels=5000, downscale_scans=True)


def test_image_pixel_limit(tmp_path):
    path = tmp_path / "wide.png"
    path.write_bytes(_png_bytes(40, 30))
    assert load_file(path, _limits(max_image_pixels=40 * 30))
    with pytest.raises(FileTooLargeError):
        load_file(path, _limits(max_image_pixels=40 * 30 - 1))


def test_pdf_render_pixel_limit(tmp_path):
    path = tmp_path / "vector.pdf"
    _vector_pdf(path, pages=1)
    with pytest.raises(FileTooLargeError):
        load_file(path, _limits(render_dpi=72, max_image_pixels=A4_POINTS[0] * A4_POINTS[1] - 1))


def test_pdf_scan_pixel_limit(tmp_path):
    path = tmp_path / "scan.pdf"
    _scan_pdf(path, image_size=(100, 141))
    assert load_file(path, _limits(max_image_pixels=100 * 141))
    with pytest.raises(FileTooLargeError):
        load_file(path, _limits(max_image_pixels=100 * 141 - 1))


def test_count_pages_pdf_and_images(tmp_path):
    path = tmp_path / "three.pdf"
    _vector_pdf(path, pages=3)
    assert count_pages(path.read_bytes(), FileKind.PDF) == 3
    assert count_pages(_png_bytes(20, 20), FileKind.PNG) == 1
    tiff = io.BytesIO()
    frames = [Image.new("L", (10, 10), level) for level in (0, 255)]
    frames[0].save(tiff, "TIFF", save_all=True, append_images=frames[1:])
    assert count_pages(tiff.getvalue(), FileKind.TIFF) == 2


@pytest.mark.parametrize("content, kind", [(b"%PDF-1.4 broken", FileKind.PDF), (b"\x89PNG\r\n\x1a\n", FileKind.PNG)])
def test_count_pages_unreadable(content, kind):
    with pytest.raises(CorruptFileError):
        count_pages(content, kind)


def test_scan_is_scaled_down_to_render_dpi_when_set(tmp_path):
    path = tmp_path / "scan.pdf"
    _scan_pdf(path)  # 1000 px across A4: about 121 DPI
    native = load_file(path, _limits(render_dpi=72))[0]
    scaled = load_file(path, _limits(render_dpi=72, downscale_scans=True))[0]
    assert native.original.shape[1] == 1000
    assert scaled.original.shape[1] == A4_POINTS[0] and scaled.dpi == 72
    assert scaled.original.shape[0] == round(1414 * A4_POINTS[0] / 1000)


def test_scaling_a_scan_gives_the_pixels_of_scaling_its_full_bgr_copy(tmp_path):
    """Ingest scales before the colour conversion; it must not change a pixel."""
    noise = np.random.default_rng(0).integers(0, 256, (1414, 1000, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(noise).save(buffer, "PNG")
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=A4_POINTS[0], height=A4_POINTS[1]).insert_image(
            pymupdf.Rect(0, 0, *A4_POINTS), stream=buffer.getvalue())
        doc.save(path)
    native = load_file(path, _limits(render_dpi=72))[0]
    scaled = load_file(path, _limits(render_dpi=72, downscale_scans=True))[0]
    expected = cv2.resize(native.original, scaled.original.shape[1::-1], interpolation=cv2.INTER_AREA)
    assert np.array_equal(scaled.original, expected)


def test_scan_coarser_than_render_dpi_is_not_scaled_up(tmp_path):
    path = tmp_path / "scan.pdf"
    _scan_pdf(path)
    assert load_file(path, _limits(render_dpi=250, downscale_scans=True))[0].original.shape[1] == 1000


def test_iter_pages_yields_one_page_at_a_time(tmp_path):
    path = tmp_path / "doc.pdf"
    _vector_pdf(path, pages=3)
    pages = iter_pages(path, _limits(render_dpi=72))
    assert next(pages).page_no == 0
    assert [page.page_no for page in pages] == [1, 2]


def test_iter_pages_raises_when_iterated(tmp_path):
    path = tmp_path / "doc.pdf"
    _vector_pdf(path, pages=3)
    pages = iter_pages(path, _limits(max_pages=2))
    with pytest.raises(TooManyPagesError):
        next(pages)


def _page_with_ocr_image(width: int = 40, height: int = 60):
    original = np.zeros((height, width, 3), np.uint8)
    return replace(PageImage(file_id="f.pdf", page_no=0, image=original[::2, ::2], original_path=Path("f.pdf"),
                             original=original), ocr_image=np.zeros((height, width), np.uint8))


def test_released_original_is_read_through_the_loader():
    page = _page_with_ocr_image()
    stored = page.original.copy()
    released = page.without_original(lambda: stored)
    assert released.original is None
    assert released.original_size == (60, 40) and released.image_scale == 0.5
    assert released.load_original() is stored
    assert page.load_original() is page.original


def test_original_is_not_released_without_ocr_image():
    page = PageImage(file_id="f.pdf", page_no=0, image=np.zeros((4, 4, 3), np.uint8),
                     original_path=Path("f.pdf"), original=np.zeros((4, 4, 3), np.uint8))
    with pytest.raises(ValueError):
        page.without_original(lambda: None)


def test_mupdf_store_is_emptied_after_each_page(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _scan_pdf(path)
    shrinks = []
    monkeypatch.setattr(pymupdf.TOOLS, "store_shrink", shrinks.append)
    load_file(path, _limits())
    assert shrinks == [100]


def _png_with_dpi(path, width: int, height: int, dpi: int) -> None:
    Image.new("RGB", (width, height), (200, 30, 30)).save(path, "PNG", dpi=(dpi, dpi))


def test_image_finer_than_render_dpi_is_scaled_down_when_set(tmp_path):
    path = tmp_path / "scan.png"
    _png_with_dpi(path, 600, 900, 300)
    assert load_file(path, _limits(render_dpi=100))[0].original.shape[:2] == (900, 600)
    scaled = load_file(path, _limits(render_dpi=100, downscale_scans=True))[0]
    assert scaled.original.shape[:2] == (300, 200) and scaled.dpi == 100


def test_image_without_dpi_is_never_scaled(tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(_png_bytes(600, 900))
    assert load_file(path, _limits(render_dpi=100, downscale_scans=True))[0].original.shape[:2] == (900, 600)


# A PDF page counts as the larger of its rendered size and its scan (rounding differs by a row).
COUNT_UPPER_BOUND_MARGIN = 1.01


def test_count_pixels_bounds_the_loaded_pages(tmp_path):
    scan, vector, image = tmp_path / "scan.pdf", tmp_path / "doc.pdf", tmp_path / "scan.png"
    _scan_pdf(scan)
    _vector_pdf(vector, pages=2)
    _png_with_dpi(image, 600, 900, 300)
    for path, kind in ((scan, FileKind.PDF), (vector, FileKind.PDF), (image, FileKind.PNG)):
        for limits in (_limits(render_dpi=72), _limits(render_dpi=72, downscale_scans=True)):
            loaded = sum(page.original.shape[0] * page.original.shape[1] for page in load_file(path, limits))
            assert loaded <= count_pixels(path.read_bytes(), kind, limits) <= loaded * COUNT_UPPER_BOUND_MARGIN


def test_count_pixels_of_a_corrupt_file():
    with pytest.raises(CorruptFileError):
        count_pixels(b"%PDF-1.7 broken", FileKind.PDF, _limits())


@pytest.mark.parametrize("make, name", [(lambda p: _vector_pdf(p, pages=3), "doc.pdf"),
                                        (lambda p: p.write_bytes(_png_bytes(100, 100)), "page.png")])
def test_file_over_the_pixel_budget_is_refused_before_any_page(tmp_path, make, name):
    path = tmp_path / name
    make(path)
    pages = iter_pages(path, _limits(render_dpi=72, max_file_pixels=9999))
    with pytest.raises(PixelBudgetError):
        next(pages)
