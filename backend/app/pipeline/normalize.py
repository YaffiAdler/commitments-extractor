"""Stage 6: normalize raw values (digits only, dates). The sender is matched
against the closed list in stage 4 (``classify.SenderMatcher``).

Never guess: a value that cannot be normalized stays empty and gets a reason,
and a missing year on a handwritten date is not filled in silently.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from app.schemas import Commitment, Sender

# Day first (Israeli forms), with a written 4-digit year; ISO as a model may answer.
_DAY_MONTH_YEAR = re.compile(r"(\d{1,2})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{4})")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def load_senders(senders_path: Path) -> list[Sender]:
    """Load the closed sender list."""
    data = json.loads(senders_path.read_text(encoding="utf-8"))
    return [Sender.model_validate(entry) for entry in data["senders"]]


def parse_date(text: str) -> date | None:
    """A date written as D.M.YYYY (also with / or -) or YYYY-MM-DD; None for
    anything else — a 2-digit or missing year is never completed."""
    text = text.strip()
    if found := _DAY_MONTH_YEAR.fullmatch(text):
        day, month, year = (int(part) for part in found.groups())
    elif found := _ISO_DATE.fullmatch(text):
        year, month, day = (int(part) for part in found.groups())
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def normalize(commitment: Commitment, senders: list[Sender]) -> Commitment:
    """Return the commitment with normalized values and added reasons."""
    raise NotImplementedError
