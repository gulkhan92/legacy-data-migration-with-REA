"""Google Gemini through the REST generateContent endpoint.

The model name comes from the GEMINI_MODEL setting: the free models and their quotas change often, so confirm the current ones
in Google AI Studio before choosing a default (a Flash-Lite class model is the safest start). The key travels in the
`x-goog-api-key` header, never in the URL, so it cannot end up in a log line. Responses carry no rate limit headers, so the
local bucket runs on the configured limits.
"""

import time

import httpx

from app.llm.errors import MalformedResponse
from app.llm.providers.base import Provider, ProviderRequest, ProviderResponse
from app.llm.tasks import gemini_schema

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, *, base_url: str = BASE_URL, client: httpx.Client | None = None) -> None:
        super().__init__(api_key, model, client=client)
        self.base_url = base_url.rstrip("/")

    def build_body(self, request: ProviderRequest) -> dict:
        system = "\n\n".join(m.text for m in request.messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.text}]}
            for m in request.messages
            if m.role != "system"
        ]
        body: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": gemini_schema(request.schema),
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self._require_key()
        started = time.perf_counter()
        response = self.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            {"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
            self.build_body(request),
            request.timeout,
        )
        latency = round((time.perf_counter() - started) * 1000)
        body = self.json_body(response)
        try:
            candidate = body["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            reason = (body.get("promptFeedback") or {}).get("blockReason")
            raise MalformedResponse(f"The Gemini response has no candidate{f' (blocked: {reason})' if reason else ''}") from exc
        usage = body.get("usageMetadata") or {}
        return ProviderResponse(
            text=text, tokens_in=int(usage.get("promptTokenCount", 0)),
            tokens_out=int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0)),
            cached_tokens=int(usage.get("cachedContentTokenCount", 0)), finish_reason=candidate.get("finishReason"),
            model=body.get("modelVersion", self.model), rate=None, latency_ms=latency, raw_usage=usage,
        )  # fmt: skip
