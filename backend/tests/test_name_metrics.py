import pytest

from app.evaluation.name_metrics import FieldOutcome, confusions, image_outcome, summarize
from app.evaluation.name_set import NameSample, SampleKind
from app.pipeline.reread_name import NAME_READINGS_AGREE, NAME_READINGS_DISAGREE
from app.schemas import Commitment, Field, FieldSignal, FieldSource, FormGroup, Job, SignalSource

HAND = SampleKind.HANDWRITTEN


def _outcome(truth: str, read: str | None, needs_review: bool = True) -> FieldOutcome:
    return FieldOutcome(name="last_name", kind=HAND, truth=truth, read=read, needs_review=needs_review,
                        source=FieldSource.HANDWRITTEN if read else FieldSource.NONE)


def test_exact_match_ignores_final_letters_quotes_and_spacing():
    assert _outcome("אברהם", " אברהמ ").exact
    assert _outcome('כ"ץ', "כץ").exact
    assert not _outcome("אברהם", "אברהמ").exact_strict
    assert _outcome("אברהם", "אברהם").exact_strict
    assert not _outcome("דוד", "רוד").exact


def test_summary_counts_and_cer():
    outcomes = [
        _outcome("כהן", "כהן"),                          # right
        _outcome("לוי", "לני", needs_review=False),     # 1 edit, wrong and not flagged
        _outcome("שטרן", None),                          # left empty: 4 deletions
        _outcome("", None),                              # nothing written, nothing read: right
    ]
    s = summarize(outcomes)
    assert (s.fields, s.exact, s.exact_strict, s.left_empty) == (4, 2, 2, 1)
    assert (s.edit_distance, s.reference_chars) == (5, 10)
    assert s.cer == pytest.approx(0.5)
    assert (s.flagged, s.wrong_not_flagged) == (3, 1)


def test_cer_is_none_without_reference_letters():
    assert summarize([_outcome("", None)]).cer is None
    assert summarize([]).cer is None


def test_confusions_keep_final_letters_and_skip_empty_reads():
    table = confusions([
        _outcome("דוד", "רוד"),
        _outcome("אהרן", "אהרו"),       # final nun read as vav
        _outcome("יוסף", "יסף"),        # vav dropped
        _outcome("לוי", "לויי"),        # yod added
        _outcome("שטרן", None),         # not read: no letters to compare
    ])
    assert table.substitutions == {("ד", "ר"): 1, ("ן", "ו"): 1}
    assert table.deletions == {"ו": 1}
    assert table.insertions == {"י": 1}


def _job(first: str | None, last: str | None, error: str | None = None, extra_groups: int = 0) -> Job:
    commitment = Commitment(first_name=Field[str](value=first, source=FieldSource.HANDWRITTEN, reasons=["handwritten"],
                                                  needs_review=True),
                            last_name=Field[str](value=last, source=FieldSource.HANDWRITTEN))
    groups = [FormGroup(source_file="p.png", pages=[0], commitment=commitment, error=error)]
    groups += [FormGroup(source_file="p.png", pages=[n + 1]) for n in range(extra_groups)]
    return Job(groups=groups)


SAMPLE = NameSample(image="p.png", first_name="משה", last_name="לוי", kind=HAND)


def test_image_outcome_takes_the_names_of_the_first_form():
    outcome = image_outcome(SAMPLE, _job("משה", "לוי"))
    assert outcome.forms == 1 and outcome.error is None and outcome.both_exact
    first, last = outcome.fields
    assert (first.name, first.read, first.needs_review, first.reasons) == ("first_name", "משה", True, ("handwritten",))
    assert (last.name, last.source) == ("last_name", FieldSource.HANDWRITTEN)


def test_image_outcome_skips_a_failed_form_and_counts_all_forms():
    job = _job(None, None, error="ProviderError", extra_groups=1)
    outcome = image_outcome(SAMPLE, job)
    assert outcome.forms == 2 and outcome.error is None
    assert [f.read for f in outcome.fields] == [None, None]  # the second form has no names


def test_image_outcome_of_a_failed_job_is_empty_and_flagged():
    outcome = image_outcome(SAMPLE, Job(error="IngestError"))
    assert outcome.forms == 0 and outcome.error == "IngestError"
    assert all(f.read is None and f.needs_review and f.left_empty for f in outcome.fields)
    assert not outcome.both_exact


def test_image_outcome_reports_the_form_error_when_every_form_failed():
    outcome = image_outcome(SAMPLE, _job(None, None, error="TimeoutError"))
    assert outcome.error == "TimeoutError"


def test_agreement_and_candidates_come_from_the_name_second_look():
    signals = [FieldSignal(source=SignalSource.REREAD, value="רוד", variant="wide", candidates=["רוד", "דוד"]),
               FieldSignal(source=SignalSource.REREAD, value="999")]  # a number second look: not a name candidate
    commitment = Commitment(first_name=Field[str](value="רוד", source=FieldSource.HANDWRITTEN,
                                                  reasons=[NAME_READINGS_DISAGREE], signals=signals))
    sample = NameSample(image="p.png", first_name="דוד", last_name="", kind=HAND)
    first, last = image_outcome(sample, Job(groups=[FormGroup(source_file="p.png", pages=[0],
                                                               commitment=commitment)])).fields
    assert first.agreement == "disagree" and first.candidates == ("רוד", "דוד") and first.truth_in_candidates
    assert last.agreement == "none" and not last.truth_in_candidates
    assert FieldOutcome(name="first_name", kind=HAND, truth="x", read="x", needs_review=True,
                        source=FieldSource.HANDWRITTEN, reasons=(NAME_READINGS_AGREE,)).agreement == "agree"
