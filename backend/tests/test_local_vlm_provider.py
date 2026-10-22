"""LocalVlmProvider (Ollama /api/chat) against a mocked HTTP transport."""
import json

import httpx
import numpy as np
import pytest

from form_fakes import blank_page
from app.providers.base import ProviderError, RetryableProviderError
from app.providers.common import FORM_READING_SCHEMA
from app.providers.local_vlm import LocalVlmProvider
from app.providers.prompts import Prompts

ANSWER = {
    "first_name": {"value": None, "bbox": None, "legible": True, "source": "printed", "note": None},
    "last_name": {"value": None, "bbox": None, "legible": True, "source": "printed", "note": None},
    "id_number": {"value": "000000018", "bbox": {"page": 0, "x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.15},
                  "legible": True, "source": "printed", "note": None},
    "central_numbers": [],
    "received_date": {"value": None, "bbox": None, "legible": True, "source": "stamp", "note": None},
    "sender": {"value": None, "bbox": None, "legible": True, "source": "printed", "note": None},
    "commitment_numbers": [],
}


def _provider(handler) -> LocalVlmProvider:
    return LocalVlmProvider("http://vlm.local:11434/", "test-model", 1.0,
                            client=httpx.Client(transport=httpx.MockTransport(handler)))


def _chat(data) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(data)}, "done": True})


def test_extract_form_sends_schema_prompt_and_images():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert (body["model"], body["stream"], body["format"]) == ("test-model", False, FORM_READING_SCHEMA)
        user = body["messages"][-1]
        assert user["content"] == Prompts.load().extract(None) and len(user["images"]) == 1
        return _chat(ANSWER)

    reading = _provider(handler).extract_form([blank_page()], None)
    assert reading.id_number.value == "000000018" and reading.id_number.bbox.x1 == 0.3


def test_read_number():
    assert _provider(lambda _: _chat({"value": "42"})).read_number(np.zeros((10, 30, 3), np.uint8)).value == "42"


def test_errors_carry_no_content():
    with pytest.raises(ProviderError, match="not valid JSON") as info:
        _provider(lambda _: httpx.Response(200, json={"message": {"content": "secret {"}})).extract_form(
            [blank_page()], None)
    assert "secret" not in str(info.value)
    with pytest.raises(RetryableProviderError, match="HTTP 503"):
        _provider(lambda _: httpx.Response(503)).extract_form([blank_page()], None)
    with pytest.raises(ProviderError, match="HTTP 404"):
        _provider(lambda _: httpx.Response(404, text="secret")).extract_form([blank_page()], None)


def test_timeout_is_retryable():
    def slow(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(RetryableProviderError, match="timed out"):
        _provider(slow).read_number(np.zeros((10, 30, 3), np.uint8))
