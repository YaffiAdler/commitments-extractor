"""Before/after images of the preprocessing chain.

Usage:
    python scripts/preprocess_examples.py                         # synthetic dummy pages -> docs/preprocess_examples/
    python scripts/preprocess_examples.py OUT --pdf F --pages 3,14  # pages (1-based) of a PDF

Each output PNG shows the input on the left and the VLM image on the right.
Pages of samples/ contain patient data: write them to a temp directory and
delete it afterwards — never into docs/.
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402
from app.ocr import create_ocr_engine  # noqa: E402
from app.pipeline.ingest import IngestLimits, PageImage, load_file  # noqa: E402
from app.pipeline.preprocess import preprocess  # noqa: E402

_spec = importlib.util.spec_from_file_location("variants", ROOT / "scripts" / "make_synthetic_variants.py")
variants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(variants)

DOCS_OUT = ROOT / "docs" / "preprocess_examples"
SYNTHETIC_EXAMPLES = ("skew_5", "perspective", "dark")
PANEL_HEIGHT = 900
GAP_PX = 20
LABEL_ORIGIN = (15, 40)
LABEL_SCALE = 1.1
LABEL_COLOR = (0, 0, 200)
GAP_COLOR = 128


def side_by_side(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    panels = []
    for label, image in (("before", before), ("after", after)):
        scale = PANEL_HEIGHT / image.shape[0]
        panel = cv2.resize(image, (round(image.shape[1] * scale), PANEL_HEIGHT), interpolation=cv2.INTER_AREA)
        cv2.putText(panel, label, LABEL_ORIGIN, cv2.FONT_HERSHEY_SIMPLEX, LABEL_SCALE, LABEL_COLOR, 2)
        panels.append(panel)
    gap = np.full((PANEL_HEIGHT, GAP_PX, 3), GAP_COLOR, np.uint8)
    return np.hstack([panels[0], gap, panels[1]])


def _write_png(path: pathlib.Path, image: np.ndarray) -> None:
    cv2.imencode(".png", image)[1].tofile(str(path))  # cv2.imwrite fails on non-ASCII paths


def _pages(args: argparse.Namespace) -> list[tuple[str, PageImage]]:
    if args.pdf:
        pages = load_file(pathlib.Path(args.pdf), IngestLimits.from_settings(Settings()))
        wanted = [int(n) for n in args.pages.split(",")]
        return [(f"page_{n:02d}", pages[n - 1]) for n in wanted]
    form = variants.dummy_form()
    result = []
    for name in SYNTHETIC_EXAMPLES:
        image = variants.VARIANTS[name](form)
        result.append((name, PageImage(file_id=name, page_no=0, image=image,
                                       original_path=pathlib.Path(name), original=image)))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", nargs="?", default=str(DOCS_OUT))
    parser.add_argument("--pdf")
    parser.add_argument("--pages", default="1")
    args = parser.parse_args()

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    ocr = create_ocr_engine(settings)
    for name, page in _pages(args):
        result = preprocess(page, settings.preprocess, ocr)
        _write_png(out / f"{name}.png", side_by_side(page.original, result.image))
        print(f"{name}: rotation {result.rotation}, skew {result.skew_deg:+.2f}, flags {list(result.flags)}")
    print(f"written to {out}")


if __name__ == "__main__":
    main()
