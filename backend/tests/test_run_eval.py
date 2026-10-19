import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "run_eval", Path(__file__).resolve().parents[2] / "eval" / "run_eval.py"
)
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)


def test_list_fields_compare_as_sets():
    assert run_eval.values_match("central_numbers", "900001;900002", "900002;900001")
    assert not run_eval.values_match("id_number", "000000018", "18")


def test_missing_prediction_counts_as_wrong():
    row = {c: "" for c in run_eval.FIELD_COLUMNS}
    truth = {("a.pdf", "0"): row, ("a.pdf", "1"): row}
    accuracy = run_eval.field_accuracy(truth, {("a.pdf", "0"): row})
    assert accuracy["sender"] == (1, 2)
