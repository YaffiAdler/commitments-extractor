"""A provider that returns fixed dummy values, for tests and offline demos.

The values are invented and do not belong to any real person. Tests can pass
their own ``FormReading`` (several stickers, nulls, a cut sticker, ...) and
the answers ``read_number`` gives, in call order.
"""
from __future__ import annotations

import threading
from collections.abc import Iterable

import numpy as np

from app.pipeline.ingest import PageImage
from app.schemas import (
    UNKNOWN_FORM_TYPE,
    FieldReading,
    FormClassification,
    FormReading,
    FormType,
    NumberReading,
    PageRelation,
)

FAKE_RELATION_REASON = "fake provider"
FAKE_FIRST_NAME = "ישראל"
FAKE_LAST_NAME = "ישראלי"
FAKE_ID_NUMBER = "000000018"
FAKE_CENTRAL_NUMBER = "123456"
FAKE_RECEIVED_DATE_TEXT = "01.01.2025"
FAKE_SENDER = "כללית"
FAKE_COMMITMENT_NUMBER = "0001234567"
FAKE_FORM_TYPE_ID = "clalit_form17"
FAKE_SENDER_RAW = "שירותי בריאות כללית"
FAKE_EVIDENCE = "fake provider"


def default_reading() -> FormReading:
    """One printed value per field, without boxes (so nothing is cropped)."""
    return FormReading(
        first_name=FieldReading(value=FAKE_FIRST_NAME),
        last_name=FieldReading(value=FAKE_LAST_NAME),
        id_number=FieldReading(value=FAKE_ID_NUMBER),
        central_numbers=[FieldReading(value=FAKE_CENTRAL_NUMBER, source="sticker")],
        received_date=FieldReading(value=FAKE_RECEIVED_DATE_TEXT, source="stamp"),
        sender=FieldReading(value=FAKE_SENDER),
        commitment_numbers=[FieldReading(value=FAKE_COMMITMENT_NUMBER)],
    )


class FakeProvider:
    name = "fake"

    def __init__(self, reading: FormReading | None = None, numbers: Iterable[str | None] = ()) -> None:
        self._reading = reading or default_reading()
        self._numbers = list(numbers)
        self._lock = threading.Lock()  # read_number is called from worker threads

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> FormReading:
        return self._reading.model_copy(deep=True)

    def read_number(self, crop: np.ndarray) -> NumberReading:
        """The next of ``numbers``; None once they are used up."""
        with self._lock:
            return NumberReading(value=self._numbers.pop(0) if self._numbers else None)

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> FormClassification:
        """A Clalit Form 17 when that type is loaded, else "unknown"; the sender
        is an alias of the Clalit entry in senders.json."""
        form_type = FAKE_FORM_TYPE_ID if FAKE_FORM_TYPE_ID in form_types else UNKNOWN_FORM_TYPE
        return FormClassification(form_type=form_type, sender_raw=FAKE_SENDER_RAW, evidence=FAKE_EVIDENCE)

    def compare_pages(self, page_a: PageImage, page_b: PageImage) -> PageRelation:
        """Every page starts its own form (the most common case in the samples)."""
        return PageRelation(same_form=False, reason=FAKE_RELATION_REASON, page_b_type="start")
