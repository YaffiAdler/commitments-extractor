"""Stage 5c: one second look at unconfirmed numbers (fake provider, no network)."""
import asyncio

from form_fakes import blank_page, value_box
from app.config import ExtractionSettings
from app.pipeline.calls import ProviderCalls
from app.pipeline.extract import to_commitment
from app.pipeline.ocr_check import OCR_AGREES, OCR_DISAGREES
from app.pipeline.reread import (
    MAX_REREADS_PER_FORM,
    REREAD_AGREES,
    REREAD_DISAGREES,
    REREAD_FAILED,
    REREAD_FOUND_VALUE,
    REREAD_LIMIT,
    REREAD_NULL,
    REREAD_UNAVAILABLE,
    second_look,
)
from app.providers.base import ProviderError
from app.providers.fake import FakeProvider
from app.schemas import FieldReading, FormReading, SignalSource

SETTINGS = ExtractionSettings(concurrency=1, call_timeout_s=5, max_attempts=1, backoff_initial_s=0, backoff_max_s=0)


class RecordingProvider(FakeProvider):
    def __init__(self, numbers=()):
        super().__init__(numbers=numbers)
        self.crops = []

    def read_number(self, crop):
        self.crops.append(crop)
        return super().read_number(crop)


def _look(reading: FormReading, provider: FakeProvider, ocr_reasons: dict[str, str] | None = None):
    commitment = to_commitment(reading, [blank_page()], None)
    for field_name, reason in (ocr_reasons or {}).items():
        getattr(commitment, field_name).reasons.append(reason)

    async def main():
        calls = ProviderCalls(asyncio.Semaphore(1), SETTINGS)
        await second_look(commitment, [blank_page()], provider, calls)
        return calls.count

    return commitment, asyncio.run(main())


def test_ocr_confirmed_value_gets_no_second_look():
    provider = RecordingProvider()
    _, count = _look(FormReading(id_number=FieldReading(value="123", bbox=value_box())), provider,
                     {"id_number": OCR_AGREES})
    assert count == 0 and provider.crops == []


def test_disagreement_gets_one_enlarged_colour_crop_and_the_answer_is_a_signal():
    provider = RecordingProvider(numbers=["124"])
    commitment, count = _look(FormReading(id_number=FieldReading(value="123", bbox=value_box())), provider,
                              {"id_number": OCR_DISAGREES})
    field = commitment.id_number
    assert count == 1 and field.value == "123"  # not overwritten
    assert REREAD_DISAGREES in field.reasons
    assert [(s.source, s.value) for s in field.signals if s.source is SignalSource.REREAD] == [
        (SignalSource.REREAD, "124")]
    crop = provider.crops[0]
    # box 200x40 px + 10 px padding on each side, enlarged 2.5 times; colour, never binarized
    assert crop.shape == (150, 550, 3)


def test_illegible_handwritten_and_cut_values_are_looked_at_once_each():
    reading = FormReading(
        id_number=FieldReading(value="123", source="handwritten", bbox=value_box()),
        central_numbers=[FieldReading(value="12", legible=False, source="sticker", bbox=value_box(top=0.3)),
                         FieldReading(value=None, legible=False, source="sticker", bbox=value_box(top=0.5))],
    )
    commitment, count = _look(reading, RecordingProvider(numbers=["123", "", "654321"]))
    assert count == 3
    assert REREAD_AGREES in commitment.id_number.reasons
    cut, unreadable = commitment.central_numbers
    assert REREAD_NULL in cut.reasons and cut.value == "12"
    assert REREAD_FOUND_VALUE in unreadable.reasons and unreadable.value is None  # never filled in
    assert unreadable.signals[-1].value == "654321"


def test_missing_value_without_box_is_left_alone_and_illegible_without_box_is_unavailable():
    reading = FormReading(id_number=FieldReading(value=None),
                          central_numbers=[FieldReading(value=None, legible=False)])
    commitment, count = _look(reading, RecordingProvider())
    assert count == 0
    assert not any(r.startswith("reread_") for r in commitment.id_number.reasons)
    assert REREAD_UNAVAILABLE in commitment.central_numbers[0].reasons


def test_failed_call_is_recorded_on_the_field_only():
    class Failing(FakeProvider):
        def read_number(self, crop):
            raise ProviderError("down")

    commitment, count = _look(FormReading(id_number=FieldReading(value="123", bbox=value_box())), Failing())
    assert count == 1 and REREAD_FAILED in commitment.id_number.reasons and commitment.id_number.value == "123"


def test_at_most_max_rereads_per_form():
    centrals = [FieldReading(value=str(100000 + i), bbox=value_box(top=0.05 * i)) for i in range(MAX_REREADS_PER_FORM + 2)]
    commitment, count = _look(FormReading(central_numbers=centrals), RecordingProvider())
    assert count == MAX_REREADS_PER_FORM
    assert sum(REREAD_LIMIT in f.reasons for f in commitment.central_numbers) == 2
