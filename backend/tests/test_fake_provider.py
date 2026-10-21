from app.providers import fake
from app.providers.fake import FakeProvider
from app.schemas import UNKNOWN_FORM_TYPE


def test_extract_form_returns_dummy_values():
    commitment = FakeProvider().extract_form([], None)
    assert commitment.id_number.value == fake.FAKE_ID_NUMBER
    assert [f.value for f in commitment.commitment_numbers] == [fake.FAKE_COMMITMENT_NUMBER]
    # Providers do not score confidence.
    assert commitment.needs_review


def test_reread_and_classify():
    provider = FakeProvider()
    assert provider.reread_field([], "central_numbers", None) == [fake.FAKE_CENTRAL_NUMBER]
    assert provider.classify([], {}).form_type == UNKNOWN_FORM_TYPE
    assert provider.classify([], {fake.FAKE_FORM_TYPE_ID: None}).form_type == fake.FAKE_FORM_TYPE_ID


def test_compare_pages_says_every_page_starts_a_form():
    relation = FakeProvider().compare_pages(None, None)
    assert (relation.same_form, relation.page_b_type) == (False, "start")
