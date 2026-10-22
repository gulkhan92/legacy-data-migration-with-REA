"""Admin hooks for the LLM router: provider order, budgets, a connection test and usage figures.

API keys are never accepted, stored or returned here: they come from the environment. The settings page of the console
(Phase 12) is built on these routes.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_tenant_db, require_role
from app.core.rate_limit import limiter
from app.core.security import Principal
from app.db.models import LlmCache, LlmCall
from app.db.models.enums import LlmCallStatus
from app.db.session import make_session_factory
from app.llm.factory import context_for, get_router
from app.llm.store import DbStore, OrgLlmSettings, save_org_settings
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
PROVIDERS = ("groq", "gemini")


class LlmSettingsIn(BaseModel):
    provider_order: list[Literal["groq", "gemini"]] = Field(min_length=1, max_length=2)
    job_token_budget: int = Field(ge=500, le=100_000)
    org_daily_token_budget: int = Field(ge=1_000, le=10_000_000)
    max_calls_per_job: int = Field(ge=1, le=10)

    @field_validator("provider_order")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("a provider can only be listed once")
        return value


class TestConnectionIn(BaseModel):
    provider: Literal["groq", "gemini"]


def _settings_view(db: Session, principal: Principal) -> dict[str, Any]:
    store = DbStore(principal.org_id, make_session_factory())
    current = store.org_settings()
    state = get_router().status()
    return {
        "provider_order": current.provider_order,
        "job_token_budget": current.job_token_budget,
        "org_daily_token_budget": current.org_daily_token_budget,
        "max_calls_per_job": current.max_calls_per_job,
        "used_today": store.org_tokens_today(),
        "providers": [{"name": n, **state[n]} for n in PROVIDERS],
    }


@router.get("/llm-settings")
def get_llm_settings(principal: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    return _settings_view(db, principal)


@router.put("/llm-settings")
def put_llm_settings(
    body: LlmSettingsIn, request: Request, principal: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db)
) -> dict[str, Any]:
    before = DbStore(principal.org_id, make_session_factory()).org_settings()
    new = OrgLlmSettings(list(body.provider_order), body.job_token_budget, body.org_daily_token_budget, body.max_calls_per_job)
    save_org_settings(db, principal.org_id, new, principal.user_id)
    changed = {k: {"from": getattr(before, k), "to": getattr(new, k)} for k in vars(new) if getattr(before, k) != getattr(new, k)}
    record_audit(
        db, AuditAction.SETTINGS_CHANGE, org_id=principal.org_id, user_id=principal.user_id, entity_type="llm_settings",
        ip=client_ip(request), detail={"changed": changed},
    )  # fmt: skip
    db.commit()
    return _settings_view(db, principal)


@router.post("/llm-settings/test-connection")
@limiter.limit("10/minute")
def test_connection(
    request: Request, body: TestConnectionIn, principal: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db)
) -> dict[str, Any]:
    llm = get_router()
    if not llm.providers[body.provider].configured:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"No API key is configured for {body.provider}")
    return llm.test_connection(body.provider, context_for(principal.org_id, None, make_session_factory()))


@router.get("/llm-usage")
def llm_usage(days: int = 7, _: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=max(1, min(days, 90)))
    day = func.date_trunc("day", LlmCall.created_at)
    ok, cache, failed = LlmCall.status == LlmCallStatus.OK, LlmCall.cache_hit.is_(True), LlmCall.status.notin_([LlmCallStatus.OK, LlmCallStatus.CACHE_HIT])
    rows = db.execute(
        select(
            day.label("day"), LlmCall.provider,
            func.coalesce(func.sum(case((ok, LlmCall.tokens_in), else_=0)), 0).label("tokens_in"),
            func.coalesce(func.sum(case((ok, LlmCall.tokens_out), else_=0)), 0).label("tokens_out"),
            func.coalesce(func.sum(case((ok, LlmCall.cached_tokens), else_=0)), 0).label("cached_tokens"),
            func.count(case((ok, 1))).label("calls"), func.count(case((cache, 1))).label("cache_hits"), func.count(case((failed, 1))).label("failed_attempts"),
        )
        .where(LlmCall.created_at >= since)
        .group_by(day, LlmCall.provider)
        .order_by(day, LlmCall.provider)
    ).all()
    saved = db.scalar(select(func.coalesce(func.sum(LlmCache.tokens * LlmCache.hits), 0))) or 0
    return {
        "days": [
            {"day": r.day.date().isoformat(), "provider": r.provider, "tokens_in": int(r.tokens_in), "tokens_out": int(r.tokens_out),
             "cached_tokens": int(r.cached_tokens), "calls": r.calls, "cache_hits": r.cache_hits, "failed_attempts": r.failed_attempts}
            for r in rows
        ],
        "tokens_saved_by_cache": int(saved),
    }  # fmt: skip
