"""GeminiProvider against a mocked HTTP transport — no network, no real key."""
import json
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError
from app.config import Settings
from app.pipeline.classify import load_form_types
from app.providers.gemini import API_KEY_ENV, API_KEY_HEADER, MAX_ATTEMPTS, GeminiProvider

DUMMY_OUTPUT = {
    "first_name": "משה",
    "last_name": "כהן",
    "id_number": "000000018",
    "central_numbers": ["111111"],
    "received_date": "2025-01-01",
    "sender": "מכבי",
    "commitment_numbers": ["0001234567"],
}


def _page() -> PageImage:
    image = np.zeros((50, 50, 3), np.uint8)
    return PageImage(file_id="a.pdf", page_no=0, image=image, original_path=Path("a.pdf"), original=image)


def _provider(handler, sleeps: list[float] | None = None) -> GeminiProvider:
    record = sleeps.append if sleeps is not None else (lambda _: None)
    return GeminiProvider("test-model", 1.0, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=record)


def _answer(data: dict, finish_reason: str = "STOP") -> dict:
    return {"candidates": [{"finishReason": finish_reason,
                            "content": {"parts": [{"text": json.dumps(data, ensure_ascii=False)}]}}]}


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "dummy")


def test_extract_form_sends_schema_and_parses_answer(api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers[API_KEY_HEADER] == "dummy"
        assert request.url.path.endswith("/models/test-model:generateContent")
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert "commitment_numbers" in body["generationConfig"]["responseJsonSchema"]["properties"]
        assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "image/jpeg"
        return httpx.Response(200, json=_answer(DUMMY_OUTPUT))

    commitment = _provider(handler).extract_form([_page()], None)
    assert commitment.id_number.value == "000000018"
    assert commitment.received_date.value == date(2025, 1, 1)
    assert [f.value for f in commitment.commitment_numbers] == ["0001234567"]
    assert commitment.id_number.confidence == 0.0  # scoring belongs to the pipeline


def test_reread_field(api_key):
    provider = _provider(lambda _: httpx.Response(200, json=_answer({"readings": ["123", " "]})))
    assert provider.reread_field([_page()], "id_number", None) == ["123"]


def test_missing_key(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(ProviderError, match=API_KEY_ENV):
        _provider(lambda _: httpx.Response(200, json=_answer(DUMMY_OUTPUT))).extract_form([_page()], None)


def test_http_error_message_has_status_only(api_key):
    provider = _provider(lambda _: httpx.Response(400, text="secret body"))
    with pytest.raises(ProviderError, match="HTTP 400") as info:
        provider.extract_form([_page()], None)
    assert "secret" not in str(info.value)


def test_timeout(api_key):
    def slow(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(ProviderError, match="timed out"):
        _provider(slow).extract_form([_page()], None)


def test_blocked_prompt(api_key):
    provider = _provider(lambda _: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(ProviderError, match="SAFETY"):
        provider.extract_form([_page()], None)


def test_truncated_answer(api_key):
    provider = _provider(lambda _: httpx.Response(200, json=_answer(DUMMY_OUTPUT, finish_reason="MAX_TOKENS")))
    with pytest.raises(ProviderError, match="MAX_TOKENS"):
        provider.extract_form([_page()], None)


def test_invalid_json_message_has_no_content(api_key):
    payload = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "secret {"}]}}]}
    with pytest.raises(ProviderError, match="not valid JSON") as info:
        _provider(lambda _: httpx.Response(200, json=payload)).extract_form([_page()], None)
    assert "secret" not in str(info.value)


def test_unknown_model_names_the_model(api_key):
    provider = _provider(lambda _: httpx.Response(404, text="secret body"))
    with pytest.raises(ProviderError, match="test-model") as info:
        provider.extract_form([_page()], None)
    assert "secret" not in str(info.value)


def test_overload_is_retried_then_succeeds(api_key):
    statuses = iter([503, 429, 200])
    sleeps: list[float] = []

    def handler(_):
        status = next(statuses)
        return httpx.Response(status, json=_answer(DUMMY_OUTPUT) if status == 200 else {})

    commitment = _provider(handler, sleeps).extract_form([_page()], None)
    assert commitment.id_number.value == "000000018"
    assert sleeps == [2.0, 4.0]


def test_overload_gives_up_after_max_attempts(api_key):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(ProviderError, match="HTTP 503"):
        _provider(handler, []).extract_form([_page()], None)
    assert len(calls) == MAX_ATTEMPTS


def test_client_error_is_not_retried(api_key):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400)

    with pytest.raises(ProviderError, match="HTTP 400"):
        _provider(handler, []).extract_form([_page()], None)
    assert len(calls) == 1


def test_compare_pages_parses_answer(api_key):
    relation = {"same_form": False, "reason": "dummy", "page_b_type": "irrelevant"}

    def handler(request: httpx.Request) -> httpx.Response:
        parts = json.loads(request.content)["contents"][0]["parts"]
        assert sum("inlineData" in part for part in parts) == 2
        return httpx.Response(200, json=_answer(relation))

    assert _provider(handler).compare_pages(_page(), _page()).page_b_type == "irrelevant"


def test_compare_pages_rejects_answer_outside_schema(api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_answer({"same_form": True, "reason": "x", "page_b_type": "maybe"}))

    with pytest.raises(ProviderError):
        _provider(handler).compare_pages(_page(), _page())


def test_classify_parses_answer_and_unknown_type(api_key):
    form_types = load_form_types(Settings().form_types_dir)

    def handler(request: httpx.Request) -> httpx.Response:
        schema = json.loads(request.content)["generationConfig"]["responseJsonSchema"]
        assert set(schema["required"]) == {"form_type", "sender_raw", "evidence"}
        return httpx.Response(200, json=_answer({"form_type": "unknown", "sender_raw": None, "evidence": "x"}))

    result = _provider(handler).classify([_page()], form_types)
    assert (result.form_type, result.sender_raw) == ("unknown", None)
