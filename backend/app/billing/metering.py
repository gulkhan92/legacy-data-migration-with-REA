"""Metering: a completed job writes one usage event, and an organization always has a subscription to bill it against."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.billing.plans import STARTER
from app.db.models import Invoice, Job, Plan, Subscription, UsageEvent
from app.db.models.enums import SubscriptionStatus


def active_subscription(session: Session, org_id: uuid.UUID) -> Subscription | None:
    return session.scalar(select(Subscription).where(Subscription.org_id == org_id, Subscription.ended_at.is_(None)))


def ensure_subscription(session: Session, org_id: uuid.UUID, *, now: datetime | None = None, simulated: bool = False) -> Subscription:
    """Every organization is on a plan. One that never chose gets the free Starter plan from the moment it first needs one."""
    current = active_subscription(session, org_id)
    if current is not None:
        return current
    starter = session.scalar(select(Plan).where(Plan.code == STARTER))
    if starter is None:
        raise RuntimeError("The plans are not installed; run the database migrations")
    subscription = Subscription(org_id=org_id, plan_id=starter.id, status=SubscriptionStatus.ACTIVE, started_at=now or datetime.now(UTC), is_simulated=simulated)
    session.add(subscription)
    session.flush()
    return subscription


def record_job_usage(session: Session, job: Job, records_loaded: int, duration_ms: int | None, *, now: datetime | None = None) -> bool:
    """One usage event per job, however often this is called. Returns True when an event was written."""
    when = now or job.completed_at or datetime.now(UTC)
    ensure_subscription(session, job.org_id, now=when)
    result = session.execute(
        pg_insert(UsageEvent)
        .values(org_id=job.org_id, job_id=job.id, records_loaded=int(records_loaded), tokens_used=int(job.tokens_used or 0), duration_ms=duration_ms, occurred_at=when, is_simulated=False)
        .on_conflict_do_nothing(index_elements=["job_id"], index_where=text("job_id IS NOT NULL"))
        .returning(UsageEvent.id)
    )
    return result.scalar_one_or_none() is not None


def void_job_usage(session: Session, job: Job) -> bool:
    """A rolled back load no longer counts, unless the period it belongs to has already been invoiced (then the invoice stands)."""
    event = session.scalar(select(UsageEvent).where(UsageEvent.job_id == job.id))
    if event is None:
        return False
    day = event.occurred_at.astimezone(UTC).date()
    invoiced = session.scalar(select(func.count()).select_from(Invoice).where(Invoice.org_id == job.org_id, Invoice.period_start <= day, Invoice.period_end >= day)) or 0
    if invoiced:
        return False
    session.execute(delete(UsageEvent).where(UsageEvent.id == event.id))
    return True


def records_in_period(session: Session, org_id: uuid.UUID, start: datetime, end: datetime) -> int:
    total = session.scalar(select(func.coalesce(func.sum(UsageEvent.records_loaded), 0)).where(UsageEvent.org_id == org_id, UsageEvent.occurred_at >= start, UsageEvent.occurred_at < end))
    return int(total or 0)
