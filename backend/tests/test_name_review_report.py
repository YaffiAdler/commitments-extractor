"""scripts/name_review_report.py: exported name review CSVs summed up into one report."""
import importlib.util
import sys
from pathlib import Path

from app.evaluation.name_review import NameReviewRecord, to_csv

_spec = importlib.util.spec_from_file_location(
    "name_review_report", Path(__file__).resolve().parents[2] / "scripts" / "name_review_report.py"
)
name_review_report = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = name_review_report
_spec.loader.exec_module(name_review_report)


def _record(correct: bool) -> NameReviewRecord:
    return NameReviewRecord(field="first_name", previous_source="handwritten", agreement="agree",
                            previous_needs_review=True, previous_confidence=0.75, read_empty=False,
                            correct=correct, correct_strict=correct, edit_distance=0 if correct else 1,
                            written_length=3, substitutions=() if correct else (("ד", "ר"),))


def test_report_sums_up_every_file(tmp_path, monkeypatch):
    files = []
    for index, records in enumerate(([_record(True)], [_record(False), _record(True)])):
        path = tmp_path / f"job{index}.csv"
        path.write_text(to_csv(records), encoding="utf-8-sig")
        files.append(str(path))
    out = tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", ["name_review_report.py", *files, "--out", str(out)])
    name_review_report.main()
    report = out.read_text(encoding="utf-8")
    assert "Exported jobs: 2. Reviewed name fields: 3." in report
    assert "| all | all | 3 |" in report
