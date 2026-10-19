"""Stage 6: normalize raw values (digits only, dates, sender from the closed list).

Never guess: a value that cannot be normalized stays empty and gets a reason,
and a missing year on a handwritten date is not filled in silently.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.schemas import Commitment, Sender


def load_senders(senders_path: Path) -> list[Sender]:
    """Load the closed sender list."""
    data = json.loads(senders_path.read_text(encoding="utf-8"))
    return [Sender.model_validate(entry) for entry in data["senders"]]


def normalize(commitment: Commitment, senders: list[Sender]) -> Commitment:
    """Return the commitment with normalized values and added reasons."""
    raise NotImplementedError
