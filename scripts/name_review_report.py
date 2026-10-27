"""Sum up exported name review records into one report.

Each input is the CSV of one job from ``GET /jobs/{id}/name-review.csv``
(letters and counts only, no names; see ``app/evaluation/name_review.py``).
The report goes to eval/name_review_report.md (git-ignored) unless ``--out``
is given.

Usage: python scripts/name_review_report.py FILE.csv [FILE.csv ...] [--out FILE]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR / "backend"))

from app.evaluation.name_review import read_csv, render_report  # noqa: E402

DEFAULT_OUT = REPO_DIR / "eval" / "name_review_report.md"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    records = [record for path in args.files for record in read_csv(path)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_report(records, len(args.files)), encoding="utf-8")
    print(f"{len(records)} records from {len(args.files)} files -> {args.out}")


if __name__ == "__main__":
    main()
