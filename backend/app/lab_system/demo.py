"""A stand-in lab system that reads dummy samples from a JSON file (no real connection).

The file (``backend/data/demo_lab_samples.json`` by default) partly matches the
fake provider's dummy data, so a demo shows all three match groups.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.lab_system.base import LabSystemError, ReceivedSample


class _DemoSamplesFile(BaseModel):
    samples: list[ReceivedSample]


class DemoLabConnector:
    name = "demo"

    def __init__(self, path: Path) -> None:
        self._path = path

    def get_received_samples(self, date_from: date | None, date_to: date | None) -> list[ReceivedSample]:
        """Read on every call, as a query to a real system would be."""
        try:
            samples = _DemoSamplesFile.model_validate(json.loads(self._path.read_text(encoding="utf-8"))).samples
        except (OSError, ValueError, ValidationError) as exc:
            raise LabSystemError(f"demo samples unreadable: {type(exc).__name__}") from None
        return [sample for sample in samples
                if (date_from is None or sample.received_date >= date_from)
                and (date_to is None or sample.received_date <= date_to)]
