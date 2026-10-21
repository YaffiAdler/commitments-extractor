"""Extraction through the Gemini API (vision).

Structured output is obtained with ``responseMimeType: application/json`` and
``responseJsonSchema`` set to the shared output schema (``common.py``). Error
messages carry the HTTP status or the API's finish/block reason only, never
response bodies or field values.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
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

API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
API_KEY_ENV = "GEMINI_API_KEY"
API_KEY_HEADER = "x-goog-api-key"
MODEL_ENV = "CE_GEMINI_MODEL"
JSON_MIME_TYPE = "application/json"
# Thinking models count their reasoning in this budget, so it is set well
# above the size of the JSON answer.
MAX_OUTPUT_TOKENS = 8192
FINISH_REASON_STOP = "STOP"
# Overload (503) and rate limit (429) are usually short spikes, so the request
# is repeated with a growing wait before the job is failed.
RETRYABLE_STATUSES = frozenset({httpx.codes.TOO_MANY_REQUESTS, httpx.codes.SERVICE_UNAVAILABLE})
MAX_ATTEMPTS = 4
RETRY_BASE_DELAY_S = 2.0


def _image_parts(pages: list[PageImage]) -> list[dict[str, Any]]:
    return [{"inlineData": {"mimeType": JPEG_MEDIA_TYPE, "data": encode_page(p)}} for p in pages]


def _output_json(payload: dict[str, Any]) -> dict[str, Any]:
    """The JSON object in the first candidate of a ``generateContent`` response."""
    candidates = payload.get("candidates") or []
    if not candidates:
        block_reason = payload.get("promptFeedback", {}).get("blockReason", "unknown")
        raise ProviderError(f"Gemini API returned no answer (block reason: {block_reason})")
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason", FINISH_REASON_STOP)
    if finish_reason != FINISH_REASON_STOP:
        raise ProviderError(f"Gemini API answer is incomplete (finish reason: {finish_reason})")
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("Gemini API answer is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ProviderError("Gemini API answer is not a JSON object")
    return data


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str, timeout_s: float, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._model = model
        self._url = API_URL_TEMPLATE.format(model=model)
        self._client = client or httpx.Client(timeout=timeout_s)
        self._sleep = sleep

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> Commitment:
        return commitment_from_output(self._generate_json(pages, extract_instruction(form_type), COMMITMENT_SCHEMA))

    def reread_field(self, pages: list[PageImage], field_name: str, form_type: FormType | None) -> list[str]:
        return readings_from_output(self._generate_json(pages, reread_instruction(field_name), READINGS_SCHEMA))

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> str | None:
        data = self._generate_json(pages, classify_instruction(form_types), form_type_schema(form_types))
        return form_type_from_output(data, form_types)

    def compare_pages(self, page_a: PageImage, page_b: PageImage) -> PageRelation:
        return page_relation_from_output(
            self._generate_json([page_a, page_b], COMPARE_PAGES_INSTRUCTION, PAGE_RELATION_SCHEMA))

    def _generate_json(self, pages: list[PageImage], instruction: str, schema: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise ProviderError(f"{API_KEY_ENV} is not set")
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [*_image_parts(pages), {"text": instruction}]}],
            "generationConfig": {
                "responseMimeType": JSON_MIME_TYPE,
                "responseJsonSchema": schema,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
            },
        }
        headers = {API_KEY_HEADER: api_key, "content-type": JSON_MIME_TYPE}
        return _output_json(self._post_with_retry(body, headers).json())

    def _post_with_retry(self, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        for attempt in range(MAX_ATTEMPTS):
            response = self._post(body, headers)
            if response.status_code not in RETRYABLE_STATUSES or attempt == MAX_ATTEMPTS - 1:
                break
            self._sleep(RETRY_BASE_DELAY_S * 2 ** attempt)
        if response.status_code == httpx.codes.NOT_FOUND:
            raise ProviderError(f"Gemini API returned HTTP 404: model '{self._model}' not found (set {MODEL_ENV})")
        if response.status_code != httpx.codes.OK:
            raise ProviderError(f"Gemini API returned HTTP {response.status_code}")
        return response

    def _post(self, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        try:
            return self._client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError("Gemini API timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gemini API request failed: {type(exc).__name__}") from exc
