import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin, TenantMixin
from app.db.models._types import str_enum
from app.db.models.enums import InvoiceLineKind, InvoiceStatus, SubscriptionStatus


class Plan(IdMixin, CreatedAtMixin, Base):
    """Illustrative plans. Platform wide, readable by everyone."""

    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    monthly_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    included_records: Mapped[int] = mapped_column(BigInteger, nullable=False)
    overage_cents_per_1000: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class Subscription(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "subscriptions"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        str_enum(SubscriptionStatus, "subscription_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class UsageEvent(IdMixin, TenantMixin, Base):
    __tablename__ = "usage_events"

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    records_loaded: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class Invoice(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "invoices"

    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(str_enum(InvoiceStatus, "invoice_status"), nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class InvoiceLine(IdMixin, TenantMixin, Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[InvoiceLineKind] = mapped_column(str_enum(InvoiceLineKind, "invoice_line_kind"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    unit_price_cents: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DailyMetric(IdMixin, CreatedAtMixin, Base):
    """Platform wide nightly snapshot, read by admins through the analytics API."""

    __tablename__ = "daily_metrics"

    metric_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    active_orgs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    new_orgs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    mrr_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    new_mrr_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    expansion_mrr_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    contraction_mrr_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    churned_mrr_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    usage_revenue_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    jobs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    tokens_groq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    tokens_gemini: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    failovers: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    success_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    avg_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
