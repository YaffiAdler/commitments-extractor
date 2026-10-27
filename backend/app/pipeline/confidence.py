"""Stage 7: combine the signals of every field into a confidence score and
set ``needs_review``.

The score is derived from independent signals, never from the model's own
claim. Per field (``FieldSignals.of``), read from the reason codes the
earlier stages left in ``Field.reasons``:

* the kind of field: printed (also sticker / stamp), handwritten, or a
  closed list (the sender);
* agreement or disagreement of OCR (``ocr_agrees`` / ``ocr_disagrees``);
* agreement or disagreement of a second reading (``reread_*`` for numbers,
  ``sender_readings_*`` for the sender, ``name_readings_*`` for names), and
  for names each further agreeing reading (``additional_agreements``);
* failed validation rules, hard or soft (``HARD_FAIL_REASONS`` /
  ``SOFT_FAIL_REASONS``; the check digit of an ID is soft only);
* ``illegible`` (the model saw only part of the value).

The name list hint (``name_in_list`` / ``name_list_suggestion``) is
deliberately not a signal here: a name that is in no list must not score
lower.

``WeightedScorer`` adds them up with the weights in
``config.ConfidenceSettings`` and clips to [0, 1]. A ``Scorer`` is the
extension point: once there is ground truth, a fitted classifier (e.g.
scikit-learn ``LogisticRegression`` on ``FieldSignals.as_features()``) can be
used through ``ProbabilityModelScorer`` without changing this stage.

``needs_review`` is set when the score is below the review threshold, or
when the value is illegible (a partial value is never accepted, whatever the
weights), or when the name second look filled it in
(``name_filled_from_second_look``, also a soft fail). An empty field is always flagged with confidence 0, unless the form
type's YAML marks it ``optional`` (``optional_missing``). Every field also
gets ``explanations``: its reasons as Hebrew text for the reviewer.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import astuple, dataclass, fields
from enum import Enum
from typing import Protocol

from app.config import ConfidenceSettings
from app.pipeline import classify, extract, normalize, ocr_check, reread, reread_name
from app.pipeline.reason_texts import SINGLE_READING, explain
from app.schemas import COMMITMENT_FIELDS, OPTIONAL_MISSING, Commitment, Field, FieldSource, FormType

# Scores are rounded so that sums of weights compare exactly with the threshold.
SCORE_DECIMALS = 6
# Index of the "value is correct" class in ``predict_proba`` (classes sorted: 0, 1).
CORRECT_CLASS_INDEX = 1

CLOSED_LIST_FIELDS = frozenset({"sender"})

# Rule outcomes of stage 6 (and the sender match of stage 4) by severity.
HARD_FAIL_REASONS = frozenset({
    normalize.ID_LENGTH_INVALID, normalize.ID_NOT_DIGITS, normalize.CENTRAL_NOT_DIGITS, normalize.DATE_INVALID,
    normalize.DATE_DAY_MONTH_SWAPPED, normalize.DATE_YEAR_MISSING, normalize.UNPARSED_DATE,
})
SOFT_FAIL_REASONS = frozenset({
    normalize.ID_CHECKSUM_FAILED, normalize.ID_PADDED, normalize.DATE_TWO_DIGIT_YEAR, normalize.DATE_IN_FUTURE,
    normalize.DATE_BEFORE_WINDOW, normalize.COMMITMENT_PATTERN_MISMATCH, classify.SENDER_FUZZY_MATCH,
    reread_name.NAME_UNCERTAIN_LETTERS, reread_name.NAME_FILLED_FROM_SECOND_LOOK,
})
SECOND_READING_AGREES = frozenset({reread.REREAD_AGREES, classify.SENDER_READINGS_AGREE,
                                   reread_name.NAME_READINGS_AGREE})
SECOND_READING_DISAGREES = frozenset({reread.REREAD_DISAGREES, classify.SENDER_READINGS_DISAGREE,
                                      reread_name.NAME_READINGS_DISAGREE})
# A field with any of these must be verified against the form whatever its score: an independent
# reading disagreed, a hard rule failed, the value is partial or came only from the name second look.
MUST_VERIFY_REASONS = (HARD_FAIL_REASONS | SECOND_READING_DISAGREES
                       | {ocr_check.OCR_DISAGREES, extract.ILLEGIBLE, reread_name.NAME_FILLED_FROM_SECOND_LOOK})


class FieldKind(str, Enum):
    PRINTED = "printed"          # printed text, a sticker or a stamp
    HANDWRITTEN = "handwritten"
    CLOSED_LIST = "closed_list"  # matched to a closed list (the sender)


@dataclass(frozen=True)
class FieldSignals:
    """The independent signals about one field with a value."""

    kind: FieldKind
    ocr_agrees: bool = False
    ocr_disagrees: bool = False
    second_agrees: bool = False
    second_disagrees: bool = False
    hard_fails: int = 0
    soft_fails: int = 0
    illegible: bool = False
    # Agreeing readings beyond the first, when every extra reading of a name agreed.
    additional_agreements: int = 0
    # The extract call read nothing; the name second look supplied the value.
    filled_by_second_look: bool = False

    @classmethod
    def of(cls, field_: Field, kind: FieldKind) -> FieldSignals:
        reasons = set(field_.reasons)
        return cls(
            kind=kind,
            ocr_agrees=ocr_check.OCR_AGREES in reasons,
            ocr_disagrees=ocr_check.OCR_DISAGREES in reasons,
            second_agrees=bool(reasons & SECOND_READING_AGREES),
            second_disagrees=bool(reasons & SECOND_READING_DISAGREES),
            hard_fails=len(reasons & HARD_FAIL_REASONS),
            soft_fails=len(reasons & SOFT_FAIL_REASONS),
            illegible=extract.ILLEGIBLE in reasons,
            additional_agreements=_additional_agreements(field_, reasons),
            filled_by_second_look=reread_name.NAME_FILLED_FROM_SECOND_LOOK in reasons,
        )

    @property
    def compared(self) -> bool:
        """True if any independent reading was compared with the value."""
        return self.ocr_agrees or self.ocr_disagrees or self.second_agrees or self.second_disagrees

    def as_features(self) -> list[float]:
        """A numeric row in ``feature_names()`` order (the kind one-hot), for a fitted model."""
        kind = [float(self.kind is option) for option in FieldKind]
        return kind + [float(value) for value in astuple(self)[1:]]

    @staticmethod
    def feature_names() -> list[str]:
        return [f"kind_{option.value}" for option in FieldKind] + [f.name for f in fields(FieldSignals)[1:]]


def _additional_agreements(field_: Field, reasons: set[str]) -> int:
    """Name second look: every one of its readings agreed, so all but the first count extra."""
    if reread_name.NAME_READINGS_AGREE not in reasons:
        return 0
    return max(0, sum(signal.variant is not None for signal in field_.signals) - 1)


class Scorer(Protocol):
    def score(self, signals: FieldSignals) -> float:
        """Confidence in [0, 1] that the value is right."""
        ...


class WeightedScorer:
    """The hand-set starting formula: a base by field kind, plus/minus a
    weight per signal, clipped to [0, 1]."""

    def __init__(self, weights: ConfidenceSettings) -> None:
        self._weights = weights
        self._base = {
            FieldKind.PRINTED: weights.base_printed,
            FieldKind.HANDWRITTEN: weights.base_handwritten,
            FieldKind.CLOSED_LIST: weights.base_closed_list,
        }

    def score(self, signals: FieldSignals) -> float:
        w = self._weights
        total = (
            self._base[signals.kind]
            + w.ocr_agreement * signals.ocr_agrees
            - w.ocr_disagreement * signals.ocr_disagrees
            + w.second_reading_agreement * signals.second_agrees
            + w.additional_reading_agreement * signals.additional_agreements
            - w.second_reading_disagreement * signals.second_disagrees
            - w.hard_fail * signals.hard_fails
            - w.soft_fail * signals.soft_fails
            - w.illegible * signals.illegible
        )
        return round(min(max(total, 0.0), 1.0), SCORE_DECIMALS)


class ProbabilityModel(Protocol):
    """What a fitted scikit-learn classifier offers (``predict_proba``)."""

    def predict_proba(self, rows: Sequence[Sequence[float]]) -> Sequence[Sequence[float]]: ...


class ProbabilityModelScorer:
    """Scores with a fitted classifier trained on ``FieldSignals.as_features()``
    rows, label 1 = the value was right."""

    def __init__(self, model: ProbabilityModel) -> None:
        self._model = model

    def score(self, signals: FieldSignals) -> float:
        return float(self._model.predict_proba([signals.as_features()])[0][CORRECT_CLASS_INDEX])


def field_kind(name: str, field_: Field) -> FieldKind:
    if name in CLOSED_LIST_FIELDS:
        return FieldKind.CLOSED_LIST
    return FieldKind.HANDWRITTEN if field_.source is FieldSource.HANDWRITTEN else FieldKind.PRINTED


def optional_fields(form_type: FormType | None) -> list[str]:
    """The fields the form type's YAML marks ``optional``, in Commitment order."""
    if form_type is None:
        return []
    return [name for name in COMMITMENT_FIELDS if form_type.fields[name].optional]


