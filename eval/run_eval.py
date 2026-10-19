"""Compare pipeline predictions with eval/ground_truth.csv and write report.md.

Usage:  python eval/run_eval.py predictions.csv [--truth eval/ground_truth.csv] [--report eval/report.md]

Both CSVs use the ground_truth.csv columns and are keyed by (source_file, pages).
List fields (central_numbers, commitment_numbers) are ";"-separated and compared
as sets; dates are YYYY-MM-DD; an empty cell means "not found".
The report contains counts only — never field values.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
KEY_COLUMNS = ("source_file", "pages")
FIELD_COLUMNS = (
    "form_type_id", "first_name", "last_name", "id_number", "central_numbers",
    "received_date", "sender", "commitment_numbers",
)
LIST_COLUMNS = {"central_numbers", "commitment_numbers"}
LIST_SEPARATOR = ";"

Row = dict[str, str]
Key = tuple[str, str]


def load_rows(path: Path) -> dict[Key, Row]:
    with path.open(encoding="utf-8", newline="") as f:
        return {(row["source_file"], row["pages"]): row for row in csv.DictReader(f)}


def values_match(column: str, expected: str, actual: str) -> bool:
    if column in LIST_COLUMNS:
        return set(filter(None, expected.split(LIST_SEPARATOR))) == set(filter(None, actual.split(LIST_SEPARATOR)))
    return expected.strip() == actual.strip()


def field_accuracy(truth: dict[Key, Row], predictions: dict[Key, Row]) -> dict[str, tuple[int, int]]:
    """Return {column: (correct, total)}; a form missing from predictions counts as wrong."""
    result = {}
    for column in FIELD_COLUMNS:
        correct = sum(
            1 for key, row in truth.items()
            if key in predictions and values_match(column, row[column], predictions[key][column])
        )
        result[column] = (correct, len(truth))
    return result


def render_report(accuracy: dict[str, tuple[int, int]], missing_forms: int) -> str:
    lines = ["# Evaluation report", "", "| Field | Correct | Total | Accuracy |", "|---|---|---|---|"]
    for column, (correct, total) in accuracy.items():
        rate = f"{correct / total:.1%}" if total else "n/a"
        lines.append(f"| `{column}` | {correct} | {total} | {rate} |")
    lines += ["", f"Forms missing from predictions: {missing_forms}", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--truth", type=Path, default=EVAL_DIR / "ground_truth.csv")
    parser.add_argument("--report", type=Path, default=EVAL_DIR / "report.md")
    args = parser.parse_args()

    truth = load_rows(args.truth)
    predictions = load_rows(args.predictions)
    missing = sum(1 for key in truth if key not in predictions)
    args.report.write_text(render_report(field_accuracy(truth, predictions), missing), encoding="utf-8")
    print(f"written to {args.report}")


if __name__ == "__main__":
    main()
