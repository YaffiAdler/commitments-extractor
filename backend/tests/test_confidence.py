"""Stage 7: confidence from several signals and ``needs_review``.

The completion criteria of the stage, with the default (uncalibrated) weights:
full agreement -> green, disagreement -> orange, empty -> orange, an ID with
a wrong check digit -> a slight drop only. All values are dummies.
"""
from datetime import date

import pytest

from app.config import ConfidenceSettings, Settings
from app.pipeline import normalize
from app.pipeline.classify import (
    SENDER_FUZZY_MATCH,
    SENDER_READINGS_AGREE,
    SENDER_READINGS_DISAGREE,
    load_form_types,
)
from app.pipeline.confidence import (
    HARD_FAIL_REASONS,
    SOFT_FAIL_REASONS,
    FieldKind,
    FieldSignals,
    ProbabilityModelScorer,
    WeightedScorer,
    field_kind,
    optional_fields,
    score_field,
    score_fields,
)
from app.pipeline.extract import HANDWRITTEN, ILLEGIBLE
from app.pipeline.ocr_check import OCR_AGREES, OCR_DISAGREES, OCR_UNAVAILABLE
from app.pipeline.reason_texts import REASON_TEXTS, SINGLE_READING
from app.pipeline.reread import REREAD_AGREES, REREAD_DISAGREES, REREAD_UNAVAILABLE
from app.pipeline.reread_name import (
    NAME_FILLED_FROM_SECOND_LOOK,
    NAME_READINGS_AGREE,
    NAME_READINGS_DISAGREE,
    NAME_UNCERTAIN_LETTERS,
)
from app.schemas import (
    CENTRAL_DUPLICATE,
    NOT_FOUND,
    OPTIONAL_MISSING,
    Commitment,
    Field,
    FieldSignal,
    FieldSource,
    SignalSource,
)

WEIGHTS = ConfidenceSettings(
    base_printed=0.8, base_handwritten=0.55, base_closed_list=0.75, ocr_agreement=0.25,
    second_reading_agreement=0.15, ocr_disagreement=0.3, second_reading_disagreement=0.3,
    hard_fail=0.3, soft_fail=0.1, illegible=0.2, review_threshold=0.9,
)
SCORER = WeightedScorer(WEIGHTS)
VALID_ID = "000000018"  # dummy, valid check digit
BAD_CHECK_DIGIT_ID = "000000019"
FORM_TYPE = load_form_types(Settings().form_types_dir)["clalit_form17"]


def _score(field: Field, kind: FieldKind = FieldKind.PRINTED, optional: bool = False) -> Field:
    return score_field(field, kind, SCORER, WEIGHTS.review_threshold, optional)


def _printed(*reasons: str, value: str = VALID_ID) -> Field[str]:
    return Field[str](value=value, raw_value=value, source=FieldSource.PRINTED, reasons=list(reasons))


def _handwritten(*reasons: str, value: str = "123456") -> Field[str]:
    return Field[str](value=value, raw_value=value, source=FieldSource.HANDWRITTEN, reasons=[HANDWRITTEN, *reasons])


def _with_optional(*names: str):
    fields = {name: spec.model_copy(update={"optional": name in names}) for name, spec in FORM_TYPE.fields.items()}
    return FORM_TYPE.model_copy(update={"fields": fields})


# --- The completion criteria ---


def test_full_agreement_is_green():
    scored = _score(_printed(OCR_AGREES))
    assert scored.confidence == 1.0 and not scored.needs_review


def test_full_agreement_on_the_second_reading_only_is_green():
    scored = _score(_printed(OCR_UNAVAILABLE, REREAD_AGREES))
    assert scored.confidence == pytest.approx(0.95) and not scored.needs_review


