"""What every vision-model provider shares: the short instructions, the JSON
schemas of the expected output, image encoding and parsing the model output
into the typed answers of ``ExtractionProvider``. The extraction and
second-look prompts are versioned files (``prompts.py``).

Providers only differ in how they send a request and where the JSON output
sits in the response.
"""
from __future__ import annotations

import base64
from typing import Any, get_args

import cv2
import numpy as np
from pydantic import ValidationError

from app.imaging.geometry import downscale_to_max_edge
from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError
from app.schemas import (
    UNKNOWN_FORM_TYPE,
    FormClassification,
    FormReading,
    FormType,
    NumberReading,
    PageRelation,
    ReadingSource,
)

# Preprocess already downscales; this guards the case where that step is
# switched off (minimal data out; the APIs do not use more resolution anyway).
MAX_IMAGE_EDGE_PX = 2000
JPEG_QUALITY = 85
JPEG_MEDIA_TYPE = "image/jpeg"

LIST_FIELDS = ("central_numbers", "commitment_numbers")
SCALAR_FIELDS = ("first_name", "last_name", "id_number", "received_date", "sender")
SENDER_FIELD = "sender"

SYSTEM_PROMPT = (
    "You read scanned Hebrew commitment forms sent to a virology laboratory. "
    "Copy values exactly as they appear. If a field is not on the form or is illegible, "
    "return null (or an empty list). Never guess, never infer a number from a barcode, "
    "and never fill in a missing year."
)

_NULLABLE_STRING = {"type": ["string", "null"]}
_NUMBER = {"type": "number"}

_RELATIVE_BOX: dict[str, Any] = {
    "type": ["object", "null"],
    "description": "where the value is, as fractions 0..1 of the image width/height; page = 0-based image index",
    "properties": {"page": {"type": "integer"}, "x0": _NUMBER, "y0": _NUMBER, "x1": _NUMBER, "y1": _NUMBER},
    "required": ["page", "x0", "y0", "x1", "y1"],
}


def _reading(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": {**_NULLABLE_STRING, "description": "exactly as written; null if absent or unclear"},
            "bbox": _RELATIVE_BOX,
            "legible": {"type": "boolean", "description": "false if cut off, smudged or covered"},
            "source": {"type": "string", "enum": list(get_args(ReadingSource))},
            "note": {**_NULLABLE_STRING, "description": "only if something is unusual; no names or numbers"},
        },
        "required": ["value", "bbox", "legible", "source", "note"],
    }


def _readings(description: str) -> dict[str, Any]:
    return {"type": "array", "description": description, "items": _reading(description)}


FORM_READING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "first_name": _reading("patient first name"),
        "last_name": _reading("patient last name"),
        "id_number": _reading("Israeli ID number, digits as written"),
        "central_numbers": _readings("every number on a 'מרכזי' sticker or handwritten in its place"),
        "received_date": _reading("date in the 'התקבל' stamp or handwritten under the central number, as written"),
        "sender": _reading("the body that sent the sample and is billed (HMO or hospital)"),
        "commitment_numbers": _readings("every commitment number ('דרישה'; 'דגימה' on Leumit, LabOS and Rambam forms)"),
    },
    "required": [*SCALAR_FIELDS, *LIST_FIELDS],
}

NUMBER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {**_NULLABLE_STRING, "description": "digits only, or null"}},
    "required": ["value"],
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


def encode_image(image: np.ndarray) -> str:
    """An image as a base64 JPEG, downscaled to ``MAX_IMAGE_EDGE_PX``."""
    ok, jpeg = cv2.imencode(".jpg", downscale_to_max_edge(image, MAX_IMAGE_EDGE_PX),
                            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ProviderError("image could not be encoded")
    return base64.standard_b64encode(jpeg.tobytes()).decode("ascii")


def encode_page(page: PageImage) -> str:
    """The page image sent to the model (never the binarized OCR copy)."""
    return encode_image(page.image)


def form_reading_from_output(data: dict[str, Any]) -> FormReading:
    """Parse output matching ``FORM_READING_SCHEMA``. The validation error is
    not chained: its text would quote the values (patient data)."""
    try:
        return FormReading.model_validate(data)
    except ValidationError:
        raise ProviderError("extraction answer does not match the schema") from None


def number_from_output(data: dict[str, Any]) -> NumberReading:
    """Parse output matching ``NUMBER_SCHEMA`` (error not chained, as above)."""
    try:
        return NumberReading.model_validate(data)
    except ValidationError:
        raise ProviderError("number answer does not match the schema") from None


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
