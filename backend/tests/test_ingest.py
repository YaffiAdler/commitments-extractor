import io

import numpy as np
import pymupdf
import pytest
from PIL import Image

from app.pipeline.ingest import (
    CorruptFileError,
    EncryptedPdfError,
    FileKind,
    FileTooLargeError,
    IngestLimits,
    TooManyPagesError,
    UnsupportedFileTypeError,
    detect_file_kind,
    load_file,
)

A4_POINTS = (595, 842)
EXIF_ORIENTATION_TAG = 0x0112
EXIF_ROTATE_90_CW = 6


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
    pages = load_file(path, IngestLimits(render_dpi=72))
    assert [p.page_no for p in pages] == [0, 1]
    assert all(p.file_id == "vector.pdf" and p.original_path == path for p in pages)
    assert pages[0].image.shape == (A4_POINTS[1], A4_POINTS[0], 3)
    assert pages[0].dpi == 72
    assert pages[0].image is pages[0].original


def test_full_page_scan_is_extracted_at_native_resolution(tmp_path):
    path = tmp_path / "scan.pdf"
    _scan_pdf(path, image_size=(1000, 1414))
    page = load_file(path, IngestLimits(render_dpi=72))[0]
    assert page.image.shape == (1414, 1000, 3)
    assert page.dpi == pytest.approx(1000 / (A4_POINTS[0] / 72))
    # The PNG was red; BGR order puts red last.
    assert tuple(page.image[0, 0]) == (30, 30, 200)


def test_partial_image_page_is_rendered(tmp_path):
    path = tmp_path / "partial.pdf"
    _scan_pdf(path, coverage=0.5)
    page = load_file(path, IngestLimits(render_dpi=72))[0]
    assert page.image.shape == (A4_POINTS[1], A4_POINTS[0], 3)


def test_image_type_comes_from_magic_bytes_not_extension(tmp_path):
    path = tmp_path / "looks_like.pdf"
    path.write_bytes(_png_bytes(30, 20))
    pages = load_file(path)
    assert len(pages) == 1 and pages[0].image.shape == (20, 30, 3)
    assert pages[0].dpi is None


def test_jpeg_exif_orientation_is_applied(tmp_path):
    path = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[EXIF_ORIENTATION_TAG] = EXIF_ROTATE_90_CW
    Image.new("RGB", (40, 20), "white").save(path, "JPEG", exif=exif)
    assert load_file(path)[0].image.shape == (40, 20, 3)


def test_multi_page_tiff(tmp_path):
    path = tmp_path / "scan.tif"
    frames = [Image.new("L", (30, 40), level) for level in (0, 128, 255)]
    frames[0].save(path, save_all=True, append_images=frames[1:], dpi=(200, 200))
    pages = load_file(path)
    assert [p.page_no for p in pages] == [0, 1, 2]
    assert all(p.image.shape == (40, 30, 3) and p.dpi == 200 for p in pages)


def test_encrypted_pdf(tmp_path):
    path = tmp_path / "locked.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    with pytest.raises(EncryptedPdfError):
        load_file(path)


@pytest.mark.parametrize("content", [b"%PDF-1.4 broken", b"%PDF-" + bytes(range(256)) * 4])
def test_corrupt_pdf(tmp_path, content):
    path = tmp_path / "bad.pdf"
    path.write_bytes(content)
    with pytest.raises(CorruptFileError):
        load_file(path)


def test_truncated_png(tmp_path):
    path = tmp_path / "cut.png"
    path.write_bytes(_png_bytes(200, 200)[:60])
    with pytest.raises(CorruptFileError):
        load_file(path)


@pytest.mark.parametrize("content", [b"", b"hello world", b"PK\x03\x04zipfile"])
def test_unsupported_file(tmp_path, content):
    path = tmp_path / "x.pdf"
    path.write_bytes(content)
    with pytest.raises(UnsupportedFileTypeError):
        load_file(path)


def test_file_size_limit(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(_png_bytes(100, 100))
    with pytest.raises(FileTooLargeError):
        load_file(path, IngestLimits(max_file_bytes=10))


def test_pdf_page_limit(tmp_path):
    path = tmp_path / "long.pdf"
    _vector_pdf(path, pages=3)
    with pytest.raises(TooManyPagesError):
        load_file(path, IngestLimits(max_pages=2))


def test_tiff_page_limit(tmp_path):
    path = tmp_path / "long.tif"
    frames = [Image.new("L", (10, 10)) for _ in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    with pytest.raises(TooManyPagesError):
        load_file(path, IngestLimits(max_pages=2))


def test_error_messages_carry_no_content(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4 SECRET-VALUE")
    with pytest.raises(CorruptFileError) as info:
        load_file(path)
    assert "SECRET" not in str(info.value)


def test_page_image_scale_defaults_to_one(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(_png_bytes(10, 10))
    page = load_file(path)[0]
    assert page.image_scale == 1.0
    assert np.array_equal(page.image, page.original)
