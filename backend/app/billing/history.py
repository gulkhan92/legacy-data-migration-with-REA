"""Demonstration history: two years of fictional customers, jobs, invoices and payments, generated deterministically.

Every figure in the revenue dashboards comes from tables. There are no real customers yet, so this module fills the tables with
a plausible, reproducible past (seed 42 by default) and marks every row `is_simulated`. The numbers on the dashboards are then
computed from those rows by the same snapshot and invoice code that processes the migrations run in the application.

What is simulated (all of it visible in the data, none of it hidden in a formula):

* About 140 organizations across five industries and four regions sign up along an S curve (a slow start, a steep middle, a
  flattening end).
* A plan mix at signup that depends on the industry (financial services and healthcare buy more Enterprise).
* Monthly churn that is highest on Starter and lowest on Enterprise, and higher for accounts that barely use the product.
* Expansion through usage growth: an organization whose usage stays above its allowance moves up a plan; one that uses a small
  part of an expensive plan sometimes moves down.
* Q4 seasonality in usage, and a quiet January and February.
* Jobs with realistic sizes, durations, token use (most runs are repeats and use none), provider failovers, failures and quality.
* Invoices from the real invoice code, and payments: most on time, some late, a few never, a few voided.

`generate_history` is pure (no database, no clock) so its shape can be tested; `write_history` stores it.
"""

import calendar
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Engine, delete, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.billing import invoicing, snapshots
from app.billing.plans import ENTERPRISE, ORDER, PROFESSIONAL, STARTER
from app.db import models
from app.db.models.enums import InvoiceStatus

INDUSTRIES = ["Healthcare", "Retail & Commerce", "Financial Services", "Manufacturing", "Public Sector"]
REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America"]
REGION_WEIGHTS = [0.38, 0.32, 0.20, 0.10]

PLAN_MIX = {  # (starter, professional, enterprise) at signup
    "Healthcare": (0.40, 0.38, 0.22), "Retail & Commerce": (0.65, 0.30, 0.05), "Financial Services": (0.38, 0.40, 0.22),
    "Manufacturing": (0.50, 0.40, 0.10), "Public Sector": (0.50, 0.35, 0.15),
}  # fmt: skip
INCLUDED = {STARTER: 100_000, PROFESSIONAL: 2_000_000, ENTERPRISE: 25_000_000}
MONTHLY_CHURN = {STARTER: 0.060, PROFESSIONAL: 0.025, ENTERPRISE: 0.008}
SUCCESS = {STARTER: 0.90, PROFESSIONAL: 0.94, ENTERPRISE: 0.965}
JOB_SIZE = {STARTER: 25_000, PROFESSIONAL: 300_000, ENTERPRISE: 2_000_000}
SEASON = {1: 0.88, 2: 0.85, 3: 1.0, 4: 1.0, 5: 1.0, 6: 0.95, 7: 0.90, 8: 0.90, 9: 1.05, 10: 1.20, 11: 1.30, 12: 1.25}  # Q4 peak
# On-time, late (paid after the due date) and never (the rest) shares of invoices by plan.
PAYMENT = {STARTER: (0.88, 0.08), PROFESSIONAL: (0.93, 0.055), ENTERPRISE: (0.965, 0.03)}
VOID_SHARE = 0.015

ADJECTIVES = ["Northwind", "Apex", "Bluefin", "Cedar", "Delta", "Evergreen", "Falcon", "Granite", "Harbor", "Ironwood", "Juniper", "Keystone", "Lumen", "Meridian", "Nimbus",
              "Orchard", "Pioneer", "Quartz", "Redwood", "Summit", "Tidewater", "Union", "Vertex", "Westbrook"]  # fmt: skip
