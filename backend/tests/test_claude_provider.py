"""ClaudeProvider against a mocked HTTP transport — no network, no real key."""
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.pipeline.ingest import PageImage
from app.providers.base import ProviderError, RetryableProviderError
from app.config import Settings
from app.pipeline.classify import load_form_types
from app.providers.claude import API_KEY_ENV, ClaudeProvider
from app.providers.common import FORM_READING_SCHEMA
from app.providers.prompts import Prompts


def _reading(value, **extra) -> dict:
    return {"value": value, "bbox": None, "legible": True, "source": "printed", "note": None, **extra}


DUMMY_INPUT = {
    "first_name": _reading("משה"),
    "last_name": _reading("כהן"),
    "id_number": _reading("000000018"),
    "central_numbers": [_reading("111111"), _reading("222222")],
    "received_date": _reading("01.01.2025"),
    "sender": _reading("מכבי"),
    "commitment_numbers": [],
}


def _page() -> PageImage:
    image = np.zeros((50, 50, 3), np.uint8)
    return PageImage(file_id="a.pdf", page_no=0, image=image, original_path=Path("a.pdf"), original=image)


def _provider(handler) -> ClaudeProvider:
    return ClaudeProvider("test-model", 1.0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def _answer(data, **payload) -> dict:
    """A Messages API response whose JSON answer is ``data`` (after an empty thinking block)."""
    content = [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(data, ensure_ascii=False)}]
    return {"stop_reason": "end_turn", "content": content, **payload}


def _json_response(request: httpx.Request) -> httpx.Response:
    assert request.headers["x-api-key"] == "dummy"
    return httpx.Response(200, json=_answer(DUMMY_INPUT))


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "dummy")


def test_extract_form_parses_the_json_answer(api_key):
    reading = _provider(_json_response).extract_form([_page()], None)
    assert reading.id_number.value == "000000018"
    assert [r.value for r in reading.central_numbers] == ["111111", "222222"]
    assert reading.commitment_numbers == []


def test_extract_prompt_is_the_versioned_file_with_the_yaml_block(api_key):
    form_type = load_form_types(Settings().form_types_dir)["leumit_lab_worklist"]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        text = body["messages"][0]["content"][-1]["text"]
        assert text == Prompts.load().extract(form_type)
        assert form_type.fields["commitment_numbers"].hint in text
        assert body["output_config"]["format"]["schema"]["properties"]["central_numbers"]["type"] == "array"
        return _json_response(request)

    _provider(handler).extract_form([_page()], form_type)


