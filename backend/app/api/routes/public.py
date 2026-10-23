"""The two endpoints the marketing site uses without signing in: aggregate demo numbers and the contact form.

Nothing here reads tenant data. The numbers come from the platform wide daily snapshot, which holds totals and no
organization names, and the form only ever inserts.
"""

import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.core.rate_limit import limiter
from app.db.models import DailyMetric, Lead
from app.db.session import make_session_factory, scoped_session

router = APIRouter(prefix="/api/v1/public", tags=["public"])

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PublicMetrics(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"demo": True, "as_of": "2026-10-21", "days": 90, "organizations": 12, "jobs_run": 540, "records_migrated": 8120000,
                                                               "success_rate": 0.97, "average_quality_score": 96.4, "average_duration_seconds": 41.0}]})  # fmt: skip

    demo: bool = Field(description="The numbers come from simulated organizations")
    as_of: date | None
    days: int
    organizations: int
    jobs_run: int
    records_migrated: int
    success_rate: float | None
    average_quality_score: float | None
    average_duration_seconds: float | None


class LeadIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"name": "Grace Hopper", "email": "grace@example.com", "company": "Example Ltd", "message": "We would like a demo.", "kind": "demo"}]})

    name: str | None = Field(None, max_length=200)
    email: str = Field(max_length=320)
    company: str | None = Field(None, max_length=200)
    message: str | None = Field(None, max_length=4000)
    kind: Literal["contact", "demo"] = "contact"
    website: str | None = Field(None, max_length=200, description="Leave empty. A field for bots: a filled one is dropped silently")

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if not EMAIL.match(value):
            raise ValueError("not an email address")
        return value


class LeadAccepted(BaseModel):
    received: bool = True


@router.get("/metrics", response_model=PublicMetrics, summary="Aggregate numbers for the marketing site")
def public_metrics(response: Response) -> PublicMetrics:
    response.headers["Cache-Control"] = "public, max-age=300"
    with scoped_session(factory=make_session_factory()) as db:
        totals = db.execute(
            select(
                func.count(DailyMetric.id), func.max(DailyMetric.metric_date), func.coalesce(func.sum(DailyMetric.jobs), 0), func.coalesce(func.sum(DailyMetric.records), 0),
                func.avg(DailyMetric.success_rate), func.avg(DailyMetric.quality_score), func.avg(DailyMetric.avg_duration_ms),
            )
        ).one()
        latest = db.scalar(select(DailyMetric.active_orgs).order_by(DailyMetric.metric_date.desc()).limit(1))
    days, as_of, jobs, records, success, quality, duration = totals
    return PublicMetrics(
        demo=True, as_of=as_of, days=int(days), organizations=int(latest or 0), jobs_run=int(jobs), records_migrated=int(records),
        success_rate=round(float(success), 4) if success is not None else None, average_quality_score=round(float(quality), 2) if quality is not None else None,
        average_duration_seconds=round(float(duration) / 1000, 1) if duration is not None else None,
    )  # fmt: skip


@router.post("/leads", response_model=LeadAccepted, status_code=status.HTTP_201_CREATED, summary="Send a contact or demo request")
@limiter.limit("5/minute")
def create_lead(request: Request, body: LeadIn) -> LeadAccepted:
    if body.website:  # a person never fills the hidden field; pretend it worked so the bot learns nothing
        return LeadAccepted()
    with scoped_session(factory=make_session_factory()) as db:
        db.add(Lead(name=body.name, email=body.email, company=body.company, message=body.message, kind=body.kind))
    return LeadAccepted()
