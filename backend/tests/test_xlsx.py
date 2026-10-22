"""Export -> open again with openpyxl -> compare. Dummy values only."""
from datetime import date, datetime

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app.config import EXPORT_COLUMN_KEYS, ExportSettings, MultiValueMode
from app.export.xlsx import (
    DATE_FORMAT,
    GENERIC_REVIEW_TEXT,
    HEADERS,
    IRRELEVANT_SHEET_TITLE,
    MAX_COLUMN_WIDTH,
    REVIEW_FILL,
    SHEET_TITLE,
    TEXT_FORMAT,
    review_notes,
    write_xlsx,
)
from app.pipeline import normalize, ocr_check
from app.pipeline.reason_texts import FORM_REASON_TEXTS, REASON_TEXTS
from app.schemas import (
    CENTRAL_DUPLICATE,
    NOT_FOUND,
    SPLIT_UNCERTAIN,
    Commitment,
    Field,
    FormGroup,
    IrrelevantPage,
    Job,
)

DEFAULT_HEADERS = ("שם פרטי", "שם משפחה", "ת.ז.", "מספר מרכזי", "תאריך קבלה", "גורם שולח", "מספר התחייבות",
                   "הערות לבדיקה", "קובץ מקור")
SOURCE = "0a1b2c.pdf"


def _ok(value):
    return Field(value=value, confidence=1.0, needs_review=False)


def _flagged(value, *reasons):
    return Field(value=value, confidence=0.5, needs_review=True, reasons=list(reasons))


def _complete() -> Commitment:
    """Every field read and confirmed."""
    return Commitment(
        first_name=_ok("אברהם"),
        last_name=_ok("כהן"),
        id_number=_ok("012345672"),
        central_numbers=[_ok("0111111"), _ok("222222")],
        received_date=_ok(date(2025, 3, 9)),
        sender=_ok("כללית"),
        commitment_numbers=[_ok("0001234567")],
    )


def _job(*commitments: Commitment, **group_fields) -> Job:
    return Job(groups=[FormGroup(source_file=SOURCE, pages=[0], commitment=c, **group_fields) for c in commitments])


def _export(tmp_path, job, settings=None):
    path = write_xlsx(job, tmp_path / "out.xlsx", settings or ExportSettings(columns=EXPORT_COLUMN_KEYS[:-1]))
    return load_workbook(path)


def _rows(sheet):
    return list(sheet.iter_rows(values_only=True))


def _column(sheet, header):
    return [cell.value for cell in sheet[1]].index(header) + 1


def _is_orange(cell):
    return cell.fill.fill_type == "solid" and cell.fill.start_color.rgb.endswith(REVIEW_FILL.start_color.rgb[-6:])


def test_default_columns_follow_the_task():
    assert ExportSettings().columns == EXPORT_COLUMN_KEYS[:-1]
    assert tuple(HEADERS[key] for key in ExportSettings().columns) == DEFAULT_HEADERS
    assert set(HEADERS) == set(EXPORT_COLUMN_KEYS)


def test_roundtrip_values_and_types(tmp_path):
    sheet = _export(tmp_path, _job(_complete()))[SHEET_TITLE]
    header, row = _rows(sheet)
    assert header == DEFAULT_HEADERS
    assert row == ("אברהם", "כהן", "012345672", "0111111; 222222", datetime(2025, 3, 9), "כללית", "0001234567",
                   None, SOURCE)


def test_id_and_numbers_are_text_with_leading_zeros(tmp_path):
    sheet = _export(tmp_path, _job(_complete()))[SHEET_TITLE]
    for header in ("ת.ז.", "מספר מרכזי", "מספר התחייבות"):
        cell = sheet.cell(row=2, column=_column(sheet, header))
        assert cell.data_type == "s"
        assert cell.number_format == TEXT_FORMAT
        assert cell.value.startswith("0")


def test_date_is_a_date_cell_in_dd_mm_yyyy(tmp_path):
    sheet = _export(tmp_path, _job(_complete()))[SHEET_TITLE]
    cell = sheet.cell(row=2, column=_column(sheet, "תאריך קבלה"))
    assert cell.is_date
    assert cell.value.date() == date(2025, 3, 9)
    assert cell.number_format == DATE_FORMAT


