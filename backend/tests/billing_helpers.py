"""Build small billing worlds for the tests: organizations, subscriptions, jobs and usage, written as the table owner.

The tests use dates in 2019 so that nothing else in the shared test database (which starts at "now") can change their numbers,
and each world removes what it created.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.db import models
from app.db.models.enums import InvoiceStatus, JobStatus, LlmCallStatus, SourceKind, SubscriptionStatus


def at(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


class World:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.orgs: list[uuid.UUID] = []
        self.days: set[date] = set()
        self.plans: dict[str, uuid.UUID] = {}
        with Session(engine) as session:
            self.plans = {p.code: p.id for p in session.query(models.Plan)}

    def org(self, name: str, *, industry: str = "Retail & Commerce", region: str = "Europe", simulated: bool = True, created: date | None = None) -> uuid.UUID:
        org = models.Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}", industry=industry, region=region, is_simulated=simulated)
        with Session(self.engine) as session:
            session.add(org)
            session.flush()
            if created:
                session.execute(text("UPDATE organizations SET created_at = :c WHERE id = :i"), {"c": at(created), "i": org.id})
            session.commit()
            self.orgs.append(org.id)
            return org.id

    def subscribe(self, org: uuid.UUID, plan: str, start: date, end: date | None = None, *, simulated: bool = True) -> uuid.UUID:
        with Session(self.engine) as session:
            sub = models.Subscription(org_id=org, plan_id=self.plans[plan], status=SubscriptionStatus.CANCELLED if end else SubscriptionStatus.ACTIVE,
                                      started_at=at(start, 8), ended_at=at(end, 8) if end else None, is_simulated=simulated)  # fmt: skip
            session.add(sub)
            session.commit()
            self.days.update({start, end} - {None})
            return sub.id

    def job(self, org: uuid.UUID, day: date, *, ok: bool = True, records: int = 0, tokens: int = 0, duration_ms: int = 1000, quality: float | None = None) -> uuid.UUID:
        """A finished job. A completed one also gets a usage event, and a reconciliation report when a quality is given."""
        with Session(self.engine) as session:
            source = models.Source(org_id=org, name="s", kind=SourceKind.SQLITE)
            session.add(source)
            session.flush()
            job = models.Job(org_id=org, source_id=source.id, status=JobStatus.COMPLETED if ok else JobStatus.FAILED, tokens_used=tokens, started_at=at(day, 9), completed_at=at(day, 10))
            session.add(job)
            session.flush()
            if ok:
                session.add(models.UsageEvent(org_id=org, job_id=job.id, records_loaded=records, tokens_used=tokens, duration_ms=duration_ms, occurred_at=at(day, 10), is_simulated=True))
                if quality is not None:
                    session.add(models.ReconciliationReport(org_id=org, job_id=job.id, source_rows=records, loaded_rows=records, quarantined_rows=0, quality_before=80, quality_after=quality, passed=True))
                    session.flush()
                    session.execute(text("UPDATE reconciliation_reports SET created_at = :c WHERE job_id = :j"), {"c": at(day, 10), "j": job.id})
            session.commit()
            self.days.add(day)
            return job.id

    def usage(self, org: uuid.UUID, when: datetime, records: int) -> None:
        with Session(self.engine) as session:
            session.add(models.UsageEvent(org_id=org, records_loaded=records, tokens_used=0, duration_ms=1, occurred_at=when, is_simulated=True))
            session.commit()
            self.days.add(when.date())

    def llm(self, org: uuid.UUID, day: date, provider: str, status: LlmCallStatus, tokens_in: int = 0, tokens_out: int = 0, cache_hit: bool = False) -> None:
        with Session(self.engine) as session:
            call = models.LlmCall(org_id=org, provider=provider, model="m", task="resolve_mappings", tokens_in=tokens_in, tokens_out=tokens_out, status=status, cache_hit=cache_hit)
            session.add(call)
            session.flush()
            session.execute(text("UPDATE llm_calls SET created_at = :c WHERE id = :i"), {"c": at(day, 11), "i": call.id})
            session.commit()
            self.days.add(day)

    def invoice(self, org: uuid.UUID, sub: uuid.UUID, start: date, end: date, status: InvoiceStatus, base: int, overage: int = 0) -> uuid.UUID:
        with Session(self.engine) as session:
            invoice = models.Invoice(org_id=org, subscription_id=sub, period_start=start, period_end=end, status=status, total_cents=base + overage, issued_at=at(end, 12),
                                     due_at=at(end, 12), is_simulated=True)  # fmt: skip
            session.add(invoice)
            session.flush()
            session.add(models.InvoiceLine(org_id=org, invoice_id=invoice.id, kind="base", description="base", quantity=1, unit_price_cents=base, amount_cents=base))
            if overage:
                session.add(models.InvoiceLine(org_id=org, invoice_id=invoice.id, kind="overage", description="over", quantity=1, unit_price_cents=overage, amount_cents=overage))
            session.commit()
            self.days.add(end)
            return invoice.id

    def clean(self) -> None:
        with self.engine.begin() as connection:
            if self.orgs:
                connection.execute(text("DELETE FROM organizations WHERE id = ANY(:ids)"), {"ids": self.orgs})
            # Only the snapshots of the 2019 test dates are removed; the rows of the simulated history (2024 and later) are never touched.
            connection.execute(text("DELETE FROM daily_metrics WHERE metric_date < '2020-01-01'"))


@contextmanager
def world(engine: Engine) -> Iterator[World]:
    w = World(engine)
    try:
        yield w
    finally:
        w.clean()
