"""Make 6 synthetic distortions of one page, to test preprocessing.

Usage:
    python scripts/make_synthetic_variants.py [out_dir]                      # dummy form (no patient data)
    python scripts/make_synthetic_variants.py [out_dir] --pdf F --page N     # page N (1-based) of a PDF
    python scripts/make_synthetic_variants.py [out_dir] --image F

The distortions use plain OpenCV only — not the code under test.
Variants of a samples/ page contain patient data: the default out_dir is a
temp directory; delete it afterwards.
"""
from __future__ import annotations

import argparse
import pathlib
import tempfile
from typing import Callable

import cv2
import numpy as np
import pymupdf

SKEW_DEG = 5.0
WHITE = (255, 255, 255)
DARK_LIGHTING_RANGE = (0.25, 0.6)  # brightness factor from one corner to the other
DARK_GAMMA = 1.4
PHOTO_MARGIN_FRACTION = 0.12  # background around the page, per side
PHOTO_CORNER_JITTER = 0.06  # how far the page corners move inwards
PHOTO_BACKGROUND_LEVEL = 70
PHOTO_BACKGROUND_NOISE = 12
NOISE_SIGMA = 25
SALT_PEPPER_FRACTION = 0.01
DUMMY_DPI = 150
SEED = 7

Variant = Callable[[np.ndarray], np.ndarray]


def rotate_90(page: np.ndarray) -> np.ndarray:
    """Content turned 90° clockwise (correction: 270° clockwise)."""
    return cv2.rotate(page, cv2.ROTATE_90_CLOCKWISE)


def rotate_180(page: np.ndarray) -> np.ndarray:
    return cv2.rotate(page, cv2.ROTATE_180)


def skew_5(page: np.ndarray) -> np.ndarray:
    """Content turned 5° counter-clockwise, as from a crooked scanner feed."""
    height, width = page.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), SKEW_DEG, 1.0)
    return cv2.warpAffine(page, matrix, (width, height), borderValue=WHITE)


def dark(page: np.ndarray) -> np.ndarray:
    """Underexposed with uneven lighting (a diagonal gradient)."""
    height, width = page.shape[:2]
    low, high = DARK_LIGHTING_RANGE
    ramp = np.add.outer(np.linspace(0, 1, height), np.linspace(0, 1, width)) / 2
    factor = (low + (high - low) * ramp)[..., None]
    darkened = 255.0 * (page / 255.0) ** DARK_GAMMA * factor
    return np.clip(darkened, 0, 255).astype(np.uint8)


def perspective(page: np.ndarray) -> np.ndarray:
    """The page photographed at an angle, on a darker table."""
    rng = np.random.default_rng(SEED)
    height, width = page.shape[:2]
    margin_x, margin_y = int(width * PHOTO_MARGIN_FRACTION), int(height * PHOTO_MARGIN_FRACTION)
    canvas_size = (width + 2 * margin_x, height + 2 * margin_y)
    jitter_x, jitter_y = width * PHOTO_CORNER_JITTER, height * PHOTO_CORNER_JITTER
    source = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    target = np.float32([
        [margin_x + jitter_x, margin_y],
        [margin_x + width - jitter_x * 0.3, margin_y + jitter_y * 0.5],
        [margin_x + width, margin_y + height],
        [margin_x, margin_y + height - jitter_y * 0.4],
    ])
    background = rng.normal(PHOTO_BACKGROUND_LEVEL, PHOTO_BACKGROUND_NOISE, (canvas_size[1], canvas_size[0], 3))
    canvas = np.clip(background, 0, 255).astype(np.uint8)
    warped = cv2.warpPerspective(page, cv2.getPerspectiveTransform(source, target), canvas_size)
    mask = cv2.warpPerspective(np.full(page.shape[:2], 255, np.uint8), cv2.getPerspectiveTransform(source, target), canvas_size)
    canvas[mask > 0] = warped[mask > 0]
    return canvas


def noise(page: np.ndarray) -> np.ndarray:
    """Sensor noise plus salt-and-pepper specks."""
    rng = np.random.default_rng(SEED)
    noisy = np.clip(page + rng.normal(0, NOISE_SIGMA, page.shape), 0, 255).astype(np.uint8)
    specks = rng.random(page.shape[:2])
    noisy[specks < SALT_PEPPER_FRACTION / 2] = 0
    noisy[specks > 1 - SALT_PEPPER_FRACTION / 2] = 255
    return noisy


VARIANTS: dict[str, Variant] = {
    "rotate_90": rotate_90,
    "rotate_180": rotate_180,
    "skew_5": skew_5,
    "dark": dark,
    "perspective": perspective,
    "noise": noise,
}


def dummy_form(dpi: int = DUMMY_DPI) -> np.ndarray:
    """A form-like A4 page with dummy values only: ruled table, labels, text
    lines and a "central" sticker. Returns a BGR image."""
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 70), "CENTRAL VIROLOGY LABORATORY - COMMITMENT FORM", fontsize=15)
        page.draw_rect(pymupdf.Rect(400, 90, 540, 130), width=1.5)
        page.insert_text((410, 116), "Central 900001", fontsize=12)
        rows = [("First name", "Moshe"), ("Last name", "Cohen"), ("ID number", "000000018"),
                ("Sender", "Dummy Health Fund"), ("Request no.", "123456789"), ("Received", "01.01.2025")]
        top, row_height = 160, 34
        for index, (label, value) in enumerate(rows):
            y = top + index * row_height
            page.insert_text((70, y + 22), label, fontsize=12)
            page.insert_text((260, y + 22), value, fontsize=12)
            page.draw_line((60, y), (540, y), width=1.2)
        bottom = top + len(rows) * row_height
        page.draw_line((60, bottom), (540, bottom), width=1.2)
        for x in (60, 250, 540):
            page.draw_line((x, top), (x, bottom), width=1.2)
        text = "This is dummy text for testing the reading of scanned forms only."
        for line_no in range(14):
            page.insert_text((60, bottom + 50 + line_no * 22), f"{line_no + 1}. {text}", fontsize=11)
        page.draw_line((60, 790), (260, 790), width=1)
        page.insert_text((60, 805), "Signature and stamp", fontsize=10)
        pixmap = page.get_pixmap(dpi=dpi)
    rgb = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _load_source(args: argparse.Namespace) -> np.ndarray:
    if args.image:
        image = cv2.imdecode(np.fromfile(args.image, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise SystemExit("image cannot be decoded")
        return image
    if args.pdf:
        with pymupdf.open(args.pdf) as doc:
            pixmap = doc[args.page - 1].get_pixmap(dpi=DUMMY_DPI)
        rgb = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return dummy_form()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", nargs="?", default=str(pathlib.Path(tempfile.gettempdir()) / "synthetic_variants"))
    parser.add_argument("--pdf")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--image")
    args = parser.parse_args()

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    source = _load_source(args)
    cv2.imencode(".png", source)[1].tofile(str(out / "original.png"))
    for name, variant in VARIANTS.items():
        cv2.imencode(".png", variant(source))[1].tofile(str(out / f"{name}.png"))
    print(f"{len(VARIANTS)} variants written to {out}")


if __name__ == "__main__":
    main()
