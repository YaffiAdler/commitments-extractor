"""Name review records: reviewers' name corrections as letters and counts only (dummy names)."""
from app.corrections import apply_correction
from app.evaluation.name_metrics import AGREE, NOT_COMPARED
from app.evaluation.name_review import NameReviewRecord, read_csv, render_report, review_records, to_csv
from app.pipeline.reread_name import NAME_READINGS_AGREE
from app.schemas import Field, FieldSource, FormGroup, Job

SENDERS = frozenset({"כללית"})
READ_FIRST, WRITTEN_FIRST = "רוד", "דוד"
READ_LAST = "כהן"


def _job() -> Job:
    group = FormGroup(source_file="a" * 32 + ".pdf", pages=[0])
    group.commitment.first_name = Field[str](value=READ_FIRST, raw_value=READ_FIRST, confidence=0.75,
                                             needs_review=True, source=FieldSource.HANDWRITTEN,
                                             reasons=["handwritten", NAME_READINGS_AGREE])
    group.commitment.last_name = Field[str](value=READ_LAST, raw_value=READ_LAST, confidence=0.95,
                                            needs_review=False, source=FieldSource.PRINTED, reasons=[])
    group.commitment.id_number = Field[str](value="000000018", raw_value="000000018", source=FieldSource.PRINTED)
    return Job(groups=[group])


def _reviewed() -> tuple[Job, list[NameReviewRecord]]:
    job = _job()
    group_id = job.groups[0].id
    apply_correction(job, group_id, "first_name", WRITTEN_FIRST, None, SENDERS)
    apply_correction(job, group_id, "last_name", READ_LAST, None, SENDERS)  # confirmed unchanged
    apply_correction(job, group_id, "id_number", "000000026", None, SENDERS)
    return job, review_records(job)


def test_records_only_for_saved_names():
    assert review_records(_job()) == []
    _, records = _reviewed()
    assert [r.field for r in records] == ["first_name", "last_name"]


def test_corrected_name_keeps_what_the_pipeline_thought():
    _, [first, last] = _reviewed()
    assert (first.previous_source, first.agreement, first.previous_needs_review, first.previous_confidence) == (
        "handwritten", AGREE, True, 0.75)
    assert (first.correct, first.edit_distance, first.written_length) == (False, 1, 3)
    assert first.substitutions == (("ד", "ר"),) and not first.wrong_not_flagged
    assert (last.correct, last.correct_strict, last.agreement, last.edit_distance) == (True, True, NOT_COMPARED, 0)


def test_the_first_correction_is_compared():
    job, _ = _reviewed()
    apply_correction(job, job.groups[0].id, "first_name", "דוב", None, SENDERS)
    [first, _] = review_records(job)
    assert first.edit_distance == 2  # the reading "רוד" against the final "דוב", not against "דוד"
    assert first.reasons == ("handwritten", NAME_READINGS_AGREE)


def test_wrong_and_not_flagged():
    job = _job()
    apply_correction(job, job.groups[0].id, "last_name", "כהנא", None, SENDERS)
    [record] = review_records(job)
    assert record.wrong_not_flagged and record.insertions == () and record.deletions == ("א",)


def test_csv_round_trip_without_names(tmp_path):
    _, records = _reviewed()
    text = to_csv(records)
    for name in (READ_FIRST, WRITTEN_FIRST, READ_LAST):
        assert name not in text
    path = tmp_path / "job.csv"
    path.write_text(text, encoding="utf-8-sig")
    assert read_csv(path) == records


def test_report_has_counts_and_letters_only():
    _, records = _reviewed()
    report = render_report(records, files=1)
    assert "Reviewed name fields: 2" in report and "| all | all | 2 |" in report
    for name in (READ_FIRST, WRITTEN_FIRST, READ_LAST):
        assert name not in report
    assert "Reviewed name fields: 0" in render_report([], files=0)