NOUNS = {
    "Healthcare": ["Health", "Clinics", "Care Group", "Medical", "Wellness"], "Retail & Commerce": ["Retail", "Trading", "Stores", "Commerce", "Outfitters"],
    "Financial Services": ["Capital", "Financial", "Credit Union", "Trust", "Mutual"], "Manufacturing": ["Industries", "Manufacturing", "Works", "Components", "Foundry"],
    "Public Sector": ["County Services", "Transit Authority", "Municipal", "Water District", "Housing Agency"],
}  # fmt: skip


@dataclass
class Period:
    plan: str
    start: datetime
    end: datetime | None = None


@dataclass
class SimOrg:
    id: uuid.UUID
    name: str
    industry: str
    region: str
    signup: datetime
    periods: list[Period] = field(default_factory=list)


@dataclass
class SimJob:
    id: uuid.UUID
    org: int  # index into History.orgs
    started: datetime
    completed: datetime
    ok: bool
    records: int
    tokens: int
    duration_ms: int
    quality_after: float | None
    quality_before: float | None
    source_rows: int
    llm: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class History:
    seed: int
    start: date
    end: date
    orgs: list[SimOrg]
    jobs: list[SimJob]
    months: list[date]


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _days_in(day: date) -> int:
    return calendar.monthrange(day.year, day.month)[1]


def _dt(day: date, hour: float = 0.0) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC) + timedelta(hours=hour)