@pytest.mark.parametrize("field, kind", [
    (_printed(OCR_DISAGREES, REREAD_AGREES), FieldKind.PRINTED),
    (_printed(OCR_DISAGREES, REREAD_DISAGREES), FieldKind.PRINTED),
    (_printed(OCR_UNAVAILABLE, REREAD_DISAGREES), FieldKind.PRINTED),
    (_handwritten(REREAD_DISAGREES), FieldKind.HANDWRITTEN),
    (_printed(SENDER_READINGS_DISAGREE, value="כללית"), FieldKind.CLOSED_LIST),
], ids=["ocr_disagrees", "both_disagree", "second_disagrees", "handwritten", "sender"])
def test_disagreement_is_orange(field, kind):
    scored = _score(field, kind)
    assert scored.needs_review and scored.confidence < WEIGHTS.review_threshold


@pytest.mark.parametrize("field", [Field[str](), Field[str](raw_value="אבג", reasons=[normalize.ID_NOT_DIGITS])],
                         ids=["not_read", "not_normalized"])
def test_empty_field_is_orange(field):
    scored = _score(field)
    assert scored.value is None and scored.needs_review and scored.confidence == 0.0
    assert NOT_FOUND in scored.reasons


def test_wrong_id_check_digit_is_a_slight_drop_only():
    good = _score(_printed(OCR_AGREES))
    bad = _score(_printed(OCR_AGREES, normalize.ID_CHECKSUM_FAILED, value=BAD_CHECK_DIGIT_ID))
    assert 0 < good.confidence - bad.confidence <= WEIGHTS.soft_fail  # less after clipping at 1
    assert not bad.needs_review
    assert bad.confidence > _score(_printed(OCR_AGREES, normalize.ID_LENGTH_INVALID)).confidence


# --- The formula ---


@pytest.mark.parametrize("signals, expected", [
    (FieldSignals(FieldKind.PRINTED), 0.8),
    (FieldSignals(FieldKind.HANDWRITTEN), 0.55),
    (FieldSignals(FieldKind.CLOSED_LIST), 0.75),
    (FieldSignals(FieldKind.PRINTED, ocr_agrees=True, second_agrees=True), 1.0),  # clipped
    (FieldSignals(FieldKind.HANDWRITTEN, second_agrees=True), 0.7),
    (FieldSignals(FieldKind.CLOSED_LIST, second_agrees=True), 0.9),
    (FieldSignals(FieldKind.PRINTED, ocr_agrees=True, illegible=True), 0.85),
    (FieldSignals(FieldKind.PRINTED, ocr_agrees=True, hard_fails=1), 0.75),
    (FieldSignals(FieldKind.PRINTED, soft_fails=2), 0.6),
    (FieldSignals(FieldKind.PRINTED, ocr_disagrees=True, second_agrees=True), 0.65),
    (FieldSignals(FieldKind.HANDWRITTEN, second_disagrees=True, hard_fails=1, illegible=True), 0.0),  # clipped
])
def test_weighted_formula(signals, expected):
    assert SCORER.score(signals) == pytest.approx(expected)


def test_weights_and_threshold_come_from_the_settings():
    lenient = ConfidenceSettings(**{**vars(WEIGHTS), "base_printed": 0.95, "review_threshold": 0.5})
    scored = score_field(_printed(), FieldKind.PRINTED, WeightedScorer(lenient), lenient.review_threshold)
    assert scored.confidence == 0.95 and not scored.needs_review


def test_illegible_is_always_reviewed_whatever_the_weights():
    no_penalty = ConfidenceSettings(**{**vars(WEIGHTS), "illegible": 0.0, "review_threshold": 0.1})
    scored = score_field(_printed(OCR_AGREES, ILLEGIBLE), FieldKind.PRINTED, WeightedScorer(no_penalty),
                         no_penalty.review_threshold)
    assert scored.confidence == 1.0 and scored.needs_review


def test_a_value_read_once_is_orange_and_says_why():
    scored = _score(_printed(OCR_UNAVAILABLE, REREAD_UNAVAILABLE))
    assert scored.confidence == 0.8 and scored.needs_review
    assert SINGLE_READING in scored.reasons
    assert REASON_TEXTS[SINGLE_READING] in scored.explanations


