"""Extraction through the Claude API (vision).

Structured output is obtained by forcing a single tool call whose
``input_schema`` is the shared output schema (``common.py``). Error messages
carry the HTTP status only, never response bodies or field values.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError
from app.providers.common import (
    COMMITMENT_SCHEMA,
    COMPARE_PAGES_INSTRUCTION,
    JPEG_MEDIA_TYPE,
    PAGE_RELATION_SCHEMA,
    READINGS_SCHEMA,
    SYSTEM_PROMPT,
    classify_instruction,
    commitment_from_output,
    encode_page,
    extract_instruction,
    form_type_from_output,
    form_type_schema,
    page_relation_from_output,
    readings_from_output,
    reread_instruction,
)
from app.schemas import Commitment, FormType, PageRelation

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
API_KEY_ENV = "ANTHROPIC_API_KEY"
MAX_TOKENS = 2048


def _image_blocks(pages: list[PageImage]) -> list[dict[str, Any]]:
    return [
        {"type": "image", "source": {"type": "base64", "media_type": JPEG_MEDIA_TYPE, "data": encode_page(p)}}
        for p in pages
    ]


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str, timeout_s: float, client: httpx.Client | None = None) -> None:
        self._model = model
        self._client = client or httpx.Client(timeout=timeout_s)

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> Commitment:
        data = self._call_tool(pages, extract_instruction(form_type), "record_commitment",
                               "Record the fields read from one commitment form.", COMMITMENT_SCHEMA)
        return commitment_from_output(data)

    def reread_field(self, pages: list[PageImage], field_name: str, form_type: FormType | None) -> list[str]:
        data = self._call_tool(pages, reread_instruction(field_name), "record_readings",
                               f"Record every reading of the '{field_name}' field.", READINGS_SCHEMA)
        return readings_from_output(data)

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> str | None:
        data = self._call_tool(pages, classify_instruction(form_types), "record_form_type",
                               "Record the matching form type id, or null if none matches.",
                               form_type_schema(form_types))
        return form_type_from_output(data, form_types)

    def compare_pages(self, page_a: PageImage, page_b: PageImage) -> PageRelation:
        data = self._call_tool([page_a, page_b], COMPARE_PAGES_INSTRUCTION, "record_page_relation",
                               "Record whether page B belongs to the same form as page A.", PAGE_RELATION_SCHEMA)
        return page_relation_from_output(data)

    def _call_tool(self, pages: list[PageImage], instruction: str, tool_name: str,
                   tool_description: str, schema: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise ProviderError(f"{API_KEY_ENV} is not set")
        body = {
            "model": self._model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "tools": [{"name": tool_name, "description": tool_description, "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": tool_name},
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
            if block.get("type") == "tool_use" and block.get("name") == tool_name:
                return block["input"]
        raise ProviderError("Claude API response has no tool call")
