"""The pure part of scripts/extract_report.py: the report holds counts, never values."""
import importlib.util
import sys
from pathlib import Path

from app.providers import fake
from app.schemas import Commitment, Field, FieldSignal, FormGroup, Job, SignalSource

_spec = importlib.util.spec_from_file_location(
    "extract_report", Path(__file__).resolve().parents[2] / "scripts" / "extract_report.py"
)
extract_report = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = extract_report
_spec.loader.exec_module(extract_report)


def test_report_counts_fields_calls_and_outcomes_without_values():
    reread = FieldSignal(source=SignalSource.REREAD, value=fake.FAKE_CENTRAL_NUMBER)
    read = FormGroup(source_file="f.pdf", pages=[0], vlm_calls=3, commitment=Commitment(
        id_number=Field[str](value=fake.FAKE_ID_NUMBER, reasons=["ocr_agrees"]),
        central_numbers=[Field[str](value=fake.FAKE_CENTRAL_NUMBER, reasons=["ocr_disagrees", "reread_agrees"],
                                    signals=[reread]),
                         Field[str](reasons=["illegible"])],
    ))
    failed = FormGroup(source_file="f.pdf", pages=[1], vlm_calls=1, error="ProviderError")
    job = Job(groups=[read, failed], split_vlm_calls=1)
    text = extract_report.render([("f.pdf", job, 2.0)], "fake", ocr_on=True)
    assert "Forms: 2 (failed: 1)" in text
    assert "| Total | 5 |" in text and "| — of which second looks answered | 1 |" in text
    assert "| id_number | 1 of 1 | — |" in text and "| central_numbers | 1 of 1 | 2 |" in text
    assert "| ocr_disagrees | 1 |" in text and "| illegible | 1 |" in text
    assert "- ProviderError: 1" in text
    assert fake.FAKE_ID_NUMBER not in text and fake.FAKE_CENTRAL_NUMBER not in text
