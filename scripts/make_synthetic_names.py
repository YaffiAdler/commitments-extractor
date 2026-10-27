"""Make a synthetic name test set: dummy form pages with names in a
handwriting-style font, for a sanity check of scripts/name_eval.py.

Every page is an A4 dummy form ("שם פרטי", "שם משפחה" and a few printed dummy
rows) with a first and last name drawn from built-in lists of common dummy
names (final letters, long surnames and a quote mark included), each in its
own size, slant, position and ink colour; the page is then turned slightly,
blurred, given sensor noise and saved as JPEG at a random quality. The
output folder is a name set (``labels.csv`` with kind ``synthetic``).

Fonts are not bundled: pass the path of a Hebrew handwriting-style TTF/OTF
(``--font``), and optionally a printed Hebrew font for the labels
(``--label-font``, default: the same font). ``--template`` also writes
``template.pdf``, the same form without names, to print and fill in by hand
(see docs/handwriting_eval.md).

Results on these pages are a sanity check only; they do not measure accuracy
on real handwriting.

Usage: python scripts/make_synthetic_names.py OUT_DIR --font F [--label-font F] [--count N] [--seed S] [--template]
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR / "backend"))

from app.evaluation.name_set import NameSample, SampleKind, write_name_set  # noqa: E402

FontLoader = Callable[[int], ImageFont.FreeTypeFont]

# Dummy names only: common Jewish first names and surnames, not taken from any form.
FIRST_NAMES = (
    "אברהם", "יצחק", "יעקב", "משה", "אהרן", "דוד", "שלמה", "יוסף", "מנחם", "שמואל", "אליהו", "חיים", "מרדכי",
    "נחמן", "ברוך", "שמעון", "ראובן", "אלחנן", "יהושע", "זאב", "שרה", "רבקה", "רחל", "לאה", "מרים", "חנה",
    "אסתר", "דבורה", "יהודית", "רות", "בתיה", "פנינה", "צפורה", "גיטל", "חוה",
)
LAST_NAMES = (
    "כהן", "לוי", "פרידמן", "גולדשטיין", "רוזנברג", "שטרן", "הורוביץ", "ויסמן", "קליין", "אדלר", "בלום",
    "פישר", "גרינברג", "שפירא", "רבינוביץ", "זילברשטיין", "אייזנברג", "וינטרויב", "מנדלבאום", "שיף", "וולף",
    'כ"ץ', "טננבוים", "ליכטנשטיין", "ברגמן", "עזריאל", "צוקרמן", "סגל", "דרוק",
)

DPI = 150
PAGE_SIZE = (1240, 1754)  # A4 at 150 DPI, width x height
PAPER = (250, 250, 247)
PRINT_INK = (35, 35, 35)
HANDWRITING_INKS = ((25, 45, 150), (15, 25, 95), (30, 30, 30), (45, 60, 120))  # blue, dark blue, black, blue-grey
TITLE = "טופס לדוגמה לבדיקת קריאת שמות"
SUBTITLE = "מסמך בדיקה בלבד - אינו טופס אמיתי"
NAME_LABELS = ("שם פרטי", "שם משפחה")
PRINTED_ROWS = (("מספר זהות", "000000018"), ("תאריך קבלה", "01.01.2025"), ("גורם שולח", "קופה לדוגמה"))
TABLE_LEFT, TABLE_RIGHT, LABEL_LEFT = 110, 1130, 830
TABLE_TOP, ROW_HEIGHT = 330, 110
LINE_WIDTH = 3
CELL_PADDING = 24
TITLE_SIZE, LABEL_SIZE = 46, 34
TITLE_Y, SUBTITLE_Y = 140, 215
NAME_SIZE_RANGE = (44, 64)
NAME_SLANT_DEG = 4.0
NAME_JITTER_PX = 14
PAGE_ROTATION_DEG = 2.0
NOISE_SIGMA_RANGE = (4.0, 12.0)
BLUR_PROBABILITY = 0.5
BLUR_KERNEL = (3, 3)
JPEG_QUALITY_RANGE = (60, 92)
DEFAULT_COUNT = 40
DEFAULT_SEED = 7
IMAGE_PATTERN = "synthetic_{:03d}.jpg"
TEMPLATE_FILE = "template.pdf"
_HEBREW = range(0x0590, 0x0600)


@dataclass(frozen=True)
class Fonts:
    hand: FontLoader   # size in px -> font for the names
    label: FontLoader  # size in px -> font for the printed labels


def visual(text: str) -> str:
    """``text`` in display order for Pillow's basic layout (no bidi): Hebrew reversed, digits as they are."""
    return text[::-1] if any(ord(ch) in _HEBREW for ch in text) else text


