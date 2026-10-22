"""Stage 5b: Tesseract cross-check of printed numbers (scripted OCR)."""
import pytest

from form_fakes import LineOcr, blank_page, value_box
from app.pipeline.crops import CROP_PADDING_PX, DIGITS
from app.pipeline.extract import to_commitment
from app.pipeline.ocr_check import OCR_AGREES, OCR_DISAGREES, OCR_EMPTY, OCR_UNAVAILABLE, check
from app.schemas import FieldReading, FormReading, RelativeBox, SignalSource


def _commitment(*centrals: FieldReading, id_number: FieldReading | None = None):
    reading = FormReading(central_numbers=list(centrals), id_number=id_number or FieldReading())
    return to_commitment(reading, [blank_page()], None)


def test_agreeing_and_disagreeing_ocr_are_signals_not_overwrites():
    commitment = _commitment(FieldReading(value="111 111", source="sticker", bbox=value_box()),
                             FieldReading(value="222222", source="sticker", bbox=value_box(top=0.3)))
    ocr = LineOcr("111111", "222228")
    check(commitment, [blank_page()], ocr)
    first, second = commitment.central_numbers
    assert OCR_AGREES in first.reasons and OCR_DISAGREES in second.reasons
    assert second.value == "222222"  # the model's value is kept
    assert [(s.source, s.value) for s in second.signals[1:]] == [(SignalSource.OCR, "222228")]
    assert second.signals[1].confidence == pytest.approx(0.9)
    assert ocr.allowed == [DIGITS, DIGITS]


def test_crop_is_the_box_plus_padding_from_the_binarized_image():
    ocr = LineOcr("123")
    check(_commitment(id_number=FieldReading(value="123", bbox=value_box())), [blank_page()], ocr)
    # value_box: 0.1..0.3 of 1000 px wide, 0.1..0.15 of 800 px high
    assert ocr.crops[0].shape == (40 + 2 * CROP_PADDING_PX, 200 + 2 * CROP_PADDING_PX)


@pytest.mark.parametrize("reading", [
    FieldReading(value="123"),                                                  # no box
    FieldReading(value="123", bbox=RelativeBox(x0=0.1, y0=0.1, x1=0.95, y1=0.9)),  # implausible box
    FieldReading(value="12AB", bbox=value_box()),                               # not a number
])
def test_no_usable_box_or_number_is_unavailable_not_a_disagreement(reading):
    commitment = _commitment(id_number=reading)
    ocr = LineOcr("999")
    check(commitment, [blank_page()], ocr)
    assert OCR_UNAVAILABLE in commitment.id_number.reasons and OCR_DISAGREES not in commitment.id_number.reasons
    assert ocr.crops == []


def test_without_ocr_engine_or_on_ocr_error_it_is_unavailable():
    commitment = _commitment(id_number=FieldReading(value="123", bbox=value_box()))
    check(commitment, [blank_page()], None)
    failing = _commitment(id_number=FieldReading(value="123", bbox=value_box()))
    check(failing, [blank_page()], LineOcr(None))
    assert OCR_UNAVAILABLE in commitment.id_number.reasons and OCR_UNAVAILABLE in failing.id_number.reasons


def test_handwritten_and_missing_values_are_not_ocrd():
    commitment = _commitment(FieldReading(value="123456", source="handwritten", bbox=value_box()),
                             id_number=FieldReading(value=None))
    ocr = LineOcr("123456")
    check(commitment, [blank_page()], ocr)
    assert ocr.crops == []
    assert not any(r.startswith("ocr_") for f in (commitment.id_number, *commitment.central_numbers)
                   for r in f.reasons)


def test_no_digits_read_is_empty():
    commitment = _commitment(id_number=FieldReading(value="123", bbox=value_box()))
    check(commitment, [blank_page()], LineOcr(""))
    assert OCR_EMPTY in commitment.id_number.reasons


def test_grayscale_original_is_used_when_there_is_no_binarized_copy():
    page = blank_page(with_ocr_image=False)
    commitment = to_commitment(FormReading(id_number=FieldReading(value="123", bbox=value_box())), [page], None)
    ocr = LineOcr("123")
    check(commitment, [page], ocr)
    assert ocr.crops[0].ndim == 2 and OCR_AGREES in commitment.id_number.reasons
