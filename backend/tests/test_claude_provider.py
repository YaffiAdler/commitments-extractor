"""ClaudeProvider against a mocked HTTP transport — no network, no real key."""
import json
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError
from app.providers.claude import API_KEY_ENV, ClaudeProvider

DUMMY_INPUT = {
    "first_name": "משה",
    "last_name": "כהן",
    "id_number": "000000018",
    "central_numbers": ["111111", "111111", "222222", " "],
    "received_date": "2025-01-01",
    "sender": "מכבי",
    "commitment_numbers": [],
}


def _page() -> PageImage:
    image = np.zeros((50, 50, 3), np.uint8)
    return PageImage(file_id="a.pdf", page_no=0, image=image, original_path=Path("a.pdf"), original=image)


def _provider(handler) -> ClaudeProvider:
    return ClaudeProvider("test-model", 1.0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def _tool_response(request: httpx.Request) -> httpx.Response:
    assert request.headers["x-api-key"] == "dummy"
    return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "record_commitment", "input": DUMMY_INPUT}]})


def test_extract_form_parses_tool_call(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "dummy")
    commitment = _provider(_tool_response).extract_form([_page()], None)
    assert commitment.id_number.value == "000000018"
    assert [f.value for f in commitment.central_numbers] == ["111111", "222222"]
    assert commitment.received_date.value == date(2025, 1, 1)
    assert commitment.commitment_numbers == []
    assert commitment.id_number.confidence == 0.0  # scoring belongs to the pipeline


def test_missing_key(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(ProviderError):
        _provider(_tool_response).extract_form([_page()], None)


def test_http_error_message_has_status_only(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "dummy")
    provider = _provider(lambda _: httpx.Response(500, text="secret body"))
    with pytest.raises(ProviderError, match="HTTP 500") as info:
        provider.extract_form([_page()], None)
    assert "secret" not in str(info.value)


def test_timeout(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "dummy")

    def slow(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(ProviderError, match="timed out"):
        _provider(slow).extract_form([_page()], None)


def test_compare_pages_sends_both_pages(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "dummy")
    relation = {"same_form": True, "reason": "dummy", "page_b_type": "continuation"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "image", "text"]
        assert body["tool_choice"]["name"] == "record_page_relation"
        return httpx.Response(200, json={"content": [{"type": "tool_use", "name": "record_page_relation",
                                                      "input": relation}]})

    result = _provider(handler).compare_pages(_page(), _page())
    assert (result.same_form, result.page_b_type) == (True, "continuation")