def test_read_number_sends_one_crop_with_the_narrow_prompt(api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.loads(request.content)["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "text"]
        assert content[-1]["text"] == Prompts.load().read_number
        return httpx.Response(200, json=_answer({"value": "123"}))

    assert _provider(handler).read_number(np.zeros((20, 60, 3), np.uint8)).value == "123"


def test_missing_key(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(ProviderError):
        _provider(_json_response).extract_form([_page()], None)


def test_http_error_message_has_status_only(api_key):
    provider = _provider(lambda _: httpx.Response(401, text="secret body"))
    with pytest.raises(ProviderError, match="HTTP 401") as info:
        provider.extract_form([_page()], None)
    assert "secret" not in str(info.value)
    assert not isinstance(info.value, RetryableProviderError)


@pytest.mark.parametrize("status", [429, 500, 503, 529])
def test_overload_is_retryable(api_key, status):
    with pytest.raises(RetryableProviderError, match=f"HTTP {status}"):
        _provider(lambda _: httpx.Response(status)).extract_form([_page()], None)


def test_timeout_is_retryable(api_key):
    def slow(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(RetryableProviderError, match="timed out"):
        _provider(slow).extract_form([_page()], None)


def test_cut_off_answer_is_an_error(api_key):
    payload = _answer(DUMMY_INPUT, stop_reason="max_tokens")
    with pytest.raises(ProviderError, match="max_tokens"):
        _provider(lambda _: httpx.Response(200, json=payload)).extract_form([_page()], None)


def test_request_uses_structured_output_not_forced_tool_use(api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "tools" not in body and "tool_choice" not in body
        schema = body["output_config"]["format"]["schema"]
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert schema["additionalProperties"] is False
        first_name, central_numbers = schema["properties"]["first_name"], schema["properties"]["central_numbers"]
        assert first_name["$ref"] == central_numbers["items"]["$ref"]
        assert first_name["description"] == FORM_READING_SCHEMA["properties"]["first_name"]["description"]
        (reading,) = schema["$defs"].values()
        assert reading["additionalProperties"] is False and "description" not in reading
        assert reading["properties"]["bbox"]["additionalProperties"] is False
        return _json_response(request)

    _provider(handler).extract_form([_page()], None)


def test_schema_without_repeated_objects_has_no_definitions(api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        schema = json.loads(request.content)["output_config"]["format"]["schema"]
        assert "$defs" not in schema
        return httpx.Response(200, json=_answer({"value": "123"}))

    _provider(handler).read_number(np.zeros((20, 60, 3), np.uint8))


MAX_NULLABLE_FIELDS = 16  # structured outputs limit


def _union_count(schema) -> int:
    if isinstance(schema, list):
        return sum(_union_count(item) for item in schema)
    if not isinstance(schema, dict):
        return 0
    own = isinstance(schema.get("type"), list) or "anyOf" in schema
    return own + sum(_union_count(value) for value in schema.values())


def test_form_schema_stays_within_the_nullable_field_limit():
    assert _union_count(FORM_READING_SCHEMA) <= MAX_NULLABLE_FIELDS


def test_shared_schema_is_not_changed(api_key):
    _provider(_json_response).extract_form([_page()], None)
    assert "additionalProperties" not in FORM_READING_SCHEMA


def test_refusal_is_an_error(api_key):
    payload = {"stop_reason": "refusal", "content": []}
    with pytest.raises(ProviderError, match="refusal"):
        _provider(lambda _: httpx.Response(200, json=payload)).extract_form([_page()], None)


@pytest.mark.parametrize("content", [[], [{"type": "thinking", "thinking": ""}]])
def test_answer_without_text_is_an_error(api_key, content):
    payload = {"stop_reason": "end_turn", "content": content}
    with pytest.raises(ProviderError, match="no JSON answer"):
        _provider(lambda _: httpx.Response(200, json=payload)).extract_form([_page()], None)


def test_invalid_json_error_does_not_quote_the_text(api_key):
    payload = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "{\"first_name\": \"משה"}]}
    with pytest.raises(ProviderError, match="not valid JSON") as info:
        _provider(lambda _: httpx.Response(200, json=payload)).extract_form([_page()], None)
    assert "משה" not in str(info.value) and info.value.__cause__ is None


def test_compare_pages_sends_both_pages(api_key):
    relation = {"same_form": True, "reason": "dummy", "page_b_type": "continuation"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "image", "text"]
        assert body["output_config"]["format"]["schema"]["required"] == ["same_form", "reason", "page_b_type"]
        return httpx.Response(200, json=_answer(relation))

    result = _provider(handler).compare_pages(_page(), _page())
    assert (result.same_form, result.page_b_type) == (True, "continuation")


def test_classify_sends_one_page_with_the_type_list(api_key):
    form_types = load_form_types(Settings().form_types_dir)
    answer = {"form_type": "maccabi_online", "sender_raw": "מכבי שירותי בריאות", "evidence": "logo"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "text"]
        assert "maccabi_online" in content[-1]["text"]
        schema = body["output_config"]["format"]["schema"]
        assert "unknown" in schema["properties"]["form_type"]["enum"]
        return httpx.Response(200, json=_answer(answer))

    result = _provider(handler).classify([_page()], form_types)
    assert (result.form_type, result.sender_raw) == ("maccabi_online", "מכבי שירותי בריאות")


DIAGNOSTICS_LOGGER = "app.providers.claude"


def test_debug_off_by_default_logs_nothing(api_key, caplog):
    assert Settings().debug_provider is False
    caplog.set_level("INFO", logger=DIAGNOSTICS_LOGGER)
    _provider(_json_response).extract_form([_page()], None)
    assert not [r for r in caplog.records if r.name == DIAGNOSTICS_LOGGER]


def test_debug_logs_request_and_answer_shape_without_values(api_key, caplog):
    provider = ClaudeProvider("test-model", 1.0, debug=True,
                              client=httpx.Client(transport=httpx.MockTransport(_json_response)))
    caplog.set_level("INFO", logger=DIAGNOSTICS_LOGGER)
    provider.extract_form([_page(), _page()], None)
    (line,) = [r.getMessage() for r in caplog.records if r.name == DIAGNOSTICS_LOGGER]
    for expected in ("model=test-model", "max_tokens=16000", "images=2",
                     "50x50 image/jpeg(actual image/jpeg)", "http=200", "blocks=['thinking', 'text']", "outcome=ok"):
        assert expected in line
    for secret in ("dummy", "משה", "000000018", "111111"):
        assert secret not in line


def test_debug_logs_the_cause_of_a_timeout(api_key, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("dummy", request=request)

    provider = ClaudeProvider("test-model", 1.0, debug=True, client=httpx.Client(transport=httpx.MockTransport(handler)))
    caplog.set_level("INFO", logger=DIAGNOSTICS_LOGGER)
    with pytest.raises(RetryableProviderError):
        provider.extract_form([_page()], None)
    (line,) = [r.getMessage() for r in caplog.records if r.name == DIAGNOSTICS_LOGGER]
    assert "http=None" in line and "outcome=RetryableProviderError <- ReadTimeout" in line


def test_key_pasted_with_a_line_break_is_sent_stripped(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "dummy\n")
    assert _provider(_json_response).extract_form([_page()], None).id_number.value == "000000018"


def test_request_refused_before_sending_is_not_retryable(api_key):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.LocalProtocolError("illegal header value")

    with pytest.raises(ProviderError, match="LocalProtocolError") as info:
        _provider(handler).extract_form([_page()], None)
    assert not isinstance(info.value, RetryableProviderError)
