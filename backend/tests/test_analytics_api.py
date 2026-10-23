"""The analytics endpoints, on the seeded history: shapes, ranges, granularity, comparison, exports, caching, and what each role may see."""

import csv
import io
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.api.routes.analytics import _safe
from app.billing import cache, snapshots
from app.billing.analytics import REVENUE_SERIES
from app.core.config import get_settings
from app.db.models.enums import InvoiceStatus
from tests.billing_helpers import world
from tests.conftest import login_as


@pytest.fixture
def admin(client, history, api_users):  # type: ignore[no-untyped-def]
    assert login_as(client, "admin@acme.test").status_code == 200
    return client


def sql(engine, query: str, **params):  # type: ignore[no-untyped-def]
    with engine.connect() as c:
        return c.execute(text(query), params).scalar()


def test_revenue_default_range_is_the_last_90_days_and_reconciles(admin) -> None:  # type: ignore[no-untyped-def]
    body = admin.get("/api/v1/analytics/revenue").json()
    assert body["range"] == {"from": "2026-07-25", "to": "2026-10-22"} and body["granularity"] == "day" and len(body["series"]) == 90
    assert set(body["series"][0]) == {"period", *REVENUE_SERIES}
    checks = body["checks"]
    assert checks["complete"] and checks["mrr_movements_add_up"] and checks["revenue_matches_invoices"]
    assert checks["revenue_shown_cents"] == checks["invoiced_cents"] == checks["base_invoiced_cents"] + checks["overage_invoiced_cents"] > 0
    assert checks["mrr_end_cents"] == checks["mrr_start_cents"] + checks["net_new_mrr_cents"]
    assert body["simulated"] is True and "simulated" in body["footnote"]
    assert "previous" not in body and "change" not in body


@pytest.mark.parametrize("granularity,count", [("day", 181), ("week", 27), ("month", 6)])
def test_granularity_sets_the_number_of_points(admin, granularity, count) -> None:  # type: ignore[no-untyped-def]
    body = admin.get("/api/v1/analytics/revenue", params={"from": "2026-01-01", "to": "2026-06-30", "granularity": granularity}).json()
    assert len(body["series"]) == count
    assert body["checks"]["mrr_movements_add_up"] and body["checks"]["revenue_matches_invoices"]  # at any granularity the range is the same


def test_the_kpis_are_the_database_numbers(admin, admin_engine) -> None:  # type: ignore[no-untyped-def]
    k = admin.get("/api/v1/analytics/revenue", params={"from": "2026-03-01", "to": "2026-03-31", "granularity": "month"}).json()["kpis"]
    a, b = date(2026, 3, 1), date(2026, 3, 31)
    total = lambda col: sql(admin_engine, f"SELECT sum({col}) FROM daily_metrics WHERE metric_date BETWEEN :a AND :b", a=a, b=b)  # noqa: E731
    assert k["mrr_cents"] == sql(admin_engine, "SELECT mrr_cents FROM daily_metrics WHERE metric_date = :b", b=b)
    assert k["mrr_start_cents"] == sql(admin_engine, "SELECT mrr_cents FROM daily_metrics WHERE metric_date = '2026-02-28'")
    assert (k["new_mrr_cents"], k["expansion_mrr_cents"], k["contraction_mrr_cents"], k["churned_mrr_cents"]) == tuple(
        int(total(c)) for c in ("new_mrr_cents", "expansion_mrr_cents", "contraction_mrr_cents", "churned_mrr_cents"))  # fmt: skip
    assert k["net_new_mrr_cents"] == k["new_mrr_cents"] + k["expansion_mrr_cents"] - k["contraction_mrr_cents"] - k["churned_mrr_cents"]
    assert k["mrr_cents"] == k["mrr_start_cents"] + k["net_new_mrr_cents"] and k["arr_cents"] == 12 * k["mrr_cents"]
    assert k["total_revenue_cents"] == int(total("subscription_revenue_cents") + total("usage_revenue_cents"))
    paying = sql(admin_engine, "SELECT paying_orgs FROM daily_metrics WHERE metric_date = :b", b=b)
    assert k["arpa_cents"] == round(k["mrr_cents"] / paying) and k["paying_orgs"] == paying
    assert k["gross_churn_rate"] == pytest.approx(k["churned_mrr_cents"] / k["mrr_start_cents"], abs=1e-4)
    assert float(k["net_revenue_retention_12m"]) == float(sql(admin_engine, "SELECT nrr_12m FROM daily_metrics WHERE metric_date = :b", b=b))
    assert k["usage_share"] == pytest.approx(k["usage_revenue_cents"] / k["total_revenue_cents"], abs=1e-4)


