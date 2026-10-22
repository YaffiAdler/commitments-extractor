"""One form through classify -> extract -> OCR check -> second look, with the
fake provider and scripted OCR: the extraction edge cases end to end."""
import asyncio
from datetime import date

from form_fakes import LineOcr, blank_page, value_box
from app.config import ExtractionSettings, Settings
from app.pipeline.calls import ProviderCalls
from app.pipeline.classify import SenderMatcher, load_form_types, load_generic_form_type
from app.pipeline.extract import ILLEGIBLE
from app.pipeline.normalize import DATE_TWO_DIGIT_YEAR, ID_PADDED, load_senders
from app.pipeline.ocr_check import OCR_AGREES, OCR_DISAGREES
from app.pipeline.read_form import FormReader
from app.pipeline.reread import REREAD_AGREES, REREAD_DISAGREES
from app.providers import fake
from app.providers.fake import FakeProvider
from app.schemas import FieldReading, FormGroup, FormReading, SignalSource

CALLS = ExtractionSettings(concurrency=2, call_timeout_s=5, max_attempts=1, backoff_initial_s=0, backoff_max_s=0)


def _read(provider: FakeProvider, ocr=None) -> tuple[FormGroup, int]:
    settings = Settings()
    reader = FormReader(provider, load_form_types(settings.form_types_dir),
                        load_generic_form_type(settings.form_types_dir),
                        SenderMatcher(load_senders(settings.senders_path)), ocr)
    group = FormGroup(source_file="f.pdf", pages=[0])

    async def main():
        calls = ProviderCalls(asyncio.Semaphore(CALLS.concurrency), CALLS)
        await reader.read(group, [blank_page()], calls)
        return calls.count

    return group, asyncio.run(main())


def test_default_fake_form_takes_two_calls_and_gets_the_matched_sender():
    group, count = _read(FakeProvider())
    commitment = group.commitment
    assert count == 2  # classify + extract; no boxes, so nothing to crop
    assert commitment.form_type_id == fake.FAKE_FORM_TYPE_ID
    assert commitment.id_number.value == fake.FAKE_ID_NUMBER
    assert (commitment.sender.value, commitment.sender.raw_value) == ("כללית", fake.FAKE_SENDER_RAW)


def test_several_central_numbers_are_checked_one_by_one():
    reading = FormReading(central_numbers=[
        FieldReading(value="111111", source="sticker", bbox=value_box(top=0.1)),
        FieldReading(value="222222", source="sticker", bbox=value_box(top=0.3)),
    ])
    group, count = _read(FakeProvider(reading=reading, numbers=["222222"]), LineOcr("111111", "222282"))
    first, second = group.commitment.central_numbers
    assert (first.value, second.value) == ("111111", "222222")
    assert OCR_AGREES in first.reasons and not any(r.startswith("reread_") for r in first.reasons)
    assert OCR_DISAGREES in second.reasons and REREAD_AGREES in second.reasons
    assert [s.source for s in second.signals] == [SignalSource.VLM, SignalSource.OCR, SignalSource.REREAD]
    assert count == 3


def test_several_commitments_are_several_items():
    reading = FormReading(commitment_numbers=[FieldReading(value="0001", bbox=value_box(top=0.1)),
                                              FieldReading(value="0002", bbox=value_box(top=0.3))])
    group, _ = _read(FakeProvider(reading=reading), LineOcr("0001", "0002"))
    assert [f.value for f in group.commitment.commitment_numbers] == ["0001", "0002"]
    assert all(OCR_AGREES in f.reasons for f in group.commitment.commitment_numbers)


def test_null_fields_stay_empty_and_cost_no_extra_call():
    group, count = _read(FakeProvider(reading=FormReading()), LineOcr())
    commitment = group.commitment
    assert count == 2
    assert commitment.id_number.value is None and commitment.central_numbers == []
    assert commitment.commitment_numbers == [] and commitment.received_date.value is None
    assert group.needs_review


def test_cut_sticker_keeps_its_partial_value_next_to_the_second_look():
    reading = FormReading(central_numbers=[FieldReading(value="1234", legible=False, source="sticker",
                                                        bbox=value_box(), note="cut")])
    group, count = _read(FakeProvider(reading=reading, numbers=["123456"]), LineOcr("1234"))
    [field] = group.commitment.central_numbers
    assert field.value == "1234" and ILLEGIBLE in field.reasons and field.needs_review
    # OCR agreed with the partial digits, but an illegible sticker still gets its second look
    assert OCR_AGREES in field.reasons and REREAD_DISAGREES in field.reasons
    assert field.signals[-1].value == "123456"
    assert count == 3


def test_printed_value_that_ocr_could_not_read_gets_a_second_look():
    reading = FormReading(id_number=FieldReading(value="000000018", bbox=value_box()))
    group, count = _read(FakeProvider(reading=reading, numbers=["000000018"]), LineOcr(""))
    assert REREAD_AGREES in group.commitment.id_number.reasons
    assert count == 3


def test_values_are_normalized_after_the_checks():
    reading = FormReading(id_number=FieldReading(value="1234567-8"),
                          received_date=FieldReading(value="15.03.25", source="stamp"),
                          central_numbers=[FieldReading(value="012 345", source="sticker")])
    group, _ = _read(FakeProvider(reading=reading))
    commitment = group.commitment
    assert (commitment.id_number.value, commitment.id_number.raw_value) == ("012345678", "1234567-8")
    assert ID_PADDED in commitment.id_number.reasons
    assert commitment.received_date.value == date(2025, 3, 15)
    assert DATE_TWO_DIGIT_YEAR in commitment.received_date.reasons
    assert [f.value for f in commitment.central_numbers] == ["012345"]
