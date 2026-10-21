"""What every vision-model provider shares: the prompts, the JSON schemas of
the expected output, page encoding and turning the model output into
``Commitment`` objects.

Providers only differ in how they send a request and where the JSON output
sits in the response.
"""
from __future__ import annotations

import base64
from datetime import date
from typing import Any

import cv2
from pydantic import ValidationError

from app.imaging.geometry import downscale_to_max_edge
from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError
from app.schemas import UNKNOWN_FORM_TYPE, Commitment, Field, FormClassification, FormType, PageRelation

# Preprocess already downscales; this guards the case where that step is
# switched off (minimal data out; the APIs do not use more resolution anyway).
MAX_IMAGE_EDGE_PX = 2000
JPEG_QUALITY = 85
JPEG_MEDIA_TYPE = "image/jpeg"
UNPARSED_DATE_REASON = "unparsed_date"

LIST_FIELDS = ("central_numbers", "commitment_numbers")
SCALAR_STR_FIELDS = ("first_name", "last_name", "id_number", "sender")
DATE_FIELD = "received_date"
SENDER_FIELD = "sender"
READINGS_KEY = "readings"

SYSTEM_PROMPT = (
    "You read scanned Hebrew commitment forms sent to a virology laboratory. "
    "Copy values exactly as they appear. If a field is not on the form or is illegible, "
    "return null (or an empty list). Never guess, never infer a number from a barcode, "
    "and never fill in a missing year."
)

_NULLABLE_STRING = {"type": ["string", "null"]}
_STRING_LIST = {"type": "array", "items": {"type": "string"}}

COMMITMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "first_name": _NULLABLE_STRING,
        "last_name": _NULLABLE_STRING,
        "id_number": {**_NULLABLE_STRING, "description": "Israeli ID number, digits as written"},
        "central_numbers": {**_STRING_LIST, "description": "every number on a 'מרכזי' sticker or handwritten in its place"},
        "received_date": {**_NULLABLE_STRING, "description": "date in the 'התקבל' stamp or handwritten under the central number, YYYY-MM-DD; null if the year is not written"},
        "sender": {**_NULLABLE_STRING, "description": "the body that sent the sample and is billed (HMO or hospital)"},
        "commitment_numbers": {**_STRING_LIST, "description": "every commitment number ('דרישה'; on Leumit forms 'דגימה')"},
    },
    "required": [*SCALAR_STR_FIELDS, DATE_FIELD, *LIST_FIELDS],
}

READINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {READINGS_KEY: _STRING_LIST},
    "required": [READINGS_KEY],
}


PAGE_TYPES = ("start", "continuation", "irrelevant")
PAGE_RELATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_form": {"type": "boolean", "description": "true if page B is part of the same form as page A"},
        "reason": {"type": "string", "description": "short explanation, without copying names or numbers"},
        "page_b_type": {"type": "string", "enum": list(PAGE_TYPES)},
    },
    "required": ["same_form", "reason", "page_b_type"],
}

COMPARE_PAGES_INSTRUCTION = (
    "The two images are adjacent pages A and B of one scanned file. Is page B part of the same "
    "commitment form as page A? page_b_type: 'start' if B is the first page of a new form, "
    "'continuation' if B continues the form on page A (e.g. page 2 of 2, a back side or an "
    "appendix), 'irrelevant' if B is not part of any form (blank page, separator, cover sheet)."
)


def form_classification_schema(form_types: dict[str, FormType]) -> dict[str, Any]:
    """Output schema of ``classify``: a known form type id or "unknown", the
    sender as printed and a short reason."""
    return {
        "type": "object",
        "properties": {
            "form_type": {"type": "string", "enum": [*form_types, UNKNOWN_FORM_TYPE]},
            "sender_raw": {**_NULLABLE_STRING, "description": "the sender's name copied as printed; null if not visible"},
            "evidence": {"type": "string", "description": "short reason (logo, title), without names or numbers"},
        },
        "required": ["form_type", "sender_raw", "evidence"],
    }


