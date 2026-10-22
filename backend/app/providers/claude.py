"""Extraction through the Claude API (vision).

Structured output is obtained by forcing a single tool call whose
``input_schema`` is the shared output schema (``common.py``). Error messages
carry the HTTP status only, never response bodies or field values. Timeouts,
connection errors, rate limits and overloads raise ``RetryableProviderError``;
retrying is the caller's decision (``app/pipeline/calls.py``).
"""
from __future__ import annotations

import json
import logging
import os
import time
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
                 prompts: Prompts | None = None, debug: bool = False) -> None:
        self._model = model
        self._client = client or httpx.Client(timeout=timeout_s)
        self._prompts = prompts or Prompts.load()
        self._debug = debug

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
        started = time.perf_counter()
        response: httpx.Response | None = None
        try:
            response = self._post(body, headers)
            tool_input = self._tool_input(response, tool_name)
        except ProviderError as exc:
            if self._debug:
                self._log_call(body, tool_name, started, response, exc)
            raise
        if self._debug:
            self._log_call(body, tool_name, started, response, None)
        return tool_input

    def _post(self, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        try:
            return self._client.post(API_URL, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("Claude API timed out") from exc
        except httpx.HTTPError as exc:
            raise RetryableProviderError(f"Claude API request failed: {type(exc).__name__}") from exc

    @staticmethod
    def _tool_input(response: httpx.Response, tool_name: str) -> dict[str, Any]:
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

    def _log_call(self, body: dict[str, Any], tool_name: str, started: float,
                  response: httpx.Response | None, error: BaseException | None) -> None:
        """One line describing the request and the answer's shape (``CE_DEBUG_PROVIDER``)."""
        elapsed_s = time.perf_counter() - started
        content = body["messages"][0]["content"]
        images = [inspect_base64_image(block["source"]["data"], block["source"]["media_type"])
                  for block in content if block.get("type") == "image"]
        request_bytes = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        status, request_id, error_type, stop_reason, block_types = _response_shape(response)
        logger.info(
            "claude call tool=%s model=%s max_tokens=%d request_bytes=%d images=%d [%s] elapsed_s=%.2f "
            "http=%s request_id=%s error_type=%s stop_reason=%s blocks=%s outcome=%s",
            tool_name, body["model"], body["max_tokens"], request_bytes, len(images),
            "; ".join(info.describe() for info in images), elapsed_s, status, request_id, error_type,
            stop_reason, block_types, exception_chain(error) if error else "ok",
        )


def _response_shape(response: httpx.Response | None) -> tuple[Any, ...]:
    """HTTP status, request id, API error type, stop reason and content block
    types of a response; never its text."""
    if response is None:
        return None, None, None, None, []
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    blocks = [block.get("type") for block in payload.get("content") or [] if isinstance(block, dict)]
    return (response.status_code, response.headers.get("request-id"), error.get("type"),
            payload.get("stop_reason"), blocks)
