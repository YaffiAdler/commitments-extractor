"""Extraction through a locally hosted vision-language model, so patient data
does not leave the lab's network.

Talks to an Ollama server (``POST {base_url}/api/chat``, no streaming) and
gets structured output by passing the shared JSON schema (``common.py``) in
``format``. Any vision model the server has pulled can be used
(``CE_LOCAL_VLM_MODEL``). Error messages carry the HTTP status only.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import numpy as np

from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError, RetryableProviderError
from app.providers.common import (
    COMPARE_PAGES_INSTRUCTION,
    FORM_READING_SCHEMA,
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

CHAT_PATH = "/api/chat"
# Deterministic decoding: a reading should not change from one run to the next.
TEMPERATURE = 0
RETRYABLE_STATUSES = frozenset({httpx.codes.TOO_MANY_REQUESTS, httpx.codes.SERVICE_UNAVAILABLE})


class LocalVlmProvider:
    name = "local_vlm"

    def __init__(self, base_url: str, model: str, timeout_s: float, client: httpx.Client | None = None,
                 prompts: Prompts | None = None) -> None:
        self._url = base_url.rstrip("/") + CHAT_PATH
        self._model = model
        self._client = client or httpx.Client(timeout=timeout_s)
        self._prompts = prompts or Prompts.load()

    def extract_form(self, pages: list[PageImage], form_type: FormType | None) -> FormReading:
        data = self._chat_json([p.image for p in pages], self._prompts.extract(form_type), FORM_READING_SCHEMA)
        return form_reading_from_output(data)

    def read_number(self, crop: np.ndarray) -> NumberReading:
        return number_from_output(self._chat_json([crop], self._prompts.read_number, NUMBER_SCHEMA))

    def classify(self, pages: list[PageImage], form_types: dict[str, FormType]) -> FormClassification:
        data = self._chat_json([p.image for p in pages], classify_instruction(form_types),
                               form_classification_schema(form_types))
        return form_classification_from_output(data, form_types)

    def compare_pages(self, page_a: PageImage, page_b: PageImage) -> PageRelation:
        return page_relation_from_output(
            self._chat_json([page_a.image, page_b.image], COMPARE_PAGES_INSTRUCTION, PAGE_RELATION_SCHEMA))

    def _chat_json(self, images: list[np.ndarray], instruction: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self._model,
            "stream": False,
            "format": schema,
            "options": {"temperature": TEMPERATURE},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": instruction, "images": [encode_image(image) for image in images]},
            ],
        }
        try:
            response = self._client.post(self._url, json=body)
        except httpx.TimeoutException as exc:
            raise RetryableProviderError("local VLM timed out") from exc
        except httpx.HTTPError as exc:
            raise RetryableProviderError(f"local VLM request failed: {type(exc).__name__}") from exc
        if response.status_code in RETRYABLE_STATUSES:
            raise RetryableProviderError(f"local VLM returned HTTP {response.status_code}")
        if response.status_code != httpx.codes.OK:
            raise ProviderError(f"local VLM returned HTTP {response.status_code}")
        try:
            data = json.loads(response.json()["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ProviderError("local VLM answer is not valid JSON") from None
        if not isinstance(data, dict):
            raise ProviderError("local VLM answer is not a JSON object")
        return data
