"""Pydantic data model for commitments-extractor.

Design rules encoded here (see README):
  * A field that was not found keeps ``value=None`` and is *always* flagged for
    review — it is never guessed.
  * ``confidence`` is computed by the pipeline from several signals; the model
    only stores the result together with the ``reasons`` that produced it.
  * The models hold patient data, so never log them; log ids/counts only.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Generic, Literal, Optional, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field as PydField, model_validator

T = TypeVar("T")


class FieldSource(str, Enum):
    """Where a field value came from."""

    PRINTED = "printed"            # printed / typed text on the form (OCR)
    HANDWRITTEN = "handwritten"    # handwriting (OCR/HTR or provider)
    BARCODE = "barcode"            # decoded from a barcode
    STAMP = "stamp"                # rubber stamp, e.g. "התקבל" ("received")
    STICKER = "sticker"            # printed label, e.g. "מרכזי" ("central") or hospital admission sticker
    MANUAL = "manual"              # entered / corrected by a reviewer
    NONE = "none"                  # not found


class BBox(BaseModel):
    """Location of a value on a page, in page pixels at the rendering DPI."""

    model_config = ConfigDict(frozen=True)

    page: int = PydField(ge=0, description="0-based page index inside the source file")
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> "BBox":
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox corners must satisfy x0<=x1 and y0<=y1")
        return self


class Field(BaseModel, Generic[T]):
    """One extracted value plus everything needed to trust or review it.

    ``value`` is the normalized value (e.g. digits only, a ``date``);
    ``raw_value`` is the text as read. ``reasons`` lists machine-readable
    codes that explain the confidence, e.g. ``"not_found"``,
    ``"id_checksum_failed"`` (a soft signal only), ``"ocr_low_score"``,
    ``"handwritten"``, ``"conflicting_candidates"``.
    """

    value: Optional[T] = None
    raw_value: Optional[str] = None
    confidence: float = PydField(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = True
    reasons: list[str] = PydField(default_factory=list)
    source: FieldSource = FieldSource.NONE
    bbox: Optional[BBox] = None

    @model_validator(mode="after")
    def _missing_is_flagged(self) -> "Field[T]":
        # Principle 1: a missing value is empty and flagged, never guessed.
        if self.value is None:
            self.needs_review = True
            self.confidence = 0.0
            if "not_found" not in self.reasons:
                self.reasons.append("not_found")
        return self

    @classmethod
    def missing(cls, *reasons: str) -> "Field[T]":
        """A field that was not found on the form."""
        return cls(reasons=["not_found", *reasons])


class Commitment(BaseModel):
    """The extracted content of one commitment form (one patient, one sender).

    ``central_numbers`` and ``commitment_numbers`` are lists because a single
    form can carry several "מרכזי" ("central") stickers and several commitment /
    "דגימה" ("sample") numbers; all of them are kept.
    """

    form_type_id: Optional[str] = PydField(
        default=None, description="id of the matching backend/form_types/*.yaml, None if unknown"
    )
    first_name: Field[str] = PydField(default_factory=Field[str])
    last_name: Field[str] = PydField(default_factory=Field[str])
    id_number: Field[str] = PydField(default_factory=Field[str])
    central_numbers: list[Field[str]] = PydField(default_factory=list)
    received_date: Field[date] = PydField(default_factory=Field[date])
    sender: Field[str] = PydField(
        default_factory=Field[str],
        description="canonical name from backend/data/senders.json (closed list)",
    )
    commitment_numbers: list[Field[str]] = PydField(default_factory=list)

    @property
    def needs_review(self) -> bool:
        """True if any field (or a missing list field) requires a human look."""
        scalars = [self.first_name, self.last_name, self.id_number, self.received_date, self.sender]
        lists = [self.central_numbers, self.commitment_numbers]
        return (
            any(f.needs_review for f in scalars)
            or any(not lst for lst in lists)
            or any(f.needs_review for lst in lists for f in lst)
        )


class FormGroup(BaseModel):
    """A run of consecutive pages in one uploaded file that form one commitment.

    Most forms are one page; some (e.g. Maccabi ONLINE "1/2") span two.
    """

    id: str = PydField(default_factory=lambda: uuid4().hex)
    source_file: str = PydField(description="stored file name (not the user's original path)")
    pages: list[int] = PydField(min_length=1, description="0-based page indices")
    rotation: int = PydField(default=0, description="clockwise degrees applied before reading")
    commitment: Commitment = PydField(default_factory=Commitment)


class JobStatus(str, Enum):
    """Lifecycle of a processing job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    """One upload-to-Excel run over one or more files."""

    id: str = PydField(default_factory=lambda: uuid4().hex)
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = PydField(default_factory=lambda: datetime.now(timezone.utc))
    files: list[str] = PydField(default_factory=list)
    groups: list[FormGroup] = PydField(default_factory=list)
    error: Optional[str] = PydField(default=None, description="must not contain field values")


# --- Reference data: backend/form_types/*.yaml and backend/data/senders.json ---


class FieldSpec(BaseModel):
    """How one Commitment field appears on a given form type."""

    model_config = ConfigDict(extra="forbid")

    labels: list[str]  # Hebrew text printed on the form, matched as-is
    hint: str
    multiple: bool
    handwritten_possible: bool


class FormTypeDetect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[str]
    logo_hint: str


class FormTypePages(BaseModel):
    model_config = ConfigDict(extra="forbid")

    typical: int = PydField(ge=1)


class FormType(BaseModel):
    """One observed form layout (a ``backend/form_types/<id>.yaml`` file)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    certainty: Literal["certain", "uncertain"]
    seen_in: str
    detect: FormTypeDetect
    fields: dict[str, FieldSpec]
    pages: FormTypePages

    @model_validator(mode="after")
    def _covers_commitment_fields(self) -> "FormType":
        expected = set(Commitment.model_fields) - {"form_type_id"}
        if set(self.fields) != expected:
            raise ValueError(f"fields must be exactly {sorted(expected)}")
        return self


class Sender(BaseModel):
    """One entry of the closed sender list."""

    canonical: str
    type: Literal["hmo", "hospital", "lab", "government", "other"]
    aliases: list[str] = PydField(default_factory=list)
    abbreviations: list[str] = PydField(default_factory=list)
    seen_in_samples: bool = False
