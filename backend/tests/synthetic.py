"""Test helpers: the synthetic-variant script and PageImage construction."""
import importlib.util
from pathlib import Path

import numpy as np

from app.pipeline.ingest import PageImage

_spec = importlib.util.spec_from_file_location(
    "make_synthetic_variants", Path(__file__).resolve().parents[2] / "scripts" / "make_synthetic_variants.py"
)
variants = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(variants)


def page_of(image: np.ndarray) -> PageImage:
    return PageImage(file_id="dummy.png", page_no=0, image=image, original_path=Path("dummy.png"), original=image)