def test_compared_value_is_not_called_a_single_reading():
    assert SINGLE_READING not in _score(_printed(OCR_DISAGREES)).reasons


@pytest.mark.parametrize("reasons, green", [
    ([SENDER_READINGS_AGREE], True),
    ([SENDER_FUZZY_MATCH, SENDER_READINGS_AGREE], False),
    ([], False),
])
def test_sender_needs_both_readings_to_agree(reasons, green):
    scored = _score(_printed(*reasons, value="כללית"), FieldKind.CLOSED_LIST)
    assert scored.needs_review is not green


def test_handwritten_number_stays_orange_even_when_the_second_look_agrees():
    scored = _score(_handwritten(REREAD_AGREES), FieldKind.HANDWRITTEN)
    assert scored.confidence == pytest.approx(0.7) and scored.needs_review


def test_every_rule_outcome_has_a_severity():
    form_level = {NOT_FOUND, CENTRAL_DUPLICATE}  # imported into normalize, not field rule outcomes
    rule_codes = {value for name, value in vars(normalize).items()
                  if name.isupper() and not name.startswith("_") and isinstance(value, str)
                  and value not in form_level}
    # Soft signals from other stages: the sender match (stage 4) and uncertain name letters (stage 5d).
    assert rule_codes == HARD_FAIL_REASONS | SOFT_FAIL_REASONS - {
        SENDER_FUZZY_MATCH, NAME_UNCERTAIN_LETTERS, NAME_FILLED_FROM_SECOND_LOOK}
    assert not HARD_FAIL_REASONS & SOFT_FAIL_REASONS
    assert normalize.ID_CHECKSUM_FAILED in SOFT_FAIL_REASONS


@pytest.mark.parametrize("name, source, kind", [
    ("sender", FieldSource.PRINTED, FieldKind.CLOSED_LIST),
    ("sender", FieldSource.HANDWRITTEN, FieldKind.CLOSED_LIST),
    ("id_number", FieldSource.HANDWRITTEN, FieldKind.HANDWRITTEN),
    ("central_numbers", FieldSource.STICKER, FieldKind.PRINTED),
    ("received_date", FieldSource.STAMP, FieldKind.PRINTED),
])
def test_field_kind(name, source, kind):
    assert field_kind(name, Field[str](value="1", source=source)) is kind


# --- Empty and optional fields ---


def test_empty_optional_field_is_not_flagged():
    scored = _score(Field[str](), optional=True)
    assert not scored.needs_review and scored.confidence == 0.0
    assert {NOT_FOUND, OPTIONAL_MISSING} <= set(scored.reasons)


def test_optional_field_with_a_value_is_scored_as_usual():
    scored = _score(_printed(OCR_DISAGREES), optional=True)
    assert scored.needs_review and OPTIONAL_MISSING not in scored.reasons


def test_optional_fields_come_from_the_yaml():
    assert optional_fields(FORM_TYPE) == []
    assert optional_fields(None) == []
    assert optional_fields(_with_optional("commitment_numbers", "first_name")) == ["first_name",
                                                                                   "commitment_numbers"]


def test_empty_optional_list_does_not_put_the_form_in_review():
    commitment = Commitment(
        first_name=_printed(REREAD_AGREES, value="ישראל"),
        last_name=_printed(REREAD_AGREES, value="ישראלי"),
        id_number=_printed(OCR_AGREES),
        central_numbers=[_printed(OCR_AGREES, value="123456")],
        received_date=Field[date](value=date(2025, 1, 1), raw_value="01.01.2025", source=FieldSource.STAMP,
                                  reasons=[OCR_AGREES]),
        sender=_printed(SENDER_READINGS_AGREE, value="כללית"),
    )
    assert score_fields(commitment, _with_optional("commitment_numbers"), WEIGHTS).needs_review is False
    scored = score_fields(commitment, FORM_TYPE, WEIGHTS)
    assert scored.needs_review and scored.field_needs_review("commitment_numbers")


