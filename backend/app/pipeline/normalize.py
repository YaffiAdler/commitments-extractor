"""Stage 6: normalize raw values and validate them. The sender is matched
against the closed list in stage 4 (``classify.SenderMatcher``).

Every ``normalize_*`` function is pure: it takes the text as read and returns
a ``Normalized`` with the uniform value (or None), the original text
untouched in ``raw_value`` and reason codes for anything noteworthy. Every
``check_*`` function is pure too and returns a ``Check``: ``ok``,
``soft_fail`` (suspicious, the value is kept) or ``hard_fail`` (the value
cannot be right as it stands; it is still kept for the reviewer).

Never guess: a value that cannot be normalized stays empty with a reason, a
missing year is not filled in, a day/month that look swapped are not swapped,
and Hebrew letters are not read as numerals. A two-digit year is expanded
into 2000-2099 and always flagged.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Callable, Generic, Optional, TypeVar

from app.schemas import CENTRAL_DUPLICATE, NOT_FOUND, Commitment, Field, FormGroup, FormType, Sender

T = TypeVar("T")

ID_LENGTH = 9
# Only IDs that lost their leading zeros (7-8 digits) are padded; other lengths are left as read.
ID_PAD_MIN_LENGTH = 7
WINDOW_START_YEAR = 2000  # two-digit years map into 2000-2099; older dates are suspicious
CENTURY = 100
MONTHS_IN_YEAR = 12

# Reason codes added to ``Field.reasons``.
UNPARSED_DATE = "unparsed_date"
ID_NOT_DIGITS = "id_not_digits"
ID_PADDED = "id_padded"
ID_LENGTH_INVALID = "id_length_invalid"
ID_CHECKSUM_FAILED = "id_checksum_failed"
CENTRAL_NOT_DIGITS = "central_not_digits"
DATE_YEAR_MISSING = "date_year_missing"
DATE_TWO_DIGIT_YEAR = "date_two_digit_year"
DATE_DAY_MONTH_SWAPPED = "date_day_month_swapped"
DATE_INVALID = "date_invalid"
DATE_IN_FUTURE = "date_in_future"
DATE_BEFORE_WINDOW = "date_before_window"
COMMITMENT_PATTERN_MISMATCH = "commitment_pattern_mismatch"

# Bidi marks and zero-width characters that PDFs and models leave inside numbers.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\u061c\ufeff]")
# Hyphen-minus, Unicode dashes, maqaf, minus sign.
_DASHES = re.compile("[\\-\u2010-\u2015\u05be\u2212]")
_NUMBER_SEPARATORS = re.compile(r"[\s./]")
_SPACES = re.compile(r"\s+")

_DATE_SEPARATOR = r"\s*[./\-]\s*"
_DAY_MONTH_YEAR = re.compile(rf"([0-9]{{1,2}}){_DATE_SEPARATOR}([0-9]{{1,2}}){_DATE_SEPARATOR}([0-9]{{4}}|[0-9]{{2}})")
_DAY_MONTH = re.compile(rf"([0-9]{{1,2}}){_DATE_SEPARATOR}([0-9]{{1,2}})")
_ISO_DATE = re.compile("([0-9]{4})-([0-9]{2})-([0-9]{2})")
# Day first (Israeli forms), with a written 4-digit year; ISO as a model may answer.
_STRICT_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{4})")


class CheckStatus(str, Enum):
    OK = "ok"
    SOFT_FAIL = "soft_fail"
    HARD_FAIL = "hard_fail"


@dataclass(frozen=True)
class Check:
    """The outcome of one validation rule; ``reason`` is set unless ``ok``."""

    status: CheckStatus
    reason: Optional[str] = None


PASSED = Check(CheckStatus.OK)


@dataclass(frozen=True)
class Normalized(Generic[T]):
    """A normalized value, the exact input it came from and reason codes."""

    value: Optional[T]
    raw_value: Optional[str]
    reasons: tuple[str, ...] = field(default=())


def load_senders(senders_path: Path) -> list[Sender]:
    """Load the closed sender list."""
    data = json.loads(senders_path.read_text(encoding="utf-8"))
    return [Sender.model_validate(entry) for entry in data["senders"]]


def parse_date(text: str) -> date | None:
    """A date written as D.M.YYYY (also with / or -) or YYYY-MM-DD; None for
    anything else — a 2-digit or missing year is never completed. Used by
    stage 5; stage 6 re-reads the text with ``normalize_date``."""
    text = text.strip()
    if found := _STRICT_DAY_MONTH_YEAR.fullmatch(text):
        day, month, year = (int(part) for part in found.groups())
    elif found := _ISO_DATE.fullmatch(text):
        year, month, day = (int(part) for part in found.groups())
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --- Normalization ---


def to_ascii_digits(text: str) -> str:
    """Every Unicode decimal digit (Arabic-Indic, Persian, full-width ...) as
    its ASCII digit; everything else unchanged."""
    return "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in text)


def _clean_number(text: str) -> str:
    """Without invisible marks, dashes and whitespace, with ASCII digits."""
    return _SPACES.sub("", _DASHES.sub("", _INVISIBLE.sub("", to_ascii_digits(text))))


def normalize_id(raw: str | None) -> Normalized[str]:
    """Digits only, padded with leading zeros to 9 digits when 7-8 were read
    (``id_padded``). Other lengths are kept as read (``check_id_length``
    rejects them); anything other than digits gives no value (``id_not_digits``)."""
    cleaned = _clean_number(raw or "")
    if not cleaned:
        return Normalized(None, raw)
    if not cleaned.isascii() or not cleaned.isdigit():
        return Normalized(None, raw, (ID_NOT_DIGITS,))
    if ID_PAD_MIN_LENGTH <= len(cleaned) < ID_LENGTH:
        return Normalized(cleaned.zfill(ID_LENGTH), raw, (ID_PADDED,))
    return Normalized(cleaned, raw)


def normalize_central(raw: str | None) -> Normalized[str]:
    """Digits only, leading zeros kept. Spaces, dashes, dots and slashes
    between digit groups are dropped; any other character gives no value
    (``central_not_digits``) rather than a number with parts thrown away."""
    cleaned = _NUMBER_SEPARATORS.sub("", _clean_number(raw or ""))
    if not cleaned:
        return Normalized(None, raw)
    if not cleaned.isascii() or not cleaned.isdigit():
        return Normalized(None, raw, (CENTRAL_NOT_DIGITS,))
    return Normalized(cleaned, raw)


def normalize_commitment_number(raw: str | None) -> Normalized[str]:
    """Invisible marks removed, ASCII digits, whitespace collapsed. Separators
    are kept, because some forms print a number with a branch ("12/345")."""
    cleaned = _SPACES.sub(" ", _INVISIBLE.sub("", to_ascii_digits(raw or ""))).strip()
    return Normalized(cleaned or None, raw)


def normalize_name(raw: str | None) -> Normalized[str]:
    """Runs of whitespace become one space and the ends are trimmed; nothing
    else is touched — names are never corrected."""
    cleaned = _SPACES.sub(" ", raw or "").strip()
    return Normalized(cleaned or None, raw)


def normalize_date(raw: str | None) -> Normalized[date]:
    """A day-first date (DD/MM/YYYY, DD.MM.YY, DD-MM-YYYY, any of the three
    separators) or YYYY-MM-DD. A two-digit year goes into 2000-2099
    (``date_two_digit_year``). No value for: a missing year
    (``date_year_missing``), a month above 12 where the day could be the month
    (``date_day_month_swapped`` — not swapped), an impossible date
    (``date_invalid``) or any other text (``unparsed_date``)."""
    text = _INVISIBLE.sub("", to_ascii_digits(raw or "")).strip()
    if not text:
        return Normalized(None, raw)
    reasons: list[str] = []
    if found := _DAY_MONTH_YEAR.fullmatch(text):
        day_text, month_text, year_text = found.groups()
        year = int(year_text)
        if len(year_text) == 2:
            year += WINDOW_START_YEAR
            reasons.append(DATE_TWO_DIGIT_YEAR)
    elif found := _ISO_DATE.fullmatch(text):
        year_text, month_text, day_text = found.groups()
        year = int(year_text)
    elif _DAY_MONTH.fullmatch(text):
        return Normalized(None, raw, (DATE_YEAR_MISSING,))
    else:
        return Normalized(None, raw, (UNPARSED_DATE,))
    day, month = int(day_text), int(month_text)
    if month > MONTHS_IN_YEAR and 1 <= day <= MONTHS_IN_YEAR:
        return Normalized(None, raw, (*reasons, DATE_DAY_MONTH_SWAPPED))
    try:
        return Normalized(date(year, month, day), raw, tuple(reasons))
    except ValueError:
        return Normalized(None, raw, (*reasons, DATE_INVALID))


# --- Validation ---


def check_id_length(value: str | None) -> Check:
    """Hard fail for an ID that is not 9 digits after normalization."""
    if value is None or len(value) == ID_LENGTH:
        return PASSED
    return Check(CheckStatus.HARD_FAIL, ID_LENGTH_INVALID)


def id_checksum_ok(digits: str) -> bool:
    """The Israeli ID check digit: weights 1,2,1,2...; digits of a product
    above 9 are summed; the total must divide by 10."""
    total = 0
    for index, char in enumerate(digits):
        product = int(char) * (1 + index % 2)
        total += product - 9 if product > 9 else product
    return total % 10 == 0


def check_id_checksum(value: str | None) -> Check:
    """Soft fail only (the sample details were altered, and a misread digit is
    for a reviewer to judge). Not evaluated unless the value is 9 ASCII
    digits — the length rule reports other lengths."""
    if value is None or len(value) != ID_LENGTH or not (value.isascii() and value.isdigit()):
        return PASSED
    return PASSED if id_checksum_ok(value) else Check(CheckStatus.SOFT_FAIL, ID_CHECKSUM_FAILED)


def check_date_range(value: date | None, today: date) -> Check:
    """Soft fail for a received date after ``today`` or before the window."""
    if value is None:
        return PASSED
    if value > today:
        return Check(CheckStatus.SOFT_FAIL, DATE_IN_FUTURE)
    if value.year < WINDOW_START_YEAR:
        return Check(CheckStatus.SOFT_FAIL, DATE_BEFORE_WINDOW)
    return PASSED


def check_commitment_pattern(value: str | None, pattern: str | None) -> Check:
    """Soft fail for a commitment number that does not fully match the form
    type's ``pattern`` (patterns come from few samples, so a mismatch is a
    suspicion, not a proof). No pattern or no value: ok."""
    if value is None or pattern is None or re.fullmatch(pattern, value):
        return PASSED
    return Check(CheckStatus.SOFT_FAIL, COMMITMENT_PATTERN_MISMATCH)


def duplicate_central_groups(groups: list[FormGroup]) -> set[str]:
    """Ids of the forms that share a central number with another form."""
    owners: dict[str, set[str]] = defaultdict(set)
    for group in groups:
        for central in group.commitment.central_numbers:
            if central.value is not None:
                owners[central.value].add(group.id)
    return {group_id for ids in owners.values() if len(ids) > 1 for group_id in ids}


def flag_duplicate_centrals(groups: list[FormGroup]) -> None:
    """Add ``central_duplicate`` to every form returned by ``duplicate_central_groups``."""
    duplicates = duplicate_central_groups(groups)
    for group in groups:
        if group.id in duplicates and CENTRAL_DUPLICATE not in group.form_reasons:
            group.form_reasons.append(CENTRAL_DUPLICATE)


# --- Applying both to a Commitment ---


def normalize(commitment: Commitment, form_type: FormType | None, today: date) -> Commitment:
    """A copy of ``commitment`` with normalized values and the reasons of every
    rule that did not pass. Each field is normalized from its ``raw_value``
    (the text as read), which is kept. The sender is left as matched."""
    pattern = form_type.fields["commitment_numbers"].pattern if form_type else None
    id_number = _apply(commitment.id_number, normalize_id)
    received_date = _apply(commitment.received_date, normalize_date, drop=UNPARSED_DATE)
    return commitment.model_copy(update={
        "first_name": _apply(commitment.first_name, normalize_name),
        "last_name": _apply(commitment.last_name, normalize_name),
        "id_number": _with_checks(id_number, check_id_length(id_number.value), check_id_checksum(id_number.value)),
        "central_numbers": _dedupe([_apply(f, normalize_central) for f in commitment.central_numbers]),
        "received_date": _with_checks(received_date, check_date_range(received_date.value, today)),
        "commitment_numbers": _dedupe([
            _with_checks(number, check_commitment_pattern(number.value, pattern))
            for number in (_apply(f, normalize_commitment_number) for f in commitment.commitment_numbers)
        ]),
    }, deep=True)


def _apply(field_: Field, normalizer: Callable[[str | None], Normalized], drop: str | None = None) -> Field:
    """``field_`` with the normalized value and reasons. A field without
    text is left as it is (missing, or an illegible item kept as a placeholder);
    ``drop`` is a reason of an earlier stage that normalization re-decides.
    ``not_found`` is re-decided too: the ``Field`` validator adds it back if
    the value is still empty."""
    if field_.raw_value is None:
        return field_
    result = normalizer(field_.raw_value)
    reasons = [reason for reason in field_.reasons if reason not in (drop, NOT_FOUND)]
    reasons += [reason for reason in result.reasons if reason not in reasons]
    return field_.revalidated(value=result.value, reasons=reasons)


def _with_checks(field_: Field, *checks: Check) -> Field:
    reasons = list(field_.reasons)
    reasons += [check.reason for check in checks if check.reason and check.reason not in reasons]
    return field_.revalidated(reasons=reasons)


def _dedupe(fields: list[Field[str]]) -> list[Field[str]]:
    """Items whose values became identical after normalization are kept once
    (the first); items without a value are all kept."""
    kept, seen = [], set()
    for item in fields:
        if item.value is not None and item.value in seen:
            continue
        seen.add(item.value)
        kept.append(item)
    return kept
