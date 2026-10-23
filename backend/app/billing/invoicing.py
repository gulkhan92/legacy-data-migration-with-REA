"""The monthly invoice run.

A subscription is billed for a calendar month when it is active on the last day of that month: the base fee of its plan, plus the
overage for the records the organization loaded during the month. Billing on the month end state is what makes the books balance:
the base lines of a month add up to the MRR of its last day. An invoice is created once per subscription and period (the database
enforces it), so running a month again changes nothing. A bill of zero (a free plan without overage) is not issued.
"""

import calendar
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.billing import metering, plans
from app.db.models import Invoice, InvoiceLine, Plan, Subscription
from app.db.models.enums import InvoiceLineKind, InvoiceStatus
from app.db.session import make_session_factory, scoped_session

log = logging.getLogger(__name__)

PAYMENT_TERMS_DAYS = 30


@dataclass
class InvoiceRun:
    period_start: date
    period_end: date
    created: int = 0
    skipped: int = 0
    total_cents: int = 0
    failed: list[str] = field(default_factory=list)


def period_of(day: date) -> tuple[date, date]:
    """The calendar month that contains `day`, as (first day, last day)."""
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def previous_month(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def subscription_at(session: Session, org_id: uuid.UUID, day: date) -> Subscription | None:
    """The subscription that is active at the end of `day`."""
    after = _midnight(day + timedelta(days=1))
    return session.scalar(
        select(Subscription)
        .where(Subscription.org_id == org_id, Subscription.started_at < after, (Subscription.ended_at.is_(None)) | (Subscription.ended_at >= after))
        .order_by(Subscription.started_at.desc())
        .limit(1)
    )


def invoice_for_org(session: Session, org_id: uuid.UUID, period_start: date, *, now: datetime | None = None, simulated: bool = False) -> Invoice | None:
    """Invoice one organization for one month. None when it is not billable or has been invoiced already."""
    start, end = period_of(period_start)
    subscription = subscription_at(session, org_id, end)
    if subscription is None:
        return None
    if session.scalar(select(Invoice.id).where(Invoice.subscription_id == subscription.id, Invoice.period_start == start)) is not None:
        return None
    plan = session.get(Plan, subscription.plan_id)
    terms = plans.PlanTerms.of(plan)  # type: ignore[arg-type]
    records = metering.records_in_period(session, org_id, _midnight(start), _midnight(end + timedelta(days=1)))

    label = start.strftime("%B %Y")
    lines = [(InvoiceLineKind.BASE, f"{terms.name} plan, {label}" + (" (free)" if terms.monthly_fee_cents == 0 else ""), 1, terms.monthly_fee_cents, terms.monthly_fee_cents)]
    over = plans.overage_cents(terms, records)
    if over > 0:
        lines.append((InvoiceLineKind.OVERAGE, f"{plans.overage_records(terms, records):,} records above the {terms.included_records:,} included in {label}, per 1,000",
                      plans.overage_quantity(terms, records), terms.overage_cents_per_1000, over))  # fmt: skip
    total = sum(line[4] for line in lines)
    if total == 0:
        return None

    issued = max(now or datetime.now(UTC), _midnight(end + timedelta(days=1)))
    invoice = Invoice(org_id=org_id, subscription_id=subscription.id, period_start=start, period_end=end, status=InvoiceStatus.OPEN, total_cents=total, issued_at=issued,
                      due_at=issued + timedelta(days=PAYMENT_TERMS_DAYS), is_simulated=simulated)  # fmt: skip
    session.add(invoice)
    session.flush()
    for kind, description, quantity, unit_price, amount in lines:
        session.add(InvoiceLine(org_id=org_id, invoice_id=invoice.id, kind=kind, description=description, quantity=quantity, unit_price_cents=unit_price, amount_cents=amount))
    session.flush()
    return invoice


def billable_org_ids(session: Session, day: date) -> list[uuid.UUID]:
    return [row[0] for row in session.execute(text("SELECT org_id FROM platform_billable_orgs(:d)"), {"d": day})]


def run_invoices(period_start: date, *, now: datetime | None = None, factory: sessionmaker[Session] | None = None) -> InvoiceRun:
    """Invoice every organization for a month, each in its own transaction scoped to that organization, then refresh the snapshot of the period's last day."""
    from app.billing import snapshots

    start, end = period_of(period_start)
    run = InvoiceRun(start, end)
    factory = factory or make_session_factory()
    now = now or datetime.now(UTC)
    with scoped_session(factory=factory) as db:
        org_ids = billable_org_ids(db, end)
    for org_id in org_ids:
        try:
            with scoped_session(org_id=org_id, factory=factory) as db:
                invoice = invoice_for_org(db, org_id, start, now=now)
            if invoice is None:
                run.skipped += 1
            else:
                run.created += 1
                run.total_cents += invoice.total_cents
        except Exception as exc:  # one organization must not stop the others
            log.exception("invoice for %s %s failed", org_id, start)
            run.failed.append(f"{org_id}: {type(exc).__name__}")
    with scoped_session(factory=factory) as db:
        db.execute(text("SELECT platform_mark_overdue(:d)"), {"d": now.astimezone(UTC).date()})
    snapshots.take_snapshot(end, factory=factory)
    return run