def test_net_revenue_retention_matches_a_recomputation_from_the_subscriptions(admin, admin_engine) -> None:  # type: ignore[no-untyped-def]
    """NRR on 2026-10-22: what the organizations that paid on 2025-10-22 pay now, over what they paid then."""
    query = """
        WITH fee AS (
          SELECT s.org_id, p.monthly_fee_cents AS f, s.started_at::date AS a, s.ended_at::date AS z FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.is_simulated),
        then_ AS (SELECT org_id, f FROM fee WHERE a <= date '2025-10-22' AND (z IS NULL OR z > date '2025-10-22') AND f > 0),
        now_ AS (SELECT org_id, f FROM fee WHERE a <= date '2026-10-22' AND (z IS NULL OR z > date '2026-10-22'))
        SELECT sum(COALESCE(n.f, 0))::numeric / sum(t.f) FROM then_ t LEFT JOIN now_ n ON n.org_id = t.org_id"""
    expected = float(sql(admin_engine, query))
    got = admin.get("/api/v1/analytics/revenue", params={"from": "2026-10-22", "to": "2026-10-22"}).json()["kpis"]["net_revenue_retention_12m"]
    assert got == pytest.approx(expected, abs=1e-4) and 0.3 < got < 1.6


def test_compare_returns_the_previous_period_and_the_change(admin) -> None:  # type: ignore[no-untyped-def]
    body = admin.get("/api/v1/analytics/revenue", params={"from": "2026-07-01", "to": "2026-09-30", "granularity": "month", "compare": "true"}).json()
    assert body["previous"]["range"] == {"from": "2026-03-31", "to": "2026-06-30"}  # 92 days like the range, ending the day before it
    mrr = body["change"]["mrr_cents"]
    assert mrr["current"] == body["kpis"]["mrr_cents"] and mrr["previous"] == body["previous"]["kpis"]["mrr_cents"] and mrr["delta"] == mrr["current"] - mrr["previous"]
    assert mrr["change"] == pytest.approx(mrr["delta"] / mrr["previous"], abs=1e-4)
    assert set(body["change"]) >= {"mrr_cents", "net_new_mrr_cents", "total_revenue_cents", "gross_churn_rate", "net_revenue_retention_12m", "arpa_cents"}
    early = admin.get("/api/v1/analytics/revenue", params={"from": "2024-11-01", "to": "2024-12-31", "compare": "true"}).json()
    assert "previous" not in early  # nothing before the first snapshot to compare with


def test_ranges_are_validated(admin) -> None:  # type: ignore[no-untyped-def]
    get = lambda **p: admin.get("/api/v1/analytics/revenue", params=p)  # noqa: E731
    assert get(**{"from": "2026-05-02", "to": "2026-05-01"}).status_code == 422
    assert get(**{"from": "2023-01-01", "to": "2026-01-01"}).status_code == 422  # more than 800 days
    assert get(granularity="year").status_code == 422
    assert get(**{"from": "not-a-date"}).status_code == 422
    none = get(**{"from": "2010-01-01", "to": "2010-01-31"})
    assert none.status_code == 404 and "snapshots" in none.json()["detail"]


