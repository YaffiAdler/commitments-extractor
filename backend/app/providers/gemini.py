"""Extraction through the Gemini API (vision).

Structured output is obtained with ``responseMimeType: application/json`` and
``responseJsonSchema`` set to the shared output schema (``common.py``). Error
messages carry the HTTP status or the API's finish/block reason only, never
response bodies or field values. Overload (503) and rate limit (429) are
retried here; a timeout or connection error raises ``RetryableProviderError``
for the caller to retry (``app/pipeline/calls.py``).
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
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
from app.providers.diagnostics import exception_chain, inspect_base64_image
from app.providers.prompts import Prompts
from app.schemas import FormClassification, FormReading, FormType, NumberReading, PageRelation

logger = logging.getLogger(__name__)

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


def _image_parts(images: list[np.ndarray]) -> list[dict[str, Any]]:
    return [{"inlineData": {"mimeType": JPEG_MEDIA_TYPE, "data": encode_image(image)}} for image in images]


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
                 sleep: Callable[[float], None] = time.sleep, prompts: Prompts | None = None,
                 debug: bool = False) -> None:
        self._model = model
        self._url = API_URL_TEMPLATE.format(model=model)
        self._client = client or httpx.Client(timeout=timeout_s)
        self._sleep = sleep
        self._prompts = prompts or Prompts.load()
        self._debug = debug

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> FormReading:
        data = self._generate_json([p.image for p in pages], self._prompts.extract(form_type), FORM_READING_SCHEMA)
        return form_reading_from_output(data)

    def read_number(self, crop: np.ndarray) -> NumberReading:
        return number_from_output(self._generate_json([crop], self._prompts.read_number, NUMBER_SCHEMA))

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> FormClassification:
        data = self._generate_json([p.image for p in pages], classify_instruction(form_types),
                                   form_classification_schema(form_types))
        return form_classification_from_output(data, form_types)

    def compare_pages(self, page_a: PageImage, page_b: PageImage) -> PageRelation:
        return page_relation_from_output(
            self._generate_json([page_a.image, page_b.image], COMPARE_PAGES_INSTRUCTION, PAGE_RELATION_SCHEMA))

    def _generate_json(self, images: list[np.ndarray], instruction: str, schema: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise ProviderError(f"{API_KEY_ENV} is not set")
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [*_image_parts(images), {"text": instruction}]}],
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
            response = self._post(body, headers, attempt + 1)
            if response.status_code not in RETRYABLE_STATUSES or attempt == MAX_ATTEMPTS - 1:
                break
            self._sleep(RETRY_BASE_DELAY_S * 2 ** attempt)
        if response.status_code == httpx.codes.NOT_FOUND:
            raise ProviderError(f"Gemini API returned HTTP 404: model '{self._model}' not found (set {MODEL_ENV})")
        if response.status_code in RETRYABLE_STATUSES:
            raise RetryableProviderError(f"Gemini API returned HTTP {response.status_code}")
        if response.status_code != httpx.codes.OK:
            raise ProviderError(f"Gemini API returned HTTP {response.status_code}")
        return response

    def _post(self, body: dict[str, Any], headers: dict[str, str], attempt: int) -> httpx.Response:
        started = time.perf_counter()
        try:
            response = self._client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            if self._debug:
                self._log_call(body, attempt, started, None, exc)
            raise RetryableProviderError("Gemini API timed out") from exc
        except httpx.HTTPError as exc:
            if self._debug:
                self._log_call(body, attempt, started, None, exc)
            raise RetryableProviderError(f"Gemini API request failed: {type(exc).__name__}") from exc
        if self._debug:
            self._log_call(body, attempt, started, response, None)
        return response

    def _log_call(self, body: dict[str, Any], attempt: int, started: float,
                  response: httpx.Response | None, error: BaseException | None) -> None:
        """One line per HTTP attempt describing the request and the answer's
        shape (``CE_DEBUG_PROVIDER``); never prompt or answer text, values or the key."""
        elapsed_s = time.perf_counter() - started
        parts = body["contents"][0]["parts"]
        images = [inspect_base64_image(part["inlineData"]["data"], part["inlineData"]["mimeType"])
                  for part in parts if "inlineData" in part]
        request_bytes = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        logger.info(
            "gemini call model=%s attempt=%d/%d max_output_tokens=%d request_bytes=%d images=%d [%s] "
            "elapsed_s=%.2f %s transport_error=%s",
            self._model, attempt, MAX_ATTEMPTS, body["generationConfig"]["maxOutputTokens"], request_bytes,
            len(images), "; ".join(info.describe() for info in images), elapsed_s, _response_shape(response),
            exception_chain(error) if error else "none",
        )


def _response_shape(response: httpx.Response | None) -> str:
    """HTTP status, API error status and detail reasons (quota ids, retry
    delay), finish reason, part kinds and token counts; never answer text."""
    if response is None:
        return "http=None"
    try:
        payload = response.json()
    except ValueError:
        return f"http={response.status_code} body=not-json content_type={response.headers.get('content-type')}"
    if not isinstance(payload, dict):
        return f"http={response.status_code} body=not-an-object"
    error = payload.get("error") if isinstance(payload.get("error"), dict) else None
    if error is not None:
        details = [
            {key: detail[key] for key in ("@type", "reason", "retryDelay") if key in detail}
            | ({"quota_ids": [v.get("quotaId") for v in detail["violations"]]} if "violations" in detail else {})
            for detail in error.get("details") or [] if isinstance(detail, dict)
        ]
        return (f"http={response.status_code} error_status={error.get('status')} details={details} "
                f"retry_after={response.headers.get('retry-after')}")
    candidates = payload.get("candidates") or []
    first = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    part_kinds = ["thought" if part.get("thought") else "text" if "text" in part else "other"
                  for part in first.get("content", {}).get("parts", [])]
    usage = {key: value for key, value in (payload.get("usageMetadata") or {}).items() if key.endswith("Count")}
    return (f"http={response.status_code} response_id={payload.get('responseId')} "
            f"finish_reason={first.get('finishReason')} block_reason="
            f"{(payload.get('promptFeedback') or {}).get('blockReason')} parts={part_kinds} usage={usage}")
