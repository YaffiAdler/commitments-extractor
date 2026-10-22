"""Match the job's commitments to the samples the lab system received (pure).

The only key is the central number, compared as an exact string (leading zeros
matter). Nothing is guessed and no field is changed: a central number that was
not read cannot match, and the commitment is listed without a sample. The
received dates are compared for the reviewer only (``date_differs``); they never
decide a match.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel, Field as PydField

from app.lab_system.base import ReceivedSample
from app.schemas import Commitment


class MatchedSample(BaseModel):
    commitment_id: str = PydField(description="FormGroup.id of the commitment")
    central_number: str
    sample: ReceivedSample
    commitment_received_date: Optional[date] = None
    # None when the form's received date was not read.
    date_differs: Optional[bool] = None


class CommitmentWithoutSample(BaseModel):
    commitment_id: str
    # None when no central number was read on the form (nothing to look up).
    central_number: Optional[str] = None


class LabMatch(BaseModel):
    matched: list[MatchedSample] = PydField(default_factory=list)
    samples_without_commitment: list[ReceivedSample] = PydField(default_factory=list)
    commitments_without_sample: list[CommitmentWithoutSample] = PydField(default_factory=list)


def query_range(commitments: Iterable[Commitment], margin_days: int) -> tuple[date | None, date | None]:
    """The dates to ask the lab system for: the earliest to the latest received date read on the
    forms, widened by ``margin_days``; open (None, None) when no date was read."""
    dates = [c.received_date.value for c in commitments if c.received_date.value is not None]
    if not dates:
        return None, None
    margin = timedelta(days=margin_days)
    return min(dates) - margin, max(dates) + margin


def _central_numbers(commitment: Commitment) -> list[str]:
    """The central numbers read on the form, identical repeats once, in form order."""
    return list(dict.fromkeys(f.value for f in commitment.central_numbers if f.value is not None))


def match_samples(commitments: Mapping[str, Commitment], samples: Iterable[ReceivedSample]) -> LabMatch:
    """Group ``commitments`` (keyed by ``FormGroup.id``) and ``samples`` by central number."""
    samples = list(samples)
    by_central: dict[str, list[ReceivedSample]] = {}
    for sample in samples:
        by_central.setdefault(sample.central_number, []).append(sample)
    result = LabMatch()
    used: set[str] = set()
    for commitment_id, commitment in commitments.items():
        centrals = _central_numbers(commitment)
        if not centrals:
            result.commitments_without_sample.append(CommitmentWithoutSample(commitment_id=commitment_id))
        form_date = commitment.received_date.value
        for central in centrals:
            found = by_central.get(central, [])
            if not found:
                result.commitments_without_sample.append(
                    CommitmentWithoutSample(commitment_id=commitment_id, central_number=central))
                continue
            used.add(central)
            for sample in found:
                result.matched.append(MatchedSample(
                    commitment_id=commitment_id, central_number=central, sample=sample,
                    commitment_received_date=form_date,
                    date_differs=None if form_date is None else form_date != sample.received_date,
                ))
    result.samples_without_commitment = [s for s in samples if s.central_number not in used]
    return result