def test_breakdowns_add_up_to_the_totals(admin) -> None:  # type: ignore[no-untyped-def]
    mrr = admin.get("/api/v1/analytics/revenue", params={"from": "2026-07-01", "to": "2026-09-30"}).json()["kpis"]["mrr_cents"]
    for dimension, keys in (("plan", {"starter", "professional", "enterprise"}), ("industry", None), ("region", {"North America", "Europe", "Asia Pacific", "Latin America"})):
        body = admin.get("/api/v1/analytics/revenue/breakdown", params={"dimension": dimension, "from": "2026-07-01", "to": "2026-09-30", "granularity": "month"}).json()
        assert [p["period"] for p in body["mrr"]] == ["2026-07-01", "2026-08-01", "2026-09-01"] and len(body["usage_revenue"]) == 3
        assert body["current"]["total_mrr_cents"] == sum(body["current"]["mrr_cents"].values()) == mrr
        assert sum(p for p in body["current"]["share"].values()) == pytest.approx(1.0, abs=0.01)
        if keys:
            assert set(body["keys"]) == keys
        else:
            assert len(body["keys"]) == 5
    assert admin.get("/api/v1/analytics/revenue/breakdown", params={"dimension": "color"}).status_code == 422


def test_usage_revenue_by_plan_adds_up_to_usage_revenue(admin) -> None:  # type: ignore[no-untyped-def]
    window = {"from": "2025-11-01", "to": "2026-09-30", "granularity": "month"}
    usage = admin.get("/api/v1/analytics/revenue", params=window).json()["kpis"]["usage_revenue_cents"]
    body = admin.get("/api/v1/analytics/revenue/breakdown", params={"dimension": "plan", **window}).json()
    assert sum(sum(p["values"].values()) for p in body["usage_revenue"]) == usage > 0


def test_top_customers(admin) -> None:  # type: ignore[no-untyped-def]
    body = admin.get("/api/v1/analytics/customers/top").json()
    items = body["items"]
    assert body["as_of"] == "2026-10-22" and len(items) == 10
    revenue = [i["revenue_12m_cents"] for i in items]
    assert revenue == sorted(revenue, reverse=True) and revenue[0] > 0
    for item in items:
        assert len(item["trend"]) == 12 and sum(item["trend"]) == item["revenue_12m_cents"]
        assert item["simulated"] is True and not item["name"].startswith("Live organization") and item["plan"] in {"starter", "professional", "enterprise"}
    assert len(admin.get("/api/v1/analytics/customers/top", params={"limit": 3}).json()["items"]) == 3
    assert admin.get("/api/v1/analytics/customers/top", params={"limit": 0}).status_code == 422
    assert admin.get("/api/v1/analytics/customers/top", params={"limit": 51}).status_code == 422


def test_a_live_organization_never_appears_by_name(admin, admin_engine) -> None:  # type: ignore[no-untyped-def]
    secret = f"SecretCorp {uuid.uuid4().hex[:8]}"
    with world(admin_engine) as w:
        org = w.org(secret, simulated=False)
        sub = w.subscribe(org, "enterprise", date(2025, 6, 1), simulated=False)
        w.invoice(org, sub, date(2026, 9, 1), date(2026, 9, 30), InvoiceStatus.PAID, 250_000, 9_000_000)  # large enough to be the top customer
        snapshots.take_snapshot(date(2026, 9, 30))  # the new invoice is part of the books from now on
        try:
            top = admin.get("/api/v1/analytics/customers/top").json()
            assert secret not in str(top)
            assert top["items"][0]["name"].startswith("Live organization ") and top["items"][0]["simulated"] is False
            page = admin.get("/api/v1/analytics/invoices", params={"q": "Live organization"}).json()
            assert page["total"] >= 1 and secret not in str(page) and all(i["customer"].startswith("Live organization") for i in page["items"])
            assert secret not in admin.get("/api/v1/analytics/invoices.csv").text
        finally:
            w.clean()
            snapshots.take_snapshot(date(2026, 9, 30))
            cache.reset()


