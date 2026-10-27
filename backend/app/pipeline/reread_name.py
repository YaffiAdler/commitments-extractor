"""Stage 5d (opt-in, ``CE_NAME_REREAD``): extra readings of names.

Which names: every name with a value (handwritten, printed or on a sticker),
or an empty one the model saw something of (it has a box, or is
``illegible`` or ``bbox_implausible``). Names that are not on the form are
left alone.

The crop: the name's box grown by a padding relative to its height (wider
than the numbers' ``CROP_PADDING_PX``, so the whole name and its row are in),
cut from the full-resolution colour ``original`` (never binarized) and
enlarged like the number second look. Without a usable box (none, or
``bbox_implausible``) the other name's box grown to its neighbourhood is used
instead (``name_reread_fallback_box``; the prompt names the field to read);
with neither, ``name_reread_unavailable``.

Each ``NameVariant`` of ``CE_NAME_REREAD_VARIANTS`` is one more reading with
a different input or prompt (padding, ink separation, wording), because the
model is not sampled. The extract-call reading counts as the first. All
readings of a form run concurrently through ``ProviderCalls``. Every answer is
a ``reread`` signal (variant, ranked candidates, uncertain letters), never
written over a value that was read. Something is always shown: when the
extract call read no name but an extra reading did, the first such candidate
(in variant order) becomes the value, marked ``name_filled_from_second_look``
— always reviewed, low confidence. Compared with ``comparable_name``:

* ``name_readings_agree`` — every extra reading answered and its first
  candidate is the value (it adds confidence; without it the value is still
  shown, at its base score);
* ``name_readings_disagree`` — some reading's first candidate is not the
  value (for a filled value: not the candidate it was filled from);
* ``name_reread_null`` — some reading found no name;
* ``name_filled_from_second_look`` — see above;
* ``name_uncertain_letters`` — the model marked letters as uncertain in a
  first candidate that is the value;
* ``name_reread_failed`` — a call failed after retries.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import numpy as np

from app.config import NameRereadSettings, NameVariant
from app.hebrew_text import comparable_name
from app.imaging.crop import enlarge
from app.imaging.ink import separate_ink
from app.pipeline.calls import ProviderCalls
from app.pipeline.crops import CROP_PADDING_PX, crop_field
from app.pipeline.extract import BBOX_IMPLAUSIBLE, ILLEGIBLE
from app.pipeline.ingest import PageImage
from app.pipeline.reread import ENLARGE_FACTOR, MAX_CROP_EDGE_PX
from app.providers.base import ExtractionProvider, ProviderError
from app.schemas import (
    NOT_FOUND,
    BBox,
    Commitment,
    Field,
    FieldSignal,
    FieldSource,
    NameCandidate,
    NameReading,
    NameWording,
    SignalSource,
)

logger = logging.getLogger(__name__)

NAME_FIELDS = ("first_name", "last_name")
OTHER_NAME = {"first_name": "last_name", "last_name": "first_name"}

NAME_READINGS_AGREE = "name_readings_agree"
NAME_READINGS_DISAGREE = "name_readings_disagree"
NAME_REREAD_NULL = "name_reread_null"
NAME_FILLED_FROM_SECOND_LOOK = "name_filled_from_second_look"
NAME_UNCERTAIN_LETTERS = "name_uncertain_letters"
NAME_REREAD_FALLBACK_BOX = "name_reread_fallback_box"
NAME_REREAD_UNAVAILABLE = "name_reread_unavailable"
NAME_REREAD_FAILED = "name_reread_failed"


@dataclass(frozen=True)
class Padding:
    """Padding around a name's box, in multiples of the box height (handwriting scales with it)."""

    x_heights: float
    y_heights: float


@dataclass(frozen=True)
class VariantSpec:
    padding: Padding
    ink: bool
    wording: NameWording


WIDE_PADDING = Padding(x_heights=1.5, y_heights=0.6)
NARROW_PADDING = Padding(x_heights=0.4, y_heights=0.25)
VARIANTS = {
    NameVariant.WIDE: VariantSpec(WIDE_PADDING, ink=False, wording=NameWording.STANDARD),
    NameVariant.NARROW: VariantSpec(NARROW_PADDING, ink=False, wording=NameWording.STANDARD),
    NameVariant.INK: VariantSpec(WIDE_PADDING, ink=True, wording=NameWording.STANDARD),
    NameVariant.LETTERS: VariantSpec(WIDE_PADDING, ink=False, wording=NameWording.LETTERS),
}
# The fallback region around the other name: its own width to each side, two heights up and down
# (first and last name sit side by side or one above the other).
FALLBACK_X_WIDTHS = 1.0
FALLBACK_Y_HEIGHTS = 2.0


@dataclass(frozen=True)
class NameRegion:
    """Where to cut a name from: a box and the height of one name in it."""

    box: BBox
    name_height: float
    fallback: bool


def needs_name_look(field: Field[str]) -> bool:
    if field.value is not None:
        return True
    return field.bbox is not None or bool({ILLEGIBLE, BBOX_IMPLAUSIBLE} & set(field.reasons))


async def second_look_names(commitment: Commitment, pages: list[PageImage], provider: ExtractionProvider,
                            calls: ProviderCalls, settings: NameRereadSettings) -> None:
    """Read every name that needs it again in each of ``settings.variants`` (in place; an empty
    name may be filled in, see the module docstring)."""
    looks = [_look_at_name(name, getattr(commitment, name), getattr(commitment, OTHER_NAME[name]), pages,
                           provider, calls, settings.variants)
             for name in NAME_FIELDS if needs_name_look(getattr(commitment, name))]
    await asyncio.gather(*looks)


