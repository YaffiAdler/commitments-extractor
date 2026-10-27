"""Name reading metrics: exact match, character error rate and letter confusions.

Pure: takes the names written on each page (``NameSample``) and the job the
pipeline produced for it, never reads files or calls a provider.

Comparison levels (``app/hebrew_text.py``):

* **exact** and **CER** use ``comparable_name`` — trimmed, whitespace
  collapsed, quotes and niqqud removed, final letters unified;
* **exact, finals as written** and the **confusion table** use
  ``strip_marks`` — the same, but final letters kept apart, so a ן read as
  ו or a ם written as מ stays visible.

CER = the sum of Levenshtein distances over the sum of the written names'
lengths (characters, spaces included). A field the pipeline left empty
counts as all-deleted in CER and as "left empty", and is left out of the
confusion table (it holds no letters to compare). Of a page split into
several forms, the first form read without error is scored.

With the name second look on, each field also has its ``agreement``
(``agree`` / ``disagree`` / ``none``) and the candidates of its extra
readings, so the report can show whether agreement goes with a right name and
how often the right name is among the candidates offered to a reviewer.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from rapidfuzz.distance import Levenshtein

from app.evaluation.name_set import NameSample, SampleKind
from app.hebrew_text import comparable_name, strip_marks
from app.pipeline.reread_name import NAME_READINGS_AGREE, NAME_READINGS_DISAGREE
from app.schemas import Field, FieldSource, FormGroup, Job

NAME_FIELDS = ("first_name", "last_name")
REPLACE, INSERT, DELETE = "replace", "insert", "delete"
AGREE, DISAGREE, NOT_COMPARED = "agree", "disagree", "none"


@dataclass(frozen=True)
class FieldOutcome:
    """One name field of one page: what was written and what the pipeline made of it."""

    name: str  # "first_name" or "last_name"
    kind: SampleKind
    truth: str
    read: str | None
    needs_review: bool
    source: FieldSource
    reasons: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()  # every candidate of the name second look, in reading order

    @property
    def exact(self) -> bool:
        return comparable_name(self.truth) == comparable_name(self.read)

    @property
    def exact_strict(self) -> bool:
        return strip_marks(self.truth) == strip_marks(self.read)

    @property
    def left_empty(self) -> bool:
        return not strip_marks(self.read) and bool(strip_marks(self.truth))

    @property
    def distance(self) -> int:
        return Levenshtein.distance(comparable_name(self.truth), comparable_name(self.read))

    @property
    def agreement(self) -> str:
        return agreement_of(self.reasons)

    @property
    def truth_in_candidates(self) -> bool:
        return comparable_name(self.truth) in {comparable_name(c) for c in self.candidates}


def agreement_of(reasons: Iterable[str]) -> str:
    """The name second look's outcome from a field's reason codes."""
    reasons = set(reasons)
    if NAME_READINGS_DISAGREE in reasons:
        return DISAGREE
    return AGREE if NAME_READINGS_AGREE in reasons else NOT_COMPARED


@dataclass(frozen=True)
class ImageOutcome:
    """One page of the set: how many forms the pipeline found and its name fields."""

    sample: NameSample
    forms: int
    error: str | None  # exception type of the job or of the scored form, never a message
    fields: tuple[FieldOutcome, ...]

    @property
    def both_exact(self) -> bool:
        return all(f.exact for f in self.fields)


@dataclass(frozen=True)
class Summary:
    fields: int = 0
    exact: int = 0
    exact_strict: int = 0
    left_empty: int = 0
    edit_distance: int = 0
    reference_chars: int = 0
    flagged: int = 0
    wrong_not_flagged: int = 0  # the dangerous case: a wrong name that would pass without review

    @property
    def cer(self) -> float | None:
        return self.edit_distance / self.reference_chars if self.reference_chars else None


@dataclass
class Confusions:
    """Letter-level edits between the written and the read names (``strip_marks`` level)."""

    substitutions: Counter[tuple[str, str]] = field(default_factory=Counter)  # (written, read)
    deletions: Counter[str] = field(default_factory=Counter)   # written, not read
    insertions: Counter[str] = field(default_factory=Counter)  # read, not written

    def add(self, truth: str, read: str) -> None:
        for op in Levenshtein.editops(truth, read):
            if op.tag == REPLACE:
                self.substitutions[(truth[op.src_pos], read[op.dest_pos])] += 1
            elif op.tag == DELETE:
                self.deletions[truth[op.src_pos]] += 1
            elif op.tag == INSERT:
                self.insertions[read[op.dest_pos]] += 1


def image_outcome(sample: NameSample, job: Job) -> ImageOutcome:
    """The name fields of the first form of ``job`` read without error (see the module docstring)."""
    group = next((g for g in job.groups if not g.error), None)
    error = None if group else (job.error or next((g.error for g in job.groups if g.error), None))
    fields = tuple(_field_outcome(name, sample, group) for name in NAME_FIELDS)
    return ImageOutcome(sample=sample, forms=len(job.groups), error=error, fields=fields)


def _field_outcome(name: str, sample: NameSample, group: FormGroup | None) -> FieldOutcome:
    truth = getattr(sample, name)
    if group is None:
        return FieldOutcome(name=name, kind=sample.kind, truth=truth, read=None, needs_review=True,
                            source=FieldSource.NONE)
    value: Field[str] = getattr(group.commitment, name)
    candidates = tuple(c for signal in value.signals if signal.variant is not None for c in signal.candidates)
    return FieldOutcome(name=name, kind=sample.kind, truth=truth, read=value.value,
                        needs_review=value.needs_review, source=value.source, reasons=tuple(value.reasons),
                        candidates=candidates)


def summarize(outcomes: Iterable[FieldOutcome]) -> Summary:
    outcomes = list(outcomes)
    return Summary(
        fields=len(outcomes),
        exact=sum(o.exact for o in outcomes),
        exact_strict=sum(o.exact_strict for o in outcomes),
        left_empty=sum(o.left_empty for o in outcomes),
        edit_distance=sum(o.distance for o in outcomes),
        reference_chars=sum(len(comparable_name(o.truth)) for o in outcomes),
        flagged=sum(o.needs_review for o in outcomes),
        wrong_not_flagged=sum(not o.exact and not o.needs_review for o in outcomes),
    )


def confusions(outcomes: Iterable[FieldOutcome]) -> Confusions:
    """Letter edits over the fields that were read (empty reads are left out)."""
    table = Confusions()
    for outcome in outcomes:
        read = strip_marks(outcome.read)
        if read:
            table.add(strip_marks(outcome.truth), read)
    return table