def test_sheet_layout(tmp_path):
    sheet = _export(tmp_path, _job(_complete()))[SHEET_TITLE]
    assert sheet.sheet_view.rightToLeft
    assert sheet.freeze_panes == "A2"
    assert all(cell.font.bold for cell in sheet[1])
    assert not any(cell.font.bold for cell in sheet[2])


def test_column_widths_follow_the_content(tmp_path):
    commitment = _complete()
    commitment.central_numbers = [_ok(str(n) * 6) for n in range(1, 10)]  # a long joined cell
    sheet = _export(tmp_path, _job(commitment))[SHEET_TITLE]
    widths = {header: sheet.column_dimensions[get_column_letter(_column(sheet, header))].width
              for header in ("מספר מרכזי", "שם פרטי")}
    assert widths["שם פרטי"] < widths["מספר מרכזי"] <= MAX_COLUMN_WIDTH


def test_empty_fields_stay_empty_and_are_orange(tmp_path):
    sheet = _export(tmp_path, _job(Commitment()))[SHEET_TITLE]
    for key in ("first_name", "last_name", "id_number", "central_numbers", "received_date", "sender",
                "commitment_numbers"):
        cell = sheet.cell(row=2, column=_column(sheet, HEADERS[key]))
        assert cell.value is None
        assert _is_orange(cell)
    notes = sheet.cell(row=2, column=_column(sheet, "הערות לבדיקה")).value.split("\n")
    assert f"ת.ז.: {REASON_TEXTS[NOT_FOUND]}" in notes
    assert f"מספר מרכזי: {REASON_TEXTS[NOT_FOUND]}" in notes
    assert len(notes) == 7


def test_empty_text_cells_still_keep_leading_zeros_when_typed(tmp_path):
    sheet = _export(tmp_path, _job(Commitment()))[SHEET_TITLE]
    assert sheet.cell(row=2, column=_column(sheet, "ת.ז.")).number_format == TEXT_FORMAT
    assert sheet.cell(row=2, column=_column(sheet, "תאריך קבלה")).number_format == DATE_FORMAT


def test_uncertain_field_is_orange_with_its_reasons(tmp_path):
    commitment = _complete()
    commitment.id_number = _flagged("012345678", normalize.ID_CHECKSUM_FAILED, ocr_check.OCR_DISAGREES)
    sheet = _export(tmp_path, _job(commitment))[SHEET_TITLE]
    id_cell = sheet.cell(row=2, column=_column(sheet, "ת.ז."))
    assert id_cell.value == "012345678"  # kept, not cleared
    assert _is_orange(id_cell)
    assert not _is_orange(sheet.cell(row=2, column=_column(sheet, "שם פרטי")))
    notes = sheet.cell(row=2, column=_column(sheet, "הערות לבדיקה"))
    assert notes.value == (f"ת.ז.: {REASON_TEXTS[normalize.ID_CHECKSUM_FAILED]}; "
                           f"{REASON_TEXTS[ocr_check.OCR_DISAGREES]}")
    assert notes.alignment.wrap_text


def test_notes_prefer_stored_explanations():
    commitment = _complete()
    commitment.sender = Field(value="כללית", needs_review=True, reasons=["x"], explanations=["הסבר שמור"])
    assert review_notes(FormGroup(source_file=SOURCE, pages=[0], commitment=commitment)) == "גורם שולח: הסבר שמור"


def test_flagged_field_without_reasons_gets_a_generic_note():
    commitment = _complete()
    commitment.first_name = Field(value="אברהם")  # needs_review defaults to True
    assert review_notes(FormGroup(source_file=SOURCE, pages=[0], commitment=commitment)) == \
        f"שם פרטי: {GENERIC_REVIEW_TEXT}"


def test_flagged_list_item_is_numbered_in_the_notes():
    commitment = _complete()
    commitment.central_numbers = [_ok("111111"), _flagged("222222", ocr_check.OCR_DISAGREES)]
    notes = review_notes(FormGroup(source_file=SOURCE, pages=[0], commitment=commitment))
    assert notes == f"מספר מרכזי 2: {REASON_TEXTS[ocr_check.OCR_DISAGREES]}"


