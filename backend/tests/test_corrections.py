from datetime import date

import pytest

from app.corrections import CorrectionError, GroupNotFoundError, apply_correction, field_at
from app.schemas import MANUAL_CORRECTION, BBox, Field, FieldSignal, FieldSource, FormGroup, Job, SignalSource

SENDERS = frozenset({"כללית", "מכבי"})
BOX = BBox(page=0, x0=1, y0=2, x1=3, y1=4)


def _job() -> Job:
    group = FormGroup(source_file="a" * 32 + ".pdf", pages=[0])
    group.commitment.id_number = Field(value="000000018", raw_value="00000001 8", confidence=0.5, needs_review=True,
                                       reasons=["ocr_disagrees"], source=FieldSource.HANDWRITTEN, bbox=BOX,
                                       signals=[FieldSignal(source=SignalSource.OCR, value="000000019")])
    group.commitment.central_numbers = [Field(value="0012345", raw_value="0012345", source=FieldSource.STICKER)]
    return Job(groups=[group])


def test_correction_replaces_value_and_keeps_raw_value_box_and_readings():
    job = _job()
    group = apply_correction(job, job.groups[0].id, "id_number", " 000000026 ", None, SENDERS)
    field = group.commitment.id_number
    assert (field.value, field.raw_value, field.source, field.confidence, field.needs_review) == (
        "000000026", "00000001 8", FieldSource.MANUAL, 1.0, False)
    assert field.reasons == [MANUAL_CORRECTION] and field.explanations
    assert field.bbox == BOX and field.signals[0].value == "000000019"
    correction = job.corrections[0]
    assert (correction.field, correction.item, correction.previous_raw_value, correction.previous_source) == (
        "id_number", None, "00000001 8", FieldSource.HANDWRITTEN)


def test_list_item_is_replaced_or_added():
    job = _job()
    group_id = job.groups[0].id
    apply_correction(job, group_id, "central_numbers", "0099999", 1, SENDERS)
    apply_correction(job, group_id, "central_numbers", "0011111", 0, SENDERS)
    assert [f.value for f in job.groups[0].commitment.central_numbers] == ["0011111", "0099999"]
    assert job.corrections[0].previous_source is FieldSource.NONE  # an added item had no reading
    assert job.groups[0].commitment.central_numbers[1].raw_value is None
    assert job.groups[0].commitment.central_numbers[0].raw_value == "0012345"  # the reading is kept


def test_date_is_parsed_and_sender_must_be_listed():
    job = _job()
    group_id = job.groups[0].id
    apply_correction(job, group_id, "received_date", "2026-01-04", None, SENDERS)
    apply_correction(job, group_id, "sender", "מכבי", None, SENDERS)
    commitment = job.groups[0].commitment
    assert commitment.received_date.value == date(2026, 1, 4)
    assert commitment.sender.value == "מכבי"
    assert not commitment.field_needs_review("received_date")


@pytest.mark.parametrize(("name", "value", "item"), [
    ("received_date", "04/01/2026", None),
    ("sender", "גוף לא ברשימה", None),
    ("first_name", "", None),
    ("first_name", "יוסף", 0),
    ("central_numbers", "1", None),
    ("central_numbers", "1", 5),
    ("form_type_id", "x", None),
])
def test_invalid_corrections_change_nothing(name, value, item):
    job = _job()
    before = job.model_copy(deep=True)
    with pytest.raises(CorrectionError) as raised:
        apply_correction(job, job.groups[0].id, name, value, item, SENDERS)
    assert not value or value not in str(raised.value)
    assert job == before


def test_unknown_group():
    with pytest.raises(GroupNotFoundError):
        apply_correction(_job(), "0" * 32, "first_name", "יוסף", None, SENDERS)


def test_field_at():
    group = _job().groups[0]
    assert field_at(group, "id_number", None) is group.commitment.id_number
    assert field_at(group, "central_numbers", 0) is group.commitment.central_numbers[0]
    assert field_at(group, "central_numbers", 1) is None
    assert field_at(group, "id_number", 0) is None
    assert field_at(group, "optional_fields", None) is None
