"""Stage 6: every normalizer and every rule, pure functions only (dummy values)."""
from datetime import date

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.pipeline.classify import load_form_types
from app.pipeline.normalize import (
    CENTRAL_NOT_DIGITS,
    COMMITMENT_PATTERN_MISMATCH,
    DATE_BEFORE_WINDOW,
    DATE_DAY_MONTH_SWAPPED,
    DATE_IN_FUTURE,
    DATE_INVALID,
    DATE_TWO_DIGIT_YEAR,
    DATE_YEAR_MISSING,
    ID_CHECKSUM_FAILED,
    ID_LENGTH_INVALID,
    ID_NOT_DIGITS,
    ID_PADDED,
    UNPARSED_DATE,
    CheckStatus,
    check_commitment_pattern,
    check_date_range,
    check_id_checksum,
    check_id_length,
    duplicate_central_groups,
    flag_duplicate_centrals,
    id_checksum_ok,
    load_senders,
    normalize,
    normalize_central,
    normalize_commitment_number,
    normalize_date,
    normalize_id,
    normalize_name,
    parse_date,
    to_ascii_digits,
)
from app.schemas import CENTRAL_DUPLICATE, Commitment, Field, FieldSpec, FormGroup, FormType

RLM, LRM, ZWSP, BOM, ALM = "\u200f", "\u200e", "\u200b", "\ufeff", "\u061c"
MAQAF, EN_DASH, MINUS = "\u05be", "\u2013", "\u2212"
TODAY = date(2025, 6, 15)
VALID_ID = "123456782"  # dummy, passes the check digit
INVALID_ID = "123456789"  # dummy, fails the check digit


def test_load_senders_from_a_file(tmp_path):
    path = tmp_path / "senders.json"
    path.write_text('{"senders": [{"canonical": "dummy", "type": "lab"}]}', encoding="utf-8")
    [sender] = load_senders(path)
    assert (sender.canonical, sender.type, sender.aliases) == ("dummy", "lab", [])


# --- parse_date (stage 5, unchanged) ---