def name_region(field: Field[str], other: Field[str]) -> NameRegion | None:
    if field.bbox is not None:
        return NameRegion(field.bbox, _height(field.bbox), fallback=False)
    if other.bbox is None:
        return None
    box, width, height = other.bbox, other.bbox.x1 - other.bbox.x0, _height(other.bbox)
    grown = _grow(box, width * FALLBACK_X_WIDTHS, height * FALLBACK_Y_HEIGHTS)
    return NameRegion(grown, height, fallback=True)


def padded_box(box: BBox, name_height: float, padding: Padding) -> BBox:
    """``box`` grown by ``padding`` (in name heights, at least ``CROP_PADDING_PX``)."""
    return _grow(box, max(CROP_PADDING_PX, padding.x_heights * name_height),
                 max(CROP_PADDING_PX, padding.y_heights * name_height))


def wide_name_box(box: BBox) -> BBox:
    """A name's box with the wide padding (the reviewer's crop, and the ``wide`` variant)."""
    return padded_box(box, _height(box), WIDE_PADDING)


def variant_crop(region: NameRegion, spec: VariantSpec, pages: list[PageImage]) -> np.ndarray | None:
    """The enlarged colour crop of ``region`` for one variant; None if it cannot be cut."""
    crop = crop_field(padded_box(region.box, region.name_height, spec.padding), pages, for_ocr=False, padding_px=0)
    if crop is None:
        return None
    if spec.ink:
        crop = separate_ink(crop)
    return enlarge(crop, ENLARGE_FACTOR, MAX_CROP_EDGE_PX)


def outcomes(value: str | None, readings: list[NameReading], failed: bool) -> list[str]:
    """The reason codes of the answered ``readings`` of one name (see the module docstring)."""
    reasons = [NAME_REREAD_FAILED] if failed else []
    tops = [reading.top for reading in readings]
    found = [top for top in tops if top is not None]
    if len(found) < len(tops):
        reasons.append(NAME_REREAD_NULL)
    filled = value is None
    if filled:
        if not found:
            return reasons
        reasons.append(NAME_FILLED_FROM_SECOND_LOOK)
        value = found[0].value  # the other readings are compared with the candidate it is filled from
    same = [top for top in found if comparable_name(top.value) == comparable_name(value)]
    if len(same) < len(found):
        reasons.append(NAME_READINGS_DISAGREE)
    elif same and not filled and not failed and len(found) == len(tops):
        reasons.append(NAME_READINGS_AGREE)
    if any(top.uncertain for top in same):
        reasons.append(NAME_UNCERTAIN_LETTERS)
    return reasons


async def _look_at_name(name: str, field: Field[str], other: Field[str], pages: list[PageImage],
                        provider: ExtractionProvider, calls: ProviderCalls,
                        variants: tuple[NameVariant, ...]) -> None:
    region = name_region(field, other)
    crops = [variant_crop(region, VARIANTS[variant], pages) for variant in variants] if region else []
    if not crops or any(crop is None for crop in crops):
        field.reasons.append(NAME_REREAD_UNAVAILABLE)
        return
    if region.fallback:
        field.reasons.append(NAME_REREAD_FALLBACK_BOX)
    answers = await asyncio.gather(*(_read(name, crop, VARIANTS[variant].wording, provider, calls, region.box.page)
                                     for variant, crop in zip(variants, crops)))
    readings = []
    for variant, reading in zip(variants, answers):
        if reading is not None:
            readings.append(reading)
            field.signals.append(_signal(variant, reading))
    field.reasons.extend(outcomes(field.value, readings, failed=len(readings) < len(answers)))
    if field.value is None:
        filled_from = next((reading.top for reading in readings if reading.top is not None), None)
        if filled_from is not None:
            _fill(field, filled_from)


def _fill(field: Field[str], candidate: NameCandidate) -> None:
    """Show ``candidate`` as the value (in place). How it was written is unknown, so it is
    scored as handwritten (the lower base)."""
    field.value = field.raw_value = candidate.value
    field.source = FieldSource.HANDWRITTEN
    if NOT_FOUND in field.reasons:
        field.reasons.remove(NOT_FOUND)


async def _read(name: str, crop: np.ndarray, wording: NameWording, provider: ExtractionProvider,
                calls: ProviderCalls, page_no: int) -> NameReading | None:
    try:
        return await calls.run(provider.read_name, crop, name, wording)
    except (ProviderError, TimeoutError) as exc:
        logger.warning("name second look on page %d failed: %s", page_no, type(exc).__name__)
        return None


def _signal(variant: NameVariant, reading: NameReading) -> FieldSignal:
    top = reading.top
    return FieldSignal(source=SignalSource.REREAD, value=top.value if top else None, variant=variant.value,
                       candidates=[c.value for c in reading.candidates], uncertain=top.uncertain if top else [])


def _height(box: BBox) -> float:
    return box.y1 - box.y0


def _grow(box: BBox, pad_x: float, pad_y: float) -> BBox:
    """``box`` grown on every side, kept at non-negative coordinates (``crop_box`` clips the far edges)."""
    return BBox(page=box.page, x0=max(0.0, box.x0 - pad_x), y0=max(0.0, box.y0 - pad_y),
                x1=box.x1 + pad_x, y1=box.y1 + pad_y)