def score_field(field_: Field, kind: FieldKind, scorer: Scorer, review_threshold: float,
                optional: bool = False) -> Field:
    """A copy of ``field_`` with ``confidence``, ``needs_review``, the reasons
    and their ``explanations`` (see the module docstring)."""
    reasons = list(field_.reasons)
    if field_.value is None:
        if optional and OPTIONAL_MISSING not in reasons:
            reasons.append(OPTIONAL_MISSING)
        return field_.revalidated(reasons=reasons, explanations=explain(reasons))
    signals = FieldSignals.of(field_, kind)
    if not signals.compared and SINGLE_READING not in reasons:
        reasons.append(SINGLE_READING)
    confidence = scorer.score(signals)
    always_review = signals.illegible or signals.filled_by_second_look
    return field_.revalidated(confidence=confidence, needs_review=confidence < review_threshold or always_review,
                              reasons=reasons, explanations=explain(reasons))


def score_fields(commitment: Commitment, form_type: FormType | None, settings: ConfidenceSettings,
                 scorer: Scorer | None = None) -> Commitment:
    """A copy of ``commitment`` with every field scored; ``scorer`` defaults
    to ``WeightedScorer(settings)``. Pure: the input is not changed."""
    scorer = scorer or WeightedScorer(settings)
    optional = optional_fields(form_type)
    update: dict[str, object] = {"optional_fields": optional}
    for name in COMMITMENT_FIELDS:
        value = getattr(commitment, name)
        if isinstance(value, list):
            update[name] = [score_field(item, field_kind(name, item), scorer, settings.review_threshold)
                            for item in value]
        else:
            update[name] = score_field(value, field_kind(name, value), scorer, settings.review_threshold,
                                       optional=name in optional)
    return commitment.model_copy(update=update, deep=True)
