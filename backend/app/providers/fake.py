"""A provider that returns fixed dummy values, for tests and offline demos.

The values are invented and do not belong to any real person.
"""
from __future__ import annotations

from datetime import date

from app.pipeline.ingest import PageImage
from app.schemas import Commitment, Field, FieldSource, FormType

FAKE_FIRST_NAME = "ישראל"
FAKE_LAST_NAME = "ישראלי"
FAKE_ID_NUMBER = "000000018"
FAKE_CENTRAL_NUMBER = "123456"
FAKE_RECEIVED_DATE = date(2025, 1, 1)
FAKE_SENDER = "כללית"
FAKE_COMMITMENT_NUMBER = "0001234567"


def _printed(value: str) -> Field[str]:
    return Field[str](value=value, raw_value=value, source=FieldSource.PRINTED)


class FakeProvider:
    name = "fake"

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> Commitment:
        return Commitment(
            first_name=_printed(FAKE_FIRST_NAME),
            last_name=_printed(FAKE_LAST_NAME),
            id_number=_printed(FAKE_ID_NUMBER),
            central_numbers=[_printed(FAKE_CENTRAL_NUMBER)],
            received_date=Field[date](
                value=FAKE_RECEIVED_DATE,
                raw_value=FAKE_RECEIVED_DATE.strftime("%d/%m/%Y"),
                source=FieldSource.STAMP,
            ),
            sender=_printed(FAKE_SENDER),
            commitment_numbers=[_printed(FAKE_COMMITMENT_NUMBER)],
        )

    def reread_field(self, pages: list[PageImage], field_name: str, form_type: FormType | None) -> list[str]:
        commitment = self.extract_form(pages, form_type)
        value = getattr(commitment, field_name)
        fields = value if isinstance(value, list) else [value]
        return [f.raw_value for f in fields if f.raw_value is not None]

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> str | None:
        return None
