from app.providers import fake
from app.providers.fake import FakeProvider


def test_extract_form_returns_dummy_values():
    commitment = FakeProvider().extract_form([], None)
    assert commitment.id_number.value == fake.FAKE_ID_NUMBER
    assert [f.value for f in commitment.commitment_numbers] == [fake.FAKE_COMMITMENT_NUMBER]
    # Providers do not score confidence.
    assert commitment.needs_review


def test_reread_and_classify():
    provider = FakeProvider()
    assert provider.reread_field([], "central_numbers", None) == [fake.FAKE_CENTRAL_NUMBER]
    assert provider.classify([], {}) is None
