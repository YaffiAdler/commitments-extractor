"""Extraction through the Claude API (vision).

Structured output is obtained by forcing a single tool call whose
``input_schema`` mirrors the ``Commitment`` fields. Error messages carry the
HTTP status only, never response bodies or field values.
"""
from __future__ import annotations

import base64
import os
from datetime import date
from typing import Any

import cv2
import httpx

from app.imaging.geometry import downscale_to_max_edge
from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError
from app.schemas import Commitment, Field, FormType

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
API_KEY_ENV = "ANTHROPIC_API_KEY"
MAX_TOKENS = 2048
# Preprocess already downscales; this guards the case where that step is
# switched off (minimal data out; the API does not use more resolution anyway).
MAX_IMAGE_EDGE_PX = 2000
JPEG_QUALITY = 85
UNPARSED_DATE_REASON = "unparsed_date"

LIST_FIELDS = ("central_numbers", "commitment_numbers")
SCALAR_STR_FIELDS = ("first_name", "last_name", "id_number", "sender")
DATE_FIELD = "received_date"

_NULLABLE_STRING = {"type": ["string", "null"]}
_STRING_LIST = {"type": "array", "items": {"type": "string"}}

EXTRACT_TOOL = {
    "name": "record_commitment",
    "description": "Record the fields read from one commitment form.",
    "input_schema": {
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
    },
}

SYSTEM_PROMPT = (
    "You read scanned Hebrew commitment forms sent to a virology laboratory. "
    "Copy values exactly as they appear. If a field is not on the form or is illegible, "
    "return null (or an empty list). Never guess, never infer a number from a barcode, "
    "and never fill in a missing year."
)


def _encode_page(page: PageImage) -> str:
    ok, jpeg = cv2.imencode(".jpg", downscale_to_max_edge(page.image, MAX_IMAGE_EDGE_PX),
                            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ProviderError("page image could not be encoded")
    return base64.standard_b64encode(jpeg.tobytes()).decode("ascii")


def _image_blocks(pages: list[PageImage]) -> list[dict[str, Any]]:
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _encode_page(p)}}
        for p in pages
    ]


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


def commitment_from_tool_input(data: dict[str, Any]) -> Commitment:
    """Build a ``Commitment`` from the tool input. Confidence is left unset
    (0, needs review) — scoring belongs to the pipeline."""
    return Commitment(
        **{name: _str_field(data.get(name)) for name in SCALAR_STR_FIELDS},
        received_date=_date_field(data.get(DATE_FIELD)),
        **{name: _str_list(data.get(name)) for name in LIST_FIELDS},
    )


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str, timeout_s: float, client: httpx.Client | None = None) -> None:
        self._model = model
        self._client = client or httpx.Client(timeout=timeout_s)

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> Commitment:
        hint = f"Expected layout: {form_type.display_name}." if form_type else ""
        return commitment_from_tool_input(self._call_tool(pages, f"Read this form. {hint}", EXTRACT_TOOL))

    def reread_field(self, pages: list[PageImage], field_name: str, form_type: FormType | None) -> list[str]:
        tool = {
            "name": "record_readings",
            "description": f"Record every reading of the '{field_name}' field.",
            "input_schema": {"type": "object", "properties": {"readings": _STRING_LIST}, "required": ["readings"]},
        }
        readings = self._call_tool(pages, f"Read only the field '{field_name}' again, carefully.", tool)["readings"]
        return [r.strip() for r in readings if isinstance(r, str) and r.strip()]

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> str | None:
        options = "\n".join(f"- {ft.id}: {ft.display_name}" for ft in form_types.values())
        tool = {
            "name": "record_form_type",
            "description": "Record the matching form type id, or null if none matches.",
            "input_schema": {
                "type": "object",
                "properties": {"form_type_id": {"type": ["string", "null"], "enum": [*form_types, None]}},
                "required": ["form_type_id"],
            },
        }
        form_type_id = self._call_tool(pages, f"Which form type is this?\n{options}", tool)["form_type_id"]
        return form_type_id if form_type_id in form_types else None

    def _call_tool(self, pages: list[PageImage], instruction: str, tool: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise ProviderError(f"{API_KEY_ENV} is not set")
        body = {
            "model": self._model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
            "messages": [{"role": "user", "content": [*_image_blocks(pages), {"type": "text", "text": instruction}]}],
        }
        headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        try:
            response = self._client.post(API_URL, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError("Claude API timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Claude API request failed: {type(exc).__name__}") from exc
        if response.status_code != httpx.codes.OK:
            raise ProviderError(f"Claude API returned HTTP {response.status_code}")
        for block in response.json().get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == tool["name"]:
                return block["input"]
        raise ProviderError("Claude API response has no tool call")
