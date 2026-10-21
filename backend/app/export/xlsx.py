"""Export a finished job to an Excel workbook, one row per form,
with fields that need review highlighted. Forms that may be split wrongly
are marked in their own column; pages that belong to no form are listed on
a second sheet."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from app.schemas import SPLIT_UNCERTAIN, Field, FormGroup, Job

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
PAGES_HEADER = "עמודים"
SPLIT_HEADER = "פיצול"
SPLIT_UNCERTAIN_TEXT = "ייתכן שפוצל לא נכון"
NEEDS_REVIEW_HEADER = "לבדיקה"
NEEDS_REVIEW_YES = "כן"
EXTRA_HEADERS = (SOURCE_FILE_HEADER, PAGES_HEADER, SPLIT_HEADER, NEEDS_REVIEW_HEADER)
PAGE_SEPARATOR = ", "
IRRELEVANT_SHEET_TITLE = "עמודים לא רלוונטיים"
IRRELEVANT_HEADERS = (SOURCE_FILE_HEADER, "עמוד")


def _cell(value: Field | list[Field]) -> tuple[str | date | None, bool]:
    """Cell value and whether it needs review. Numbers are kept as text
    (leading zeros matter); an empty list counts as missing."""
    if isinstance(value, list):
        text = LIST_SEPARATOR.join(f.value for f in value if f.value is not None)
        return text or None, not value or any(f.needs_review for f in value)
    return value.value, value.needs_review


def _page_numbers(pages: list[int]) -> str:
    """1-based, as a reader counts pages."""
    return PAGE_SEPARATOR.join(str(page + 1) for page in pages)


def _write_row(sheet: Worksheet, row: int, group: FormGroup) -> None:
    commitment = group.commitment
    for column, (_, attribute) in enumerate(COLUMNS, start=1):
        value, needs_review = _cell(getattr(commitment, attribute))
        cell = sheet.cell(row=row, column=column, value=value)
        if isinstance(value, date):
            cell.number_format = DATE_FORMAT
        elif value is not None:
            cell.number_format = "@"
        if needs_review:
            cell.fill = REVIEW_FILL
    split_uncertain = SPLIT_UNCERTAIN in group.split_reasons
    extra = (group.source_file, _page_numbers(group.pages), SPLIT_UNCERTAIN_TEXT if split_uncertain else None,
             NEEDS_REVIEW_YES if group.needs_review else None)
    for column, value in enumerate(extra, start=len(COLUMNS) + 1):
        sheet.cell(row=row, column=column, value=value)
    if split_uncertain:
        sheet.cell(row=row, column=len(COLUMNS) + 1 + EXTRA_HEADERS.index(SPLIT_HEADER)).fill = REVIEW_FILL


def _write_irrelevant_pages(sheet: Worksheet, job: Job) -> None:
    sheet.sheet_view.rightToLeft = True
    sheet.append(list(IRRELEVANT_HEADERS))
    for page in job.irrelevant_pages:
        sheet.append([page.source_file, page.page + 1])


def write_xlsx(job: Job, out_path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE
    sheet.sheet_view.rightToLeft = True
    sheet.append([header for header, _ in COLUMNS] + list(EXTRA_HEADERS))
    for row, group in enumerate(job.groups, start=2):
        _write_row(sheet, row, group)
    if job.irrelevant_pages:
        _write_irrelevant_pages(workbook.create_sheet(IRRELEVANT_SHEET_TITLE), job)
    workbook.save(out_path)
    return out_path
