"""Persistence behind the router: the answer cache, the call log, token usage and organization settings.

`DbStore` runs every operation in its own short transaction (scoped to the organization), so a call that is logged stays logged
even when the request that triggered it fails and rolls back, and budgets always see the real usage. `MemoryStore` has the same
behaviour without a database, for the unit tests of the router.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import Job, LlmCache, LlmCall, OrgSettings
from app.db.models.enums import LlmCallStatus
from app.db.session import scoped_session


@dataclass
class OrgLlmSettings:
    provider_order: list[str]
    job_token_budget: int
    org_daily_token_budget: int
    max_calls_per_job: int

    @classmethod
    def defaults(cls) -> "OrgLlmSettings":
        s = get_settings()
        order = [p.strip() for p in s.llm_provider_order.split(",") if p.strip()]
        return cls(order or ["groq", "gemini"], s.llm_job_token_budget, s.llm_org_daily_token_budget, s.llm_max_calls_per_job)


@dataclass
class CallRecord:
    provider: str
    model: str
    task: str
    status: LlmCallStatus
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    latency_ms: int | None = None
    cache_hit: bool = False
    job_id: uuid.UUID | None = None
    error_code: str | None = None
    prompt_version: str | None = None


@dataclass
class JobUsage:
    tokens_used: int
    token_budget: int
    calls: int  # completed model calls (answered and accepted), cache hits excluded


@dataclass
class CachedAnswer:
    response: dict[str, Any]
    tokens: int


class LlmStore(Protocol):
    def cache_get(self, key: str) -> CachedAnswer | None: ...
    def cache_put(self, key: str, task: str, prompt_version: str, response: dict[str, Any], tokens: int) -> None: ...
    def cache_hit(self, key: str) -> None: ...
    def record_call(self, call: CallRecord) -> None: ...
    def job_usage(self, job_id: uuid.UUID) -> JobUsage | None: ...
    def add_job_tokens(self, job_id: uuid.UUID, tokens: int) -> None: ...
    def org_tokens_today(self) -> int: ...
    def org_settings(self) -> OrgLlmSettings: ...


# ------------------------------------------------------------------ memory


@dataclass
class MemoryStore:
    """In memory stand in with the same behaviour. `job_budgets` maps a job id to its token budget."""

    settings: OrgLlmSettings = field(default_factory=OrgLlmSettings.defaults)
    job_budgets: dict[uuid.UUID, int] = field(default_factory=dict)
    cache: dict[str, CachedAnswer] = field(default_factory=dict)
    cache_hits: dict[str, int] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)
    job_tokens: dict[uuid.UUID, int] = field(default_factory=dict)
    daily_tokens: int = 0

    def cache_get(self, key: str) -> CachedAnswer | None:
        return self.cache.get(key)

    def cache_put(self, key: str, task: str, prompt_version: str, response: dict[str, Any], tokens: int) -> None:
        self.cache[key] = CachedAnswer(response, tokens)

    def cache_hit(self, key: str) -> None:
        self.cache_hits[key] = self.cache_hits.get(key, 0) + 1

    def record_call(self, call: CallRecord) -> None:
        self.calls.append(call)
        if not call.cache_hit:
            self.daily_tokens += call.tokens_in + call.tokens_out

    def job_usage(self, job_id: uuid.UUID) -> JobUsage | None:
        if job_id not in self.job_budgets:
            return None
        ok = sum(1 for c in self.calls if c.job_id == job_id and c.status is LlmCallStatus.OK and not c.cache_hit)
        return JobUsage(self.job_tokens.get(job_id, 0), self.job_budgets[job_id], ok)

    def add_job_tokens(self, job_id: uuid.UUID, tokens: int) -> None:
        self.job_tokens[job_id] = self.job_tokens.get(job_id, 0) + tokens

    def org_tokens_today(self) -> int:
        return self.daily_tokens

    def org_settings(self) -> OrgLlmSettings:
        return self.settings


# ------------------------------------------------------------------ database


class DbStore:
    def __init__(self, org_id: uuid.UUID, factory: sessionmaker[Session] | None = None) -> None:
        self.org_id = org_id
        self.factory = factory

    def _session(self):  # type: ignore[no-untyped-def]
        return scoped_session(org_id=self.org_id, factory=self.factory)

    def cache_get(self, key: str) -> CachedAnswer | None:
        with self._session() as s:
            row = s.scalar(select(LlmCache).where(LlmCache.request_hash == key))
            return CachedAnswer(row.response, row.tokens) if row else None

    def cache_put(self, key: str, task: str, prompt_version: str, response: dict[str, Any], tokens: int) -> None:
        with self._session() as s:
            stmt = (
                pg_insert(LlmCache)
                .values(org_id=self.org_id, request_hash=key, task=task, prompt_version=prompt_version, response=response, tokens=tokens)
                .on_conflict_do_update(constraint="uq_llm_cache_org_hash", set_={"response": response, "tokens": tokens, "prompt_version": prompt_version})
            )
            s.execute(stmt)

    def cache_hit(self, key: str) -> None:
        with self._session() as s:
            s.execute(update(LlmCache).where(LlmCache.request_hash == key).values(hits=LlmCache.hits + 1, last_hit_at=datetime.now(UTC)))

    def record_call(self, call: CallRecord) -> None:
        with self._session() as s:
            s.add(
                LlmCall(
                    org_id=self.org_id, job_id=call.job_id, provider=call.provider, model=call.model, task=call.task,
                    tokens_in=call.tokens_in, tokens_out=call.tokens_out, cached_tokens=call.cached_tokens, latency_ms=call.latency_ms,
                    status=call.status, cache_hit=call.cache_hit, error_code=call.error_code, prompt_version=call.prompt_version,
                )  # fmt: skip
            )

    def job_usage(self, job_id: uuid.UUID) -> JobUsage | None:
        with self._session() as s:
            job = s.get(Job, job_id)
            if job is None:
                return None
            calls = s.scalar(
                select(func.count()).select_from(LlmCall).where(LlmCall.job_id == job_id, LlmCall.status == LlmCallStatus.OK, LlmCall.cache_hit.is_(False))
            )
            return JobUsage(job.tokens_used, job.token_budget, int(calls or 0))

    def add_job_tokens(self, job_id: uuid.UUID, tokens: int) -> None:
        with self._session() as s:
            s.execute(update(Job).where(Job.id == job_id).values(tokens_used=Job.tokens_used + tokens))  # atomic in the database

    def org_tokens_today(self) -> int:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._session() as s:
            total = s.scalar(
                select(func.coalesce(func.sum(LlmCall.tokens_in + LlmCall.tokens_out), 0)).where(LlmCall.created_at >= midnight, LlmCall.cache_hit.is_(False))
            )
            return int(total or 0)

    def org_settings(self) -> OrgLlmSettings:
        with self._session() as s:
            row = s.get(OrgSettings, self.org_id)
            if row is None:
                return OrgLlmSettings.defaults()
            return OrgLlmSettings(list(row.llm_provider_order), row.llm_job_token_budget, row.llm_org_daily_token_budget, row.llm_max_calls_per_job)


def save_org_settings(session: Session, org_id: uuid.UUID, values: OrgLlmSettings, user_id: uuid.UUID | None) -> None:
    """Upsert the organization's settings inside the caller's (tenant scoped) session."""
    row = session.get(OrgSettings, org_id)
    if row is None:
        row = OrgSettings(org_id=org_id)
        session.add(row)
    row.llm_provider_order = values.provider_order
    row.llm_job_token_budget = values.job_token_budget
    row.llm_org_daily_token_budget = values.org_daily_token_budget
    row.llm_max_calls_per_job = values.max_calls_per_job
    row.updated_by = user_id
    session.flush()
