"""Plans and the arithmetic of a bill. Pure functions: no database, no clock."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Plan

STARTER, PROFESSIONAL, ENTERPRISE = "starter", "professional", "enterprise"
ORDER = [STARTER, PROFESSIONAL, ENTERPRISE]  # cheapest first: moving right is an upgrade


@dataclass(frozen=True)
class PlanTerms:
    code: str
    name: str
    monthly_fee_cents: int
    included_records: int
    overage_cents_per_1000: Decimal

    @classmethod
    def of(cls, plan: Plan) -> "PlanTerms":
        return cls(plan.code, plan.name, int(plan.monthly_fee_cents), int(plan.included_records), Decimal(plan.overage_cents_per_1000))


def overage_records(terms: PlanTerms, records: int) -> int:
    return max(0, int(records) - terms.included_records)


def overage_quantity(terms: PlanTerms, records: int) -> Decimal:
    """Thousands of records above the allowance, as printed on the invoice line."""
    return (Decimal(overage_records(terms, records)) / Decimal(1000)).quantize(Decimal("0.001"), ROUND_HALF_UP)


def overage_cents(terms: PlanTerms, records: int) -> int:
    """The overage charge in whole cents. The rate is per 1,000 records, a part of a thousand is charged in proportion, and the total is rounded half up once."""
    cents = Decimal(overage_records(terms, records)) / Decimal(1000) * terms.overage_cents_per_1000
    return int(cents.quantize(Decimal(1), ROUND_HALF_UP))


def estimate(terms: PlanTerms, records: int) -> dict[str, int]:
    over = overage_cents(terms, records)
    return {
        "records": int(records), "included_records": terms.included_records, "overage_records": overage_records(terms, records),
        "base_cents": terms.monthly_fee_cents, "overage_cents": over, "total_cents": terms.monthly_fee_cents + over,
    }  # fmt: skip


def is_upgrade(old_code: str, new_code: str) -> bool:
    return ORDER.index(new_code) > ORDER.index(old_code)


def load_plans(session: Session, *, active_only: bool = True) -> dict[str, PlanTerms]:
    stmt = select(Plan).order_by(Plan.monthly_fee_cents)
    if active_only:
        stmt = stmt.where(Plan.is_active.is_(True))
    return {p.code: PlanTerms.of(p) for p in session.scalars(stmt)}