def test_form_level_reasons_come_first_in_the_notes(tmp_path):
    job = _job(_complete(), split_confidence=0.3, split_reasons=[SPLIT_UNCERTAIN],
               form_reasons=[CENTRAL_DUPLICATE])
    sheet = _export(tmp_path, job)[SHEET_TITLE]
    notes = sheet.cell(row=2, column=_column(sheet, "הערות לבדיקה")).value
    assert notes.split("\n") == [FORM_REASON_TEXTS[SPLIT_UNCERTAIN], FORM_REASON_TEXTS[CENTRAL_DUPLICATE]]


def test_optional_empty_list_is_not_orange(tmp_path):
    commitment = _complete()
    commitment.commitment_numbers = []
    commitment.optional_fields = ["commitment_numbers"]
    sheet = _export(tmp_path, _job(commitment))[SHEET_TITLE]
    cell = sheet.cell(row=2, column=_column(sheet, "מספר התחייבות"))
    assert cell.value is None
    assert cell.fill.fill_type is None
    assert sheet.cell(row=2, column=_column(sheet, "הערות לבדיקה")).value is None


def test_separate_columns_mode(tmp_path):
    many = _complete()
    many.central_numbers = [_ok("0111111"), _flagged("222222", ocr_check.OCR_DISAGREES), _ok("333333")]
    empty = _complete()
    empty.central_numbers = []
    settings = ExportSettings(columns=("id_number", "central_numbers", "commitment_numbers"),
                              multi_values=MultiValueMode.COLUMNS)
    sheet = _export(tmp_path, _job(many, empty), settings)[SHEET_TITLE]
    rows = _rows(sheet)
    assert rows[0] == ("ת.ז.", "מספר מרכזי 1", "מספר מרכזי 2", "מספר מרכזי 3", "מספר התחייבות 1")
    assert rows[1] == ("012345672", "0111111", "222222", "333333", "0001234567")
    assert rows[2] == ("012345672", None, None, None, "0001234567")
    assert [_is_orange(sheet.cell(row=2, column=c)) for c in (2, 3, 4)] == [False, True, False]
    # An empty list that must not be empty: flagged once, in its first column.
    assert [_is_orange(sheet.cell(row=3, column=c)) for c in (2, 3, 4)] == [True, False, False]
    assert sheet.cell(row=2, column=2).number_format == TEXT_FORMAT


def test_columns_follow_the_settings_order(tmp_path):
    settings = ExportSettings(columns=("source_file", "pages", "id_number"))
    job = Job(groups=[FormGroup(source_file=SOURCE, pages=[1, 2], commitment=_complete())])
    assert _rows(_export(tmp_path, job, settings)[SHEET_TITLE]) == [
        ("קובץ מקור", "עמודים", "ת.ז."), (SOURCE, "2, 3", "012345672")]


def test_irrelevant_pages_sheet(tmp_path):
    job = _job(_complete())
    job.irrelevant_pages = [IrrelevantPage(source_file=SOURCE, page=0)]
    sheet = _export(tmp_path, job)[IRRELEVANT_SHEET_TITLE]
    assert _rows(sheet) == [("קובץ מקור", "עמוד"), (SOURCE, 1)]
    assert sheet.sheet_view.rightToLeft and sheet.freeze_panes == "A2"


def test_no_irrelevant_sheet_when_none(tmp_path):
    assert _export(tmp_path, _job(_complete())).sheetnames == [SHEET_TITLE]


def test_empty_job_writes_headers_only(tmp_path):
    settings = ExportSettings(multi_values=MultiValueMode.COLUMNS)
    rows = _rows(_export(tmp_path, Job(), settings)[SHEET_TITLE])
    assert len(rows) == 1
    assert "מספר מרכזי 1" in rows[0]


@pytest.mark.parametrize("columns", [("first_name", "nope"), ("id_number", "id_number")])
def test_invalid_column_settings_rejected(columns):
    with pytest.raises(ValueError):
        ExportSettings(columns=columns)
