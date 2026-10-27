"""Stage 5d: extra readings of handwritten names (fake provider, no network)."""
import asyncio

import pytest

from form_fakes import PAGE_HEIGHT, PAGE_WIDTH, blank_page, value_box
from app.config import ExtractionSettings, NameRereadSettings, NameVariant
from app.pipeline.calls import ProviderCalls
from app.pipeline.extract import BBOX_IMPLAUSIBLE, ILLEGIBLE, to_commitment
from app.pipeline.reread import ENLARGE_FACTOR
from app.pipeline.reread_name import (
    NAME_READINGS_AGREE,
    NAME_READINGS_DISAGREE,
    NAME_REREAD_FAILED,
    NAME_REREAD_FALLBACK_BOX,
    NAME_FILLED_FROM_SECOND_LOOK,
    NAME_REREAD_NULL,
    NAME_REREAD_UNAVAILABLE,
    NAME_UNCERTAIN_LETTERS,
    VARIANTS,
    needs_name_look,
    outcomes,
    second_look_names,
)
from app.providers.base import ProviderError
from app.providers.fake import FakeProvider
from app.schemas import (
    Field,
    FieldReading,
    FieldSource,
    FormReading,
    NameCandidate,
    NameReading,
    NameWording,
    RelativeBox,
    SignalSource,
)

CALLS = ExtractionSettings(concurrency=2, call_timeout_s=5, max_attempts=1, backoff_initial_s=0, backoff_max_s=0)
TWO = NameRereadSettings(enabled=True, variants=(NameVariant.WIDE, NameVariant.LETTERS))
FIRST, LAST = "משה", "כהן"


def _name(value: str | None, box: RelativeBox | None = None, source: str = "handwritten", legible: bool = True):
    return FieldReading(value=value, bbox=box, source=source, legible=legible)


def _answer(*values: str, uncertain=()) -> NameReading:
    return NameReading(candidates=[NameCandidate(value=v, uncertain=list(uncertain) if i == 0 else [])
                                   for i, v in enumerate(values)])


class RecordingProvider(FakeProvider):
    def __init__(self, names=(), fail: bool = False):
        super().__init__(names=names)
        self.crops = []
        self._fail = fail

    def read_name(self, crop, field_name, wording):
        self.crops.append(crop)
        if self._fail:
            raise ProviderError("scripted failure")
        return super().read_name(crop, field_name, wording)


class ByFieldProvider(RecordingProvider):
    """Answers every reading of a field with that field's dummy name (call order does not matter)."""

    def read_name(self, crop, field_name, wording):
        super().read_name(crop, field_name, wording)
        return _answer(FIRST if field_name == "first_name" else LAST)


def _look(reading: FormReading, provider: FakeProvider, settings: NameRereadSettings = TWO):
    pages = [blank_page()]
    commitment = to_commitment(reading, pages, None)

    async def main():
        calls = ProviderCalls(asyncio.Semaphore(CALLS.concurrency), CALLS)
        await second_look_names(commitment, pages, provider, calls, settings)
        return calls.count

    return commitment, asyncio.run(main())


def test_every_variant_reads_once_and_agreement_is_recorded_without_overwriting():
    provider = RecordingProvider(names=[_answer(FIRST), _answer(FIRST + " ")])
    commitment, count = _look(FormReading(first_name=_name(FIRST, value_box())), provider)
    field = commitment.first_name
    assert count == 2 and field.value == FIRST
    assert NAME_READINGS_AGREE in field.reasons and NAME_READINGS_DISAGREE not in field.reasons
    rereads = [s for s in field.signals if s.source is SignalSource.REREAD]
    assert sorted(s.variant for s in rereads) == ["letters", "wide"]
    assert sorted(provider.name_requests) == [("first_name", NameWording.LETTERS), ("first_name", NameWording.STANDARD)]


