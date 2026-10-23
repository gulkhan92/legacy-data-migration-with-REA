"""An organization's plan, usage meter and invoices, the plan change, and the public pricing endpoints."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.billing import invoicing, plans
from app.db import models
from app.db.models.enums import Role
from tests.billing_helpers import at, world
from tests.conftest import login_as, make_user


@pytest.fixture
def shop(client, admin_engine, api_users):  # type: ignore[no-untyped-def]
    """A live organization of its own with an admin, an analyst and a viewer."""
    with world(admin_engine) as w:
        org = w.org("Shop", simulated=False)
        emails = {role.value: f"{role.value}-{uuid.uuid4().hex[:6]}@shop.test" for role in Role}
        users = [make_user(admin_engine, email, Role(role), org) for role, email in emails.items()]
        yield type("Shop", (), {"world": w, "org": org, "emails": emails, "engine": admin_engine})
        with admin_engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
        client.cookies.clear()


def as_(client, shop, role: str) -> None:  # type: ignore[no-untyped-def]
    client.cookies.clear()
    assert login_as(client, shop.emails[role]).status_code == 200


def test_an_organization_without_a_subscription_is_on_the_free_plan(client, shop) -> None:  # type: ignore[no-untyped-def]
    as_(client, shop, "viewer")
    body = client.get("/api/v1/billing/subscription").json()
    assert body["plan"]["code"] == "starter" and body["status"] == "default" and body["since"] is None
    assert body["usage"]["records"] == 0 and body["usage"]["included_records"] == 100_000 and body["usage"]["percent_of_allowance"] == 0.0 and body["usage"]["total_cents"] == 0
    assert body["period"]["start"] == date.today().replace(day=1).isoformat()


def test_the_usage_meter_prices_this_months_records(client, shop) -> None:  # type: ignore[no-untyped-def]
    shop.world.usage(shop.org, datetime.now(UTC), 1_500_000)
    as_(client, shop, "viewer")
    usage = client.get("/api/v1/billing/subscription").json()["usage"]
    starter = plans.PlanTerms("starter", "Starter", 0, 100_000, Decimal("50.00"))
    assert usage["records"] == 1_500_000 and usage["percent_of_allowance"] == 1500.0
    assert usage["overage_records"] == 1_400_000 and usage["overage_cents"] == plans.overage_cents(starter, 1_500_000) == 70_000 and usage["total_cents"] == 70_000
    assert usage["projected_records"] >= 1_500_000 and usage["projected_total_cents"] >= usage["total_cents"]
    listing = client.get("/api/v1/billing/usage").json()
    assert listing["total"] == 1 and listing["items"][0]["records_loaded"] == 1_500_000 and listing["months"][0]["records"] == 1_500_000 and listing["months"][0]["jobs"] == 1


def test_an_admin_changes_the_plan_and_the_meter_follows(client, shop) -> None:  # type: ignore[no-untyped-def]
    shop.world.usage(shop.org, datetime.now(UTC), 1_500_000)
    as_(client, shop, "admin")
    changed = client.post("/api/v1/billing/subscription/plan", json={"plan_code": "professional"})
    assert changed.status_code == 200 and changed.json()["change"] == "upgrade" and changed.json()["plan"]["monthly_fee_cents"] == 49_900
    body = client.get("/api/v1/billing/subscription").json()
    assert body["plan"]["code"] == "professional" and body["status"] == "active" and body["since"]
    assert body["usage"]["overage_cents"] == 0 and body["usage"]["total_cents"] == 49_900  # the same records, a bigger allowance
    assert client.post("/api/v1/billing/subscription/plan", json={"plan_code": "professional"}).status_code == 409
    assert client.post("/api/v1/billing/subscription/plan", json={"plan_code": "platinum"}).status_code == 422
    assert client.post("/api/v1/billing/subscription/plan", json={"plan_code": "starter"}).json()["change"] == "downgrade"
    with Session(shop.engine) as session:  # the history of plans is kept, and only one is active
        rows = session.query(models.Subscription).filter_by(org_id=shop.org).order_by(models.Subscription.started_at).all()
        assert [r.ended_at is None for r in rows] == [False, False, True]  # the free plan it started on, Professional, and the free plan again
        assert [session.get(models.Plan, r.plan_id).code for r in rows] == ["starter", "professional", "starter"]
    actions = [e["action"] for e in client.get("/api/v1/admin/audit-log?limit=20").json()]
    assert actions.count("billing.plan_change") == 2


def test_only_admins_change_plans_and_see_invoices(client, shop) -> None:  # type: ignore[no-untyped-def]
    for role in ("analyst", "viewer"):
        as_(client, shop, role)
        assert client.post("/api/v1/billing/subscription/plan", json={"plan_code": "professional"}).status_code == 403
        assert client.get("/api/v1/billing/invoices").status_code == 403
        assert client.get("/api/v1/billing/subscription").status_code == 200 and client.get("/api/v1/billing/usage").status_code == 200


def make_invoices(shop, app_factory):  # type: ignore[no-untyped-def]
    shop.world.subscribe(shop.org, "professional", date(2019, 1, 1), date(2019, 6, 1), simulated=False)
    for when, records in [(at(date(2019, 3, 10)), 2_500_000), (at(date(2019, 4, 10)), 100)]:
        shop.world.usage(shop.org, when, records)
    for month in (date(2019, 3, 1), date(2019, 4, 1)):
        invoicing.run_invoices(month, now=at(date(2019, month.month + 1, 2)), factory=app_factory)


def test_invoices_are_listed_and_printable(client, shop, app_factory) -> None:  # type: ignore[no-untyped-def]
    make_invoices(shop, app_factory)
    as_(client, shop, "admin")
    page = client.get("/api/v1/billing/invoices").json()
    assert set(page) == {"items", "total", "limit", "offset"} and page["total"] == 2
    newest, oldest = page["items"]
    assert (newest["period_end"], oldest["period_end"]) == ("2019-04-30", "2019-03-31") and oldest["currency"] == "USD" and oldest["total_cents"] == 49_900 + 10_000
    detail = client.get(f"/api/v1/billing/invoices/{oldest['id']}").json()
    assert detail["plan"] == "professional" and [(line["kind"], line["amount_cents"]) for line in detail["lines"]] == [("base", 49_900), ("overage", 10_000)]
    assert sum(line["amount_cents"] for line in detail["lines"]) == detail["total_cents"]
    assert "March 2019" in detail["lines"][0]["description"] and detail["lines"][1]["quantity"] == 500.0 and detail["lines"][1]["unit_price_cents"] == 20.0
    assert client.get("/api/v1/billing/invoices?status=open").json()["total"] == 2 and client.get("/api/v1/billing/invoices?status=paid").json()["total"] == 0
    assert client.get(f"/api/v1/billing/invoices/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/v1/billing/invoices?status=lost").status_code == 422


def test_an_admin_marks_invoices_paid_or_void(client, shop, app_factory) -> None:  # type: ignore[no-untyped-def]
    make_invoices(shop, app_factory)
    as_(client, shop, "admin")
    newest, oldest = client.get("/api/v1/billing/invoices").json()["items"]
    paid = client.patch(f"/api/v1/billing/invoices/{oldest['id']}", json={"status": "paid"})
    assert paid.status_code == 200 and paid.json()["status"] == "paid" and paid.json()["paid_at"]
    assert client.patch(f"/api/v1/billing/invoices/{oldest['id']}", json={"status": "void"}).status_code == 409  # money was received: no voiding
    assert client.patch(f"/api/v1/billing/invoices/{oldest['id']}", json={"status": "paid"}).status_code == 409
    void = client.patch(f"/api/v1/billing/invoices/{newest['id']}", json={"status": "void"})
    assert void.status_code == 200 and void.json()["status"] == "void" and void.json()["paid_at"] is None
    assert client.patch(f"/api/v1/billing/invoices/{newest['id']}", json={"status": "paid"}).status_code == 409
    assert client.patch(f"/api/v1/billing/invoices/{newest['id']}", json={"status": "open"}).status_code == 422
    assert client.patch(f"/api/v1/billing/invoices/{uuid.uuid4()}", json={"status": "paid"}).status_code == 404
    assert [e["action"] for e in client.get("/api/v1/admin/audit-log?limit=10").json()].count("billing.invoice_status") == 2


def test_invoices_are_private_to_their_organization(client, shop, app_factory, api_users) -> None:  # type: ignore[no-untyped-def]
    make_invoices(shop, app_factory)
    as_(client, shop, "admin")
    invoice = client.get("/api/v1/billing/invoices").json()["items"][0]["id"]
    client.cookies.clear()
    assert login_as(client, "admin@globex.test").status_code == 200
    assert client.get("/api/v1/billing/invoices").json()["total"] == 0
    assert client.get(f"/api/v1/billing/invoices/{invoice}").status_code == 404
    assert client.patch(f"/api/v1/billing/invoices/{invoice}", json={"status": "paid"}).status_code == 404


def test_pricing_is_public(client) -> None:  # type: ignore[no-untyped-def]
    client.cookies.clear()
    response = client.get("/api/v1/public/plans")
    assert response.status_code == 200 and response.headers["cache-control"] == "public, max-age=300"
    listed = response.json()
    assert [p["code"] for p in listed] == ["starter", "professional", "enterprise"]
    assert [(p["monthly_fee_cents"], p["included_records"], p["overage_cents_per_1000"]) for p in listed] == [(0, 100_000, 50.0), (49_900, 2_000_000, 20.0), (250_000, 25_000_000, 8.0)]


def test_the_cost_calculator(client) -> None:  # type: ignore[no-untyped-def]
    estimate = lambda plan, records: client.get("/api/v1/public/pricing/estimate", params={"plan": plan, "records": records})  # noqa: E731
    client.cookies.clear()
    assert estimate("professional", 3_000_000).json() == {"plan": "professional", "currency": "USD", "records": 3_000_000, "included_records": 2_000_000, "overage_records": 1_000_000,
                                                          "base_cents": 49_900, "overage_cents": 20_000, "total_cents": 69_900}  # fmt: skip
    assert estimate("enterprise", 25_000_000).json()["total_cents"] == 250_000 and estimate("starter", 50).json()["total_cents"] == 0
    assert estimate("platinum", 1).status_code == 404 and estimate("starter", -1).status_code == 422 and estimate("starter", "many").status_code == 422
