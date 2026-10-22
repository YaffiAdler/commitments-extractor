"""The interface to the laboratory's sample system (which samples were received, and when).

The pipeline and the API depend only on ``LabSystemConnector``; ``demo.py`` is a
stand-in that reads dummy data from a JSON file. A real connection implements the
same Protocol against the lab system.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ReceivedSample(BaseModel):
    """One sample as the lab system recorded it. ``central_number`` is a string
    (leading zeros matter); ``sender`` is the name as the lab system holds it."""

    model_config = ConfigDict(frozen=True)

    central_number: str
    received_date: date
    sender: str


class LabSystemError(RuntimeError):
    """The lab system could not be queried; the message names the cause, never a value."""


class LabSystemConnector(Protocol):
    name: str

    def get_received_samples(self, date_from: date | None, date_to: date | None) -> list[ReceivedSample]:
        """The samples received between ``date_from`` and ``date_to`` (inclusive; None = open)."""
        ...
