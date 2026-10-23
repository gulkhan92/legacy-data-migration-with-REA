"""The nightly metrics snapshot: one `daily_metrics` row per day, computed by the database from subscriptions, invoices, jobs and LLM calls."""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import DailyMetric
from app.db.session import make_session_factory, scoped_session

log = logging.getLogger(__name__)

INT_COLUMNS = (
    "active_orgs", "paying_orgs", "new_orgs", "mrr_cents", "new_mrr_cents", "expansion_mrr_cents", "contraction_mrr_cents", "churned_mrr_cents",
    "subscription_revenue_cents", "usage_revenue_cents", "jobs", "records", "tokens_groq", "tokens_gemini", "failovers", "cache_hits",
)  # fmt: skip
OPTIONAL_COLUMNS = ("success_rate", "avg_duration_ms", "quality_score", "nrr_12m")


def compute_day(session: Session, day: date) -> dict[str, Any]:
    raw = session.execute(text("SELECT platform_day_metrics(:d)"), {"d": day}).scalar_one()
    data = raw if isinstance(raw, dict) else json.loads(raw)
    values: dict[str, Any] = {name: int(data[name] or 0) for name in INT_COLUMNS}
    for name in OPTIONAL_COLUMNS:
        values[name] = None if data.get(name) is None else (int(data[name]) if name == "avg_duration_ms" else Decimal(str(data[name])))
    values["breakdown"] = data.get("breakdown") or {}
    values["is_simulated"] = bool(data.get("is_simulated"))
    return values


def store_day(session: Session, day: date, values: dict[str, Any]) -> None:
    statement = pg_insert(DailyMetric).values(metric_date=day, **values)
    update = {name: statement.excluded[name] for name in values}
    session.execute(statement.on_conflict_do_update(index_elements=["metric_date"], set_=update))


def take_snapshot(day: date, *, factory: sessionmaker[Session] | None = None) -> dict[str, Any]:
    """Compute and store the snapshot of one day. Running it again for the same day replaces the row with the current numbers."""
    with scoped_session(factory=factory or make_session_factory()) as db:
        values = compute_day(db, day)
        store_day(db, day, values)
    return values


def snapshot_range(first: date, last: date, *, factory: sessionmaker[Session] | None = None) -> int:
    factory = factory or make_session_factory()
    count, day = 0, first
    while day <= last:
        take_snapshot(day, factory=factory)
        count += 1
        day += timedelta(days=1)
    return count


def nightly(now: datetime | None = None, *, factory: sessionmaker[Session] | None = None) -> list[date]:
    """What the nightly job does: close yesterday and refresh today, so the dashboards are never more than a day behind."""
    today = (now or datetime.now(UTC)).astimezone(UTC).date()
    days = [today - timedelta(days=1), today]
    for day in days:
        take_snapshot(day, factory=factory)
    return days
