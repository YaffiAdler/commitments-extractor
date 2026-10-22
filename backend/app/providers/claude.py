"""Extraction through the Claude API (vision).

Structured output is obtained by forcing a single tool call whose
``input_schema`` is the shared output schema (``common.py``). Error messages
carry the HTTP status only, never response bodies or field values. Timeouts,
connection errors, rate limits and overloads raise ``RetryableProviderError``;
retrying is the caller's decision (``app/pipeline/calls.py``).
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import numpy as np

from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError, RetryableProviderError
from app.providers.common import (
    COMPARE_PAGES_INSTRUCTION,
    FORM_READING_SCHEMA,
    JPEG_MEDIA_TYPE,
    NUMBER_SCHEMA,
    PAGE_RELATION_SCHEMA,
    SYSTEM_PROMPT,
    classify_instruction,
    encode_image,
    form_classification_from_output,
    form_classification_schema,
    form_reading_from_output,
    number_from_output,
    page_relation_from_output,
)
from app.providers.prompts import Prompts
from app.schemas import FormClassification, FormReading, FormType, NumberReading, PageRelation

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
API_KEY_ENV = "ANTHROPIC_API_KEY"
# Per-value boxes and notes make the extraction answer several times longer than the values alone.
MAX_TOKENS = 4096
STOP_REASON_MAX_TOKENS = "max_tokens"
OVERLOADED_STATUS = 529  # Anthropic-specific
RETRYABLE_STATUSES = frozenset({
    httpx.codes.REQUEST_TIMEOUT, httpx.codes.TOO_MANY_REQUESTS, httpx.codes.INTERNAL_SERVER_ERROR,
    httpx.codes.BAD_GATEWAY, httpx.codes.SERVICE_UNAVAILABLE, httpx.codes.GATEWAY_TIMEOUT, OVERLOADED_STATUS,
})


def _image_blocks(images: list[np.ndarray]) -> list[dict[str, Any]]:
    return [
        {"type": "image", "source": {"type": "base64", "media_type": JPEG_MEDIA_TYPE, "data": encode_image(image)}}
        for image in images
    ]


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str, timeout_s: float, client: httpx.Client | None = None,
                 prompts: Prompts | None = None) -> None:
        self._model = model
        self._client = client or httpx.Client(timeout=timeout_s)
        self._prompts = prompts or Prompts.load()

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> FormReading:
        data = self._call_tool([p.image for p in pages], self._prompts.extract(form_type), "record_form",
                               "Record every field read from one commitment form.", FORM_READING_SCHEMA)
        return form_reading_from_output(data)

    def read_number(self, crop: np.ndarray) -> NumberReading:
        data = self._call_tool([crop], self._prompts.read_number, "record_number",
                               "Record the number seen in the image, or null.", NUMBER_SCHEMA)
        return number_from_output(data)

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> FormClassification:
        data = self._call_tool([p.image for p in pages], classify_instruction(form_types), "record_form_type",
                               "Record the form type id (or 'unknown') and the sender as printed.",
                               form_classification_schema(form_types))
        return form_classification_from_output(data, form_types)

    def compare_pages(self, page_a: PageImage, page_b: PageImage) -> PageRelation:
        data = self._call_tool([page_a.image, page_b.image], COMPARE_PAGES_INSTRUCTION, "record_page_relation",
                               "Record whether page B belongs to the same form as page A.", PAGE_RELATION_SCHEMA)
        return page_relation_from_output(data)

    def _call_tool(self, images: list[np.ndarray], instruction: str, tool_name: str,
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
            "messages": [{"role": "user", "content": [*_image_blocks(images), {"type": "text", "text": instruction}]}],
        }
        headers = {"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        try:
            response = self._client.post(API_URL, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Claude API timed out") from exc
        except httpx.HTTPError as exc:
            raise RetryableProviderError(f"Claude API request failed: {type(exc).__name__}") from exc
        if response.status_code in RETRYABLE_STATUSES:
            raise RetryableProviderError(f"Claude API returned HTTP {response.status_code}")
        if response.status_code != httpx.codes.OK:
            raise ProviderError(f"Claude API returned HTTP {response.status_code}")
        payload = response.json()
        if payload.get("stop_reason") == STOP_REASON_MAX_TOKENS:
            raise ProviderError("Claude API answer was cut off (max_tokens)")
        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == tool_name:
                return block["input"]
        raise ProviderError("Claude API response has no tool call")
