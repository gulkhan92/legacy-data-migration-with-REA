"""Errors of the LLM router. The first group is what a provider can raise, the second what the router raises."""


class LlmError(Exception):
    """Base class. `code` is stable and safe to show; `detail` never holds prompts, payloads or keys."""

    code = "llm_error"

    def __init__(self, message: str = "", *, detail: dict | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail or {}


# ------------------------------------------------------------------ raised by providers


class ProviderError(LlmError):
    code = "provider_error"
    retryable = False


class ProviderNotConfigured(ProviderError):
    code = "provider_not_configured"


class RateLimited(ProviderError):
    """HTTP 429. `retry_after` is the number of seconds the provider asked us to wait, when it said."""

    code = "rate_limited"
    retryable = True

    def __init__(self, message: str = "", *, retry_after: float | None = None, detail: dict | None = None) -> None:
        super().__init__(message, detail=detail)
        self.retry_after = retry_after


class ProviderTimeout(ProviderError):
    code = "timeout"
    retryable = True


class ProviderServerError(ProviderError):
    """HTTP 5xx."""

    code = "server_error"
    retryable = True


class ProviderClientError(ProviderError):
    """HTTP 4xx other than 429 (bad request, bad key, unknown model). Retrying the same request cannot help."""

    code = "client_error"


class MalformedResponse(ProviderError):
    """The provider answered, but not with the expected envelope."""

    code = "malformed_response"


# ------------------------------------------------------------------ raised by the router


class SchemaFailure(LlmError):
    """The model's answer is not valid JSON, does not match the schema, or fails the task's own checks."""

    code = "schema_error"


class BudgetExceeded(LlmError):
    """A token or call budget would be exceeded. `scope` is job_tokens, job_calls or org_daily_tokens."""

    code = "budget_exceeded"

    def __init__(self, scope: str, message: str = "", *, detail: dict | None = None) -> None:
        super().__init__(message or f"The {scope} budget would be exceeded", detail=detail)
        self.scope = scope


class PayloadRejected(LlmError):
    """The payload holds data that must never leave the system (PII, rows) or is too large."""

    code = "payload_rejected"


class AllProvidersFailed(LlmError):
    code = "all_providers_failed"

    def __init__(self, message: str = "", *, attempts: list[dict] | None = None) -> None:
        super().__init__(message or "No provider could answer", detail={"attempts": attempts or []})
        self.attempts = attempts or []