def test_disagreement_keeps_the_value_and_all_candidates():
    provider = RecordingProvider(names=[_answer("רוד", "דוד"), _answer(FIRST)])
    commitment, _ = _look(FormReading(first_name=_name(FIRST, value_box())), provider)
    field = commitment.first_name
    assert field.value == FIRST and NAME_READINGS_DISAGREE in field.reasons
    assert ["רוד", "דוד"] in [s.candidates for s in field.signals]


def test_printed_names_are_read_again_too():
    provider = RecordingProvider(names=[_answer(FIRST), _answer(FIRST)])
    commitment, count = _look(FormReading(first_name=_name(FIRST, value_box(), source="printed")), provider)
    assert count == 2 and NAME_READINGS_AGREE in commitment.first_name.reasons
    assert commitment.first_name.source is FieldSource.PRINTED


def test_names_not_on_the_form_are_left_alone():
    provider = RecordingProvider()
    commitment, count = _look(FormReading(last_name=_name(None)), provider)
    assert count == 0
    assert commitment.last_name.value is None
    assert not [r for r in commitment.last_name.reasons if r.startswith("name_")]


@pytest.mark.parametrize("field, expected", [
    (Field[str](value=FIRST, source=FieldSource.HANDWRITTEN), True),
    (Field[str](value=FIRST, source=FieldSource.PRINTED), True),
    (Field[str](value=None), False),
    (Field[str](value=None, reasons=[ILLEGIBLE]), True),
    (Field[str](value=None, reasons=[BBOX_IMPLAUSIBLE]), True),
])
def test_needs_name_look(field, expected):
    assert needs_name_look(field) is expected


def test_wide_crop_is_wider_than_the_number_crop_and_enlarged_from_the_colour_original():
    provider = RecordingProvider()
    box = RelativeBox(page=0, x0=0.4, y0=0.4, x1=0.5, y1=0.45)  # 100 x 40 px on the 1000 x 800 page
    _look(FormReading(first_name=_name(FIRST, box)), provider,
          NameRereadSettings(enabled=True, variants=(NameVariant.WIDE, NameVariant.NARROW)))
    wide, narrow = provider.crops
    assert wide.ndim == 3 and narrow.ndim == 3  # colour, never binarized
    # wide: 1.5 box heights (60 px) to each side and 0.6 (24 px) above and below, then enlarged.
    assert wide.shape[:2] == (round((40 + 2 * 24) * ENLARGE_FACTOR), round((100 + 2 * 60) * ENLARGE_FACTOR))
    assert narrow.shape[1] < wide.shape[1]


def test_missing_box_falls_back_to_the_other_names_region():
    commitment, count = _look(FormReading(first_name=_name(FIRST), last_name=_name(LAST, value_box(), source="printed")),
                              ByFieldProvider())
    assert count == 4  # 2 names x 2 variants
    assert NAME_REREAD_FALLBACK_BOX not in commitment.last_name.reasons
    assert NAME_REREAD_FALLBACK_BOX in commitment.first_name.reasons
    assert NAME_READINGS_AGREE in commitment.first_name.reasons


def test_implausible_box_without_a_neighbour_is_unavailable_not_skipped():
    provider = RecordingProvider()
    huge = RelativeBox(page=0, x0=0.0, y0=0.0, x1=1.0, y1=1.0)  # the whole page: not one value
    commitment, count = _look(FormReading(first_name=_name(FIRST, huge)), provider)
    assert count == 0
    assert BBOX_IMPLAUSIBLE in commitment.first_name.reasons
    assert NAME_REREAD_UNAVAILABLE in commitment.first_name.reasons


def test_failed_calls_are_recorded_and_the_value_kept():
    commitment, _ = _look(FormReading(first_name=_name(FIRST, value_box())), RecordingProvider(fail=True))
    assert commitment.first_name.value == FIRST
    assert commitment.first_name.reasons.count(NAME_REREAD_FAILED) == 1
    assert NAME_READINGS_AGREE not in commitment.first_name.reasons