def test_invoices_table_filters_and_paginates(admin, admin_engine) -> None:  # type: ignore[no-untyped-def]
    page = admin.get("/api/v1/analytics/invoices").json()
    assert page["limit"] == 25 and page["offset"] == 0 and len(page["items"]) == 25 and page["total"] > 900
    first = page["items"][0]
    assert set(first) >= {"id", "customer", "industry", "region", "plan_code", "period_start", "period_end", "status", "total_cents", "issued_at", "due_at", "paid_at"}
    periods = [i["period_end"] for i in page["items"]]
    assert periods == sorted(periods, reverse=True)
    nxt = admin.get("/api/v1/analytics/invoices", params={"offset": 25}).json()
    assert {i["id"] for i in nxt["items"]}.isdisjoint({i["id"] for i in page["items"]}) and nxt["total"] == page["total"]
    overdue = admin.get("/api/v1/analytics/invoices", params={"status": "overdue", "limit": 200}).json()
    assert overdue["total"] > 0 and {i["status"] for i in overdue["items"]} == {"overdue"}
    assert overdue["total"] == sql(admin_engine, "SELECT count(*) FROM invoices WHERE is_simulated AND status = 'overdue'")
    month = admin.get("/api/v1/analytics/invoices", params={"from": "2026-09-01", "to": "2026-09-30", "limit": 200}).json()
    assert month["total"] > 50 and {i["period_end"] for i in month["items"]} == {"2026-09-30"}
    name = first["customer"].split()[0]
    found = admin.get("/api/v1/analytics/invoices", params={"q": name.lower()}).json()
    assert found["total"] >= 1 and all(name.lower() in i["customer"].lower() for i in found["items"])
    assert admin.get("/api/v1/analytics/invoices", params={"q": "%_%"}).status_code == 200  # wildcards are neutralised, not an error
    assert admin.get("/api/v1/analytics/invoices", params={"status": "lost"}).status_code == 422
    assert admin.get("/api/v1/analytics/invoices", params={"limit": 201}).status_code == 422


def test_operations_for_analysts_and_admins(client, history, api_users, admin_engine) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    body = client.get("/api/v1/analytics/operations", params={"from": "2026-09-01", "to": "2026-09-30", "granularity": "week", "compare": "true"}).json()
    k = body["kpis"]
    assert k["jobs"] == sql(admin_engine, "SELECT sum(jobs) FROM daily_metrics WHERE metric_date BETWEEN '2026-09-01' AND '2026-09-30'") and k["jobs"] > 100
    assert k["records"] == sql(admin_engine, "SELECT sum(records) FROM daily_metrics WHERE metric_date BETWEEN '2026-09-01' AND '2026-09-30'")
    assert 0.85 < k["success_rate"] < 1.0 and 90 < k["quality_score"] < 100 and k["avg_duration_ms"] > 0
    assert k["tokens_total"] == k["tokens_groq"] + k["tokens_gemini"] and k["tokens_groq"] > k["tokens_gemini"] > 0 and k["failovers"] > 0 and k["cache_hits"] > 0
    assert len(body["series"]) == 5 and "previous" in body and "jobs" in body["change"]
    assert "mrr_cents" not in body["kpis"]  # an analyst sees operations, not revenue
    assert client.get("/api/v1/analytics/revenue").status_code == 403


