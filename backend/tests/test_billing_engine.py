"""Invoices, metering and the daily snapshot, against the database with worlds of a few organizations and known answers."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.billing import invoicing, metering, snapshots
from app.db import models
from app.db.models.enums import InvoiceStatus, JobStatus, LlmCallStatus, SourceKind
from app.db.session import scoped_session
from tests.billing_helpers import at, world


@pytest.fixture
def w(admin_engine):  # type: ignore[no-untyped-def]
    with world(admin_engine) as built:
        yield built


def invoices_of(engine, org: uuid.UUID):  # type: ignore[no-untyped-def]
    with Session(engine) as session:
        rows = session.query(models.Invoice).filter(models.Invoice.org_id == org).order_by(models.Invoice.period_start).all()
        return [(i, [(line.kind.value, line.amount_cents, float(line.quantity)) for line in session.query(models.InvoiceLine).filter(models.InvoiceLine.invoice_id == i.id).order_by(models.InvoiceLine.amount_cents)]) for i in rows]


# ------------------------------------------------------------------ invoices


def test_a_professional_month_with_overage(w, admin_engine, app_factory) -> None:  # type: ignore[no-untyped-def]
    org = w.org("Pro Org")
    w.subscribe(org, "professional", date(2019, 1, 1))
    for when, records in [(at(date(2019, 2, 28), 23), 777), (at(date(2019, 3, 1), 0), 1_000_000), (at(date(2019, 3, 31), 23), 1_500_000), (at(date(2019, 4, 1), 0), 999)]:
        w.usage(org, when, records)  # the first and last are outside March
    run = invoicing.run_invoices(date(2019, 3, 1), now=at(date(2019, 4, 2)), factory=app_factory)
    assert run.created >= 1 and not run.failed and (run.period_start, run.period_end) == (date(2019, 3, 1), date(2019, 3, 31))
    [(invoice, lines)] = invoices_of(admin_engine, org)
    assert lines == [("overage", 10_000, 500.0), ("base", 49_900, 1.0)] or lines == [("base", 49_900, 1.0), ("overage", 10_000, 500.0)]
    assert invoice.total_cents == 59_900 and invoice.status is InvoiceStatus.OPEN
    assert (invoice.period_start, invoice.period_end) == (date(2019, 3, 1), date(2019, 3, 31)) and invoice.due_at - invoice.issued_at == timedelta(days=30)
    assert invoice.issued_at == at(date(2019, 4, 2))


def test_running_a_month_again_changes_nothing(w, admin_engine, app_factory) -> None:  # type: ignore[no-untyped-def]
    org = w.org("Twice")
    w.subscribe(org, "professional", date(2019, 1, 1))
    first = invoicing.run_invoices(date(2019, 3, 1), now=at(date(2019, 4, 2)), factory=app_factory)
    again = invoicing.run_invoices(date(2019, 3, 1), now=at(date(2019, 4, 3)), factory=app_factory)
    assert first.created == 1 and again.created == 0 and again.skipped >= 1
    assert len(invoices_of(admin_engine, org)) == 1
    with pytest.raises(IntegrityError), Session(admin_engine) as session:  # the database refuses a second invoice for the period as well
        sub = session.query(models.Subscription).filter_by(org_id=org).one()
        session.add(models.Invoice(org_id=org, subscription_id=sub.id, period_start=date(2019, 3, 1), period_end=date(2019, 3, 31), status=InvoiceStatus.OPEN, total_cents=1, issued_at=at(date(2019, 4, 2))))
        session.commit()


def test_free_plans_are_invoiced_only_when_they_owe_something(w, admin_engine, app_factory) -> None:  # type: ignore[no-untyped-def]
    quiet, busy = w.org("Quiet Starter"), w.org("Busy Starter")
    w.subscribe(quiet, "starter", date(2019, 1, 1))
    w.subscribe(busy, "starter", date(2019, 1, 1))
    w.usage(quiet, at(date(2019, 3, 5)), 50_000)
    w.usage(busy, at(date(2019, 3, 5)), 150_000)
    invoicing.run_invoices(date(2019, 3, 1), now=at(date(2019, 4, 2)), factory=app_factory)
    assert invoices_of(admin_engine, quiet) == []
    [(invoice, lines)] = invoices_of(admin_engine, busy)
    assert invoice.total_cents == 2_500 and sorted(lines) == [("base", 0, 1.0), ("overage", 2_500, 50.0)]


def test_a_month_is_billed_on_the_plan_active_on_its_last_day(w, admin_engine, app_factory) -> None:  # type: ignore[no-untyped-def]
    upgraded, left_early, left_later = w.org("Upgraded"), w.org("Left early"), w.org("Left later")
    w.subscribe(upgraded, "starter", date(2019, 1, 1), date(2019, 3, 15))
    w.subscribe(upgraded, "professional", date(2019, 3, 15))
    w.subscribe(left_early, "professional", date(2019, 1, 1), date(2019, 3, 20))
    w.subscribe(left_later, "professional", date(2019, 1, 1), date(2019, 4, 1))
    invoicing.run_invoices(date(2019, 3, 1), now=at(date(2019, 4, 2)), factory=app_factory)
    assert [i.total_cents for i, _ in invoices_of(admin_engine, upgraded)] == [49_900]
    assert invoices_of(admin_engine, left_early) == []  # gone before the month ended
    assert [i.total_cents for i, _ in invoices_of(admin_engine, left_later)] == [49_900]  # still there on the 31st


def test_overdue_invoices_are_marked_by_the_run_and_paid_ones_are_left(w, admin_engine, app_factory) -> None:  # type: ignore[no-untyped-def]
    late, paid = w.org("Late"), w.org("Paid")
    for org in (late, paid):
        w.subscribe(org, "professional", date(2019, 1, 1))
    invoicing.run_invoices(date(2019, 3, 1), now=at(date(2019, 4, 2)), factory=app_factory)
    with Session(admin_engine) as session:
        session.execute(text("UPDATE invoices SET status = 'paid', paid_at = :t WHERE org_id = :o"), {"t": at(date(2019, 4, 20)), "o": paid})
        session.commit()
    invoicing.run_invoices(date(2019, 4, 1), now=at(date(2019, 6, 10)), factory=app_factory)  # more than 30 days after the March invoice was issued
    statuses = {org: [i.status.value for i, _ in invoices_of(admin_engine, org)] for org in (late, paid)}
    assert statuses[late] == ["overdue", "open"] and statuses[paid] == ["paid", "open"]


def test_one_organization_failing_does_not_stop_the_run(w, admin_engine, app_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    broken, fine = w.org("Broken"), w.org("Fine")
    for org in (broken, fine):
        w.subscribe(org, "professional", date(2019, 1, 1))
    real = invoicing.invoice_for_org

    def flaky(session, org_id, *args, **kwargs):  # type: ignore[no-untyped-def]
        if org_id == broken:
            raise RuntimeError("boom")
        return real(session, org_id, *args, **kwargs)

    monkeypatch.setattr(invoicing, "invoice_for_org", flaky)
    run = invoicing.run_invoices(date(2019, 3, 1), now=at(date(2019, 4, 2)), factory=app_factory)
    assert len(run.failed) == 1 and str(broken) in run.failed[0] and "boom" not in run.failed[0]
    assert len(invoices_of(admin_engine, fine)) == 1 and invoices_of(admin_engine, broken) == []


# ------------------------------------------------------------------ metering


def completed_job(engine, org: uuid.UUID, when: datetime, tokens: int = 0) -> uuid.UUID:  # type: ignore[no-untyped-def]
    with Session(engine) as session:
        source = models.Source(org_id=org, name="s", kind=SourceKind.SQLITE)
        session.add(source)
        session.flush()
        job = models.Job(org_id=org, source_id=source.id, status=JobStatus.COMPLETED, tokens_used=tokens, started_at=when, completed_at=when)
        session.add(job)
        session.commit()
        return job.id


def test_an_organization_without_a_plan_gets_starter_once(w, app_factory) -> None:  # type: ignore[no-untyped-def]
    org = w.org("Newcomer", simulated=False)
    with scoped_session(org_id=org, factory=app_factory) as db:
        first = metering.ensure_subscription(db, org, now=at(date(2019, 5, 5)))
        second = metering.ensure_subscription(db, org)
        assert first.id == second.id and first.is_simulated is False and first.started_at == at(date(2019, 5, 5))
        assert db.get(models.Plan, first.plan_id).code == "starter"  # type: ignore[union-attr]
        metering.ensure_subscription(db, org)
    with scoped_session(org_id=org, factory=app_factory) as db:
        assert db.query(models.Subscription).count() == 1
    with pytest.raises(IntegrityError), Session(w.engine) as session:  # the database allows one active subscription per organization
        session.add(models.Subscription(org_id=org, plan_id=w.plans["professional"], status="active", started_at=at(date(2019, 6, 1))))
        session.commit()


def test_a_job_writes_one_usage_event_however_often_it_is_recorded(w, app_factory, admin_engine) -> None:  # type: ignore[no-untyped-def]
    org = w.org("Meter", simulated=False)
    job_id = completed_job(admin_engine, org, at(date(2019, 5, 6)), tokens=1234)
    with scoped_session(org_id=org, factory=app_factory) as db:
        job = db.get(models.Job, job_id)
        assert metering.record_job_usage(db, job, 4_321, 2_500) is True  # type: ignore[arg-type]
        assert metering.record_job_usage(db, job, 4_321, 2_500) is False  # type: ignore[arg-type]
    with scoped_session(org_id=org, factory=app_factory) as db:
        [event] = db.query(models.UsageEvent).all()
        assert (event.records_loaded, event.tokens_used, event.duration_ms, event.job_id, event.is_simulated) == (4_321, 1_234, 2_500, job_id, False)
        assert event.occurred_at == at(date(2019, 5, 6))
        assert db.query(models.Subscription).count() == 1  # and the organization was put on a plan


def test_a_rolled_back_load_is_not_billed_unless_its_month_was_already_invoiced(w, app_factory, admin_engine) -> None:  # type: ignore[no-untyped-def]
    org = w.org("Rollback", simulated=False)
    w.subscribe(org, "professional", date(2019, 1, 1), simulated=False)
    open_job = completed_job(admin_engine, org, at(date(2019, 5, 6)))
    billed_job = completed_job(admin_engine, org, at(date(2019, 3, 6)))
    with scoped_session(org_id=org, factory=app_factory) as db:
        metering.record_job_usage(db, db.get(models.Job, open_job), 100, 1)  # type: ignore[arg-type]
        metering.record_job_usage(db, db.get(models.Job, billed_job), 200, 1)  # type: ignore[arg-type]
    invoicing.run_invoices(date(2019, 3, 1), now=at(date(2019, 4, 2)), factory=app_factory)
    with scoped_session(org_id=org, factory=app_factory) as db:
        assert metering.void_job_usage(db, db.get(models.Job, open_job)) is True  # type: ignore[arg-type]
        assert metering.void_job_usage(db, db.get(models.Job, billed_job)) is False  # type: ignore[arg-type]
        assert metering.void_job_usage(db, db.get(models.Job, open_job)) is False  # type: ignore[arg-type]
    with scoped_session(org_id=org, factory=app_factory) as db:
        assert [e.records_loaded for e in db.query(models.UsageEvent)] == [200]


# ------------------------------------------------------------------ the daily snapshot


@pytest.fixture
def scenario(w):  # type: ignore[no-untyped-def]
    """Nine organizations whose plan changes happen on known days of March 2019."""
    o = {}
    o["A"] = w.org("A new", industry="Healthcare", region="North America", created=date(2019, 3, 9))
    o["B"] = w.org("B upgrades", industry="Retail & Commerce", region="Europe", created=date(2019, 2, 1))
    o["C"] = w.org("C downgrades", industry="Financial Services", region="Europe", created=date(2019, 1, 1))
    o["D"] = w.org("D churns", industry="Manufacturing", region="Asia Pacific", created=date(2019, 1, 1))
    o["E"] = w.org("E free", industry="Healthcare", region="North America", created=date(2019, 3, 14))
    o["F"] = w.org("F steady", industry="Manufacturing", region="Asia Pacific", created=date(2018, 1, 1))
    o["G"] = w.org("G gone", industry="Retail & Commerce", region="Europe", created=date(2018, 1, 1))
    o["I"] = w.org("I grows", industry="Public Sector", region="Latin America", created=date(2018, 1, 1))
    s = {}
    s["A"] = w.subscribe(o["A"], "professional", date(2019, 3, 10))
    w.subscribe(o["B"], "starter", date(2019, 2, 1), date(2019, 3, 11))
    s["B"] = w.subscribe(o["B"], "professional", date(2019, 3, 11))
    w.subscribe(o["C"], "enterprise", date(2019, 1, 1), date(2019, 3, 12))
    s["C"] = w.subscribe(o["C"], "professional", date(2019, 3, 12))
    s["D"] = w.subscribe(o["D"], "professional", date(2019, 1, 1), date(2019, 3, 13))
    s["E"] = w.subscribe(o["E"], "starter", date(2019, 3, 14))
    s["F"] = w.subscribe(o["F"], "professional", date(2018, 1, 1))
    w.subscribe(o["G"], "enterprise", date(2018, 1, 1), date(2018, 12, 31))
    w.subscribe(o["I"], "professional", date(2018, 1, 1), date(2018, 10, 1))
    s["I"] = w.subscribe(o["I"], "enterprise", date(2018, 10, 1))
    return o, s


def snap(app_factory, day):  # type: ignore[no-untyped-def]
    return snapshots.take_snapshot(day, factory=app_factory)


def test_mrr_movements_each_day(scenario, app_factory) -> None:  # type: ignore[no-untyped-def]
    days = {d: snap(app_factory, date(2019, 3, d)) for d in range(9, 16)}
    expected_mrr = {9: 599_800, 10: 649_700, 11: 699_600, 12: 499_500, 13: 449_600, 14: 449_600, 15: 449_600}
    assert {d: v["mrr_cents"] for d, v in days.items()} == expected_mrr
    moves = {d: (v["new_mrr_cents"], v["expansion_mrr_cents"], v["contraction_mrr_cents"], v["churned_mrr_cents"]) for d, v in days.items()}
    assert moves[10] == (49_900, 0, 0, 0)  # a new paying customer
    assert moves[11] == (0, 49_900, 0, 0)  # Starter -> Professional is expansion
    assert moves[12] == (0, 0, 200_100, 0)  # Enterprise -> Professional is contraction
    assert moves[13] == (0, 0, 0, 49_900)  # a subscription that ended
    assert moves[14] == (0, 0, 0, 0) and moves[9] == (0, 0, 0, 0) and moves[15] == (0, 0, 0, 0)  # a new free account moves no MRR
    for d in range(10, 16):  # the movements explain every change of MRR
        new, exp, con, churn = moves[d]
        assert days[d]["mrr_cents"] == days[d - 1]["mrr_cents"] + new + exp - con - churn


def test_organizations_counts_and_net_revenue_retention(scenario, app_factory) -> None:  # type: ignore[no-untyped-def]
    day13, day14 = snap(app_factory, date(2019, 3, 13)), snap(app_factory, date(2019, 3, 14))
    assert (day14["active_orgs"], day14["paying_orgs"], day14["new_orgs"]) == (day13["active_orgs"] + 1, 5, 1)
    assert day13["active_orgs"] == 5 and day13["paying_orgs"] == 5
    # a year earlier F (Pro), G (Enterprise) and I (Pro) paid 349,800; today F (Pro) and I (Enterprise) pay 299,900, G is gone
    assert abs(day14["nrr_12m"] - Decimal(299_900) / Decimal(349_800)) < Decimal("0.0001")  # 0.8573
    assert snap(app_factory, date(2017, 6, 1))["nrr_12m"] is None  # nothing paid a year before


def test_the_breakdowns_add_up_to_the_totals(scenario, app_factory) -> None:  # type: ignore[no-untyped-def]
    day = snap(app_factory, date(2019, 3, 14))
    b = day["breakdown"]
    assert b["mrr_by_plan"] == {"professional": 199_600, "enterprise": 250_000, "starter": 0} and b["orgs_by_plan"] == {"professional": 4, "enterprise": 1, "starter": 1}
    assert b["mrr_by_industry"] == {"Healthcare": 49_900, "Retail & Commerce": 49_900, "Financial Services": 49_900, "Manufacturing": 49_900, "Public Sector": 250_000}
    assert b["mrr_by_region"] == {"North America": 49_900, "Europe": 99_800, "Asia Pacific": 49_900, "Latin America": 250_000}
    for key in ("mrr_by_plan", "mrr_by_industry", "mrr_by_region"):
        assert sum(b[key].values()) == day["mrr_cents"], key
    assert day["is_simulated"] is True


def test_operations_metrics_of_a_day(scenario, app_factory, w) -> None:  # type: ignore[no-untyped-def]
    o, _ = scenario
    day = date(2019, 3, 12)
    w.job(o["F"], day, records=1_000, duration_ms=2_000, quality=90)
    w.job(o["F"], day, records=500, duration_ms=4_000, quality=96)
    w.job(o["D"], day, ok=False)
    w.llm(o["F"], day, "groq", LlmCallStatus.OK, 1_000, 200)
    w.llm(o["F"], day, "gemini", LlmCallStatus.OK, 250, 50)
    w.llm(o["F"], day, "groq", LlmCallStatus.RATE_LIMITED)
    w.llm(o["F"], day, "gemini", LlmCallStatus.TIMEOUT)
    w.llm(o["F"], day, "groq", LlmCallStatus.CACHE_HIT, cache_hit=True)
    v = snap(app_factory, day)
    assert (v["jobs"], v["records"], v["avg_duration_ms"]) == (3, 1_500, 3_000)
    assert v["success_rate"] == Decimal("0.6667") and v["quality_score"] == Decimal("93.00")
    assert (v["tokens_groq"], v["tokens_gemini"], v["failovers"], v["cache_hits"]) == (1_200, 300, 2, 1)
    quiet = snap(app_factory, date(2019, 3, 20))
    assert (quiet["jobs"], quiet["records"], quiet["success_rate"], quiet["avg_duration_ms"], quiet["quality_score"]) == (0, 0, None, None, None)


def test_revenue_is_recognised_on_the_last_day_of_the_invoiced_period(scenario, app_factory, w) -> None:  # type: ignore[no-untyped-def]
    o, s = scenario
    w.invoice(o["F"], s["F"], date(2019, 3, 1), date(2019, 3, 31), InvoiceStatus.PAID, 49_900, 1_200)
    w.invoice(o["I"], s["I"], date(2019, 3, 1), date(2019, 3, 31), InvoiceStatus.OPEN, 250_000)
    w.invoice(o["A"], s["A"], date(2019, 3, 1), date(2019, 3, 31), InvoiceStatus.VOID, 49_900, 7_000)
    v = snap(app_factory, date(2019, 3, 31))
    assert v["subscription_revenue_cents"] == 299_900 and v["usage_revenue_cents"] == 1_200  # the void invoice is not revenue
    assert v["breakdown"]["usage_by_plan"] == {"professional": 1_200} and v["breakdown"]["usage_by_industry"] == {"Manufacturing": 1_200}
    assert snap(app_factory, date(2019, 3, 30))["subscription_revenue_cents"] == 0


def test_a_snapshot_can_be_taken_again_and_replaces_the_day(scenario, app_factory, w) -> None:  # type: ignore[no-untyped-def]
    o, _ = scenario
    day = date(2019, 3, 16)
    before = snap(app_factory, day)
    w.job(o["F"], day, records=10, quality=95)
    after = snap(app_factory, day)
    assert before["jobs"] == 0 and after["jobs"] == 1
    with scoped_session(factory=app_factory) as db:
        assert db.query(models.DailyMetric).filter(models.DailyMetric.metric_date == day).count() == 1


def test_a_range_and_the_nightly_job(scenario, app_factory) -> None:  # type: ignore[no-untyped-def]
    assert snapshots.snapshot_range(date(2019, 3, 10), date(2019, 3, 13), factory=app_factory) == 4
    days = snapshots.nightly(at(date(2019, 3, 15), 1), factory=app_factory)
    assert days == [date(2019, 3, 14), date(2019, 3, 15)]
    with scoped_session(factory=app_factory) as db:
        assert db.query(models.DailyMetric).filter(models.DailyMetric.metric_date.between(date(2019, 3, 10), date(2019, 3, 15))).count() == 6


# ------------------------------------------------------------------ the platform wide functions and the tenancy they must not break


def test_the_functions_see_every_organization_while_plain_queries_do_not(scenario, app_factory) -> None:  # type: ignore[no-untyped-def]
    o, _ = scenario
    with scoped_session(org_id=o["F"], factory=app_factory) as db:
        assert db.execute(text("SELECT count(*) FROM subscriptions")).scalar() == 1  # row level security still confines the tenant to its own rows
        data = db.execute(text("SELECT platform_day_metrics(:d)"), {"d": date(2019, 3, 14)}).scalar_one()
        assert data["mrr_cents"] == 449_600 and data["active_orgs"] == 6  # and the function aggregates all of them


def test_the_per_organization_function_is_not_callable_by_the_application(scenario, app_factory) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ProgrammingError, match="permission denied"), scoped_session(factory=app_factory) as db:
        db.execute(text("SELECT * FROM platform_org_state(:d)"), {"d": date(2019, 3, 14)})


def test_the_functions_are_not_open_to_everyone(admin_engine) -> None:  # type: ignore[no-untyped-def]
    with Session(admin_engine) as session:
        rows = session.execute(text(
            "SELECT p.proname, has_function_privilege('legacylift_app', p.oid, 'EXECUTE'), has_function_privilege('public', p.oid, 'EXECUTE'), p.prosecdef "
            "FROM pg_proc p WHERE p.proname LIKE 'platform\\_%' ORDER BY 1"
        )).all()
    seen = {r[0]: r[1:] for r in rows}
    assert set(seen) == {"platform_org_state", "platform_billable_orgs", "platform_day_metrics", "platform_top_customers", "platform_invoice_page", "platform_revenue_totals", "platform_mark_overdue",
                    "platform_operations_detail", "platform_cohort_retention", "platform_orgs_with_uploads", "platform_user_organizations"}
    assert all(definer for _, _, definer in seen.values())  # all run with the owner's rights...
    assert not any(public for _, public, _ in seen.values())  # ...so none is open to PUBLIC
    assert [name for name, (app, _, _) in seen.items() if not app] == ["platform_org_state"]  # and only the internal one is closed to the application


# ------------------------------------------------------------------ the scheduled jobs


def test_the_nightly_and_monthly_jobs_are_scheduled() -> None:
    from celery.schedules import crontab

    from app.jobs.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert schedule["nightly-snapshot"]["task"] == "legacylift.nightly_snapshot" and schedule["nightly-snapshot"]["schedule"] == crontab(hour=0, minute=30)
    assert schedule["monthly-invoices"]["task"] == "legacylift.monthly_invoices" and schedule["monthly-invoices"]["schedule"] == crontab(day_of_month=1, hour=1, minute=0)
    assert {"legacylift.nightly_snapshot", "legacylift.monthly_invoices"} <= set(celery_app.tasks)


def test_the_scheduled_tasks_call_the_snapshot_and_the_invoice_run(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The tasks are thin: what they run is tested above. This checks what they pass on, without touching today's real books."""
    from app.billing import invoicing as invoicing_module
    from app.billing import snapshots as snapshots_module
    from app.jobs.tasks import monthly_invoices_task, nightly_snapshot_task

    calls: dict[str, object] = {}
    monkeypatch.setattr(snapshots_module, "nightly", lambda *a, **k: [date(2026, 10, 22), date(2026, 10, 23)])

    def fake_run(period, **kwargs):  # type: ignore[no-untyped-def]
        calls["period"] = period
        return invoicing_module.InvoiceRun(period, period, created=3, skipped=1, failed=[])

    monkeypatch.setattr(invoicing_module, "run_invoices", fake_run)
    assert nightly_snapshot_task() == ["2026-10-22", "2026-10-23"]
    assert monthly_invoices_task() == {"created": 3, "skipped": 1, "failed": 0}
    assert calls["period"] == invoicing_module.previous_month(datetime.now(UTC).date())  # the month that just ended
