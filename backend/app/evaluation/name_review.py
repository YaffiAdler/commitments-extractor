"""Name review records: what reviewers did to name fields, as letters and counts only.

A record is made for every first or last name a reviewer saved (corrected or
confirmed unchanged: both give ``source=manual``). It compares the reading
the pipeline had before the first correction (``Correction.previous_raw_value``)
with the reviewer's final value, and keeps what the pipeline thought then
(source, reasons, confidence, review flag, the name second look's agreement).

**No name leaves this module**: a record holds only whether the reading was
right, the edit distance, the written length and the single letters that
were substituted, dropped or added (``strip_marks`` level, final letters kept
apart; a space is written as ``␣``). The CSV of a job
(``GET /jobs/{id}/name-review.csv``) can therefore be kept after the job
expires, and many such files can be summed up into one report
(``scripts/name_review_report.py``) — the name-accuracy set from real review
work, without patient data.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from app.evaluation.name_metrics import AGREE, DISAGREE, NAME_FIELDS, NOT_COMPARED, Confusions, agreement_of
from app.evaluation.name_report import SPACE_SYMBOL, confusion_section, rate
from app.hebrew_text import comparable_name, strip_marks
from app.schemas import Correction, FieldSource, Job

COLUMNS = ("field", "previous_source", "agreement", "previous_needs_review", "previous_confidence", "read_empty",
           "correct", "correct_strict", "edit_distance", "written_length", "substitutions", "deletions",
           "insertions", "reasons")
PAIR_SEPARATOR = ">"
ITEM_SEPARATOR = "|"
REASON_SEPARATOR = ";"
TRUE, FALSE = "1", "0"
CSV_ENCODING = "utf-8-sig"


@dataclass(frozen=True)
class NameReviewRecord:
    field: str
    previous_source: str
    agreement: str
    previous_needs_review: bool | None
    previous_confidence: float | None
    read_empty: bool
    correct: bool         # after ``comparable_name`` (final letters unified)
    correct_strict: bool  # final letters as written
    edit_distance: int
    written_length: int
    substitutions: tuple[tuple[str, str], ...] = ()  # (written, read)
    deletions: tuple[str, ...] = ()
    insertions: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def wrong_not_flagged(self) -> bool:
        return not self.correct and self.previous_needs_review is False


def review_records(job: Job) -> list[NameReviewRecord]:
    """One record per name field a reviewer saved, in form order."""
    records = []
    for group in job.groups:
        for name in NAME_FIELDS:
            field = getattr(group.commitment, name)
            first = next((c for c in job.corrections if c.group_id == group.id and c.field == name), None)
            if field.source is FieldSource.MANUAL and field.value is not None and first is not None:
                records.append(_record(name, first, field.value))
    return records


def _record(name: str, correction: Correction, final: str) -> NameReviewRecord:
    written, read = strip_marks(final), strip_marks(correction.previous_raw_value)
    edits = Confusions()
    if read:
        edits.add(written, read)
    return NameReviewRecord(
        field=name,
        previous_source=correction.previous_source.value,
        agreement=agreement_of(correction.previous_reasons),
        previous_needs_review=correction.previous_needs_review,
        previous_confidence=correction.previous_confidence,
        read_empty=not read,
        correct=comparable_name(final) == comparable_name(read),
        correct_strict=written == read,
        edit_distance=Levenshtein.distance(comparable_name(final), comparable_name(read)),
        written_length=len(comparable_name(final)),
        substitutions=tuple(pair for pair, n in edits.substitutions.items() for _ in range(n)),
        deletions=tuple(edits.deletions.elements()),
        insertions=tuple(edits.insertions.elements()),
        reasons=tuple(correction.previous_reasons),
    )


def _symbol(char: str) -> str:
    return SPACE_SYMBOL if char.isspace() else char


def _unsymbol(char: str) -> str:
    return " " if char == SPACE_SYMBOL else char


def _flag(value: bool | None) -> str:
    return "" if value is None else (TRUE if value else FALSE)


def _unflag(text: str) -> bool | None:
    return None if text == "" else text == TRUE


def to_csv(records: Iterable[NameReviewRecord]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for r in records:
        writer.writerow((
            r.field, r.previous_source, r.agreement, _flag(r.previous_needs_review),
            "" if r.previous_confidence is None else f"{r.previous_confidence:.6f}",
            _flag(r.read_empty), _flag(r.correct), _flag(r.correct_strict), r.edit_distance, r.written_length,
            ITEM_SEPARATOR.join(f"{_symbol(w)}{PAIR_SEPARATOR}{_symbol(x)}" for w, x in r.substitutions),
            ITEM_SEPARATOR.join(_symbol(c) for c in r.deletions),
            ITEM_SEPARATOR.join(_symbol(c) for c in r.insertions),
            REASON_SEPARATOR.join(r.reasons),
        ))
    return out.getvalue()


def read_csv(path: Path) -> list[NameReviewRecord]:
    with path.open(encoding=CSV_ENCODING, newline="") as f:
        return [_parse(row) for row in csv.DictReader(f)]


def _items(text: str) -> tuple[str, ...]:
    return tuple(_unsymbol(c) for c in text.split(ITEM_SEPARATOR)) if text else ()


def _parse(row: dict[str, str]) -> NameReviewRecord:
    pairs = tuple(tuple(_unsymbol(c) for c in item.split(PAIR_SEPARATOR))
                  for item in row["substitutions"].split(ITEM_SEPARATOR)) if row["substitutions"] else ()
    return NameReviewRecord(
        field=row["field"], previous_source=row["previous_source"], agreement=row["agreement"],
        previous_needs_review=_unflag(row["previous_needs_review"]),
        previous_confidence=float(row["previous_confidence"]) if row["previous_confidence"] else None,
        read_empty=row["read_empty"] == TRUE, correct=row["correct"] == TRUE,
        correct_strict=row["correct_strict"] == TRUE, edit_distance=int(row["edit_distance"]),
        written_length=int(row["written_length"]), substitutions=pairs,  # type: ignore[arg-type]
        deletions=_items(row["deletions"]), insertions=_items(row["insertions"]),
        reasons=tuple(row["reasons"].split(REASON_SEPARATOR)) if row["reasons"] else (),
    )


def render_report(records: list[NameReviewRecord], files: int) -> str:
    """Counts and single letters over the records of ``files`` exported jobs."""
    lines = [
        "# Name review report", "",
        f"Exported jobs: {files}. Reviewed name fields: {len(records)}.", "",
        "From reviewers' work: a field counts as right when the reviewer saved the name as it was read. "
        "Only names a reviewer saved are counted, so the rates describe reviewed fields, not every field.", "",
        "| Source as read | Second look | Fields | Right | Right, finals as written | Read empty | CER "
        "| Wrong and not flagged |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for source in sorted({r.previous_source for r in records}):
        for agreement in (AGREE, DISAGREE, NOT_COMPARED):
            group = [r for r in records if r.previous_source == source and r.agreement == agreement]
            if group:
                lines.append(_row(source, agreement, group))
    if records:
        lines.append(_row("all", "all", records))
    table = Confusions(substitutions=Counter(p for r in records for p in r.substitutions),
                       deletions=Counter(c for r in records for c in r.deletions),
                       insertions=Counter(c for r in records for c in r.insertions))
    lines += ["", *confusion_section("Letter confusions — reviewed names", table)]
    return "\n".join(lines) + "\n"


def _row(source: str, agreement: str, records: list[NameReviewRecord]) -> str:
    n, chars = len(records), sum(r.written_length for r in records)
    cer = f"{sum(r.edit_distance for r in records) / chars:.1%}" if chars else "—"
    return (f"| {source} | {agreement} | {n} | {rate(sum(r.correct for r in records), n)} "
            f"| {rate(sum(r.correct_strict for r in records), n)} | {sum(r.read_empty for r in records)} | {cer} "
            f"| {sum(r.wrong_not_flagged for r in records)} |")
