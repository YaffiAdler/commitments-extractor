"""Stage 5: a raw FormReading becomes a Commitment (dummy values only)."""
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from app.pipeline.extract import BBOX_IMPLAUSIBLE, HANDWRITTEN, ILLEGIBLE, UNPARSED_DATE, pixel_box, to_commitment
from app.pipeline.ingest import PageImage
from app.schemas import FieldReading, FieldSource, FormReading, RelativeBox, SignalSource

WIDTH, HEIGHT = 1000, 800


def _page(page_no: int = 0) -> PageImage:
    original = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)
    return PageImage(file_id="f.pdf", page_no=page_no, image=original[::2, ::2], original_path=Path("f.pdf"),
                     original=original)


def _box(page=0, x0=0.1, y0=0.1, x1=0.3, y1=0.15) -> RelativeBox:
    return RelativeBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)


def test_several_central_numbers_are_all_kept_in_order():
    reading = FormReading(central_numbers=[FieldReading(value="111111", source="sticker"),
                                           FieldReading(value="222222", source="sticker"),
                                           FieldReading(value="333333", source="handwritten")])
    commitment = to_commitment(reading, [_page()], None)
    assert [f.value for f in commitment.central_numbers] == ["111111", "222222", "333333"]
    assert [f.source for f in commitment.central_numbers] == [FieldSource.STICKER, FieldSource.STICKER,
                                                             FieldSource.HANDWRITTEN]
    assert HANDWRITTEN in commitment.central_numbers[2].reasons


def test_several_commitments_give_several_items_and_repeats_are_kept_once():
    reading = FormReading(commitment_numbers=[FieldReading(value="0001"), FieldReading(value=" 0002 "),
                                              FieldReading(value="0001")])
    commitment = to_commitment(reading, [_page()], None)
    assert [f.value for f in commitment.commitment_numbers] == ["0001", "0002"]  # leading zeros kept


def test_null_values_stay_empty_and_flagged():
    reading = FormReading(id_number=FieldReading(value=None), first_name=FieldReading(value="  "),
                          central_numbers=[FieldReading(value=None)])
    commitment = to_commitment(reading, [_page()], None)
    for field in (commitment.id_number, commitment.first_name):
        assert field.value is None and field.needs_review and "not_found" in field.reasons
        assert field.source is FieldSource.NONE
    assert commitment.central_numbers == []  # a legible "nothing" is not an item
    assert commitment.needs_review


def test_cut_sticker_keeps_the_partial_value_and_is_flagged():
    reading = FormReading(central_numbers=[FieldReading(value="1234", legible=False, source="sticker",
                                                        note="cut at the right edge", bbox=_box())])
    field = to_commitment(reading, [_page()], None).central_numbers[0]
    assert (field.value, field.raw_value) == ("1234", "1234")  # as seen, not completed
    assert ILLEGIBLE in field.reasons and field.needs_review
    assert field.signals[0].source is SignalSource.VLM
    assert (field.signals[0].legible, field.signals[0].note) == (False, "cut at the right edge")


def test_unreadable_sticker_without_value_is_kept_as_an_item():
    reading = FormReading(central_numbers=[FieldReading(value=None, legible=False, bbox=_box())])
    [field] = to_commitment(reading, [_page()], None).central_numbers
    assert field.value is None and ILLEGIBLE in field.reasons and field.bbox is not None


def test_relative_box_becomes_original_pixels_on_the_right_page():
    pages = [_page(4), _page(5)]
    bbox = pixel_box(_box(page=1, x0=0.1, y0=0.5, x1=0.2, y1=0.55), pages)
    assert bbox.page == 5
    assert (bbox.x0, bbox.y0, bbox.x1, bbox.y1) == pytest.approx((100, 400, 200, 440))


@pytest.mark.parametrize("box", [
    _box(page=1),                          # no such page in the form
    _box(x0=0.3, x1=0.1),                  # corners swapped
    _box(x0=-0.2),                         # far outside the image
    _box(x0=0.0, y0=0.0, x1=0.9, y1=0.9),  # most of the page, not one value
])
def test_implausible_box_is_dropped_and_flagged(box):
    reading = FormReading(id_number=FieldReading(value="123", bbox=box))
    field = to_commitment(reading, [_page()], None).id_number
    assert field.bbox is None and BBOX_IMPLAUSIBLE in field.reasons


def test_small_overshoot_is_clipped_to_the_image():
    bbox = pixel_box(_box(x0=0.9, x1=1.01), [_page()])
    assert bbox.x1 == WIDTH


def test_date_is_parsed_and_unparsed_date_stays_empty_with_the_text():
    parsed = to_commitment(FormReading(received_date=FieldReading(value="01.02.2025", source="stamp")),
                           [_page()], None).received_date
    assert parsed.value == date(2025, 2, 1) and parsed.raw_value == "01.02.2025"
    no_year = to_commitment(FormReading(received_date=FieldReading(value="31.8", source="handwritten")),
                            [_page()], None).received_date
    assert no_year.value is None and no_year.raw_value == "31.8"
    assert UNPARSED_DATE in no_year.reasons and HANDWRITTEN in no_year.reasons


def test_no_confidence_is_set():
    commitment = to_commitment(FormReading(id_number=FieldReading(value="123")), [_page()], None)
    assert commitment.id_number.confidence == 0.0 and commitment.id_number.needs_review