def test_name_found_only_by_an_extra_reading_is_shown_and_flagged():
    provider = RecordingProvider(names=[NameReading(), _answer(FIRST, "משח")])
    commitment, _ = _look(FormReading(first_name=_name(None, value_box(), legible=False)), provider)
    field = commitment.first_name
    assert field.value == field.raw_value == FIRST and field.source is FieldSource.HANDWRITTEN
    assert field.needs_review and "not_found" not in field.reasons
    assert {NAME_FILLED_FROM_SECOND_LOOK, NAME_REREAD_NULL} <= set(field.reasons)
    assert NAME_READINGS_AGREE not in field.reasons


def test_empty_name_stays_empty_when_no_reading_finds_one():
    commitment, _ = _look(FormReading(first_name=_name(None, value_box(), legible=False)), RecordingProvider())
    assert commitment.first_name.value is None and "not_found" in commitment.first_name.reasons


def test_both_names_are_read_and_bounded_by_the_variants():
    provider = RecordingProvider()
    settings = NameRereadSettings(enabled=True, variants=(NameVariant.WIDE, NameVariant.NARROW, NameVariant.INK))
    _, count = _look(FormReading(first_name=_name(FIRST, value_box(top=0.1)), last_name=_name(LAST, value_box(top=0.3))),
                     provider, settings)
    assert count == 6  # 2 names x 3 variants


def test_ink_variant_sends_the_separated_crop():
    page_box = RelativeBox(page=0, x0=0.4, y0=0.4, x1=0.5, y1=0.45)
    provider = RecordingProvider()

    def page_with_line_and_blue_ink():
        page = blank_page()
        page.original[int(0.47 * PAGE_HEIGHT):int(0.47 * PAGE_HEIGHT) + 3, :] = 0  # a ruled line under the name
        page.original[int(0.41 * PAGE_HEIGHT):int(0.44 * PAGE_HEIGHT), int(0.42 * PAGE_WIDTH):int(0.48 * PAGE_WIDTH)] \
            = (160, 40, 20)  # blue (BGR) handwriting
        return page

    pages = [page_with_line_and_blue_ink()]
    commitment = to_commitment(FormReading(first_name=_name(FIRST, page_box)), pages, None)

    async def main():
        await second_look_names(commitment, pages, provider, ProviderCalls(asyncio.Semaphore(1), CALLS),
                                NameRereadSettings(enabled=True, variants=(NameVariant.INK,)))

    asyncio.run(main())
    [crop] = provider.crops
    assert crop.ndim == 3
    assert not (crop.max(axis=2) < 60).any()  # the black line is gone
    assert ((crop[..., 0] > 120) & (crop[..., 2] < 80)).any()  # the blue ink is still there


@pytest.mark.parametrize("value, readings, failed, expected", [
    (FIRST, [_answer(FIRST), _answer(FIRST)], False, [NAME_READINGS_AGREE]),
    (FIRST, [_answer(FIRST), _answer("משח")], False, [NAME_READINGS_DISAGREE]),
    (FIRST, [_answer(FIRST), NameReading()], False, [NAME_REREAD_NULL]),
    (FIRST, [_answer(FIRST)], True, [NAME_REREAD_FAILED]),
    (FIRST, [_answer(FIRST, uncertain=[0])], False, [NAME_READINGS_AGREE, NAME_UNCERTAIN_LETTERS]),
    ("אברהם", [_answer("אברהמ")], False, [NAME_READINGS_AGREE]),  # final letters unified
    (None, [NameReading()], False, [NAME_REREAD_NULL]),
    (None, [_answer(FIRST), _answer(FIRST)], False, [NAME_FILLED_FROM_SECOND_LOOK]),        # filled: never "agree"
    (None, [_answer(FIRST), _answer("משח")], False, [NAME_FILLED_FROM_SECOND_LOOK, NAME_READINGS_DISAGREE]),
])
def test_outcomes(value, readings, failed, expected):
    assert outcomes(value, readings, failed) == expected


def test_every_variant_has_a_spec():
    assert set(VARIANTS) == set(NameVariant)
