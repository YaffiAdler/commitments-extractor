"""Export a finished job to an Excel workbook, one row per form,
with fields that need review highlighted."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.schemas import Commitment, Field, Job

SHEET_TITLE = "התחייבויות"
LIST_SEPARATOR = "; "
DATE_FORMAT = "DD/MM/YYYY"
REVIEW_FILL = PatternFill(fill_type="solid", start_color="FFF2CC", end_color="FFF2CC")

# (header, Commitment attribute) — header text is exported verbatim.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("שם פרטי", "first_name"),
    ("שם משפחה", "last_name"),
    ("ת.ז.", "id_number"),
    ("מספר מרכזי", "central_numbers"),
    ("תאריך קבלה", "received_date"),
    ("גורם שולח", "sender"),
    ("מספר התחייבות", "commitment_numbers"),
)
SOURCE_FILE_HEADER = "קובץ"
NEEDS_REVIEW_HEADER = "לבדיקה"
NEEDS_REVIEW_YES = "כן"


def _cell(value: Field | list[Field]) -> tuple[str | date | None, bool]:
    """Cell value and whether it needs review. Numbers are kept as text
    (leading zeros matter); an empty list counts as missing."""
    if isinstance(value, list):
        text = LIST_SEPARATOR.join(f.value for f in value if f.value is not None)
        return text or None, not value or any(f.needs_review for f in value)
    return value.value, value.needs_review


def _write_row(sheet: Worksheet, row: int, source_file: str, commitment: Commitment) -> None:
    for column, (_, attribute) in enumerate(COLUMNS, start=1):
        value, needs_review = _cell(getattr(commitment, attribute))
        cell = sheet.cell(row=row, column=column, value=value)
        if isinstance(value, date):
            cell.number_format = DATE_FORMAT
        elif value is not None:
            cell.number_format = "@"
        if needs_review:
            cell.fill = REVIEW_FILL
    sheet.cell(row=row, column=len(COLUMNS) + 1, value=source_file)
    sheet.cell(row=row, column=len(COLUMNS) + 2, value=NEEDS_REVIEW_YES if commitment.needs_review else None)


def write_xlsx(job: Job, out_path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE
    sheet.sheet_view.rightToLeft = True
    sheet.append([header for header, _ in COLUMNS] + [SOURCE_FILE_HEADER, NEEDS_REVIEW_HEADER])
    for row, group in enumerate(job.groups, start=2):
        _write_row(sheet, row, group.source_file, group.commitment)
    workbook.save(out_path)
    return out_path