def extract_instruction(form_type: FormType | None) -> str:
    hint = f"Expected layout: {form_type.display_name}." if form_type else ""
    return f"Read this form. {hint}"


def reread_instruction(field_name: str) -> str:
    return f"Read only the field '{field_name}' again, carefully."


def classify_instruction(form_types: dict[str, FormType]) -> str:
    """Ask for the layout of the first page and the sender as printed, with
    each type's own sender hint from its YAML."""
    options = "\n".join(f"- {ft.id}: {ft.display_name}. Sender: {ft.fields[SENDER_FIELD].hint}"
                        for ft in form_types.values())
    return (
        "This is the first page of one commitment form. Which form type is it? "
        f"Answer '{UNKNOWN_FORM_TYPE}' if none of the types below matches.\n"
        "sender_raw: the name of the body that sent the sample and is billed, copied as printed: "
        "the HMO on HMO forms, the hospital on hospital forms, the insurer name on insurance screens. "
        "Not the HMO on a hospital admission sticker and not a clinic or department. "
        "null if no sender is visible.\n"
        f"{options}"
    )


def encode_page(page: PageImage) -> str:
    """The page as a base64 JPEG, downscaled to ``MAX_IMAGE_EDGE_PX``."""
    ok, jpeg = cv2.imencode(".jpg", downscale_to_max_edge(page.image, MAX_IMAGE_EDGE_PX),
                            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ProviderError("page image could not be encoded")
    return base64.standard_b64encode(jpeg.tobytes()).decode("ascii")


def _str_field(raw: Any) -> Field[str]:
    text = raw.strip() if isinstance(raw, str) else ""
    return Field[str](value=text, raw_value=text) if text else Field[str]()


def _str_list(raw: Any) -> list[Field[str]]:
    """Non-empty readings, identical repeats removed (order kept)."""
    fields = [_str_field(v) for v in raw] if isinstance(raw, list) else []
    unique = dict.fromkeys(f.value for f in fields if f.value)
    return [_str_field(v) for v in unique]


def _date_field(raw: Any) -> Field[date]:
    if not isinstance(raw, str) or not raw.strip():
        return Field[date]()
    try:
        return Field[date](value=date.fromisoformat(raw.strip()), raw_value=raw.strip())
    except ValueError:
        return Field[date](raw_value=raw.strip(), reasons=[UNPARSED_DATE_REASON])


def commitment_from_output(data: dict[str, Any]) -> Commitment:
    """Build a ``Commitment`` from output matching ``COMMITMENT_SCHEMA``.
    Confidence is left unset (0, needs review) — scoring belongs to the pipeline."""
    return Commitment(
        **{name: _str_field(data.get(name)) for name in SCALAR_STR_FIELDS},
        received_date=_date_field(data.get(DATE_FIELD)),
        **{name: _str_list(data.get(name)) for name in LIST_FIELDS},
    )


def readings_from_output(data: dict[str, Any]) -> list[str]:
    readings = data.get(READINGS_KEY)
    if not isinstance(readings, list):
        return []
    return [r.strip() for r in readings if isinstance(r, str) and r.strip()]


def form_classification_from_output(data: dict[str, Any], form_types: dict[str, FormType]) -> FormClassification:
    """Parse output matching ``form_classification_schema``. A form type that is
    not a known id becomes "unknown"; a blank sender becomes None."""
    try:
        answer = FormClassification.model_validate(data)
    except ValidationError as exc:
        raise ProviderError("classification answer does not match the schema") from exc
    sender_raw = answer.sender_raw.strip() if answer.sender_raw else ""
    form_type = answer.form_type if answer.form_type in form_types else UNKNOWN_FORM_TYPE
    return answer.model_copy(update={"form_type": form_type, "sender_raw": sender_raw or None})


def page_relation_from_output(data: dict[str, Any]) -> PageRelation:
    """Parse output matching ``PAGE_RELATION_SCHEMA``; anything else is a provider error."""
    try:
        return PageRelation.model_validate(data)
    except ValidationError as exc:
        raise ProviderError("page comparison answer does not match the schema") from exc
