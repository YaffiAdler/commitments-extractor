"""One form through classify -> extract -> OCR check -> second look, with the
fake provider and scripted OCR: the extraction edge cases end to end."""
import asyncio
from datetime import date

from form_fakes import LineOcr, blank_page, value_box
from app.config import ConfidenceSettings, ExtractionSettings, NameRereadSettings, NameVariant, Settings
from app.pipeline.calls import ProviderCalls
from app.pipeline.classify import (
    SENDER_READINGS_AGREE,
    SENDER_READINGS_DISAGREE,
    SenderMatcher,
    load_form_types,
    load_generic_form_type,
)
from app.pipeline.extract import ILLEGIBLE
from app.pipeline.normalize import DATE_TWO_DIGIT_YEAR, ID_PADDED, load_senders
from app.pipeline.ocr_check import OCR_AGREES, OCR_DISAGREES
from app.pipeline.read_form import FormReader
from app.pipeline.reason_texts import REASON_TEXTS, SINGLE_READING
from app.pipeline.reread import REREAD_AGREES, REREAD_DISAGREES
from app.pipeline.reread_name import NAME_READINGS_AGREE
from app.providers import fake
from app.providers.fake import FakeProvider
from app.schemas import FieldReading, FormGroup, FormReading, NameCandidate, NameReading, SignalSource

CALLS = ExtractionSettings(concurrency=2, call_timeout_s=5, max_attempts=1, backoff_initial_s=0, backoff_max_s=0)


def _read(provider: FakeProvider, ocr=None, confidence: ConfidenceSettings | None = None,
          name_reread: NameRereadSettings | None = None) -> tuple[FormGroup, int]:
    settings = Settings()
    reader = FormReader(provider, load_form_types(settings.form_types_dir),
                        load_generic_form_type(settings.form_types_dir),
                        SenderMatcher(load_senders(settings.senders_path)), ocr, confidence_settings=confidence,
                        name_reread=name_reread)
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


def test_confidence_is_scored_from_the_signals():
    reading = fake.default_reading()
    reading.id_number = FieldReading(value=fake.FAKE_ID_NUMBER, bbox=value_box(top=0.1))
    reading.central_numbers = [FieldReading(value="123456", source="sticker", bbox=value_box(top=0.3))]
    group, _ = _read(FakeProvider(reading=reading, numbers=["123450"]), LineOcr(fake.FAKE_ID_NUMBER, "123450"))
    commitment = group.commitment
    assert commitment.id_number.confidence == 1.0 and not commitment.id_number.needs_review
    central = commitment.central_numbers[0]
    assert central.needs_review and REASON_TEXTS[REREAD_DISAGREES] in central.explanations
    # Names are read once only (no OCR, no second look), so they stay in review.
    assert commitment.first_name.needs_review and SINGLE_READING in commitment.first_name.reasons
    assert group.needs_review


def test_sender_is_green_when_both_readings_match_the_same_listed_sender():
    group, _ = _read(FakeProvider())
    sender = group.commitment.sender
    assert SENDER_READINGS_AGREE in sender.reasons
    assert sender.confidence == 0.9 and not sender.needs_review


def test_sender_is_orange_when_the_two_readings_differ():
    reading = fake.default_reading()
    reading.sender = FieldReading(value="מכבי")
    group, _ = _read(FakeProvider(reading=reading))
    sender = group.commitment.sender
    assert sender.value == "כללית"  # the classified sender is kept, not replaced
    assert SENDER_READINGS_DISAGREE in sender.reasons and sender.needs_review


def test_confidence_settings_are_used():
    group, _ = _read(FakeProvider(), confidence=ConfidenceSettings(review_threshold=0.5))
    assert not group.commitment.first_name.needs_review


def test_classification_is_kept_on_the_form():
    group, _ = _read(FakeProvider())
    result = group.classification
    assert (result.form_type_id, result.known, result.sender_raw, result.sender_status) == (
        fake.FAKE_FORM_TYPE_ID, True, fake.FAKE_SENDER_RAW, "matched")
    assert result.sender_score > 0
    assert result.sender_candidates[0].name == "כללית"


def _handwritten_name_form() -> FormReading:
    reading = fake.default_reading()
    reading.first_name = FieldReading(value="משה", bbox=value_box(top=0.5), source="handwritten")
    reading.last_name = FieldReading()  # not on the form: not read again
    return reading


def test_name_second_look_is_off_by_default():
    provider = FakeProvider(reading=_handwritten_name_form())
    group, count = _read(provider)
    assert count == 2 and provider.name_requests == []
    assert SINGLE_READING in group.commitment.first_name.reasons


def test_name_second_look_when_enabled_is_scored_but_still_reviewed():
    answer = NameReading(candidates=[NameCandidate(value="משה")])
    provider = FakeProvider(reading=_handwritten_name_form(), names=[answer, answer])
    group, count = _read(provider, name_reread=NameRereadSettings(
        enabled=True, variants=(NameVariant.WIDE, NameVariant.LETTERS)))
    first = group.commitment.first_name
    assert count == 4 and len(provider.name_requests) == 2  # classify + extract + 2 name readings
    assert first.value == "משה" and NAME_READINGS_AGREE in first.reasons
    assert first.confidence == 0.75 and first.needs_review  # 0.55 + 0.15 + 0.05, below 0.9
    assert REASON_TEXTS[NAME_READINGS_AGREE] in first.explanations