def name_pairs(count: int, rng: np.random.Generator) -> list[tuple[str, str]]:
    """``count`` different (first, last) pairs of dummy names."""
    total = len(FIRST_NAMES) * len(LAST_NAMES)
    if not 0 < count <= total:
        raise ValueError(f"count must be between 1 and {total}")
    picks = rng.choice(total, size=count, replace=False)
    return [(FIRST_NAMES[i // len(LAST_NAMES)], LAST_NAMES[i % len(LAST_NAMES)]) for i in picks]


def blank_form(fonts: Fonts) -> Image.Image:
    """The dummy form without names (RGB)."""
    page = Image.new("RGB", PAGE_SIZE, PAPER)
    draw = ImageDraw.Draw(page)
    draw.text((TABLE_RIGHT, TITLE_Y), visual(TITLE), font=fonts.label(TITLE_SIZE), fill=PRINT_INK, anchor="ra")
    draw.text((TABLE_RIGHT, SUBTITLE_Y), visual(SUBTITLE), font=fonts.label(LABEL_SIZE), fill=PRINT_INK, anchor="ra")
    rows = [(label, None) for label in NAME_LABELS] + list(PRINTED_ROWS)
    bottom = TABLE_TOP + len(rows) * ROW_HEIGHT
    for index, (label, value) in enumerate(rows):
        top = TABLE_TOP + index * ROW_HEIGHT
        middle = top + ROW_HEIGHT // 2
        draw.line((TABLE_LEFT, top, TABLE_RIGHT, top), fill=PRINT_INK, width=LINE_WIDTH)
        draw.text((TABLE_RIGHT - CELL_PADDING, middle), visual(label), font=fonts.label(LABEL_SIZE), fill=PRINT_INK,
                  anchor="rm")
        if value is not None:
            draw.text((LABEL_LEFT - CELL_PADDING, middle), visual(value), font=fonts.label(LABEL_SIZE),
                      fill=PRINT_INK, anchor="rm")
    draw.line((TABLE_LEFT, bottom, TABLE_RIGHT, bottom), fill=PRINT_INK, width=LINE_WIDTH)
    for x in (TABLE_LEFT, LABEL_LEFT, TABLE_RIGHT):
        draw.line((x, TABLE_TOP, x, bottom), fill=PRINT_INK, width=LINE_WIDTH)
    return page


def _draw_name(page: Image.Image, text: str, row: int, fonts: Fonts, rng: np.random.Generator) -> None:
    """``text`` in the value cell of ``row``, in a random size, slant, position and ink."""
    font = fonts.hand(int(rng.integers(*NAME_SIZE_RANGE)))
    ink = HANDWRITING_INKS[int(rng.integers(len(HANDWRITING_INKS)))]
    left, top, right, bottom = font.getbbox(visual(text))
    layer = Image.new("RGBA", (right - left + 2 * CELL_PADDING, bottom - top + 2 * CELL_PADDING), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((CELL_PADDING - left, CELL_PADDING - top), visual(text), font=font, fill=(*ink, 255))
    layer = layer.rotate(float(rng.uniform(-NAME_SLANT_DEG, NAME_SLANT_DEG)), resample=Image.Resampling.BICUBIC, expand=True)
    jitter = rng.integers(-NAME_JITTER_PX, NAME_JITTER_PX + 1, size=2)
    x = LABEL_LEFT - CELL_PADDING - layer.width + int(jitter[0])
    y = TABLE_TOP + row * ROW_HEIGHT + (ROW_HEIGHT - layer.height) // 2 + int(jitter[1])
    page.paste(layer, (max(x, TABLE_LEFT), y), layer)


def degrade(page: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Turn slightly, maybe blur, add sensor noise (BGR in, BGR out)."""
    height, width = page.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), float(rng.uniform(-PAGE_ROTATION_DEG, PAGE_ROTATION_DEG)), 1.0)
    turned = cv2.warpAffine(page, matrix, (width, height), flags=cv2.INTER_CUBIC, borderValue=PAPER[::-1])
    if rng.random() < BLUR_PROBABILITY:
        turned = cv2.GaussianBlur(turned, BLUR_KERNEL, 0)
    noise = rng.normal(0.0, float(rng.uniform(*NOISE_SIGMA_RANGE)), turned.shape)
    return np.clip(turned + noise, 0, 255).astype(np.uint8)


def render_page(first: str, last: str, fonts: Fonts, rng: np.random.Generator) -> np.ndarray:
    """One degraded page with ``first`` and ``last`` written in, as BGR."""
    page = blank_form(fonts)
    for row, text in enumerate((first, last)):
        _draw_name(page, text, row, fonts, rng)
    return degrade(cv2.cvtColor(np.asarray(page), cv2.COLOR_RGB2BGR), rng)


def make_set(out_dir: Path, fonts: Fonts, count: int, seed: int) -> list[NameSample]:
    """Write ``count`` pages and ``labels.csv`` to ``out_dir``; the same seed gives the same set."""
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    for index, (first, last) in enumerate(name_pairs(count, rng), 1):
        image = IMAGE_PATTERN.format(index)
        quality = int(rng.integers(*JPEG_QUALITY_RANGE))
        ok, jpeg = cv2.imencode(".jpg", render_page(first, last, fonts, rng), [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        jpeg.tofile(str(out_dir / image))
        samples.append(NameSample(image=image, first_name=first, last_name=last, kind=SampleKind.SYNTHETIC))
    write_name_set(out_dir, samples)
    return samples


def write_template(out_dir: Path, fonts: Fonts) -> Path:
    """The blank form as a one-page A4 PDF, for writing names by hand."""
    path = out_dir / TEMPLATE_FILE
    blank_form(fonts).save(path, resolution=DPI)
    return path


def _loader(path: Path) -> FontLoader:
    if not path.is_file():
        raise SystemExit(f"font not found: {path}")
    return lambda size: ImageFont.truetype(str(path), size)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--font", type=Path, required=True, help="Hebrew handwriting-style TTF/OTF")
    parser.add_argument("--label-font", type=Path, help="printed Hebrew font for the labels (default: --font)")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--template", action="store_true", help=f"also write {TEMPLATE_FILE} without names")
    args = parser.parse_args()

    hand = _loader(args.font)
    fonts = Fonts(hand=hand, label=_loader(args.label_font) if args.label_font else hand)
    samples = make_set(args.out_dir, fonts, args.count, args.seed)
    print(f"{len(samples)} synthetic pages -> {args.out_dir}")
    if args.template:
        print(f"-> {write_template(args.out_dir, fonts)}")


if __name__ == "__main__":
    main()
