"""The LLM router: one interface, `complete_json(task, payload)`, in front of Groq and Gemini.

Order of work for a request
  1. The payload guard refuses anything that must not leave the system.
  2. The answer cache (per organization, SHA-256 of the normalized request) is asked first. A hit costs zero tokens.
  3. Budgets: the job's token budget, the job's call limit and the organization's daily token budget are checked against a
     pessimistic estimate (request plus the output cap) before anything is sent.
  4. Providers are tried in the organization's order. One that has an open circuit breaker, no room in its local rate limit
     bucket, or no key is skipped. Retryable failures (429, 5xx, timeout) are retried with exponential backoff and jitter,
     honouring Retry-After when it is short; when retries run out the next provider is tried.
  5. The answer must be valid JSON that matches the task schema and passes the task's own checks. If it does not, one repair
     request follows (the model is shown what was wrong); if that fails too, the next provider is tried.
  6. Every request to a provider writes a row in llm_calls. A good answer goes into the cache, and its tokens are added to the job.

Nothing here logs prompts, payloads, answers or keys.
"""

import hashlib
import json
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.db.models.enums import LlmCallStatus
from app.llm.errors import (
    AllProvidersFailed,
    BudgetExceeded,
    LlmError,
    ProviderError,
    RateLimited,
    SchemaFailure,
)
from app.llm.guard import check_payload
from app.llm.providers.base import Message, Provider, ProviderRequest, ProviderResponse
from app.llm.resilience import CircuitBreaker, TokenBucket, backoff_delay
from app.llm.store import CallRecord, LlmStore, OrgLlmSettings
from app.llm.tasks import TASKS, TaskSpec, strict_schema
from app.llm.tokens import estimate_json_tokens, estimate_tokens

STATUS_FOR_ERROR = {"rate_limited": LlmCallStatus.RATE_LIMITED, "timeout": LlmCallStatus.TIMEOUT}
REPAIR_MESSAGE = (
    "Your previous answer was not accepted: {problem}. "
    "Answer again with JSON only, matching the schema exactly and following the rules."
)


@dataclass
class RouterConfig:
    timeout: float = 30.0
    max_output_tokens: int = 800
    retry_attempts: int = 2
    backoff_base: float = 0.5
    backoff_cap: float = 8.0
    max_retry_after: float = 10.0
    breaker_failures: int = 3
    breaker_open_seconds: float = 60.0
    tokens_per_minute: dict[str, int] = field(default_factory=lambda: {"groq": 8000, "gemini": 250_000})
    requests_per_minute: dict[str, int] = field(default_factory=lambda: {"groq": 30, "gemini": 15})


@dataclass
class LlmContext:
    """Who is asking: the organization's store (cache, log, usage) and the job the tokens are charged to."""

    store: LlmStore
    job_id: uuid.UUID | None = None


@dataclass
class LlmResult:
    output: BaseModel
    provider: str
    model: str
    cached: bool
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    @property
    def data(self) -> dict[str, Any]:
        return self.output.model_dump()


def normalize(value: Any) -> Any:
    """Canonical form for hashing: sorted keys, floats rounded, so tiny numeric noise cannot defeat the cache."""
    if isinstance(value, dict):
        return {k: normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, float):
        return round(value, 2)
    return value


