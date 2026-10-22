"""Shared provider logic: output parsing and page encoding."""
import base64
from pathlib import Path

from typing import get_args

import cv2
import numpy as np
import pytest

from app.pipeline.ingest import PageImage
from app.providers.common import (
    FORM_READING_SCHEMA,
    MAX_IMAGE_EDGE_PX,
    NUMBER_SCHEMA,
    PAGE_RELATION_SCHEMA,
    encode_page,
    form_classification_from_output,
    form_classification_schema,
    form_reading_from_output,
    number_from_output,
    page_relation_from_output,
)
from app.providers.base import ProviderError
from app.schemas import (
    UNKNOWN_FORM_TYPE,
    FieldReading,
    FormClassification,
    FormReading,
    NumberReading,
    PageRelation,
    PageType,
    ReadingSource,
)


def _reading(value, **extra) -> dict:
    return {"value": value, "bbox": None, "legible": True, "source": "printed", "note": None, **extra}


DUMMY_OUTPUT = {
    "first_name": _reading("משה"),
    "last_name": _reading("כהן"),
    "id_number": _reading("000000018", bbox={"page": 0, "x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.25}),
    "central_numbers": [_reading("111111", source="sticker"), _reading("2222", legible=False, source="sticker",
                                                                       note="cut at the edge")],
    "received_date": _reading("01.01.2025", source="stamp"),
    "sender": _reading("מכבי"),
    "commitment_numbers": [],
}


def test_form_reading_keeps_every_item_and_box():
    reading = form_reading_from_output(DUMMY_OUTPUT)
    assert [r.value for r in reading.central_numbers] == ["111111", "2222"]
    assert reading.central_numbers[1].legible is False
    assert reading.id_number.bbox.x1 == 0.3 and reading.commitment_numbers == []


def test_form_reading_outside_schema_is_provider_error_without_values():
    with pytest.raises(ProviderError) as info:
        form_reading_from_output({**DUMMY_OUTPUT, "id_number": {**_reading("000000018"), "source": "guess"}})
    assert "000000018" not in str(info.value) and info.value.__cause__ is None


def test_form_reading_schema_matches_the_model():
    assert set(FORM_READING_SCHEMA["required"]) == set(FormReading.model_fields)
    item = FORM_READING_SCHEMA["properties"]["central_numbers"]["items"]
    assert set(item["required"]) == set(FieldReading.model_fields)
    assert tuple(item["properties"]["source"]["enum"]) == get_args(ReadingSource)


def test_number_reading():
    assert number_from_output({"value": "123"}) == NumberReading(value="123")
    assert number_from_output({"value": None}).value is None
    assert set(NUMBER_SCHEMA["required"]) == set(NumberReading.model_fields)
    with pytest.raises(ProviderError):
        number_from_output({"value": 5})


def test_classification_keeps_a_known_form_type():
    answer = form_classification_from_output(
        {"form_type": "clalit", "sender_raw": " כללית ", "evidence": "logo"}, {"clalit": object()})
    assert answer == FormClassification(form_type="clalit", sender_raw="כללית", evidence="logo")


def test_classification_of_an_unloaded_id_is_unknown():
    answer = form_classification_from_output({"form_type": "other", "sender_raw": None, "evidence": ""}, {})
    assert answer.form_type == UNKNOWN_FORM_TYPE and answer.sender_raw is None


def test_blank_sender_is_none():
    answer = form_classification_from_output({"form_type": "unknown", "sender_raw": "  ", "evidence": ""}, {})
    assert answer.sender_raw is None


def test_classification_missing_key_is_provider_error():
    with pytest.raises(ProviderError):
        form_classification_from_output({"form_type": "clalit"}, {"clalit": object()})


def test_classification_schema_offers_unknown_and_matches_the_model():
    schema = form_classification_schema({"clalit": object()})
    assert schema["properties"]["form_type"]["enum"] == ["clalit", UNKNOWN_FORM_TYPE]
    assert set(schema["required"]) == set(FormClassification.model_fields)


def test_large_page_is_downscaled_before_sending():
    image = np.zeros((MAX_IMAGE_EDGE_PX * 2, 100, 3), np.uint8)
    page = PageImage(file_id="a.pdf", page_no=0, image=image, original_path=Path("a.pdf"), original=image)
    sent = cv2.imdecode(np.frombuffer(base64.b64decode(encode_page(page)), np.uint8), cv2.IMREAD_COLOR)
    assert max(sent.shape[:2]) == MAX_IMAGE_EDGE_PX


def test_page_relation_schema_matches_the_model():
    assert set(PAGE_RELATION_SCHEMA["required"]) == set(PageRelation.model_fields)
    assert tuple(PAGE_RELATION_SCHEMA["properties"]["page_b_type"]["enum"]) == get_args(PageType)


def test_page_relation_missing_key_is_provider_error():
    with pytest.raises(ProviderError):
        page_relation_from_output({"same_form": True})