def test_unreadable_list_item_is_flagged_even_if_the_field_is_optional():
    commitment = Commitment(commitment_numbers=[Field[str](raw_value=None, reasons=[ILLEGIBLE])])
    scored = score_fields(commitment, _with_optional("commitment_numbers"), WEIGHTS)
    assert scored.commitment_numbers[0].needs_review and scored.field_needs_review("commitment_numbers")


# --- The whole Commitment ---


def test_score_fields_scores_every_field_and_is_pure():
    commitment = Commitment(
        id_number=_printed(OCR_AGREES),
        central_numbers=[_printed(OCR_AGREES, value="111111"), _handwritten(REREAD_DISAGREES)],
        sender=_printed(SENDER_READINGS_AGREE, value="כללית"),
        commitment_numbers=[_printed(OCR_DISAGREES, value="0001234567")],
    )
    before = commitment.model_dump()
    scored = score_fields(commitment, FORM_TYPE, WEIGHTS)
    assert commitment.model_dump() == before
    assert not scored.id_number.needs_review and not scored.sender.needs_review
    assert [f.needs_review for f in scored.central_numbers] == [False, True]
    assert scored.commitment_numbers[0].needs_review
    assert scored.first_name.needs_review and NOT_FOUND in scored.first_name.reasons
    assert scored.central_numbers[1].explanations[-1] == REASON_TEXTS[REREAD_DISAGREES]
    assert all(f.explanations for f in (scored.first_name, scored.received_date, scored.id_number))


def test_scoring_twice_gives_the_same_result():
    commitment = Commitment(id_number=_printed(OCR_UNAVAILABLE))
    once = score_fields(commitment, FORM_TYPE, WEIGHTS)
    assert score_fields(once, FORM_TYPE, WEIGHTS) == once


def test_raw_value_and_signals_are_kept():
    field = _printed(OCR_AGREES, value="0001")
    scored = _score(field)
    assert (scored.value, scored.raw_value, scored.signals) == (field.value, field.raw_value, field.signals)


# --- Extension point for a learned model ---


class _FixedModel:
    """Stands in for a fitted scikit-learn classifier."""

    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.rows = []

    def predict_proba(self, rows):
        self.rows.extend(rows)
        return [[1 - self.probability, self.probability] for _ in rows]


def test_a_fitted_model_can_replace_the_formula():
    model = _FixedModel(0.97)
    scored = score_fields(Commitment(id_number=_printed(OCR_DISAGREES)), FORM_TYPE, WEIGHTS,
                          scorer=ProbabilityModelScorer(model))
    assert scored.id_number.confidence == pytest.approx(0.97) and not scored.id_number.needs_review
    assert len(model.rows) == 1 and len(model.rows[0]) == len(FieldSignals.feature_names())


def test_features_are_numeric_and_named():
    signals = FieldSignals(FieldKind.HANDWRITTEN, ocr_disagrees=True, soft_fails=2)
    features = dict(zip(FieldSignals.feature_names(), signals.as_features()))
    assert features["kind_handwritten"] == 1.0 and features["kind_printed"] == 0.0
    assert features["ocr_disagrees"] == 1.0 and features["soft_fails"] == 2.0
    assert "kind" not in features


def _name_signals(count: int) -> list[FieldSignal]:
    return [FieldSignal(source=SignalSource.REREAD, value="משה", variant=f"variant_{n}") for n in range(count)]


def _handwritten_name(*reasons: str, readings: int = 0) -> Field[str]:
    return Field[str](value="משה", raw_value="משה", source=FieldSource.HANDWRITTEN, reasons=[HANDWRITTEN, *reasons],
                      signals=_name_signals(readings))


