"""Provider adapters against recorded responses (no network): request shapes, parsing, error mapping, schema strictness."""

import json

import httpx
import pytest

from app.core.config import Settings
from app.llm.errors import (
    MalformedResponse,
    ProviderClientError,
    ProviderNotConfigured,
    ProviderServerError,
    ProviderTimeout,
    RateLimited,
)
from app.llm.providers.base import Message, ProviderRequest
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.groq import GroqProvider
from app.llm.resilience import parse_reset
from app.llm.tasks import ResolveMappingsOutput, gemini_schema, strict_schema
from tests.llm_helpers import client_for, recorded

SCHEMA = strict_schema(ResolveMappingsOutput)
REQUEST = ProviderRequest([Message("system", "SYSTEM PROMPT"), Message("user", '{"columns": []}')], SCHEMA, "resolve_mappings", 800, 30.0)


def groq(*script):  # type: ignore[no-untyped-def]
    client, transport = client_for(*script)
    return GroqProvider("gsk_secret_key_123", "openai/gpt-oss-20b", client=client), transport


def gemini(*script):  # type: ignore[no-untyped-def]
    client, transport = client_for(*script)
    return GeminiProvider("AIza_secret_key_456", "gemini-2.5-flash-lite", client=client), transport


# ------------------------------------------------------------------ schemas


def walk(node, visit):  # type: ignore[no-untyped-def]
    if isinstance(node, dict):
        visit(node)
        for v in node.values():
            walk(v, visit)
    elif isinstance(node, list):
        for v in node:
            walk(v, visit)


def test_the_schema_is_strict_for_structured_outputs() -> None:
    objects = []
    walk(SCHEMA, lambda n: objects.append(n) if n.get("type") == "object" else None)
    assert len(objects) == 2 and all(o["additionalProperties"] is False and o["required"] == list(o["properties"]) for o in objects)
    text = json.dumps(SCHEMA)
    assert "$ref" not in text and "$defs" not in text and '"title"' not in text and '"default"' not in text
    answer = SCHEMA["properties"]["answers"]["items"]
    assert answer["properties"]["target"]["type"] == ["string", "null"] and answer["properties"]["hint"]["type"] == ["string", "null"]
    assert answer["properties"]["confidence"]["minimum"] == 0 and answer["properties"]["confidence"]["maximum"] == 1


def test_the_gemini_schema_uses_the_openapi_subset() -> None:
    converted = gemini_schema(SCHEMA)
    text = json.dumps(converted)
    assert "additionalProperties" not in text and '"null"' not in text
    item = converted["properties"]["answers"]["items"]
    assert converted["type"] == "OBJECT" and item["properties"]["id"]["type"] == "STRING"
    assert item["properties"]["target"] == {"type": "STRING", "nullable": True} and item["properties"]["confidence"]["type"] == "NUMBER"
    assert converted["required"] == ["answers"]


# ------------------------------------------------------------------ Groq