@pytest.mark.parametrize("text, expected", [
    ("01.02.2025", date(2025, 2, 1)),
    ("1/2/2025", date(2025, 2, 1)),
    (" 01 - 02 - 2025 ", date(2025, 2, 1)),
    ("2025-02-01", date(2025, 2, 1)),
])
def test_full_dates_day_first(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", ["31.8", "-31.8", "01.02.25", "31.02.2025", "abc", ""])
def test_partial_or_invalid_dates_are_not_completed(text):
    assert parse_date(text) is None


# --- to_ascii_digits ---


@pytest.mark.parametrize("text, expected", [
    ("0123", "0123"),
    ("١٢٣", "123"),        # Arabic-Indic
    ("۴۵۶", "456"),        # Extended Arabic-Indic (Persian)
    ("７８", "78"),               # full-width
    ("a1-٩", "a1-9"),
    ("", ""),
    ("אבג", "אבג"),                       # Hebrew letters are not read as numerals
    ("²", "²"),                 # superscript two is not a decimal digit
])
def test_to_ascii_digits(text, expected):
    assert to_ascii_digits(text) == expected


# --- normalize_id ---


@pytest.mark.parametrize("raw, value, reasons", [
    (VALID_ID, VALID_ID, ()),
    ("123 456 782", VALID_ID, ()),
    ("12345678-2", VALID_ID, ()),
    (f"{RLM}123456782{LRM}", VALID_ID, ()),
    (f"1{ZWSP}2345{ALM}6782{BOM}", VALID_ID, ()),
    (f"12345678{MAQAF}2", VALID_ID, ()),
    (f"12345678{EN_DASH}2", VALID_ID, ()),
    ("١٢٣٤٥٦٧٨٢", VALID_ID, ()),  # Arabic-Indic digits
    ("12345678", "012345678", (ID_PADDED,)),       # 8 digits: lost a leading zero
    ("1234567", "001234567", (ID_PADDED,)),        # 7 digits
    ("123456", "123456", ()),                      # too short to pad; the length rule rejects it
    ("1234567890", "1234567890", ()),              # too long, kept as read
    ("000000018", "000000018", ()),                # leading zeros kept
    ("", None, ()),
    ("   ", None, ()),
    (None, None, ()),
    (f"{RLM}{LRM}", None, ()),
    ("12345א782", None, (ID_NOT_DIGITS,)),         # a letter is not dropped silently
    ("12.345.678", None, (ID_NOT_DIGITS,)),
])
def test_normalize_id(raw, value, reasons):
    result = normalize_id(raw)
    assert (result.value, result.reasons) == (value, reasons)
    assert result.raw_value == raw


# --- normalize_central ---


@pytest.mark.parametrize("raw, value, reasons", [
    ("123456", "123456", ()),
    ("012345", "012345", ()),                      # leading zero kept
    ("000123", "000123", ()),
    ("12 34 56", "123456", ()),
    ("123-456", "123456", ()),
    ("12.34/56", "123456", ()),
    (f"{LRM}123456{RLM}", "123456", ()),
    ("۱۲۳۴۵۶", "123456", ()),
    ("", None, ()),
    (None, None, ()),
    (f"{ZWSP} ", None, ()),
    ("12345X", None, (CENTRAL_NOT_DIGITS,)),
    ("מרכזי 123456", None, (CENTRAL_NOT_DIGITS,)),
])
def test_normalize_central(raw, value, reasons):
    result = normalize_central(raw)
    assert (result.value, result.reasons, result.raw_value) == (value, reasons, raw)


# --- normalize_commitment_number ---


@pytest.mark.parametrize("raw, value", [
    ("0001234567", "0001234567"),
    ("  123456789  ", "123456789"),
    ("12/345678", "12/345678"),                    # branch/number kept as printed
    ("12  /  345", "12 / 345"),
    (f"{RLM}123456789{LRM}", "123456789"),
    ("١٢٣", "123"),
    ("", None),
    (None, None),
    (f"{BOM}", None),
])
def test_normalize_commitment_number(raw, value):
    result = normalize_commitment_number(raw)
    assert (result.value, result.reasons, result.raw_value) == (value, (), raw)


# --- normalize_name ---


@pytest.mark.parametrize("raw, value", [
    ("ישראל", "ישראל"),
    ("  ישראל  ", "ישראל"),
    ("בן  ציון", "בן ציון"),
    ("בן\tציון", "בן ציון"),
    ("בן\n ציון", "בן ציון"),
    ("כהן-לוי", "כהן-לוי"),                       # hyphen in a name is kept
    ("ישראל1", "ישראל1"),                         # not corrected
    ("COHEN  levi", "COHEN levi"),                 # case is not changed
    ("", None),
    ("   ", None),
    (None, None),
])
def test_normalize_name(raw, value):
    result = normalize_name(raw)
    assert (result.value, result.reasons, result.raw_value) == (value, (), raw)


# --- normalize_date ---


@pytest.mark.parametrize("raw, value, reasons", [
    ("15/03/2025", date(2025, 3, 15), ()),
    ("15.03.2025", date(2025, 3, 15), ()),
    ("15-03-2025", date(2025, 3, 15), ()),
    ("5.3.2025", date(2025, 3, 5), ()),
    ("15 -03- 2025", date(2025, 3, 15), ()),       # the "התקבל" stamp layout
    ("2025-03-15", date(2025, 3, 15), ()),         # ISO
    ("15.03.25", date(2025, 3, 15), (DATE_TWO_DIGIT_YEAR,)),
    ("01/01/99", date(2099, 1, 1), (DATE_TWO_DIGIT_YEAR,)),  # window 2000-2099, never 1999
    ("01-01-00", date(2000, 1, 1), (DATE_TWO_DIGIT_YEAR,)),
    (f"{RLM}15.03.2025{LRM}", date(2025, 3, 15), ()),
    ("١٥/٠٣/٢٠٢٥", date(2025, 3, 15), ()),
    ("03/15/2025", None, (DATE_DAY_MONTH_SWAPPED,)),          # month first: not swapped
    ("03/15/25", None, (DATE_TWO_DIGIT_YEAR, DATE_DAY_MONTH_SWAPPED)),
    ("15/13/2025", None, (DATE_INVALID,)),         # month 13 but the day cannot be a month
    ("31.02.2025", None, (DATE_INVALID,)),
    ("00.01.2025", None, (DATE_INVALID,)),
    ("31.8", None, (DATE_YEAR_MISSING,)),          # never completed
    ("15/3", None, (DATE_YEAR_MISSING,)),
    ("15.03.025", None, (UNPARSED_DATE,)),
    ("מרץ 2025", None, (UNPARSED_DATE,)),
    ("", None, ()),
    ("  ", None, ()),
    (None, None, ()),
])
def test_normalize_date(raw, value, reasons):
    result = normalize_date(raw)
    assert (result.value, result.reasons, result.raw_value) == (value, reasons, raw)


# --- check_id_length ---


@pytest.mark.parametrize("value, status, reason", [
    (VALID_ID, CheckStatus.OK, None),
    (INVALID_ID, CheckStatus.OK, None),            # length only
    ("000000018", CheckStatus.OK, None),
    (None, CheckStatus.OK, None),                  # missing is not_found, not a length error
    ("123456", CheckStatus.HARD_FAIL, ID_LENGTH_INVALID),
    ("1", CheckStatus.HARD_FAIL, ID_LENGTH_INVALID),
    ("12345678", CheckStatus.HARD_FAIL, ID_LENGTH_INVALID),   # unpadded input
    ("1234567890", CheckStatus.HARD_FAIL, ID_LENGTH_INVALID),
    ("", CheckStatus.HARD_FAIL, ID_LENGTH_INVALID),
])
def test_check_id_length(value, status, reason):
    check = check_id_length(value)
    assert (check.status, check.reason) == (status, reason)


# --- check digit ---


@pytest.mark.parametrize("digits, ok", [
    (VALID_ID, True),
    ("000000018", True),
    ("000000000", True),
    ("039337423", True),
    (INVALID_ID, False),
    ("000000019", False),
    ("123456783", False),
    ("111111111", False),
])
def test_id_checksum_ok(digits, ok):
    assert id_checksum_ok(digits) is ok


@pytest.mark.parametrize("value, status", [
    (VALID_ID, CheckStatus.OK),
    ("000000018", CheckStatus.OK),
    (INVALID_ID, CheckStatus.SOFT_FAIL),
    ("000000019", CheckStatus.SOFT_FAIL),
    (None, CheckStatus.OK),
    ("12345678", CheckStatus.OK),                  # wrong length: left to the length rule
    ("1234567890", CheckStatus.OK),
    ("12345678X", CheckStatus.OK),
    ("١٢٣٤٥٦٧٨٩", CheckStatus.OK),  # not normalized: not judged
])
def test_check_id_checksum_is_soft_only(value, status):
    check = check_id_checksum(value)
    assert check.status is status and check.status is not CheckStatus.HARD_FAIL
    assert check.reason == (ID_CHECKSUM_FAILED if status is CheckStatus.SOFT_FAIL else None)


# --- check_date_range ---


@pytest.mark.parametrize("value, status, reason", [
    (TODAY, CheckStatus.OK, None),
    (date(2025, 6, 14), CheckStatus.OK, None),
    (date(2000, 1, 1), CheckStatus.OK, None),
    (None, CheckStatus.OK, None),
    (date(2025, 6, 16), CheckStatus.SOFT_FAIL, DATE_IN_FUTURE),
    (date(2099, 1, 1), CheckStatus.SOFT_FAIL, DATE_IN_FUTURE),   # a 2-digit "99" lands here
    (date(1999, 12, 31), CheckStatus.SOFT_FAIL, DATE_BEFORE_WINDOW),
    (date(1950, 5, 5), CheckStatus.SOFT_FAIL, DATE_BEFORE_WINDOW),
])
def test_check_date_range(value, status, reason):
    check = check_date_range(value, TODAY)
    assert (check.status, check.reason) == (status, reason)


# --- check_commitment_pattern ---


NINE_DIGITS = "[0-9]{9}"


@pytest.mark.parametrize("value, pattern, status", [
    ("123456789", NINE_DIGITS, CheckStatus.OK),
    ("000000001", NINE_DIGITS, CheckStatus.OK),
    ("12345678", NINE_DIGITS, CheckStatus.SOFT_FAIL),
    ("1234567890", NINE_DIGITS, CheckStatus.SOFT_FAIL),   # full match, not a prefix
    ("12345678X", NINE_DIGITS, CheckStatus.SOFT_FAIL),
    ("12/34567", r"\d{2}/\d{5,8}", CheckStatus.OK),
    ("anything", None, CheckStatus.OK),                    # no pattern in the YAML
    (None, NINE_DIGITS, CheckStatus.OK),
])
def test_check_commitment_pattern(value, pattern, status):
    check = check_commitment_pattern(value, pattern)
    assert check.status is status
    assert check.reason == (COMMITMENT_PATTERN_MISMATCH if status is CheckStatus.SOFT_FAIL else None)


# --- duplicate central numbers across forms ---


def _group(*centrals: str | None) -> FormGroup:
    fields = [Field[str](value=c, raw_value=c) if c else Field[str](reasons=["illegible"]) for c in centrals]
    return FormGroup(source_file="f.pdf", pages=[0], commitment=Commitment(central_numbers=fields))


@pytest.mark.parametrize("centrals, duplicated", [
    ([], set()),
    ([("111111",)], set()),
    ([("111111",), ("222222",)], set()),
    ([("111111",), ("111111",)], {0, 1}),
    ([("111111", "222222"), ("333333",), ("222222",)], {0, 2}),
    ([("111111",), ("111111",), ("111111",)], {0, 1, 2}),
    ([(None,), (None,)], set()),                           # unreadable stickers are not duplicates
    ([("011111",), ("11111",)], set()),                    # leading zeros make them different
    ([("111111", "111111"), ("222222",)], set()),          # a repeat inside one form is not a duplicate
])
def test_duplicate_central_groups(centrals, duplicated):
    groups = [_group(*c) for c in centrals]
    assert duplicate_central_groups(groups) == {groups[i].id for i in duplicated}


def test_flag_duplicate_centrals_flags_the_forms_once():
    groups = [_group("111111"), _group("111111"), _group("222222")]
    flag_duplicate_centrals(groups)
    flag_duplicate_centrals(groups)
    assert [g.form_reasons for g in groups] == [[CENTRAL_DUPLICATE], [CENTRAL_DUPLICATE], []]
    assert groups[0].needs_review


# --- normalize(commitment): applying both to the model ---


def _read(raw: str | None, *reasons: str):
    return Field[str](value=raw, raw_value=raw, reasons=list(reasons))


def _form_type(pattern: str | None) -> FormType:
    form_type = load_form_types(Settings().form_types_dir)["clalit_form17"]
    spec = form_type.fields["commitment_numbers"].model_copy(update={"pattern": pattern})
    return form_type.model_copy(update={"fields": {**form_type.fields, "commitment_numbers": spec}})


def test_normalize_commitment_keeps_raw_values_and_adds_reasons():
    commitment = Commitment(
        first_name=_read("  בן  ציון "),
        last_name=_read("כהן"),
        id_number=_read(f"{RLM}1234567-8", "handwritten"),
        central_numbers=[_read("012 345"), _read("012-345"), _read("12345X")],
        received_date=Field[date](raw_value="15.03.25", reasons=[UNPARSED_DATE]),
        commitment_numbers=[_read("12345678"), _read(" 123456789 ")],
    )
    result = normalize(commitment, _form_type(NINE_DIGITS), TODAY)

    assert (result.first_name.value, result.first_name.raw_value) == ("בן ציון", "  בן  ציון ")
    assert result.id_number.value == "012345678" and result.id_number.raw_value == f"{RLM}1234567-8"
    assert result.id_number.reasons == ["handwritten", ID_PADDED, ID_CHECKSUM_FAILED]
    assert [f.value for f in result.central_numbers] == ["012345", None]  # equal after normalizing: kept once
    assert result.central_numbers[1].reasons == [CENTRAL_NOT_DIGITS, "not_found"]
    assert result.received_date.value == date(2025, 3, 15)
    assert result.received_date.reasons == [DATE_TWO_DIGIT_YEAR]       # unparsed_date re-decided
    first, second = result.commitment_numbers
    assert COMMITMENT_PATTERN_MISMATCH in first.reasons and COMMITMENT_PATTERN_MISMATCH not in second.reasons
    assert commitment.first_name.value == "  בן  ציון "                  # the input is not changed


def test_normalize_commitment_hard_fail_and_future_date():
    commitment = Commitment(id_number=_read("123456"),
                            received_date=Field[date](value=date(2025, 7, 1), raw_value="01.07.2025"))
    result = normalize(commitment, None, TODAY)
    assert result.id_number.value == "123456" and ID_LENGTH_INVALID in result.id_number.reasons
    assert DATE_IN_FUTURE in result.received_date.reasons and result.received_date.needs_review


def test_normalize_commitment_leaves_missing_fields_and_sender_alone():
    sender = Field[str](value="כללית", raw_value="שירותי בריאות כללית", confidence=1.0, needs_review=False)
    placeholder = Field[str](reasons=["illegible"])
    commitment = Commitment(sender=sender, central_numbers=[placeholder, Field[str](reasons=["illegible"])])
    result = normalize(commitment, None, TODAY)
    assert result.sender == sender
    assert result.id_number.reasons == ["not_found"]
    assert len(result.central_numbers) == 2                             # items without a value all kept


def test_normalize_unreadable_value_becomes_empty_and_flagged():
    commitment = Commitment(received_date=Field[date](raw_value="03/15/2025", reasons=[UNPARSED_DATE]))
    result = normalize(commitment, None, TODAY).received_date
    assert result.value is None and result.raw_value == "03/15/2025" and result.needs_review
    assert result.reasons == [DATE_DAY_MONTH_SWAPPED, "not_found"]


# --- the YAML pattern ---


def test_pattern_is_only_allowed_on_commitment_numbers():
    form_type = load_form_types(Settings().form_types_dir)["clalit_form17"]
    data = form_type.model_dump()
    data["fields"]["id_number"]["pattern"] = "[0-9]{9}"
    with pytest.raises(ValidationError):
        FormType.model_validate(data)


def test_invalid_pattern_is_rejected():
    with pytest.raises(ValidationError):
        FieldSpec(labels=[], hint="", multiple=True, handwritten_possible=False, pattern="[0-9")


@pytest.mark.parametrize("form_type_id", ["leumit_lab_worklist", "rambam_external", "meuhedet_form17",
                                          "clalit_form17", "maccabi_online"])
def test_documented_nine_digit_types_have_a_pattern(form_type_id):
    spec = load_form_types(Settings().form_types_dir)[form_type_id].fields["commitment_numbers"]
    assert spec.pattern == NINE_DIGITS