@pytest.mark.parametrize("reasons, readings, expected", [
    ([NAME_READINGS_AGREE], 1, 0.55 + 0.15),              # extract + 1 extra reading agree
    ([NAME_READINGS_AGREE], 2, 0.55 + 0.15 + 0.05),       # extract + 2 extra readings agree
    ([NAME_READINGS_AGREE], 3, 0.55 + 0.15 + 0.05 * 2),   # extract + 3 extra readings agree (the cap)
    ([NAME_READINGS_AGREE, NAME_UNCERTAIN_LETTERS], 2, 0.55 + 0.15 + 0.05 - 0.1),
    ([NAME_READINGS_DISAGREE], 2, 0.55 - 0.3),
    ([], 2, 0.55),                                         # readings that did not all agree add nothing
])
def test_handwritten_name_score_arithmetic_with_the_default_weights(reasons, readings, expected):
    weights = ConfidenceSettings()
    scored = score_field(_handwritten_name(*reasons, readings=readings), FieldKind.HANDWRITTEN,
                         WeightedScorer(weights), weights.review_threshold)
    assert scored.confidence == pytest.approx(expected)
    assert scored.needs_review  # with the default weights a handwritten name never reaches 0.9


def test_handwritten_name_passes_only_with_a_higher_additional_weight():
    weights = ConfidenceSettings(additional_reading_agreement=0.1)
    scored = score_field(_handwritten_name(NAME_READINGS_AGREE, readings=3), FieldKind.HANDWRITTEN,
                         WeightedScorer(weights), weights.review_threshold)
    assert scored.confidence == pytest.approx(0.9) and not scored.needs_review


def test_name_agreement_is_a_comparison_not_a_single_reading():
    scored = _score(_handwritten_name(NAME_READINGS_AGREE, readings=1), FieldKind.HANDWRITTEN)
    assert SINGLE_READING not in scored.reasons
    assert FieldSignals.of(_handwritten_name(NAME_READINGS_AGREE, readings=3), FieldKind.HANDWRITTEN) \
        .additional_agreements == 2


def test_other_rereads_do_not_count_as_name_agreements():
    field = _handwritten_name(NAME_READINGS_AGREE, readings=1)
    field.signals.append(FieldSignal(source=SignalSource.REREAD, value="משה"))  # no variant: not a name reading
    assert FieldSignals.of(field, FieldKind.HANDWRITTEN).additional_agreements == 0


def _printed_name(*reasons: str, readings: int = 0) -> Field[str]:
    return Field[str](value="משה", raw_value="משה", source=FieldSource.PRINTED, reasons=list(reasons),
                      signals=_name_signals(readings))


@pytest.mark.parametrize("reasons, readings, expected, reviewed", [
    ([], 0, 0.8, True),                                    # read once, as today
    ([NAME_READINGS_AGREE], 1, 0.95, False),               # extract + 1 extra reading agree
    ([NAME_READINGS_AGREE], 2, 1.0, False),                # the default 2 variants (clipped at 1)
    ([NAME_READINGS_DISAGREE], 2, 0.5, True),
])
def test_printed_name_score_arithmetic_with_the_default_weights(reasons, readings, expected, reviewed):
    weights = ConfidenceSettings()
    scored = score_field(_printed_name(*reasons, readings=readings), FieldKind.PRINTED, WeightedScorer(weights),
                         weights.review_threshold)
    assert scored.confidence == pytest.approx(expected) and scored.needs_review is reviewed


def test_name_filled_by_the_second_look_is_low_and_always_reviewed():
    weights = ConfidenceSettings(additional_reading_agreement=1.0, second_reading_agreement=1.0)
    field = _handwritten_name(NAME_FILLED_FROM_SECOND_LOOK, readings=2)
    scored = score_field(field, FieldKind.HANDWRITTEN, WeightedScorer(weights), weights.review_threshold)
    assert scored.confidence == pytest.approx(0.55 - 0.1) and scored.needs_review
