import pytest

from app.pipeline import classify, extract, normalize, ocr_check, reread
from app.pipeline.reason_texts import REASON_TEXTS, SINGLE_READING, explain
from app.schemas import CENTRAL_DUPLICATE, FORM_TYPE_UNKNOWN, NOT_FOUND, OPTIONAL_MISSING

HEBREW_LETTERS = range(ord("א"), ord("ת") + 1)
# Module constants that are not field reasons: form-level reasons and the generic form type id.
NOT_FIELD_CODES = {CENTRAL_DUPLICATE, FORM_TYPE_UNKNOWN, classify.GENERIC_FORM_TYPE_ID}


def _field_codes(module) -> set[str]:
    """The public reason-code constants of a stage module (lower-case string values)."""
    return {value for name, value in vars(module).items()
            if name.isupper() and not name.startswith("_") and isinstance(value, str)
            and value.islower() and not value.endswith(":") and value not in NOT_FIELD_CODES}


@pytest.mark.parametrize("module", [extract, normalize, ocr_check, reread, classify], ids=lambda m: m.__name__)
def test_every_field_reason_code_has_a_text(module):
    assert _field_codes(module) - set(REASON_TEXTS) == set()


def test_texts_are_hebrew():
    assert all(any(ord(char) in HEBREW_LETTERS for char in text) for text in REASON_TEXTS.values())
    assert {NOT_FOUND, OPTIONAL_MISSING, SINGLE_READING} <= set(REASON_TEXTS)


def test_explain_keeps_order():
    assert explain([reread.REREAD_DISAGREES, NOT_FOUND]) == [REASON_TEXTS[reread.REREAD_DISAGREES],
                                                            REASON_TEXTS[NOT_FOUND]]


def test_sender_candidate_names_the_listed_sender_and_score():
    assert explain([f"{classify.SENDER_CANDIDATE_PREFIX}מכבי=91"]) == ["מועמד אפשרי: מכבי (התאמה 91)"]


def test_unknown_code_is_shown_as_it_is():
    assert explain(["some_new_code"]) == ["some_new_code"]
