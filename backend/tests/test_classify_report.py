"""The pure parts of scripts/classify_report.py."""
import importlib.util
import sys
from pathlib import Path

from app.config import Settings
from app.pipeline.classify import SenderMatch, SenderMatchStatus, load_form_types

_spec = importlib.util.spec_from_file_location(
    "classify_report", Path(__file__).resolve().parents[2] / "scripts" / "classify_report.py"
)
classify_report = importlib.util.module_from_spec(_spec)
# @dataclass looks its module up in sys.modules.
sys.modules[_spec.name] = classify_report
_spec.loader.exec_module(classify_report)


def test_expected_form_types_cover_every_sample_page():
    expected = classify_report.expected_form_types(load_form_types(Settings().form_types_dir))
    # docs/form_mapping.md: 26 + 22 pages, each assigned a type.
    assert {page for part, page in expected if part == 1} == set(range(1, 27))
    assert {page for part, page in expected if part == 2} == set(range(1, 23))
    assert expected[(1, 25)] == "sheba_insurance_screen" and expected[(2, 1)] == "shamir_external"


def test_file_part():
    assert classify_report.file_part("התחייבויות - דוגמאות, חלק 2 (PDF).pdf") == 2
    assert classify_report.file_part("other.pdf") is None


def test_suggestion_names_the_closest_sender_without_applying_it():
    near = SenderMatch(raw="בית חולים א", status=SenderMatchStatus.NOT_IN_LIST, candidates=(("ב", 80.0),))
    assert "alias of 'ב'" in classify_report.suggestion(near)
    far = SenderMatch(raw="ג", status=SenderMatchStatus.NOT_IN_LIST, candidates=(("ב", 40.0),))
    assert "new sender" in classify_report.suggestion(far)
