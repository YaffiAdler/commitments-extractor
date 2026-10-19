from openpyxl import load_workbook

from app.export.xlsx import COLUMNS, NEEDS_REVIEW_YES, REVIEW_FILL, write_xlsx
from app.schemas import Commitment, Field, FormGroup, Job


def _job(commitment: Commitment) -> Job:
    return Job(groups=[FormGroup(source_file="a.pdf", pages=[0], commitment=commitment)])


def test_writes_header_and_row(tmp_path):
    commitment = Commitment(
        id_number=Field[str](value="012345678", confidence=1.0, needs_review=False),
        central_numbers=[Field[str](value="111111"), Field[str](value="222222")],
    )
    sheet = load_workbook(write_xlsx(_job(commitment), tmp_path / "out.xlsx")).active
    assert sheet.sheet_view.rightToLeft
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][: len(COLUMNS)] == tuple(h for h, _ in COLUMNS)
    assert rows[1][2] == "012345678"  # leading zero kept
    assert rows[1][3] == "111111; 222222"
    assert rows[1][-2:] == ("a.pdf", NEEDS_REVIEW_YES)


def test_missing_fields_are_empty_and_highlighted(tmp_path):
    sheet = load_workbook(write_xlsx(_job(Commitment()), tmp_path / "out.xlsx")).active
    for column in range(1, len(COLUMNS) + 1):
        cell = sheet.cell(row=2, column=column)
        assert cell.value is None
        assert cell.fill.start_color.rgb.endswith(REVIEW_FILL.start_color.rgb[-6:])
