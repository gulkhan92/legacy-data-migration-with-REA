"""Metrics and analytics over the daily snapshots.

Definitions (they are the ones on the dashboards, and `docs/architecture.md` repeats them):

* MRR is the sum of the base fees of the subscriptions that are active at the end of the day. Usage revenue is tracked apart from it.
* New MRR is the fee of an organization that had no subscription the day before. Expansion is the increase of an organization that
  keeps a subscription (an upgrade, Starter to a paid plan included). Contraction is a decrease. Churned MRR is the fee of a
  subscription that ended without a successor.
* Net new MRR = new + expansion - contraction - churned. The MRR at the end of a period is the MRR at its start plus net new MRR; the
  reports check it on every request.
* Gross churn rate = churned MRR of the period / MRR at the start of the period.
* Net revenue retention (12 months) = today's MRR of the organizations that paid something twelve months ago / what those
  organizations paid then. It includes their expansion and contraction and leaves out newcomers.
* ARPA = MRR / paying accounts at the end of the period. ARR = 12 x MRR.
* Subscription revenue and usage revenue are the base and overage lines of invoices that are not void, recognised on the last day of the
  period they bill. For any range their sum equals the invoice totals of that range; the report says whether it does.
* Ratios (success rate, average duration, quality) are averages over the days, weighted by the jobs of each day.

The functions that take `Day` rows are pure; the ones that take a session read the snapshots and the platform wide database functions.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import DailyMetric

Granularity = Literal["day", "week", "month"]
FOOTNOTE = "Revenue figures are simulated for demonstration. Every number is computed from the tables; seeded rows are marked is_simulated."

SUM_FIELDS = (
    "new_orgs", "new_mrr_cents", "expansion_mrr_cents", "contraction_mrr_cents", "churned_mrr_cents", "subscription_revenue_cents", "usage_revenue_cents",
    "jobs", "records", "tokens_groq", "tokens_gemini", "failovers", "cache_hits",
)  # fmt: skip
DIMENSIONS = {"plan": "plan", "industry": "industry", "region": "region"}


@dataclass(frozen=True)
class Day:
    date: date
    mrr_cents: int = 0
    active_orgs: int = 0
    paying_orgs: int = 0
    new_orgs: int = 0
    new_mrr_cents: int = 0
    expansion_mrr_cents: int = 0
    contraction_mrr_cents: int = 0
    churned_mrr_cents: int = 0
    subscription_revenue_cents: int = 0
    usage_revenue_cents: int = 0
    jobs: int = 0
    records: int = 0
    tokens_groq: int = 0
    tokens_gemini: int = 0
    failovers: int = 0
    cache_hits: int = 0
    success_rate: float | None = None
    avg_duration_ms: float | None = None
    quality_score: float | None = None
    nrr_12m: float | None = None
    breakdown: dict[str, Any] | None = None
    is_simulated: bool = False

    @classmethod
    def of(cls, row: DailyMetric) -> "Day":
        def f(value: Any) -> float | None:
            return None if value is None else float(value)

        return cls(
            row.metric_date, row.mrr_cents, row.active_orgs, row.paying_orgs, row.new_orgs, row.new_mrr_cents, row.expansion_mrr_cents, row.contraction_mrr_cents,
            row.churned_mrr_cents, row.subscription_revenue_cents, row.usage_revenue_cents, row.jobs, row.records, row.tokens_groq, row.tokens_gemini, row.failovers,
            row.cache_hits, f(row.success_rate), f(row.avg_duration_ms), f(row.quality_score), f(row.nrr_12m), row.breakdown or {}, row.is_simulated,
        )  # fmt: skip


# ------------------------------------------------------------------ pure formulas


def net_new(d: Day) -> int:
    return d.new_mrr_cents + d.expansion_mrr_cents - d.contraction_mrr_cents - d.churned_mrr_cents


def bucket_start(day: date, granularity: Granularity) -> date:
    if granularity == "week":
        return day - timedelta(days=day.weekday())  # ISO weeks start on Monday
    if granularity == "month":
        return day.replace(day=1)
    return day


def weighted(rows: Iterable[Day], field: str) -> float | None:
    """Average of a ratio over days, weighted by the jobs of each day."""
    num = den = 0.0
    for r in rows:
        value = getattr(r, field)
        if value is not None and r.jobs > 0:
            num += value * r.jobs
            den += r.jobs
    return num / den if den else None


def ratio(numerator: float, denominator: float, digits: int = 4) -> float | None:
    return round(numerator / denominator, digits) if denominator else None


def summarize(rows: list[Day]) -> dict[str, Any]:
    """Every metric for a run of consecutive days, treated as one period."""
    first, last = rows[0], rows[-1]
    totals = {name: sum(getattr(r, name) for r in rows) for name in SUM_FIELDS}
    net = totals["new_mrr_cents"] + totals["expansion_mrr_cents"] - totals["contraction_mrr_cents"] - totals["churned_mrr_cents"]
    start = first.mrr_cents - net_new(first)  # the MRR at the end of the day before the period
    revenue = totals["subscription_revenue_cents"] + totals["usage_revenue_cents"]
    nrr = last.nrr_12m
    duration = weighted(rows, "avg_duration_ms")
    return {
        "from": first.date.isoformat(), "to": last.date.isoformat(), "days": len(rows),
        "mrr_cents": last.mrr_cents, "arr_cents": 12 * last.mrr_cents, "mrr_start_cents": start,
        "new_mrr_cents": totals["new_mrr_cents"], "expansion_mrr_cents": totals["expansion_mrr_cents"], "contraction_mrr_cents": totals["contraction_mrr_cents"],
        "churned_mrr_cents": totals["churned_mrr_cents"], "net_new_mrr_cents": net, "mrr_growth_rate": ratio(net, start),
        "gross_churn_rate": ratio(totals["churned_mrr_cents"], start), "net_revenue_retention_12m": None if nrr is None else round(nrr, 4),
        "subscription_revenue_cents": totals["subscription_revenue_cents"], "usage_revenue_cents": totals["usage_revenue_cents"], "total_revenue_cents": revenue,
        "usage_share": ratio(totals["usage_revenue_cents"], revenue),
        "active_orgs": last.active_orgs, "paying_orgs": last.paying_orgs, "new_orgs": totals["new_orgs"],
        "arpa_cents": round(last.mrr_cents / last.paying_orgs) if last.paying_orgs else None,
        "jobs": totals["jobs"], "records": totals["records"], "success_rate": None if (v := weighted(rows, "success_rate")) is None else round(v, 4),
        "avg_duration_ms": None if duration is None else round(duration), "quality_score": None if (q := weighted(rows, "quality_score")) is None else round(q, 2),
        "tokens_groq": totals["tokens_groq"], "tokens_gemini": totals["tokens_gemini"], "tokens_total": totals["tokens_groq"] + totals["tokens_gemini"],
        "failovers": totals["failovers"], "cache_hits": totals["cache_hits"],
        "mrr_movements_add_up": last.mrr_cents == start + net,
    }  # fmt: skip


def series(rows: list[Day], granularity: Granularity, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """One point per bucket. Stocks (MRR, accounts) are the value at the end of the bucket, flows are summed, ratios are weighted by jobs."""
    buckets: dict[date, list[Day]] = {}
    for r in rows:
        buckets.setdefault(bucket_start(r.date, granularity), []).append(r)
    out = []
    for start in sorted(buckets):
        summary = summarize(buckets[start])
        out.append({"period": start.isoformat(), **{name: summary[name] for name in fields}})
    return out


def percent_change(current: float | int | None, previous: float | int | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous), 4)  # type: ignore[operator]


def compare(current: dict[str, Any], previous: dict[str, Any], keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for key in keys:
        a, b = current.get(key), previous.get(key)
        out[key] = {"current": a, "previous": b, "delta": None if a is None or b is None else round(a - b, 4), "change": percent_change(a, b)}
    return out


def previous_range(start: date, end: date) -> tuple[date, date]:
    """The period of the same length that ends the day before `start`."""
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=length - 1), prev_end


# ------------------------------------------------------------------ what each report contains

REVENUE_SERIES = (
    "mrr_cents", "new_mrr_cents", "expansion_mrr_cents", "contraction_mrr_cents", "churned_mrr_cents", "net_new_mrr_cents", "subscription_revenue_cents", "usage_revenue_cents",
    "total_revenue_cents", "active_orgs", "paying_orgs", "new_orgs", "arpa_cents", "net_revenue_retention_12m",
)  # fmt: skip
REVENUE_COMPARED = (
    "mrr_cents", "arr_cents", "net_new_mrr_cents", "new_mrr_cents", "expansion_mrr_cents", "contraction_mrr_cents", "churned_mrr_cents", "total_revenue_cents",
    "subscription_revenue_cents", "usage_revenue_cents", "arpa_cents", "paying_orgs", "active_orgs", "gross_churn_rate", "net_revenue_retention_12m",
)  # fmt: skip
OPERATIONS_SERIES = ("jobs", "success_rate", "avg_duration_ms", "records", "tokens_groq", "tokens_gemini", "tokens_total", "failovers", "cache_hits", "quality_score")
OPERATIONS_COMPARED = OPERATIONS_SERIES


class NoData(Exception):
    """There are no snapshots in the requested range."""


def latest_date(db: Session) -> date | None:
    return db.scalar(select(func.max(DailyMetric.metric_date)))


def load_days(db: Session, start: date, end: date) -> list[Day]:
    rows = db.scalars(select(DailyMetric).where(DailyMetric.metric_date.between(start, end)).order_by(DailyMetric.metric_date))
    return [Day.of(r) for r in rows]


def default_range(db: Session, start: date | None, end: date | None, default_days: int = 90) -> tuple[date, date]:
    end = end or latest_date(db) or date.today()
    start = start or end - timedelta(days=default_days - 1)
    return start, end


def _period(db: Session, start: date, end: date, granularity: Granularity, fields: tuple[str, ...]) -> tuple[list[Day], list[dict[str, Any]], dict[str, Any]]:
    days = load_days(db, start, end)
    if not days:
        raise NoData
    return days, series(days, granularity, fields), summarize(days)


def revenue_checks(db: Session, start: date, end: date, days: list[Day], summary: dict[str, Any]) -> dict[str, Any]:
    expected = (end - start).days + 1
    totals = db.execute(text("SELECT platform_revenue_totals(:a, :b)"), {"a": start, "b": end}).scalar_one()
    totals = totals if isinstance(totals, dict) else json.loads(totals)
    shown = summary["total_revenue_cents"]
    return {
        "snapshot_days": len(days), "expected_days": expected, "complete": len(days) == expected,
        "mrr_movements_add_up": summary["mrr_movements_add_up"], "mrr_start_cents": summary["mrr_start_cents"], "net_new_mrr_cents": summary["net_new_mrr_cents"], "mrr_end_cents": summary["mrr_cents"],
        "revenue_matches_invoices": shown == totals["invoice_total_cents"], "revenue_shown_cents": shown, "invoiced_cents": totals["invoice_total_cents"],
        "base_invoiced_cents": totals["base_cents"], "overage_invoiced_cents": totals["overage_cents"], "invoices": totals["invoices"],
    }  # fmt: skip


def revenue_report(db: Session, start: date, end: date, granularity: Granularity, with_previous: bool) -> dict[str, Any]:
    days, points, summary = _period(db, start, end, granularity, REVENUE_SERIES)
    report: dict[str, Any] = {
        "range": {"from": start.isoformat(), "to": end.isoformat()}, "granularity": granularity, "series": points, "kpis": summary,
        "checks": revenue_checks(db, start, end, days, summary), "simulated": any(d.is_simulated for d in days), "footnote": FOOTNOTE,
    }  # fmt: skip
    if with_previous:
        p_start, p_end = previous_range(start, end)
        previous = load_days(db, p_start, p_end)
        if previous:
            before = summarize(previous)
            report["previous"] = {"range": {"from": p_start.isoformat(), "to": p_end.isoformat()}, "kpis": before}
            report["change"] = compare(summary, before, REVENUE_COMPARED)
    return report


def operations_report(db: Session, start: date, end: date, granularity: Granularity, with_previous: bool) -> dict[str, Any]:
    days, points, summary = _period(db, start, end, granularity, OPERATIONS_SERIES)
    report: dict[str, Any] = {"range": {"from": start.isoformat(), "to": end.isoformat()}, "granularity": granularity, "series": points, "kpis": {k: summary[k] for k in ("from", "to", "days", *OPERATIONS_SERIES)}}
    if with_previous:
        p_start, p_end = previous_range(start, end)
        previous = load_days(db, p_start, p_end)
        if previous:
            before = summarize(previous)
            report["previous"] = {"range": {"from": p_start.isoformat(), "to": p_end.isoformat()}, "kpis": {k: before[k] for k in ("from", "to", "days", *OPERATIONS_SERIES)}}
            report["change"] = compare(summary, before, OPERATIONS_COMPARED)
    return report


def breakdown_report(db: Session, dimension: str, start: date, end: date, granularity: Granularity) -> dict[str, Any]:
    """MRR (at the end of each bucket) and usage revenue (summed over each bucket) by plan, industry or region."""
    days = load_days(db, start, end)
    if not days:
        raise NoData
    mrr_key, usage_key = f"mrr_by_{dimension}", f"usage_by_{dimension}"
    buckets: dict[date, list[Day]] = {}
    for d in days:
        buckets.setdefault(bucket_start(d.date, granularity), []).append(d)
    keys: set[str] = set()
    mrr_points, usage_points = [], []
    for start_of in sorted(buckets):
        rows = buckets[start_of]
        stock = dict((rows[-1].breakdown or {}).get(mrr_key, {}))
        flow: dict[str, int] = {}
        for r in rows:
            for k, v in (r.breakdown or {}).get(usage_key, {}).items():
                flow[k] = flow.get(k, 0) + int(v)
        keys |= set(stock) | set(flow)
        mrr_points.append({"period": start_of.isoformat(), "values": {k: int(v) for k, v in stock.items()}})
        usage_points.append({"period": start_of.isoformat(), "values": flow})
    last = mrr_points[-1]["values"]
    total = sum(last.values())
    return {
        "dimension": dimension, "granularity": granularity, "range": {"from": start.isoformat(), "to": end.isoformat()}, "keys": sorted(keys), "mrr": mrr_points, "usage_revenue": usage_points,
        "current": {"mrr_cents": last, "share": {k: ratio(v, total) for k, v in last.items()}, "total_mrr_cents": total}, "simulated": any(d.is_simulated for d in days), "footnote": FOOTNOTE,
    }  # fmt: skip


def top_customers(db: Session, as_of: date, limit: int) -> dict[str, Any]:
    raw = db.execute(text("SELECT platform_top_customers(:d, :n)"), {"d": as_of, "n": limit}).scalar_one()
    items = raw if isinstance(raw, list) else json.loads(raw)
    return {"as_of": as_of.isoformat(), "items": items, "footnote": FOOTNOTE}


def invoice_page(db: Session, status: str | None, q: str | None, start: date | None, end: date | None, limit: int, offset: int) -> dict[str, Any]:
    pattern = None if not q else q.replace("\\", "").replace("%", "").replace("_", "")
    raw = db.execute(text("SELECT platform_invoice_page(:s, :q, :a, :b, :l, :o)"), {"s": status, "q": pattern, "a": start, "b": end, "l": limit, "o": offset}).scalar_one()
    data = raw if isinstance(raw, dict) else json.loads(raw)
    return {**data, "limit": limit, "offset": offset, "footnote": FOOTNOTE}
