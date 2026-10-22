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


class SignalSource(str, Enum):
    """Who produced one reading of a field."""

    VLM = "vlm"        # the full-form extraction call
    OCR = "ocr"        # Tesseract on the field's crop (printed numbers only)
    REREAD = "reread"  # a second, narrow vision-model call on the enlarged crop


class FieldSignal(BaseModel):
    """One independent reading of a field, kept next to the value and never
    written over it. Holds patient data (``value``) and model text that may
    quote the page (``note``) — never log either."""

    source: SignalSource
    value: Optional[str] = None
    confidence: Optional[float] = PydField(default=None, ge=0.0, le=1.0, description="OCR only")
    legible: Optional[bool] = None
    note: Optional[str] = None


class Field(BaseModel, Generic[T]):
    """One extracted value plus everything needed to trust or review it.

    ``value`` is the normalized value (e.g. digits only, a ``date``);
    ``raw_value`` is the text as read. ``reasons`` lists machine-readable
    codes that explain the confidence, e.g. ``"not_found"``,
    ``"id_checksum_failed"`` (a soft signal only), ``"ocr_low_score"``,
    ``"handwritten"``, ``"conflicting_candidates"``. ``signals`` holds every
    independent reading (model, OCR, second look) for the confidence stage
    and for a reviewer.
    """

    value: Optional[T] = None
    raw_value: Optional[str] = None
    confidence: float = PydField(default=0.0, ge=0.0, le=1.0)
    needs_review: bool = True
    reasons: list[str] = PydField(default_factory=list)
    source: FieldSource = FieldSource.NONE
    bbox: Optional[BBox] = None
    signals: list[FieldSignal] = PydField(default_factory=list)

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
        default=None,
        description="id of the backend/form_types/*.yaml used for extraction ('generic' when the "
                    "layout is unknown); None if the form was not classified",
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


class RelativeBox(BaseModel):
    """A box as a vision model reports it: fractions (0..1) of the width and
    height of one of the images it was sent. Not validated here — the
    pipeline decides whether a box is plausible enough to crop."""

    page: int = PydField(default=0, description="0-based index among the images sent")
    x0: float
    y0: float
    x1: float
    y1: float


ReadingSource = Literal["printed", "handwritten", "sticker", "stamp"]


class FieldReading(BaseModel):
    """One value as the vision model read it, before any checking.

    ``value`` is copied as seen (None if absent or unclear); ``legible`` is
    False for a value that is cut off, smudged or partly hidden (``value`` then
    holds the part that is visible, if any). ``note`` is model text that may
    quote the page — never logged.
    """

    value: Optional[str] = None
    bbox: Optional[RelativeBox] = None
    legible: bool = True
    source: ReadingSource = "printed"
    note: Optional[str] = None


class FormReading(BaseModel):
    """The raw answer of ``ExtractionProvider.extract_form`` for one form:
    one reading per ``Commitment`` field, lists for the fields that may occur
    several times (one item per sticker / number)."""

    first_name: FieldReading = PydField(default_factory=FieldReading)
    last_name: FieldReading = PydField(default_factory=FieldReading)
    id_number: FieldReading = PydField(default_factory=FieldReading)
    central_numbers: list[FieldReading] = PydField(default_factory=list)
    received_date: FieldReading = PydField(default_factory=FieldReading)
    sender: FieldReading = PydField(default_factory=FieldReading)
    commitment_numbers: list[FieldReading] = PydField(default_factory=list)


class NumberReading(BaseModel):
    """The answer of ``ExtractionProvider.read_number`` on one enlarged crop:
    the digits seen, or None."""

    value: Optional[str] = None


SPLIT_UNCERTAIN = "split_uncertain"
FORM_TYPE_UNKNOWN = "form_type_unknown"
EXTRACTION_FAILED = "extraction_failed"


class FormGroup(BaseModel):
    """A run of pages in one uploaded file that form one commitment.

    Most forms are one page; some (e.g. Maccabi ONLINE "1/2") span two. The
    pages are consecutive except where an irrelevant page was taken out.
    ``split_confidence`` scores the boundaries of the group; below the review
    threshold ``split_reasons`` holds ``"split_uncertain"`` (the form may be
    split wrongly), next to codes for how the boundaries were decided.
    ``form_reasons`` holds flags about the form as a whole, e.g.
    ``"form_type_unknown"`` (no known layout matched; read with ``generic.yaml``)
    or ``"extraction_failed"`` (reading the form failed; ``error`` holds the
    exception type only, and the other forms of the job are still read).
    """

    id: str = PydField(default_factory=lambda: uuid4().hex)
    source_file: str = PydField(description="stored file name (not the user's original path)")
    pages: list[int] = PydField(min_length=1, description="0-based page indices")
    rotation: int = PydField(default=0, description="clockwise degrees applied before reading")
    split_confidence: float = PydField(default=1.0, ge=0.0, le=1.0)
    split_reasons: list[str] = PydField(default_factory=list)
    form_reasons: list[str] = PydField(default_factory=list)
    commitment: Commitment = PydField(default_factory=Commitment)
    vlm_calls: int = PydField(default=0, ge=0, description="provider calls made to read this form, retries included")
    error: Optional[str] = PydField(default=None, description="exception type only; must not contain field values")

    @property
    def needs_review(self) -> bool:
        return (self.commitment.needs_review or SPLIT_UNCERTAIN in self.split_reasons
                or FORM_TYPE_UNKNOWN in self.form_reasons or EXTRACTION_FAILED in self.form_reasons)


class IrrelevantPage(BaseModel):
    """A page that belongs to no form. Kept and reported, never dropped silently."""

    source_file: str
    page: int = PydField(ge=0, description="0-based page index")


PageType = Literal["start", "continuation", "irrelevant"]


class PageRelation(BaseModel):
    """A vision model's answer about two adjacent pages A and B.

    ``reason`` is free model text: it may quote the page, so it is never
    logged or stored.
    """

    same_form: bool
    reason: str
    page_b_type: PageType


UNKNOWN_FORM_TYPE = "unknown"


class FormClassification(BaseModel):
    """A vision model's answer about the first page of one form.

    ``form_type`` is a form type id or ``"unknown"``; ``sender_raw`` is the
    sender's name as printed (None if not visible), matched against the closed
    list by the pipeline. ``evidence`` is free model text: it may quote the
    page, so it is never logged or stored.
    """

    form_type: str
    sender_raw: Optional[str] = None
    evidence: str


class Segmentation(BaseModel):
    """The forms (and the irrelevant pages) found in one file."""

    groups: list[FormGroup]
    irrelevant_pages: list[IrrelevantPage] = PydField(default_factory=list)
    vlm_calls: int = PydField(default=0, ge=0, description="page pairs sent to the provider")


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
    irrelevant_pages: list[IrrelevantPage] = PydField(default_factory=list)
    split_vlm_calls: int = PydField(default=0, ge=0)
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
