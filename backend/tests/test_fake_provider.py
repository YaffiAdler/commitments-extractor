import numpy as np

from app.providers import fake
from app.providers.fake import FakeProvider
from app.schemas import UNKNOWN_FORM_TYPE, FieldReading, FormReading, NameCandidate, NameReading, NameWording


def test_extract_form_returns_dummy_values():
    reading = FakeProvider().extract_form([], None)
    assert reading.id_number.value == fake.FAKE_ID_NUMBER
    assert [r.value for r in reading.commitment_numbers] == [fake.FAKE_COMMITMENT_NUMBER]
    assert reading.id_number.bbox is None  # nothing to crop by default


def test_extract_form_returns_the_given_reading_as_a_copy():
    given = FormReading(central_numbers=[FieldReading(value="111111"), FieldReading(value="222222")])
    provider = FakeProvider(reading=given)
    provider.extract_form([], None).central_numbers.clear()
    assert len(provider.extract_form([], None).central_numbers) == 2


def test_read_number_answers_in_order_then_none():
    provider = FakeProvider(numbers=["123", None])
    crop = np.zeros((10, 10, 3), np.uint8)
    assert [provider.read_number(crop).value for _ in range(3)] == ["123", None, None]


def test_classify():
    provider = FakeProvider()
    assert provider.classify([], {}).form_type == UNKNOWN_FORM_TYPE
    assert provider.classify([], {fake.FAKE_FORM_TYPE_ID: None}).form_type == fake.FAKE_FORM_TYPE_ID


def test_compare_pages_says_every_page_starts_a_form():
    relation = FakeProvider().compare_pages(None, None)
    assert (relation.same_form, relation.page_b_type) == (False, "start")


def test_read_name_answers_in_order_records_requests_then_null():
    answer = NameReading(candidates=[NameCandidate(value="משה")])
    provider = FakeProvider(names=[answer])
    crop = np.zeros((10, 10, 3), np.uint8)
    assert provider.read_name(crop, "first_name", NameWording.STANDARD).top.value == "משה"
    assert provider.read_name(crop, "last_name", NameWording.LETTERS).top is None
    assert provider.name_requests == [("first_name", NameWording.STANDARD), ("last_name", NameWording.LETTERS)]
