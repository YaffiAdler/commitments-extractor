"""scripts/make_synthetic_names.py with Pillow's built-in font (no font file is bundled or needed)."""
import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import ImageFont

from app.evaluation.name_set import SampleKind, load_name_set
from app.hebrew_text import FINAL_LETTERS

_spec = importlib.util.spec_from_file_location(
    "make_synthetic_names", Path(__file__).resolve().parents[2] / "scripts" / "make_synthetic_names.py"
)
synthetic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = synthetic
_spec.loader.exec_module(synthetic)

FONTS = synthetic.Fonts(hand=lambda size: ImageFont.load_default(size), label=lambda size: ImageFont.load_default(size))
COUNT = 3


def test_set_is_written_and_loads_as_synthetic(tmp_path):
    samples = synthetic.make_set(tmp_path, FONTS, COUNT, seed=1)
    assert load_name_set(tmp_path) == samples
    assert len(samples) == COUNT and all(s.kind is SampleKind.SYNTHETIC for s in samples)
    assert all(s.first_name in synthetic.FIRST_NAMES and s.last_name in synthetic.LAST_NAMES for s in samples)
    page = cv2.imdecode(np.fromfile(str(tmp_path / samples[0].image), np.uint8), cv2.IMREAD_COLOR)
    assert page.shape[:2] == synthetic.PAGE_SIZE[::-1]


def test_same_seed_gives_the_same_set(tmp_path):
    first = synthetic.make_set(tmp_path / "a", FONTS, COUNT, seed=5)
    second = synthetic.make_set(tmp_path / "b", FONTS, COUNT, seed=5)
    assert first == second
    assert (tmp_path / "a" / first[0].image).read_bytes() == (tmp_path / "b" / first[0].image).read_bytes()


def test_names_are_drawn_in_their_cells():
    blank = cv2.cvtColor(np.asarray(synthetic.blank_form(FONTS)), cv2.COLOR_RGB2BGR)
    page = synthetic.render_page("משה", "כהן", FONTS, np.random.default_rng(0))
    assert page.shape == blank.shape
    # Inside the first value cell the written page is darker than the blank form.
    top = synthetic.TABLE_TOP + synthetic.LINE_WIDTH * 3
    cell = (slice(top, top + synthetic.ROW_HEIGHT - synthetic.LINE_WIDTH * 6),
            slice(synthetic.TABLE_LEFT + 20, synthetic.LABEL_LEFT - 20))
    assert page[cell].mean() < blank[cell].mean() - 1


def test_visual_order_reverses_hebrew_only():
    assert synthetic.visual("שם פרטי") == "יטרפ םש"
    assert synthetic.visual("01.01.2025") == "01.01.2025"


def test_name_lists_cover_final_letters_long_names_and_quotes():
    names = synthetic.FIRST_NAMES + synthetic.LAST_NAMES
    assert all(any(letter in name for name in names) for letter in FINAL_LETTERS)
    assert any(len(name) >= 9 for name in synthetic.LAST_NAMES)
    assert any('"' in name for name in synthetic.LAST_NAMES)


def test_pairs_are_distinct_and_bounded():
    pairs = synthetic.name_pairs(50, np.random.default_rng(2))
    assert len(set(pairs)) == 50
    with pytest.raises(ValueError):
        synthetic.name_pairs(0, np.random.default_rng(2))


def test_template_is_a_pdf(tmp_path):
    path = synthetic.write_template(tmp_path, FONTS)
    assert path.read_bytes().startswith(b"%PDF")