def test_csv_exports_match_the_json(admin) -> None:  # type: ignore[no-untyped-def]
    params = {"from": "2026-01-01", "to": "2026-03-31", "granularity": "month"}
    series = admin.get("/api/v1/analytics/revenue", params=params).json()["series"]
    response = admin.get("/api/v1/analytics/revenue.csv", params=params)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="revenue-2026-01-01-2026-03-31-month.csv"' and response.headers["x-content-type-options"] == "nosniff"
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["period"] for r in rows] == [p["period"] for p in series] and [int(r["mrr_cents"]) for r in rows] == [p["mrr_cents"] for p in series]
    assert list(rows[0]) == ["period", *REVENUE_SERIES]
    ops = admin.get("/api/v1/analytics/operations.csv", params={"from": "2026-03-01", "to": "2026-03-07"}).text.splitlines()
    assert len(ops) == 8 and ops[0].startswith("period,jobs,success_rate")


def test_the_invoice_export_has_every_invoice(admin) -> None:  # type: ignore[no-untyped-def]
    total = admin.get("/api/v1/analytics/invoices", params={"limit": 1}).json()["total"]
    rows = list(csv.reader(io.StringIO(admin.get("/api/v1/analytics/invoices.csv").text)))
    assert len(rows) - 1 == total > 900 and rows[0][:3] == ["id", "customer", "industry"]
    only = list(csv.reader(io.StringIO(admin.get("/api/v1/analytics/invoices.csv", params={"status": "void"}).text)))
    assert 0 < len(only) - 1 < 40 and {r[7] for r in only[1:]} == {"void"}


@pytest.mark.parametrize("value,expected", [("=HYPERLINK(1)", "'=HYPERLINK(1)"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"), ("\tx", "'\tx"), ("Apex Health", "Apex Health"), (None, ""), (5, 5)])
def test_csv_text_cells_cannot_become_formulas(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert _safe(value) == expected


def test_answers_are_cached_for_a_minute(admin) -> None:  # type: ignore[no-untyped-def]
    cache.Stats.hits = cache.Stats.misses = 0
    first = admin.get("/api/v1/analytics/revenue", params={"from": "2026-02-01", "to": "2026-02-28"})
    again = admin.get("/api/v1/analytics/revenue", params={"from": "2026-02-01", "to": "2026-02-28"})
    assert (cache.Stats.misses, cache.Stats.hits) == (1, 1) and first.json() == again.json()
    admin.get("/api/v1/analytics/revenue", params={"from": "2026-02-01", "to": "2026-02-27"})  # other parameters, other answer
    admin.get("/api/v1/analytics/revenue", params={"from": "2026-02-01", "to": "2026-02-28", "granularity": "week"})
    assert cache.Stats.misses == 3
    admin.get("/api/v1/analytics/operations", params={"from": "2026-02-01", "to": "2026-02-28"})
    assert cache.Stats.misses == 4  # and the endpoints do not share entries


def test_the_cache_expires(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "analytics_cache_seconds", 0)
    cache.Stats.hits = cache.Stats.misses = 0
    for _ in range(2):
        assert admin.get("/api/v1/analytics/revenue", params={"from": "2026-04-01", "to": "2026-04-30"}).status_code == 200
    assert cache.Stats.hits == 0 and cache.Stats.misses == 2


def test_an_unreachable_redis_only_costs_the_speedup(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings()
    monkeypatch.setattr(settings, "cache_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    cache.reset()
    cache.Stats.errors = 0
    response = admin.get("/api/v1/analytics/revenue", params={"from": "2026-05-01", "to": "2026-05-31"})
    assert response.status_code == 200 and response.json()["checks"]["mrr_movements_add_up"]
    assert cache.Stats.errors >= 1  # it noticed, logged it, and answered anyway
    monkeypatch.setattr(settings, "cache_backend", "memory")
    cache.reset()


def test_the_cache_can_be_switched_off(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "cache_backend", "off")
    cache.reset()
    cache.Stats.hits = cache.Stats.misses = 0
    for _ in range(2):
        admin.get("/api/v1/analytics/revenue", params={"from": "2026-05-01", "to": "2026-05-31"})
    assert (cache.Stats.hits, cache.Stats.misses) == (0, 2)
    monkeypatch.setattr(get_settings(), "cache_backend", "memory")
    cache.reset()
