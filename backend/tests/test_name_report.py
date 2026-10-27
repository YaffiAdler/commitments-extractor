from app.evaluation.name_metrics import FieldOutcome, ImageOutcome
from app.evaluation.name_report import SYNTHETIC_NOTE, render
from app.evaluation.name_set import NameSample, SampleKind
from app.schemas import FieldSource

CONFIG = {"provider": "fake", "prompt": "v1"}
WRITTEN = [("דוד", "רבינוביץ"), ("אהרן", "כהן")]
READ = [("רוד", "רבינוביץ"), ("אהרו", None)]


def _image(kind: SampleKind, index: int) -> ImageOutcome:
    (first, last), (read_first, read_last) = WRITTEN[index], READ[index]
    sample = NameSample(image=f"{kind.value}_{index}.png", first_name=first, last_name=last, kind=kind)
    fields = tuple(
        FieldOutcome(name=name, kind=kind, truth=truth, read=read, needs_review=read is None,
                     source=FieldSource.HANDWRITTEN if read else FieldSource.NONE,
                     reasons=("handwritten",) if read else ("not_found",))
        for name, truth, read in (("first_name", first, read_first), ("last_name", last, read_last)))
    return ImageOutcome(sample=sample, forms=1, error=None, fields=fields)


def _report(kinds=(SampleKind.HANDWRITTEN,)) -> str:
    outcomes = [_image(kind, index) for kind in kinds for index in range(len(WRITTEN))]
    outcomes.append(ImageOutcome(sample=NameSample("x.png", "משה", "לוי", kinds[0]), forms=0, error="IngestError",
                                 fields=tuple(FieldOutcome(name=n, kind=kinds[0], truth=t, read=None, needs_review=True,
                                                           source=FieldSource.NONE)
                                              for n, t in (("first_name", "משה"), ("last_name", "לוי")))))
    return render(outcomes, CONFIG)


def test_report_holds_counts_and_letters_never_names_or_files():
    text = _report((SampleKind.HANDWRITTEN, SampleKind.SYNTHETIC))
    names = {name for pair in WRITTEN + READ for name in pair if name} | {"משה", "לוי"}
    assert not [name for name in names if name in text]
    assert ".png" not in text


def test_accuracy_rows():
    text = _report()
    # 3 pages: first names 0 of 3 exact, last names 1 of 3; one wrong first name not flagged per read page.
    assert "| handwritten | first_name | 3 | 0 (0%) | 0 (0%) | 1 | " in text
    assert "| handwritten | last_name | 3 | 1 (33%) | 1 (33%) | 2 | " in text
    assert "| handwritten | 3 | 0 (0%) |" in text
    assert "Configuration: provider `fake`, prompt `v1`." in text


def test_watched_groups_and_final_letters():
    text = _report()
    assert "| ד/ר | ד | ר | 1 |" in text
    assert "| ו/ן/ז/ג | ן | ו | 1 |" in text
    assert "| ע/צ | | | 0 |" in text
    assert "| ץ/צ | | | 0 |" in text


def test_pipeline_outcomes():
    text = _report()
    assert "Pages with no form read: 1." in text and "- IngestError: 1" in text
    assert "handwritten 3" in text and "none 3" in text and "not_found 1" in text


def test_synthetic_note_only_with_synthetic_pages():
    assert SYNTHETIC_NOTE not in _report()
    text = _report((SampleKind.SYNTHETIC,))
    assert SYNTHETIC_NOTE in text and "## Letter confusions — synthetic" in text


def test_second_look_section_only_when_it_ran():
    assert "## Name second look" not in _report()
    sample = NameSample("y.png", "דוד", "לוי", SampleKind.HANDWRITTEN)
    fields = (
        FieldOutcome(name="first_name", kind=sample.kind, truth="דוד", read="רוד", needs_review=True,
                     source=FieldSource.HANDWRITTEN, reasons=("name_readings_disagree",), candidates=("רוד", "דוד")),
        FieldOutcome(name="last_name", kind=sample.kind, truth="לוי", read="לוי", needs_review=True,
                     source=FieldSource.HANDWRITTEN, reasons=("name_readings_agree",)),
    )
    text = render([ImageOutcome(sample=sample, forms=1, error=None, fields=fields)], CONFIG)
    assert "| handwritten | agree | 1 | 1 | 0 | 0 |" in text
    assert "| handwritten | disagree | 1 | 0 | 1 | 1 |" in text
    assert "דוד" not in text and "לוי" not in text