def test_groq_request_follows_the_token_minimisation_rules() -> None:
    provider, transport = groq(recorded("groq_resolve_ok.json"))
    provider.complete(REQUEST)
    [request] = transport.requests
    body = transport.bodies()[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer gsk_secret_key_123"
    assert body["model"] == "openai/gpt-oss-20b" and body["temperature"] == 0 and body["max_completion_tokens"] == 800
    assert body["reasoning_effort"] == "low"
    assert [m["role"] for m in body["messages"]] == ["system", "user"] and body["messages"][0]["content"] == "SYSTEM PROMPT"  # constant prefix first
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["name"] == "resolve_mappings"
    assert rf["json_schema"]["schema"] == SCHEMA
    assert "stream" not in body and "tools" not in body


def test_groq_response_is_parsed_with_usage_and_rate_headers() -> None:
    provider, _ = groq(recorded("groq_resolve_ok.json"))
    response = provider.complete(REQUEST)
    assert json.loads(response.text)["answers"][0]["id"] == "T.c0" and response.finish_reason == "stop"
    assert (response.tokens_in, response.tokens_out, response.cached_tokens, response.total_tokens) == (612, 118, 480, 730)
    assert response.model == "openai/gpt-oss-20b"
    rate = response.rate
    assert rate.remaining_tokens == 7216 and rate.remaining_requests == 997
    assert rate.reset_tokens_seconds == pytest.approx(5.88) and rate.reset_requests_seconds == pytest.approx(179.56)


@pytest.mark.parametrize(
    "fixture,error,check",
    [
        ("groq_429.json", RateLimited, lambda e: e.retry_after == 2.0 and e.retryable),
        ("groq_429_long_wait.json", RateLimited, lambda e: e.retry_after == 3600.0),
        ("groq_503.json", ProviderServerError, lambda e: e.retryable and e.detail["status"] == 503),
        ("groq_401.json", ProviderClientError, lambda e: not e.retryable and "Invalid API Key" in e.message),
    ],
)
def test_groq_errors_are_classified(fixture: str, error: type, check) -> None:  # type: ignore[no-untyped-def]
    provider, _ = groq(recorded(fixture))
    with pytest.raises(error) as info:
        provider.complete(REQUEST)
    assert check(info.value)
    assert "gsk_secret_key_123" not in str(info.value) and "gsk_secret_key_123" not in repr(info.value.detail)


def test_transport_failures_are_classified() -> None:
    provider, _ = groq(httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTimeout) as timeout:
        provider.complete(REQUEST)
    assert timeout.value.retryable and "30 seconds" in timeout.value.message
    provider, _ = groq(httpx.ConnectError("refused"))
    with pytest.raises(ProviderServerError) as down:
        provider.complete(REQUEST)
    assert "ConnectError" in down.value.message and down.value.retryable


def test_a_response_without_choices_is_malformed() -> None:
    provider, _ = groq(httpx.Response(200, json={"id": "x"}))
    with pytest.raises(MalformedResponse):
        provider.complete(REQUEST)
    provider, _ = groq(httpx.Response(200, content=b"<html>gateway</html>"))
    with pytest.raises(MalformedResponse):
        provider.complete(REQUEST)


# ------------------------------------------------------------------ Gemini


def test_gemini_request_shape() -> None:
    provider, transport = gemini(recorded("gemini_resolve_ok.json"))
    provider.complete(REQUEST)
    [request] = transport.requests
    body = transport.bodies()[0]
    assert request.url.path == "/v1beta/models/gemini-2.5-flash-lite:generateContent" and not request.url.query
    assert request.headers["x-goog-api-key"] == "AIza_secret_key_456" and "AIza" not in str(request.url)  # the key is never in the URL
    assert body["systemInstruction"] == {"parts": [{"text": "SYSTEM PROMPT"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": '{"columns": []}'}]}]
    config = body["generationConfig"]
    assert config["temperature"] == 0 and config["maxOutputTokens"] == 800 and config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["type"] == "OBJECT"


def test_gemini_repair_conversation_uses_the_model_role() -> None:
    provider, transport = gemini(recorded("gemini_resolve_ok.json"))
    repair = ProviderRequest(
        [Message("system", "S"), Message("user", "U"), Message("assistant", "bad"), Message("user", "fix it")], SCHEMA, "resolve_mappings", 800, 30.0
    )  # fmt: skip
    provider.complete(repair)
    assert [c["role"] for c in transport.bodies()[0]["contents"]] == ["user", "model", "user"]


def test_gemini_response_counts_thinking_tokens_as_output() -> None:
    provider, _ = gemini(recorded("gemini_resolve_ok.json"))
    response = provider.complete(REQUEST)
    assert (response.tokens_in, response.tokens_out, response.cached_tokens) == (640, 96 + 12, 300)
    assert response.finish_reason == "STOP" and response.rate is None and response.model == "gemini-2.5-flash-lite"
    assert json.loads(response.text)["answers"][1]["hint"] == "parse_date"


def test_gemini_errors() -> None:
    provider, _ = gemini(recorded("gemini_429.json"))
    with pytest.raises(RateLimited) as limited:
        provider.complete(REQUEST)
    assert limited.value.retry_after == 12.0
    provider, _ = gemini(recorded("gemini_400.json"))
    with pytest.raises(ProviderClientError) as bad:
        provider.complete(REQUEST)
    assert "AIza_secret_key_456" not in str(bad.value) and not bad.value.retryable
    provider, _ = gemini(recorded("gemini_blocked.json"))
    with pytest.raises(MalformedResponse) as blocked:
        provider.complete(REQUEST)
    assert "SAFETY" in blocked.value.message


# ------------------------------------------------------------------ keys


def test_a_provider_without_a_key_never_sends_anything() -> None:
    client, transport = client_for()
    provider = GroqProvider("", client=client)
    assert provider.configured is False
    with pytest.raises(ProviderNotConfigured):
        provider.complete(REQUEST)
    assert transport.requests == []


def test_keys_do_not_leak_through_settings_representations() -> None:
    settings = Settings(groq_api_key="gsk_leak_me", gemini_api_key="AIza_leak_me")
    assert "gsk_leak_me" not in repr(settings) and "AIza_leak_me" not in str(settings) and "gsk_leak_me" not in json.dumps(settings.model_dump(mode="json"))
    assert settings.groq_api_key.get_secret_value() == "gsk_leak_me"


@pytest.mark.parametrize("value,seconds", [("7.66s", 7.66), ("2m59.56s", 179.56), ("1h2m3s", 3723.0), ("250ms", 0.25), ("45", 45.0), (None, None), ("junk", None)])
def test_reset_headers_are_parsed(value: str | None, seconds: float | None) -> None:
    result = parse_reset(value)
    assert (result is None) if seconds is None else result == pytest.approx(seconds)
