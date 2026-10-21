"""Shared provider logic: output parsing and page encoding."""
import base64
from pathlib import Path

from typing import get_args

import cv2
import numpy as np
import pytest

from app.pipeline.ingest import PageImage
from app.providers.common import (
    MAX_IMAGE_EDGE_PX,
    PAGE_RELATION_SCHEMA,
    UNPARSED_DATE_REASON,
    commitment_from_output,
    encode_page,
    form_type_from_output,
    page_relation_from_output,
    readings_from_output,
)
from app.providers.base import ProviderError
from app.schemas import PageRelation, PageType

DUMMY_OUTPUT = {
    "first_name": "משה",
    "last_name": "כהן",
    "id_number": "000000018",
    "central_numbers": ["111111", "111111", "222222", " "],
    "received_date": "2025-01-01",
    "sender": "מכבי",
    "commitment_numbers": [],
}


def test_list_fields_are_deduped_and_blanks_dropped():
    commitment = commitment_from_output(DUMMY_OUTPUT)
    assert [f.value for f in commitment.central_numbers] == ["111111", "222222"]
    assert commitment.commitment_numbers == []


def test_null_field_stays_empty_and_flagged():
    commitment = commitment_from_output({**DUMMY_OUTPUT, "first_name": None})
    assert commitment.first_name.value is None
    assert commitment.first_name.needs_review


def test_unparsed_date_stays_empty():
    commitment = commitment_from_output({**DUMMY_OUTPUT, "received_date": "31.8"})
    assert commitment.received_date.value is None
    assert UNPARSED_DATE_REASON in commitment.received_date.reasons


def test_readings_drop_blanks_and_non_strings():
    assert readings_from_output({"readings": [" 123 ", "", 5]}) == ["123"]
    assert readings_from_output({}) == []


def test_unknown_form_type_is_none():
    form_types = {"clalit": object()}
    assert form_type_from_output({"form_type_id": "clalit"}, form_types) == "clalit"
    assert form_type_from_output({"form_type_id": "other"}, form_types) is None


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
