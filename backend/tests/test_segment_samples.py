"""Segmentation of the two sample PDFs against a manual review of their pages.

The expected split comes from looking at every page image (see
docs/form_mapping.md): every sample page is a separate one-page form. The
test needs ``samples/`` (patient data, not in git) and Tesseract with heb;
it is skipped where either is missing. The VLM is never reached for real:
the provider answers "start" for every pair it is asked about (the right
answer for these files) and counts the calls, so the rules are what is
tested here.
"""
from pathlib import Path

import pytest

from app.config import Settings
from app.ocr import create_ocr_engine
from app.pipeline import ingest, preprocess, segment
from app.pipeline.classify import load_form_types
from app.providers.fake import FakeProvider

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
# file name -> (page count, number of forms); all forms are one page.
EXPECTED = {
    "התחייבויות - דוגמאות, חלק 1 (PDF).pdf": (26, 26),
    "התחייבויות - דוגמאות, חלק 2 (PDF).pdf": (22, 22),
}
# Upper bound on page pairs sent to the VLM (measured: 2 + 8 of 46 boundaries).
MAX_VLM_SHARE = 0.4


class CountingProvider(FakeProvider):
    def __init__(self) -> None:
        self.calls = 0

    def compare_pages(self, page_a, page_b):
        self.calls += 1
        return super().compare_pages(page_a, page_b)


@pytest.fixture(scope="module")
def settings_and_ocr():
    settings = Settings()
    ocr = create_ocr_engine(settings)
    if ocr is None:
        pytest.skip("tesseract is not installed")
    return settings, ocr


@pytest.mark.parametrize("file_name", list(EXPECTED))
def test_sample_split(file_name, settings_and_ocr):
    path = SAMPLES_DIR / file_name
    if not path.exists():
        pytest.skip("samples/ is not available")
    settings, ocr = settings_and_ocr
    pages = [preprocess.preprocess(p, settings.preprocess, ocr) for p in ingest.load_file(path)]
    provider = CountingProvider()
    result = segment.segment(pages, ocr, load_form_types(settings.form_types_dir), provider,
                             settings.review_threshold)

    page_count, form_count = EXPECTED[file_name]
    assert len(pages) == page_count
    assert [g.pages for g in result.groups] == [[i] for i in range(form_count)]
    assert result.irrelevant_pages == []
    assert result.vlm_calls == provider.calls <= MAX_VLM_SHARE * (page_count - 1)
