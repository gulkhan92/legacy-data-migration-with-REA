"""The billing of the caller's organization: plan, usage meter, invoices. Isolated by Row Level Security like everything else.

Plan, usage and the meter can be read by any signed in user; invoices and plan changes are for admins. There is no payment processor:
an admin marks an invoice paid or void, and a plan change takes effect at once.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.api.pagination import Page, PageParams
from app.billing import metering, plans
from app.billing.invoicing import period_of
from app.core.security import Principal
from app.db.models import Invoice, InvoiceLine, Plan, Subscription, UsageEvent
from app.db.models.enums import InvoiceStatus, SubscriptionStatus
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
admin_only = require_role()


class PlanOut(BaseModel):
    code: str
    name: str
    monthly_fee_cents: int
    included_records: int
    overage_cents_per_1000: float


class PlanChange(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"plan_code": "professional"}]})

    plan_code: str = Field(min_length=1, max_length=50)


class InvoiceStatusChange(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "paid"}]})

    status: Literal["paid", "void"]


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_start: date
    period_end: date
    status: InvoiceStatus
    total_cents: int
    issued_at: datetime
    due_at: datetime | None
    paid_at: datetime | None
    is_simulated: bool
    currency: str = "USD"


class LineOut(BaseModel):
    kind: str
    description: str
    quantity: float
    unit_price_cents: float
    amount_cents: int


class InvoiceDetail(InvoiceOut):
    plan: str | None
    lines: list[LineOut]


def _plan_out(terms: plans.PlanTerms) -> PlanOut:
    return PlanOut(code=terms.code, name=terms.name, monthly_fee_cents=terms.monthly_fee_cents, included_records=terms.included_records, overage_cents_per_1000=float(terms.overage_cents_per_1000))


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


@router.get("/subscription", summary="The current plan and this month's usage meter")
def subscription(principal: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    now = datetime.now(UTC)
    sub = metering.active_subscription(db, principal.org_id)
    catalogue = plans.load_plans(db, active_only=False)
    terms = catalogue[db.get(Plan, sub.plan_id).code] if sub else catalogue[plans.STARTER]  # type: ignore[union-attr]
    start, end = period_of(now.date())
    records = metering.records_in_period(db, principal.org_id, _midnight(start), _midnight(end + timedelta(days=1)))
    elapsed = max(1, (now.date() - start).days + 1)
    projected = round(records * ((end - start).days + 1) / elapsed)
    return {
        "plan": _plan_out(terms), "status": sub.status.value if sub else "default", "since": sub.started_at.isoformat() if sub else None,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "usage": {
            **plans.estimate(terms, records), "percent_of_allowance": round(100 * records / terms.included_records, 1) if terms.included_records else None,
            "projected_records": projected, "projected_total_cents": plans.estimate(terms, projected)["total_cents"],
        },
        "is_simulated": bool(sub.is_simulated) if sub else False,
    }  # fmt: skip


@router.get("/usage", summary="Usage events and the last twelve months")
def usage(page: PageParams = Depends(), principal: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(UsageEvent)) or 0
    events = db.scalars(select(UsageEvent).order_by(UsageEvent.occurred_at.desc()).limit(page.limit).offset(page.offset)).all()
    month = func.date_trunc("month", UsageEvent.occurred_at)
    rows = db.execute(
        select(month.label("m"), func.sum(UsageEvent.records_loaded), func.sum(UsageEvent.tokens_used), func.count()).group_by(month).order_by(month.desc()).limit(12)
    ).all()
    return {
        "items": [{"job_id": str(e.job_id) if e.job_id else None, "records_loaded": e.records_loaded, "tokens_used": e.tokens_used, "duration_ms": e.duration_ms, "occurred_at": e.occurred_at.isoformat()} for e in events],
        "total": int(total), "limit": page.limit, "offset": page.offset,
        "months": [{"month": m.date().isoformat(), "records": int(r or 0), "tokens": int(t or 0), "jobs": int(j)} for m, r, t, j in rows],
    }  # fmt: skip


@router.post("/subscription/plan", summary="Change the plan, effective immediately (admin)")
def change_plan(body: PlanChange, request: Request, principal: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    target = db.scalar(select(Plan).where(Plan.code == body.plan_code, Plan.is_active.is_(True)))
    if target is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown plan: {body.plan_code}")
    now = datetime.now(UTC)
    current = metering.ensure_subscription(db, principal.org_id, now=now)
    if current.plan_id == target.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That is already the current plan")
    previous = db.get(Plan, current.plan_id)
    current.ended_at, current.status = now, SubscriptionStatus.CANCELLED
    db.flush()
    db.add(Subscription(org_id=principal.org_id, plan_id=target.id, status=SubscriptionStatus.ACTIVE, started_at=now, is_simulated=False))
    db.flush()
    record_audit(db, AuditAction.PLAN_CHANGE, org_id=principal.org_id, user_id=principal.user_id, entity_type="plan", entity_id=target.code, ip=client_ip(request),
                 detail={"from": previous.code if previous else None, "to": target.code})  # fmt: skip
    return {"plan": _plan_out(plans.PlanTerms.of(target)), "since": now.isoformat(), "change": "upgrade" if previous and plans.is_upgrade(previous.code, target.code) else "downgrade"}


def _invoice_or_404(db: Session, invoice_id: uuid.UUID) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return invoice


@router.get("/invoices", response_model=Page[InvoiceOut], summary="The organization's invoices, newest first (admin)")
def list_invoices(
    invoice_status: InvoiceStatus | None = Query(None, alias="status"), page: PageParams = Depends(), _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function")
) -> Page[InvoiceOut]:
    stmt = select(Invoice)
    if invoice_status:
        stmt = stmt.where(Invoice.status == invoice_status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Invoice.period_end.desc(), Invoice.id).limit(page.limit).offset(page.offset)).all()
    return Page[InvoiceOut](items=[InvoiceOut.model_validate(r) for r in rows], total=int(total), limit=page.limit, offset=page.offset)


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail, summary="One invoice with its lines, ready to print (admin)")
def get_invoice(invoice_id: uuid.UUID, _: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function")) -> InvoiceDetail:
    invoice = _invoice_or_404(db, invoice_id)
    lines = db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.kind, InvoiceLine.amount_cents)).all()
    sub = db.get(Subscription, invoice.subscription_id) if invoice.subscription_id else None
    plan = db.get(Plan, sub.plan_id) if sub else None
    return InvoiceDetail(**InvoiceOut.model_validate(invoice).model_dump(), plan=plan.code if plan else None,
                         lines=[LineOut(kind=line.kind.value, description=line.description, quantity=float(line.quantity), unit_price_cents=float(line.unit_price_cents), amount_cents=line.amount_cents) for line in lines])  # fmt: skip


@router.patch("/invoices/{invoice_id}", response_model=InvoiceOut, summary="Mark an invoice paid or void (admin)")
def set_invoice_status(invoice_id: uuid.UUID, body: InvoiceStatusChange, request: Request, principal: Principal = Depends(admin_only), db: Session = Depends(get_tenant_db, scope="function")) -> Invoice:
    invoice = _invoice_or_404(db, invoice_id)
    if invoice.status not in (InvoiceStatus.OPEN, InvoiceStatus.OVERDUE):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"A {invoice.status.value} invoice cannot be changed")
    previous = invoice.status
    invoice.status = InvoiceStatus(body.status)
    if invoice.status is InvoiceStatus.PAID:
        invoice.paid_at = datetime.now(UTC)
    db.flush()
    record_audit(db, AuditAction.INVOICE_STATUS, org_id=principal.org_id, user_id=principal.user_id, entity_type="invoice", entity_id=str(invoice.id), ip=client_ip(request),
                 detail={"from": previous.value, "to": invoice.status.value})  # fmt: skip
    return invoice