def _weighted(rng: random.Random, options: list[str], weights: tuple[float, ...] | list[float]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def generate_history(seed: int = 42, end: date | None = None, months: int = 24, org_count: int = 140) -> History:
    end = end or datetime.now(UTC).date()
    rng = random.Random(seed)
    first = _add_months(end.replace(day=1), -(months - 1))
    month_starts = [_add_months(first, i) for i in range(months)]
    horizon = _dt(end + timedelta(days=1))

    # --- who signs up and when: a logistic curve over the window
    orgs: list[SimOrg] = []
    signups: list[datetime] = []
    for _ in range(org_count):
        u = rng.uniform(0.02, 0.98)
        t = months * 0.48 + math.log(u / (1 - u)) / 0.30
        if t < 0:  # the early adopters of the first weeks
            t = rng.uniform(0.0, 1.5)
        elif t > months - 0.55:  # and the late ones, so growth flattens instead of stopping
            t = rng.uniform(months - 2.5, months - 0.55)
        index = int(t)
        moment = _dt(month_starts[index]) + timedelta(days=(t - index) * _days_in(month_starts[index]), hours=rng.randint(8, 17))
        signups.append(min(moment, horizon - timedelta(days=1)))
    signups.sort()
    taken: set[str] = set()
    for signup in signups:
        industry = _weighted(rng, INDUSTRIES, [0.22, 0.26, 0.18, 0.20, 0.14])
        region = _weighted(rng, REGIONS, REGION_WEIGHTS)
        while True:
            name = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS[industry])}"
            if name not in taken:
                taken.add(name)
                break
        org = SimOrg(uuid.UUID(int=rng.getrandbits(128), version=4), name, industry, region, signup)
        org.periods.append(Period(_weighted(rng, ORDER, PLAN_MIX[industry]), signup))
        orgs.append(org)

    # --- month by month: usage, upgrades, downgrades and churn
    level = [INCLUDED[o.periods[0].plan] * rng.lognormvariate(math.log(0.5), 0.5) for o in orgs]
    growth = [rng.uniform(1.0, 1.09) for _ in orgs]
    over_streak, under_streak, tenure = [0] * len(orgs), [0] * len(orgs), [0] * len(orgs)
    last_ratio = [0.5] * len(orgs)
    churned = [False] * len(orgs)
    usage: list[tuple[int, datetime, datetime, int]] = []  # org, window start, window end, records in the window

    for m, month_start in enumerate(month_starts):
        window_start = _dt(month_start)
        window_end = min(_dt(_add_months(month_start, 1)), horizon)
        length = (_dt(_add_months(month_start, 1)) - window_start).total_seconds()
        for i, org in enumerate(orgs):
            if churned[i] or org.signup >= window_end:
                continue
            current = org.periods[-1]
            active_from = max(window_start, org.signup)
            active_to = window_end
            if tenure[i] > 0:  # the signup month has no transitions
                churn_p = MONTHLY_CHURN[current.plan] * (1.6 if last_ratio[i] < 0.2 else 1.0)
                moment = active_from + (active_to - active_from) * rng.random()
                if tenure[i] >= 2 and rng.random() < churn_p:
                    current.end, churned[i], active_to = moment, True, moment
                elif over_streak[i] >= 2 and current.plan != ENTERPRISE and rng.random() < 0.55:
                    nxt = ORDER[ORDER.index(current.plan) + 1]
                    current.end = moment
                    org.periods.append(Period(nxt, moment))
                    over_streak[i] = under_streak[i] = 0
                elif under_streak[i] >= 3 and current.plan != STARTER and rng.random() < 0.25:
                    prv = ORDER[ORDER.index(current.plan) - 1]
                    current.end = moment
                    org.periods.append(Period(prv, moment))
                    over_streak[i] = under_streak[i] = 0
            if tenure[i] > 0:
                level[i] *= growth[i] * rng.lognormvariate(0, 0.10)
            full = level[i] * SEASON[month_start.month]
            fraction = max(0.0, (active_to - active_from).total_seconds() / length)
            records = int(full * fraction)
            plan_now = org.periods[-1].plan
            last_ratio[i] = full / INCLUDED[plan_now]
            over_streak[i] = over_streak[i] + 1 if last_ratio[i] > 1.25 else 0
            under_streak[i] = under_streak[i] + 1 if last_ratio[i] < 0.30 else 0
            tenure[i] += 1
            if records > 0 and active_to > active_from:
                usage.append((i, active_from, active_to, records))

    # --- the jobs that make up the usage
    jobs: list[SimJob] = []
    for i, window_start, window_end, records in usage:
        org = orgs[i]
        plan_at_end = org.periods[-1].plan if org.periods[-1].end is None or org.periods[-1].end >= window_end else org.periods[-1].plan
        size = JOB_SIZE[plan_at_end] * rng.lognormvariate(0, 0.4)
        count = max(1, min(12, round(records / size)))
        weights = [rng.gammavariate(2.0, 1.0) for _ in range(count)]
        total = sum(weights)
        shares = [int(records * w / total) for w in weights]
        shares[-1] += records - sum(shares)
        span = max(1.0, (min(window_end, horizon - timedelta(hours=2)) - window_start).total_seconds() - 3_600)
        for share in shares:
            completed = window_start + timedelta(seconds=3_600 + rng.random() * span)
            completed = min(completed, horizon - timedelta(hours=1))
            attempts = 1 + (1 if rng.random() < (1 - SUCCESS[plan_at_end]) / SUCCESS[plan_at_end] else 0)
            for attempt in range(attempts):
                ok = attempt == attempts - 1  # a failed run comes first, then the run that works
                rows = share if ok else 0
                duration = int((12_000 + rows / 4_000 * 1_000) * rng.lognormvariate(0, 0.35)) if ok else int(rng.uniform(3_000, 40_000))
                finished = completed - timedelta(minutes=attempts - attempt - 1)
                tokens = 0 if rng.random() < 0.6 else int(min(max(rng.lognormvariate(math.log(2_800), 0.5), 600), 5_900))
                quality = round(min(max(rng.normalvariate(96.2, 1.8), 85.0), 99.9), 2) if ok else None
                calls: list[dict[str, Any]] = []
                if ok:
                    if tokens == 0:
                        calls.append({"provider": "groq", "status": "cache_hit", "tokens_in": 0, "tokens_out": 0, "cache_hit": True, "latency_ms": int(rng.uniform(3, 20))})
                    else:
                        provider = "groq" if rng.random() < 0.78 else "gemini"
                        if rng.random() < 0.12:
                            calls.append({"provider": "groq", "status": rng.choice(["rate_limited", "timeout"]), "tokens_in": 0, "tokens_out": 0, "cache_hit": False, "latency_ms": int(rng.uniform(800, 9_000))})
                            provider = "gemini"
                        calls.append({"provider": provider, "status": "ok", "tokens_in": int(tokens * 0.7), "tokens_out": tokens - int(tokens * 0.7), "cache_hit": False, "latency_ms": int(rng.uniform(400, 3_000))})
                else:
                    tokens = 0
                quarantine = rng.uniform(0.01, 0.06)
                jobs.append(SimJob(uuid.UUID(int=rng.getrandbits(128), version=4), i, finished - timedelta(milliseconds=duration), finished, ok, rows, tokens, duration, quality,
                                   round(max(40.0, quality - rng.uniform(4, 14)), 2) if quality else None, round(rows / (1 - quarantine)) if ok else 0, calls))  # fmt: skip
    jobs.sort(key=lambda j: (j.completed, str(j.id)))
    return History(seed, first, end, orgs, jobs, month_starts)


