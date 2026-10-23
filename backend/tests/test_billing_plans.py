"""The arithmetic of a bill, without a database."""

from decimal import Decimal

import pytest

from app.billing import plans
from app.billing.plans import ENTERPRISE, PROFESSIONAL, STARTER, PlanTerms

STARTER_TERMS = PlanTerms(STARTER, "Starter", 0, 100_000, Decimal("50.00"))
PRO = PlanTerms(PROFESSIONAL, "Professional", 49_900, 2_000_000, Decimal("20.00"))
ENT = PlanTerms(ENTERPRISE, "Enterprise", 250_000, 25_000_000, Decimal("8.00"))


@pytest.mark.parametrize(
    "terms,records,overage_cents",
    [
        (PRO, 0, 0), (PRO, 2_000_000, 0), (PRO, 2_000_001, 0),  # a thousandth of a cent rounds away
        (PRO, 2_001_000, 20), (PRO, 3_000_000, 20_000), (PRO, 2_000_500, 10),
        (ENT, 25_000_000, 0), (ENT, 26_000_000, 8_000), (ENT, 25_000_125, 1),  # 0.125 thousand * 8 = 1 cent
        (STARTER_TERMS, 100_000, 0), (STARTER_TERMS, 150_000, 2_500),
    ],
)  # fmt: skip
def test_overage_is_charged_per_thousand_records_over_the_allowance(terms, records, overage_cents) -> None:  # type: ignore[no-untyped-def]
    assert plans.overage_cents(terms, records) == overage_cents


def test_half_a_cent_rounds_up_once_on_the_total() -> None:
    odd = PlanTerms("x", "X", 0, 0, Decimal("0.10"))
    assert plans.overage_cents(odd, 5_000) == 1  # 5 thousand * 0.10 = 0.5 cent -> 1
    assert plans.overage_cents(odd, 4_000) == 0  # 0.4 -> 0


def test_the_invoice_quantity_is_in_thousands() -> None:
    assert plans.overage_quantity(PRO, 2_500_000) == Decimal("500.000")
    assert plans.overage_quantity(PRO, 1_000_000) == Decimal("0.000")
    assert plans.overage_quantity(PRO, 2_000_123) == Decimal("0.123")


def test_estimate_adds_the_base_fee_and_the_overage() -> None:
    est = plans.estimate(PRO, 3_000_000)
    assert est == {"records": 3_000_000, "included_records": 2_000_000, "overage_records": 1_000_000, "base_cents": 49_900, "overage_cents": 20_000, "total_cents": 69_900}
    assert plans.estimate(STARTER_TERMS, 10)["total_cents"] == 0


def test_plans_are_ordered_from_cheapest() -> None:
    assert plans.ORDER == [STARTER, PROFESSIONAL, ENTERPRISE]
    assert plans.is_upgrade(STARTER, PROFESSIONAL) and plans.is_upgrade(PROFESSIONAL, ENTERPRISE) and not plans.is_upgrade(ENTERPRISE, PROFESSIONAL)


def test_the_installed_plans_are_the_illustrative_ones(admin_engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.orm import Session

    with Session(admin_engine) as session:
        installed = plans.load_plans(session)
    assert list(installed) == [STARTER, PROFESSIONAL, ENTERPRISE]
    assert (installed[PROFESSIONAL].monthly_fee_cents, installed[PROFESSIONAL].included_records, installed[PROFESSIONAL].overage_cents_per_1000) == (49_900, 2_000_000, Decimal("20.00"))
    assert (installed[ENTERPRISE].monthly_fee_cents, installed[ENTERPRISE].included_records, installed[ENTERPRISE].overage_cents_per_1000) == (250_000, 25_000_000, Decimal("8.00"))
    assert installed[STARTER].monthly_fee_cents == 0 and installed[STARTER].included_records == 100_000
