"""Manual corrections by a reviewer (``PATCH /jobs/{id}/commitments/{cid}/fields/{field}``).

A corrected field takes the reviewer's value as ``value``, ``source=manual``,
confidence 1 and no review flag, with the single reason ``manual_correction``.
``raw_value`` keeps the text as it was read from the form (None for an added
list item or a field that was not found), and the box and earlier readings
(``signals``) are kept too, so the crop and the history stay available. Every
correction is also recorded in ``Job.corrections`` with the previous raw value
and source. Values are patient data: the errors below never quote them.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.pipeline.reason_texts import explain
from app.schemas import (
    COMMITMENT_FIELDS,
    MANUAL_CORRECTION,
    Correction,
    Field,
    FieldSource,
    FormGroup,
    Job,
)

LIST_FIELDS = frozenset({"central_numbers", "commitment_numbers"})
DATE_FIELDS = frozenset({"received_date"})
SENDER_FIELD = "sender"


class CorrectionError(ValueError):
    """The correction cannot be applied; the message names the rule, never the value."""


class GroupNotFoundError(LookupError):
    """No form with that id in the job."""


def field_at(group: FormGroup, name: str, item: int | None) -> Field[Any] | None:
    """The field ``name`` (item ``item`` of a list field) of the form, or None
    if there is no such field or item."""
    if name not in COMMITMENT_FIELDS:
        return None
    value = getattr(group.commitment, name)
    if name in LIST_FIELDS:
        return value[item] if item is not None and 0 <= item < len(value) else None
    return value if item is None else None


def apply_correction(job: Job, group_id: str, name: str, value: str, item: int | None,
                     senders: frozenset[str]) -> FormGroup:
    """Set field ``name`` of form ``group_id`` to the reviewer's ``value`` (in
    place) and record the correction on the job. For a list field ``item`` is
    the index to replace, or the list length to add a value. ``senders`` are
    the canonical names a sender must be one of. Returns the changed form."""
    group = job.group(group_id)
    if group is None:
        raise GroupNotFoundError("form not found")
    text = value.strip()
    if not text:
        raise CorrectionError("value must not be empty")
    if name not in COMMITMENT_FIELDS:
        raise CorrectionError("unknown field")
    previous = _previous_field(group, name, item)
    corrected = _corrected_field(name, text, previous, senders)
    if name in LIST_FIELDS:
        values = getattr(group.commitment, name)
        if item == len(values):
            values.append(corrected)
        else:
            values[item] = corrected
    else:
        setattr(group.commitment, name, corrected)
    job.corrections.append(Correction(
        group_id=group.id, field=name, item=item,
        previous_raw_value=previous.raw_value if previous else None,
        previous_source=previous.source if previous else FieldSource.NONE,
    ))
    return group


def _previous_field(group: FormGroup, name: str, item: int | None) -> Field[Any] | None:
    """The field being replaced; None when a list item is added."""
    if name in LIST_FIELDS:
        length = len(getattr(group.commitment, name))
        if item is None or not 0 <= item <= length:
            raise CorrectionError("item must be an index of the list, or its length to add a value")
        return field_at(group, name, item)
    if item is not None:
        raise CorrectionError("item is only allowed for list fields")
    return field_at(group, name, None)


def _corrected_field(name: str, text: str, previous: Field[Any] | None, senders: frozenset[str]) -> Field[Any]:
    if name in DATE_FIELDS:
        try:
            parsed: Any = date.fromisoformat(text)
        except ValueError:
            raise CorrectionError("a date must be YYYY-MM-DD") from None
    elif name == SENDER_FIELD and text not in senders:
        raise CorrectionError("sender must be a canonical name from the closed list")
    else:
        parsed = text
    reasons = [MANUAL_CORRECTION]
    # The exact generic type, so the commitment serializes without type warnings.
    field_type = Field[date] if name in DATE_FIELDS else Field[str]
    return field_type(
        value=parsed, raw_value=previous.raw_value if previous else None, confidence=1.0, needs_review=False,
        reasons=reasons, explanations=explain(reasons), source=FieldSource.MANUAL,
        bbox=previous.bbox if previous else None,
        signals=previous.signals if previous else [],
    )
