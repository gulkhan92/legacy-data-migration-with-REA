"""The provider interface and the pieces the two HTTP providers share."""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.llm.errors import (
    MalformedResponse,
    ProviderClientError,
    ProviderNotConfigured,
    ProviderServerError,
    ProviderTimeout,
    RateLimited,
)
from app.llm.resilience import RateInfo

ERROR_BODY_LIMIT = 200


@dataclass(frozen=True)
class Message:
    role: str  # system, user or assistant
    text: str


@dataclass
class ProviderRequest:
    messages: list[Message]
    schema: dict[str, Any]
    schema_name: str
    max_output_tokens: int = 800
    timeout: float = 30.0


@dataclass
class ProviderResponse:
    text: str
    tokens_in: int
    tokens_out: int
    cached_tokens: int = 0
    finish_reason: str | None = None
    model: str = ""
    rate: RateInfo | None = None
    latency_ms: int = 0
    raw_usage: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


class Provider(ABC):
    name: str
    model: str

    def __init__(self, api_key: str, model: str, *, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self.model = model
        self._client = client or httpx.Client()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _require_key(self) -> None:
        if not self.configured:
            raise ProviderNotConfigured(f"No API key is configured for {self.name}")

    @abstractmethod
    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """One request to the provider. Raises a ProviderError subclass for every failure."""

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> httpx.Response:
        """POST JSON and turn transport failures and error statuses into provider errors (never echoing keys or prompts)."""
        try:
            response = self._client.post(url, headers=headers, json=body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{self.name} did not answer within {timeout:g} seconds") from exc
        except httpx.HTTPError as exc:
            raise ProviderServerError(f"{self.name} could not be reached ({type(exc).__name__})") from exc
        if response.status_code == 429:
            raise RateLimited(f"{self.name} rate limit reached", retry_after=self.retry_after(response), detail={"status": 429})
        if response.status_code >= 500:
            raise ProviderServerError(f"{self.name} returned {response.status_code}", detail={"status": response.status_code})
        if response.status_code >= 400:
            raise ProviderClientError(f"{self.name} refused the request ({response.status_code}): {self.error_message(response)}", detail={"status": response.status_code})
        return response

    @staticmethod
    def error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
            message = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        except (ValueError, AttributeError):
            message = None
        return str(message or "no detail")[:ERROR_BODY_LIMIT]

    @staticmethod
    def retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if value:
            try:
                return float(value)
            except ValueError:
                return None
        # Gemini: error.details[].retryDelay such as "12s"
        try:
            for detail in response.json().get("error", {}).get("details", []):
                delay = detail.get("retryDelay")
                if delay:
                    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(delay))
                    if match:
                        return float(match.group(1))
        except (ValueError, AttributeError):
            pass
        return None

    @staticmethod
    def json_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise MalformedResponse("The provider did not return JSON") from exc
        if not isinstance(body, dict):
            raise MalformedResponse("The provider returned an unexpected body")
        return body
