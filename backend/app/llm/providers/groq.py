"""Groq (OpenAI compatible chat completions), model openai/gpt-oss-20b by default.

Structured outputs are requested in strict mode (`response_format` of type json_schema), `temperature` is 0, reasoning effort is
low, and the output cap counts reasoning tokens too, which is why it is a request field the router sets from its settings.
The rate limit headers `x-ratelimit-remaining-tokens` and `x-ratelimit-remaining-requests` of every response seed the local bucket.
"""

import time

import httpx

from app.llm.errors import MalformedResponse
from app.llm.providers.base import Provider, ProviderRequest, ProviderResponse
from app.llm.resilience import RateInfo, parse_reset

BASE_URL = "https://api.groq.com/openai/v1"


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


class GroqProvider(Provider):
    name = "groq"

    def __init__(self, api_key: str, model: str = "openai/gpt-oss-20b", *, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        super().__init__(api_key, model, client=client)
        self.base_url = base_url.rstrip("/")

    def build_body(self, request: ProviderRequest) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.text} for m in request.messages],
            "temperature": 0,
            "max_completion_tokens": request.max_output_tokens,
            "reasoning_effort": "low",
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
            },
        }

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self._require_key()
        started = time.perf_counter()
        response = self.post(
            f"{self.base_url}/chat/completions",
            {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            self.build_body(request),
            request.timeout,
        )
        latency = round((time.perf_counter() - started) * 1000)
        body = self.json_body(response)
        try:
            choice = body["choices"][0]
            text = choice["message"].get("content") or ""
            usage = body.get("usage") or {}
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise MalformedResponse("The Groq response has no choices") from exc
        headers = response.headers
        rate = RateInfo(
            remaining_tokens=_int(headers.get("x-ratelimit-remaining-tokens")),
            remaining_requests=_int(headers.get("x-ratelimit-remaining-requests")),
            reset_tokens_seconds=parse_reset(headers.get("x-ratelimit-reset-tokens")),
            reset_requests_seconds=parse_reset(headers.get("x-ratelimit-reset-requests")),
        )
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return ProviderResponse(
            text=text, tokens_in=int(usage.get("prompt_tokens", 0)), tokens_out=int(usage.get("completion_tokens", 0)), cached_tokens=int(cached),
            finish_reason=choice.get("finish_reason"), model=body.get("model", self.model), rate=rate, latency_ms=latency, raw_usage=usage,
        )  # fmt: skip