# ------------------------------------------------------------------ writing it down


def reset_simulated(engine: Engine) -> None:
    """Remove every simulated organization (and, by cascade, everything of theirs) and the simulated snapshots."""
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM organizations WHERE is_simulated"))
        connection.execute(text("DELETE FROM daily_metrics WHERE is_simulated"))


def _chunks(rows: list[dict[str, Any]], size: int = 2_000) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def write_history(engine: Engine, history: History, *, snapshot: bool = True) -> dict[str, Any]:
    """Store the history as the table owner (it bypasses Row Level Security), invoice every month with the real invoice code, settle the payments, and take the snapshots."""
    rng = random.Random(history.seed + 1)  # payments: a stream of their own, so changing them never moves the history
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with Session(engine) as session:
        plan_ids = {p.code: p.id for p in session.scalars(select(models.Plan))}
        if session.scalar(text("SELECT count(*) FROM organizations WHERE is_simulated")):
            raise RuntimeError("Simulated organizations already exist; reset them first")

        session.execute(insert(models.Organization.__table__), [
            {"id": o.id, "name": o.name, "slug": f"{o.name.lower().replace(' ', '-')}-{o.id.hex[:5]}", "industry": o.industry, "region": o.region, "is_simulated": True, "created_at": o.signup}
            for o in history.orgs
        ])  # fmt: skip
        subs = [
            {"id": uuid.UUID(int=rng.getrandbits(128), version=4), "org_id": o.id, "plan_id": plan_ids[p.plan], "status": "cancelled" if p.end else "active", "started_at": p.start,
             "ended_at": p.end, "is_simulated": True, "created_at": p.start}
            for o in history.orgs for p in o.periods
        ]  # fmt: skip
        for chunk in _chunks(subs):
            session.execute(insert(models.Subscription.__table__), chunk)

        sources = {i: uuid.UUID(int=history.orgs[i].id.int ^ 0xA5A5) for i in range(len(history.orgs))}
        session.execute(insert(models.Source.__table__), [
            {"id": sources[i], "org_id": o.id, "name": "Legacy system (simulated)", "kind": "sqlite", "is_sample": True, "encrypted": False, "created_at": o.signup}
            for i, o in enumerate(history.orgs)
        ])  # fmt: skip
        job_rows, usage_rows, report_rows, call_rows = [], [], [], []
        for j in history.jobs:
            org = history.orgs[j.org]
            job_rows.append({"id": j.id, "org_id": org.id, "source_id": sources[j.org], "status": "COMPLETED" if j.ok else "FAILED", "tokens_used": j.tokens, "started_at": j.started,
                             "completed_at": j.completed, "created_at": j.started, "updated_at": j.completed, "error_message": None if j.ok else "The load failed and was rolled back (simulated)"})  # fmt: skip
            if j.ok:
                usage_rows.append({"id": uuid.UUID(int=j.id.int ^ 0x5A5A), "org_id": org.id, "job_id": j.id, "records_loaded": j.records, "tokens_used": j.tokens, "duration_ms": j.duration_ms,
                                   "occurred_at": j.completed, "is_simulated": True})  # fmt: skip
                report_rows.append({"id": uuid.UUID(int=j.id.int ^ 0x3C3C), "org_id": org.id, "job_id": j.id, "source_rows": j.source_rows, "loaded_rows": j.records,
                                    "quarantined_rows": j.source_rows - j.records, "quality_before": j.quality_before, "quality_after": j.quality_after, "passed": True, "checks": {}, "created_at": j.completed})  # fmt: skip
            for n, call in enumerate(j.llm):
                call_rows.append({"org_id": org.id, "job_id": j.id, "provider": call["provider"], "model": "openai/gpt-oss-20b" if call["provider"] == "groq" else "gemini-2.5-flash-lite",
                                  "task": "resolve_mappings", "tokens_in": call["tokens_in"], "tokens_out": call["tokens_out"], "cached_tokens": 0, "latency_ms": call["latency_ms"],
                                  "status": call["status"], "cache_hit": call["cache_hit"], "created_at": j.started + timedelta(seconds=2 + n)})  # fmt: skip
        for table, rows in ((models.Job.__table__, job_rows), (models.UsageEvent.__table__, usage_rows), (models.ReconciliationReport.__table__, report_rows), (models.LlmCall.__table__, call_rows)):
            for chunk in _chunks(rows):
                session.execute(insert(table), chunk)
        session.commit()

        # invoices: the real code, month by month, for every month that has ended
        created = 0
        for month in history.months:
            _, period_end = invoicing.period_of(month)
            issued = _dt(period_end + timedelta(days=1), 2)
            if issued > _dt(history.end + timedelta(days=1)):
                continue
            for org in history.orgs:
                if org.signup > _dt(period_end + timedelta(days=1)):
                    continue
                if invoicing.invoice_for_org(session, org.id, month, now=issued, simulated=True) is not None:
                    created += 1
            session.commit()

        # payments
        horizon = _dt(history.end + timedelta(days=1))
        plan_of = {s["id"]: next(code for code, pid in plan_ids.items() if pid == s["plan_id"]) for s in subs}
        for invoice in session.scalars(select(models.Invoice).where(models.Invoice.is_simulated.is_(True)).order_by(models.Invoice.issued_at, models.Invoice.id)):
            on_time, late = PAYMENT[plan_of.get(invoice.subscription_id, STARTER)]
            roll = rng.random()
            if roll < VOID_SHARE:
                invoice.status = InvoiceStatus.VOID
                continue
            if roll < VOID_SHARE + on_time:
                paid_at = invoice.issued_at + timedelta(days=rng.randint(2, 28))
            elif roll < VOID_SHARE + on_time + late:
                paid_at = invoice.issued_at + timedelta(days=rng.randint(33, 55))
            else:
                paid_at = None
            if paid_at is not None and paid_at <= horizon:
                invoice.status, invoice.paid_at = InvoiceStatus.PAID, paid_at
            elif invoice.due_at is not None and invoice.due_at < horizon:
                invoice.status = InvoiceStatus.OVERDUE
        session.commit()

    days = 0
    if snapshot:
        days = snapshots.snapshot_range(history.start, history.end, factory=factory)
    return {"organizations": len(history.orgs), "subscriptions": len(subs), "jobs": len(history.jobs), "usage_events": len(usage_rows), "llm_calls": len(call_rows), "invoices": created,
            "snapshots": days}  # fmt: skip


def seed_history(engine: Engine, seed: int = 42, end: date | None = None, *, reset: bool = False, snapshot: bool = True) -> dict[str, Any]:
    if reset:
        reset_simulated(engine)
    return write_history(engine, generate_history(seed, end), snapshot=snapshot)
