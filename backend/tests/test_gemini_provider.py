"""GeminiProvider against a mocked HTTP transport — no network, no real key."""
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError, RetryableProviderError
from app.config import Settings
from app.pipeline.classify import load_form_types
from app.providers.gemini import API_KEY_ENV, API_KEY_HEADER, MAX_ATTEMPTS, GeminiProvider


def _reading(value) -> dict:
    return {"value": value, "bbox": None, "legible": True, "source": "printed", "note": None}


DUMMY_OUTPUT = {
    "first_name": _reading("משה"),
    "last_name": _reading("כהן"),
    "id_number": _reading("000000018"),
    "central_numbers": [_reading("111111")],
    "received_date": _reading("01.01.2025"),
    "sender": _reading("מכבי"),
    "commitment_numbers": [_reading("0001234567")],
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

    reading = _provider(handler).extract_form([_page()], None)
    assert reading.id_number.value == "000000018"
    assert [r.value for r in reading.commitment_numbers] == ["0001234567"]


def test_read_number(api_key):
    provider = _provider(lambda _: httpx.Response(200, json=_answer({"value": None})))
    assert provider.read_number(np.zeros((20, 60, 3), np.uint8)).value is None


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

    with pytest.raises(RetryableProviderError, match="timed out"):
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

    reading = _provider(handler, sleeps).extract_form([_page()], None)
    assert reading.id_number.value == "000000018"
    assert sleeps == [2.0, 4.0]


def test_overload_gives_up_after_max_attempts(api_key):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(RetryableProviderError, match="HTTP 503"):
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


DIAGNOSTICS_LOGGER = "app.providers.gemini"


def _debug_provider(handler) -> GeminiProvider:
    return GeminiProvider("test-model", 1.0, client=httpx.Client(transport=httpx.MockTransport(handler)),
                          sleep=lambda _: None, debug=True)


def _diagnostic_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == DIAGNOSTICS_LOGGER]


def test_debug_off_by_default_logs_nothing(api_key, caplog):
    caplog.set_level("INFO", logger=DIAGNOSTICS_LOGGER)
    with pytest.raises(RetryableProviderError):
        _provider(lambda request: httpx.Response(503)).read_number(np.zeros((5, 5, 3), np.uint8))
    assert not _diagnostic_lines(caplog)


def test_debug_logs_each_attempt_with_quota_reason_and_no_secrets(api_key, caplog):
    quota_error = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "dummy 123", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "41s"}]}}
    caplog.set_level("INFO", logger=DIAGNOSTICS_LOGGER)
    with pytest.raises(RetryableProviderError):
        _debug_provider(lambda request: httpx.Response(429, json=quota_error)).extract_form([_page()], None)
    lines = _diagnostic_lines(caplog)
    assert [f"attempt={n}/4" in line for n, line in enumerate(lines, 1)] == [True] * 4
    for expected in ("model=test-model", "images=1", "image/jpeg(actual image/jpeg)", "http=429",
                     "error_status=RESOURCE_EXHAUSTED", "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                     "'retryDelay': '41s'", "transport_error=none"):
        assert expected in lines[0]
    assert "dummy" not in "".join(lines)


def test_debug_logs_answer_shape_without_text(api_key, caplog):
    answer = {"responseId": "resp-1", "usageMetadata": {"promptTokenCount": 10, "thoughtsTokenCount": 7},
              "candidates": [{"finishReason": "STOP", "content": {"parts": [
                  {"text": "dummy thought", "thought": True}, {"text": '{"value": "98765"}'}]}}]}
    caplog.set_level("INFO", logger=DIAGNOSTICS_LOGGER)
    _debug_provider(lambda request: httpx.Response(200, json=answer)).read_number(np.zeros((5, 5, 3), np.uint8))
    (line,) = _diagnostic_lines(caplog)
    for expected in ("http=200", "response_id=resp-1", "finish_reason=STOP", "parts=['thought', 'text']",
                     "'thoughtsTokenCount': 7"):
        assert expected in line
    assert "98765" not in line and "dummy" not in line


def test_debug_logs_the_cause_of_a_timeout(api_key, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("dummy", request=request)

    caplog.set_level("INFO", logger=DIAGNOSTICS_LOGGER)
    with pytest.raises(RetryableProviderError):
        _debug_provider(handler).extract_form([_page()], None)
    (line,) = _diagnostic_lines(caplog)
    assert "http=None" in line and "transport_error=ReadTimeout" in line
