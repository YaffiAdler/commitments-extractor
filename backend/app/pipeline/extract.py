"""Stage 5: turn a provider's raw ``FormReading`` of one form into a
``Commitment``.

Every value is kept as read (nothing is completed or corrected) and gets:

* ``raw_value`` = ``value`` = the text as read (dates: parsed, see below);
* ``source`` from how the model says it was written;
* ``bbox`` in ``PageImage.original`` pixels, when the model's relative box
  is plausible for a single value (on a page of the form, inside the image
  give or take ``BOX_EDGE_TOLERANCE``, at most ``MAX_BOX_AREA_FRACTION`` of
  the page); otherwise none, with ``bbox_implausible``;
* reasons ``illegible`` (cut off, smudged or covered — the partial value is
  kept) and ``handwritten``;
* one ``vlm`` signal with the model's ``legible`` and ``note``.

A missing value stays empty and flagged (``not_found``). In the list fields
an item without a value is dropped unless it is marked illegible (a sticker
that is there but cannot be read); identical values are kept once.
``received_date`` must be a full date as written (``normalize.parse_date``),
otherwise it stays empty with ``unparsed_date`` and the text in ``raw_value``
(stage 6 re-reads that text and may still parse it, e.g. a 2-digit year).
Confidence is not set here — scoring belongs to the confidence stage.
"""
from __future__ import annotations

from datetime import date

from app.pipeline.ingest import PageImage
from app.pipeline.normalize import UNPARSED_DATE, parse_date
from app.schemas import (
    BBox,
    Commitment,
    Field,
    FieldReading,
    FieldSignal,
    FieldSource,
    FormReading,
    FormType,
    RelativeBox,
    SignalSource,
)

ILLEGIBLE = "illegible"
HANDWRITTEN = "handwritten"
BBOX_IMPLAUSIBLE = "bbox_implausible"

# A box covering more of the page than this is not the box of one value.
MAX_BOX_AREA_FRACTION = 0.15
# Model boxes may overshoot the image edge a little; beyond this they are not trusted.
BOX_EDGE_TOLERANCE = 0.02

_SOURCES = {
    "printed": FieldSource.PRINTED,
    "handwritten": FieldSource.HANDWRITTEN,
    "sticker": FieldSource.STICKER,
    "stamp": FieldSource.STAMP,
}


def to_commitment(reading: FormReading, pages: list[PageImage], form_type: FormType | None) -> Commitment:
    """The ``Commitment`` of one form read with ``form_type`` (see the module docstring)."""
    return Commitment(
        form_type_id=form_type.id if form_type else None,
        first_name=_text_field(reading.first_name, pages),
        last_name=_text_field(reading.last_name, pages),
        id_number=_text_field(reading.id_number, pages),
        central_numbers=_list_fields(reading.central_numbers, pages),
        received_date=_date_field(reading.received_date, pages),
        sender=_text_field(reading.sender, pages),
        commitment_numbers=_list_fields(reading.commitment_numbers, pages),
    )


def _clean(text: str | None) -> str | None:
    text = text.strip() if text else ""
    return text or None


def _common(reading: FieldReading, text: str | None, pages: list[PageImage]) -> dict:
    """What every field gets from its reading, apart from the value."""
    reasons = []
    if not reading.legible:
        reasons.append(ILLEGIBLE)
    if reading.source == "handwritten":
        reasons.append(HANDWRITTEN)
    bbox = pixel_box(reading.bbox, pages)
    if reading.bbox is not None and bbox is None:
        reasons.append(BBOX_IMPLAUSIBLE)
    signal = FieldSignal(source=SignalSource.VLM, value=text, legible=reading.legible, note=_clean(reading.note))
    return {"raw_value": text, "reasons": reasons, "bbox": bbox, "signals": [signal],
            "source": _SOURCES[reading.source] if text else FieldSource.NONE}


def _text_field(reading: FieldReading, pages: list[PageImage]) -> Field[str]:
    text = _clean(reading.value)
    return Field[str](value=text, **_common(reading, text, pages))


def _date_field(reading: FieldReading, pages: list[PageImage]) -> Field[date]:
    text = _clean(reading.value)
    parsed = parse_date(text) if text else None
    common = _common(reading, text, pages)
    if text and parsed is None:
        common["reasons"].append(UNPARSED_DATE)
    return Field[date](value=parsed, **common)


def _list_fields(readings: list[FieldReading], pages: list[PageImage]) -> list[Field[str]]:
    fields, seen = [], set()
    for reading in readings:
        text = _clean(reading.value)
        if (text is None and reading.legible) or (text is not None and text in seen):
            continue
        seen.add(text)
        fields.append(_text_field(reading, pages))
    return fields


def pixel_box(box: RelativeBox | None, pages: list[PageImage]) -> BBox | None:
    """``box`` in pixels of the page's ``original`` image (same geometry as the
    image sent to the model), or None if it is not a plausible single-value box."""
    if box is None or not 0 <= box.page < len(pages):
        return None
    tolerance = (-BOX_EDGE_TOLERANCE, 1 + BOX_EDGE_TOLERANCE)
    if not all(tolerance[0] <= v <= tolerance[1] for v in (box.x0, box.y0, box.x1, box.y1)):
        return None
    x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in (box.x0, box.y0, box.x1, box.y1))
    if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) > MAX_BOX_AREA_FRACTION:
        return None
    page = pages[box.page]
    height, width = page.original.shape[:2]
    return BBox(page=page.page_no, x0=x0 * width, y0=y0 * height, x1=x1 * width, y1=y1 * height)