def cache_key(spec: TaskSpec, payload: dict[str, Any]) -> str:
    body = {"task": spec.name, "prompt": spec.prompt.sha256, "payload": normalize(payload)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class LLMRouter:
    def __init__(
        self,
        providers: dict[str, Provider],
        config: RouterConfig | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.providers = providers
        self.config = config or RouterConfig()
        self.sleep, self.clock, self.rng = sleep, clock, rng or random.Random()
        self.breakers = {
            n: CircuitBreaker(self.config.breaker_failures, self.config.breaker_open_seconds, clock) for n in providers
        }
        self.buckets = {
            n: TokenBucket(self.config.tokens_per_minute.get(n, 8000), self.config.requests_per_minute.get(n, 30), clock) for n in providers
        }

    # ------------------------------------------------------------------ public

    def complete_json(
        self,
        task: str,
        payload: dict[str, Any],
        ctx: LlmContext,
        *,
        schema: type[BaseModel] | None = None,
        use_cache: bool = True,
    ) -> LlmResult:
        spec = TASKS[task]
        output_model = schema or spec.output
        check_payload(payload)
        key = cache_key(spec, payload)
        prompt = spec.prompt

        if use_cache and (hit := self._from_cache(ctx, spec, output_model, payload, key)) is not None:
            return hit

        messages = [Message("system", prompt.text), Message("user", json.dumps(payload, separators=(",", ":"), ensure_ascii=False))]
        estimate_in = estimate_tokens(prompt.text) + estimate_json_tokens(payload)
        worst_case = estimate_in + self.config.max_output_tokens
        settings = ctx.store.org_settings()
        self._check_budgets(ctx, settings, worst_case)

        attempts: list[dict[str, Any]] = []
        for name in settings.provider_order:
            provider = self.providers.get(name)
            if provider is None or not provider.configured:
                attempts.append({"provider": name, "outcome": "skipped", "reason": "not configured"})
                continue
            if not self.breakers[name].allow():
                attempts.append({"provider": name, "outcome": "skipped", "reason": "circuit open"})
                continue
            if not self.buckets[name].can_send(worst_case):
                self.breakers[name]._trial_in_flight = False  # nothing was sent, a half open trial is still available
                attempts.append({"provider": name, "outcome": "skipped", "reason": "rate limit"})
                continue
            try:
                return self._run_provider(provider, spec, output_model, payload, messages, ctx, settings, worst_case, key, attempts)
            except BudgetExceeded:
                raise
            except LlmError as exc:
                attempts.append({"provider": name, "outcome": "failed", "reason": exc.code})
        raise AllProvidersFailed(attempts=attempts)

    def test_connection(self, provider_name: str, ctx: LlmContext) -> dict[str, Any]:
        """One tiny request to one provider, no failover and no cache. Counts against the organization's daily budget."""
        provider = self.providers[provider_name]
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        request = ProviderRequest(
            [Message("system", "Answer with JSON only."), Message("user", '{"reply_with": {"ok": true}}')],
            schema, "connection_test", 100, self.config.timeout,
        )  # fmt: skip
        try:
            response = provider.complete(request)
        except ProviderError as exc:
            ctx.store.record_call(CallRecord(provider_name, provider.model, "connection_test", STATUS_FOR_ERROR.get(exc.code, LlmCallStatus.ERROR), error_code=exc.code))
            return {"ok": False, "provider": provider_name, "model": provider.model, "error": exc.code, "message": exc.message}
        ctx.store.record_call(CallRecord(provider_name, response.model or provider.model, "connection_test", LlmCallStatus.OK, response.tokens_in,
                                         response.tokens_out, response.cached_tokens, response.latency_ms))  # fmt: skip
        return {"ok": True, "provider": provider_name, "model": response.model or provider.model, "latency_ms": response.latency_ms, "tokens": response.total_tokens}

    def status(self) -> dict[str, dict[str, Any]]:
        out = {}
        for name, provider in self.providers.items():
            tokens, requests = self.buckets[name].available()
            out[name] = {
                "model": provider.model, "configured": provider.configured, "circuit": self.breakers[name].state,
                "tokens_available": int(tokens), "requests_available": int(requests), "limits_from_headers": self.buckets[name].seeded_from_headers,
            }  # fmt: skip
        return out

    # ------------------------------------------------------------------ cache and budgets

    def _from_cache(self, ctx: LlmContext, spec: TaskSpec, model: type[BaseModel], payload: dict[str, Any], key: str) -> LlmResult | None:
        cached = ctx.store.cache_get(key)
        if cached is None:
            return None
        try:
            output = model.model_validate(cached.response)
            spec.check(output, payload)
        except (ValidationError, SchemaFailure):
            return None  # a stale or damaged entry is ignored and replaced by the next real answer
        ctx.store.cache_hit(key)
        ctx.store.record_call(CallRecord("cache", "cache", spec.name, LlmCallStatus.CACHE_HIT, cache_hit=True, job_id=ctx.job_id, prompt_version=spec.prompt.label))
        return LlmResult(output, "cache", "cache", True)

    def _check_budgets(self, ctx: LlmContext, settings: OrgLlmSettings, worst_case: int) -> None:
        if ctx.job_id is not None:
            usage = ctx.store.job_usage(ctx.job_id)
            if usage is not None:
                if usage.calls >= settings.max_calls_per_job:
                    raise BudgetExceeded("job_calls", f"The job has used its {settings.max_calls_per_job} LLM calls", detail={"calls": usage.calls})
                if usage.tokens_used + worst_case > usage.token_budget:
                    raise BudgetExceeded("job_tokens", "The job's token budget would be exceeded",
                                         detail={"used": usage.tokens_used, "budget": usage.token_budget, "needed": worst_case})  # fmt: skip
        used_today = ctx.store.org_tokens_today()
        if used_today + worst_case > settings.org_daily_token_budget:
            raise BudgetExceeded("org_daily_tokens", "The organization's daily token budget would be exceeded",
                                 detail={"used": used_today, "budget": settings.org_daily_token_budget, "needed": worst_case})  # fmt: skip

    # ------------------------------------------------------------------ one provider

    def _run_provider(
        self, provider: Provider, spec: TaskSpec, model: type[BaseModel], payload: dict[str, Any], messages: list[Message],
        ctx: LlmContext, settings: OrgLlmSettings, worst_case: int, key: str, attempts: list[dict[str, Any]],
    ) -> LlmResult:  # fmt: skip
        schema = strict_schema(model)
        conversation = list(messages)
        repaired = False
        total_in = total_out = total_cached = 0
        while True:
            response = self._request_with_retries(provider, spec, schema, conversation, ctx, worst_case)
            used = response.total_tokens
            total_in, total_out, total_cached = total_in + response.tokens_in, total_out + response.tokens_out, total_cached + response.cached_tokens
            if ctx.job_id is not None:
                ctx.store.add_job_tokens(ctx.job_id, used)
            try:
                output = self._parse(response, model, spec, payload)
            except SchemaFailure as exc:
                ctx.store.record_call(self._record(provider, response, spec, ctx, LlmCallStatus.SCHEMA_ERROR, exc.code))
                if repaired:
                    self.breakers[provider.name].failure()
                    raise
                repaired = True
                conversation = [*messages, Message("assistant", response.text[:2000]), Message("user", REPAIR_MESSAGE.format(problem=exc.message))]
                if ctx.job_id is not None:  # the repair is another request: it needs budget too
                    self._check_budgets(ctx, settings, worst_case)
                continue
            ctx.store.record_call(self._record(provider, response, spec, ctx, LlmCallStatus.OK, None))
            self.breakers[provider.name].success()
            ctx.store.cache_put(key, spec.name, spec.prompt.label, output.model_dump(), total_in + total_out)
            attempts.append({"provider": provider.name, "outcome": "ok", "repaired": repaired})
            return LlmResult(output, provider.name, response.model or provider.model, False, total_in, total_out, total_cached, attempts)

    def _request_with_retries(self, provider: Provider, spec: TaskSpec, schema: dict[str, Any], conversation: list[Message], ctx: LlmContext, reserve: int) -> ProviderResponse:
        bucket, breaker = self.buckets[provider.name], self.breakers[provider.name]
        request = ProviderRequest(conversation, schema, spec.name, self.config.max_output_tokens, self.config.timeout)
        last: ProviderError | None = None
        for attempt in range(self.config.retry_attempts):
            bucket.reserve(reserve)
            try:
                response = provider.complete(request)
            except ProviderError as exc:
                last = exc
                bucket.settle(reserve, 0)
                breaker.failure()
                status = STATUS_FOR_ERROR.get(exc.code, LlmCallStatus.ERROR)
                ctx.store.record_call(CallRecord(provider.name, provider.model, spec.name, status, job_id=ctx.job_id, error_code=exc.code, prompt_version=spec.prompt.label))
                wait = None
                if isinstance(exc, RateLimited):
                    bucket.penalise(exc.retry_after)
                    wait = exc.retry_after
                if not exc.retryable or attempt + 1 >= self.config.retry_attempts or breaker.state != "closed":
                    raise
                if wait is not None and wait > self.config.max_retry_after:
                    raise  # waiting longer than we allow: fail over instead
                delay = backoff_delay(attempt, self.config.backoff_base, self.config.backoff_cap, self.rng)
                self.sleep(max(delay, wait or 0.0))
                continue
            bucket.settle(reserve, max(0, response.tokens_in - response.cached_tokens) + response.tokens_out)
            bucket.update(response.rate)
            return response
        assert last is not None  # pragma: no cover
        raise last

    def _parse(self, response: ProviderResponse, model: type[BaseModel], spec: TaskSpec, payload: dict[str, Any]) -> BaseModel:
        if response.finish_reason in ("length", "MAX_TOKENS"):
            raise SchemaFailure("the answer was cut off because it reached the output limit")
        text = response.text.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            output = model.model_validate_json(text)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])
            raise SchemaFailure(f"it is not valid for the schema ({problems})") from None
        spec.check(output, payload)
        return output

    @staticmethod
    def _record(provider: Provider, response: ProviderResponse, spec: TaskSpec, ctx: LlmContext, status: LlmCallStatus, error: str | None) -> CallRecord:
        return CallRecord(
            provider.name, response.model or provider.model, spec.name, status, response.tokens_in, response.tokens_out, response.cached_tokens,
            response.latency_ms, False, ctx.job_id, error, spec.prompt.label,
        )  # fmt: skip
