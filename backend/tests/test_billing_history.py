"""The simulated history: deterministic, shaped as described, and when written, consistent with the books."""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import text

from app.billing.history import INDUSTRIES, REGIONS, generate_history, reset_simulated, write_history
from app.billing.plans import ENTERPRISE, ORDER, PROFESSIONAL, STARTER
from tests.conftest import HISTORY_END


@pytest.fixture(scope="module")
def data():  # type: ignore[no-untyped-def]
    return generate_history(42, HISTORY_END)


def months_between(a, b) -> float:  # type: ignore[no-untyped-def]
    return max(0.0, (b - a).total_seconds() / (30.44 * 86_400))


def test_the_history_is_deterministic_and_the_seed_matters(data) -> None:  # type: ignore[no-untyped-def]
    again = generate_history(42, HISTORY_END)
    assert [(o.id, o.name, o.signup, [(p.plan, p.start, p.end) for p in o.periods]) for o in again.orgs] == [(o.id, o.name, o.signup, [(p.plan, p.start, p.end) for p in o.periods]) for o in data.orgs]
    assert [(j.id, j.records, j.tokens, j.completed) for j in again.jobs] == [(j.id, j.records, j.tokens, j.completed) for j in data.jobs]
    other = generate_history(7, HISTORY_END)
    assert [o.name for o in other.orgs] != [o.name for o in data.orgs]


def test_about_140_organizations_across_five_industries_and_four_regions_over_24_months(data) -> None:  # type: ignore[no-untyped-def]
    assert len(data.orgs) == 140 and len({o.name for o in data.orgs}) == 140 and len({o.id for o in data.orgs}) == 140
    assert {o.industry for o in data.orgs} == set(INDUSTRIES) and {o.region for o in data.orgs} == set(REGIONS)
    assert (data.start, data.end, len(data.months)) == (date(2024, 11, 1), HISTORY_END, 24)
    assert all(data.start <= o.signup.date() <= data.end for o in data.orgs)


def test_signups_follow_an_s_curve(data) -> None:  # type: ignore[no-untyped-def]
    per_month = Counter((o.signup.year, o.signup.month) for o in data.orgs)
    counts = [per_month.get((m.year, m.month), 0) for m in data.months]
    first, middle, last = sum(counts[:8]), sum(counts[8:16]), sum(counts[16:])
    assert middle > first and middle > last and last > 0  # slow start, steep middle, a tail that goes on
    cumulative = [sum(counts[: i + 1]) for i in range(len(counts))]
    assert cumulative[-1] == 140 and cumulative == sorted(cumulative)


def test_the_plan_mix_depends_on_the_industry(data) -> None:  # type: ignore[no-untyped-def]
    first_plan = [(o.industry, o.periods[0].plan) for o in data.orgs]
    assert {p for _, p in first_plan} == set(ORDER)
    enterprise_share = lambda industry: sum(1 for i, p in first_plan if i == industry and p == ENTERPRISE) / sum(1 for i, _ in first_plan if i == industry)  # noqa: E731
    assert enterprise_share("Financial Services") + enterprise_share("Healthcare") > enterprise_share("Retail & Commerce") * 2


def test_churn_is_highest_on_starter_and_lowest_on_enterprise(data) -> None:  # type: ignore[no-untyped-def]
    horizon = datetime.combine(data.end + timedelta(days=1), time.min, tzinfo=UTC)
    exposure: dict[str, float] = defaultdict(float)  # months spent on each plan
    churn: Counter[str] = Counter()
    for o in data.orgs:
        for p in o.periods:
            exposure[p.plan] += months_between(p.start, p.end or horizon)
        if o.periods[-1].end is not None:
            churn[o.periods[-1].plan] += 1
    rate = {plan: churn[plan] / exposure[plan] for plan in ORDER}
    assert rate[STARTER] > rate[PROFESSIONAL] > rate[ENTERPRISE] > 0
    assert 0.02 < rate[STARTER] < 0.07 and rate[ENTERPRISE] < 0.02 and sum(churn.values()) >= 15


def test_accounts_expand_through_usage_growth_and_sometimes_contract(data) -> None:  # type: ignore[no-untyped-def]
    upgrades = downgrades = 0
    for o in data.orgs:
        for a, b in zip(o.periods, o.periods[1:]):
            assert a.end == b.start  # no gap and no overlap between plans
            if ORDER.index(b.plan) > ORDER.index(a.plan):
                upgrades += 1
            else:
                downgrades += 1
    assert upgrades >= 15 and 0 < downgrades < upgrades


def test_usage_is_seasonal_with_a_q4_peak(data) -> None:  # type: ignore[no-untyped-def]
    records: Counter[tuple[int, int]] = Counter()
    for j in data.jobs:
        if j.ok:
            records[(j.completed.year, j.completed.month)] += j.records
    active: Counter[tuple[int, int]] = Counter()
    for m in data.months:
        end = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        for o in data.orgs:
            gone = o.periods[-1].end
            if o.signup.date() < end and (gone is None or gone.date() >= m):
                active[(m.year, m.month)] += 1
    per_org = {k: records[k] / active[k] for k in active if active[k] >= 40}
    q4 = [v for (y, mo), v in per_org.items() if mo in (10, 11, 12)]
    winter = [v for (y, mo), v in per_org.items() if mo in (1, 2)]
    assert q4 and winter and sum(q4) / len(q4) > 1.25 * sum(winter) / len(winter)


def test_jobs_have_plausible_tokens_latency_failures_and_quality(data) -> None:  # type: ignore[no-untyped-def]
    ok = [j for j in data.jobs if j.ok]
    failed = [j for j in data.jobs if not j.ok]
    assert 5_000 < len(data.jobs) < 9_000 and len(failed) / len(data.jobs) == pytest.approx(0.07, abs=0.03)
    zero = sum(1 for j in ok if j.tokens == 0) / len(ok)
    assert 0.5 < zero < 0.7  # most runs are repeats of something cached
    assert max(j.tokens for j in ok) < 6_000  # a first run stays under the budget of 6,000 tokens
    assert all(j.tokens == 0 for j in failed) and all(j.records == 0 for j in failed)
    assert all(85.0 <= j.quality_after <= 99.9 and j.quality_before < j.quality_after for j in ok)  # type: ignore[operator]
    assert 95 < sum(j.quality_after for j in ok) / len(ok) < 97.5  # type: ignore[misc]
    statuses = Counter(c["status"] for j in data.jobs for c in j.llm)
    assert statuses["cache_hit"] > 1_000 and statuses["ok"] > 1_000 and (statuses["rate_limited"] + statuses["timeout"]) > 50  # failovers happen
    by_size = sorted(ok, key=lambda j: j.records)
    small, big = by_size[: len(by_size) // 4], by_size[-len(by_size) // 4 :]
    assert sum(j.duration_ms for j in big) / len(big) > 3 * sum(j.duration_ms for j in small) / len(small)  # bigger loads take longer


def test_jobs_stay_inside_the_window_and_the_life_of_the_organization(data) -> None:  # type: ignore[no-untyped-def]
    horizon = data.jobs[-1].completed
    assert horizon.date() <= HISTORY_END
    for j in data.jobs:
        org = data.orgs[j.org]
        assert j.started < j.completed and j.completed >= org.signup
        last = org.periods[-1].end
        assert last is None or j.completed <= last + timedelta(days=1)


# ------------------------------------------------------------------ written to the database


def test_the_written_history_matches_the_generated_one(history, admin_engine) -> None:  # type: ignore[no-untyped-def]
    summary = history.summary
    assert summary["organizations"] == 140 and summary["jobs"] == len(history.jobs) and summary["snapshots"] == (history.end - history.start).days + 1
    with admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM organizations WHERE is_simulated")).scalar() == 140
        assert c.execute(text("SELECT count(*) FROM jobs j JOIN organizations o ON o.id = j.org_id WHERE o.is_simulated")).scalar() == len(history.jobs)
        assert c.execute(text("SELECT COALESCE(sum(records_loaded), 0) FROM usage_events WHERE is_simulated")).scalar() == sum(j.records for j in history.jobs)
        assert c.execute(text("SELECT count(*) FROM subscriptions WHERE is_simulated")).scalar() == sum(len(o.periods) for o in history.orgs)


def test_the_books_balance(history, admin_engine) -> None:  # type: ignore[no-untyped-def]
    with admin_engine.connect() as c:
        invoiced = c.execute(text("SELECT COALESCE(sum(total_cents), 0) FROM invoices WHERE is_simulated AND status <> 'void'")).scalar()
        shown = c.execute(text("SELECT sum(subscription_revenue_cents + usage_revenue_cents) FROM daily_metrics WHERE metric_date BETWEEN :a AND :b"), {"a": history.start, "b": history.end}).scalar()
        assert invoiced == shown > 0  # the revenue shown is exactly the invoices
        lines = c.execute(text("SELECT COALESCE(sum(il.amount_cents), 0) FROM invoice_lines il JOIN invoices i ON i.id = il.invoice_id WHERE i.is_simulated AND i.status <> 'void'")).scalar()
        assert lines == invoiced  # and the invoice lines add up to the invoice totals
        mismatched = c.execute(text("SELECT count(*) FROM invoices i WHERE i.is_simulated AND i.total_cents <> (SELECT COALESCE(sum(amount_cents), 0) FROM invoice_lines WHERE invoice_id = i.id)")).scalar()
        assert mismatched == 0
        assert c.execute(text("SELECT count(*) FROM invoices WHERE is_simulated AND total_cents <= 0")).scalar() == 0  # nothing is invoiced for zero
        # MRR moves exactly by the movements, every single day
        broken = c.execute(text(
            "SELECT count(*) FROM (SELECT mrr_cents - lag(mrr_cents) OVER (ORDER BY metric_date) AS d, new_mrr_cents + expansion_mrr_cents - contraction_mrr_cents - churned_mrr_cents AS n "
            "FROM daily_metrics WHERE metric_date BETWEEN :a AND :b) x WHERE d IS NOT NULL AND d <> n"), {"a": history.start, "b": history.end}).scalar()
        assert broken == 0
        # and the last day's MRR is the sum of the fees of the subscriptions still running
        live = c.execute(text("SELECT COALESCE(sum(p.monthly_fee_cents), 0) FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.is_simulated AND s.ended_at IS NULL")).scalar()
        assert live == c.execute(text("SELECT mrr_cents FROM daily_metrics WHERE metric_date = :d"), {"d": history.end}).scalar()


def test_invoices_have_a_realistic_payment_pattern(history, admin_engine) -> None:  # type: ignore[no-untyped-def]
    with admin_engine.connect() as c:
        statuses = dict(c.execute(text("SELECT status, count(*) FROM invoices WHERE is_simulated GROUP BY status")).all())
        total = sum(statuses.values())
        assert set(statuses) == {"paid", "open", "overdue", "void"}
        assert statuses["paid"] / total > 0.85 and 0 < statuses["void"] / total < 0.04 and statuses["overdue"] > 0
        late = c.execute(text("SELECT count(*) FROM invoices WHERE status = 'paid' AND is_simulated AND paid_at > due_at")).scalar()
        assert late > 0  # some customers pay late
        assert c.execute(text("SELECT count(*) FROM invoices WHERE status = 'open' AND is_simulated AND due_at < :d"), {"d": history.end}).scalar() == 0  # an unpaid invoice past its date is overdue
        assert c.execute(text("SELECT count(*) FROM invoices WHERE status = 'paid' AND paid_at IS NULL")).scalar() == 0


def test_writing_over_an_existing_history_is_refused(history, admin_engine) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(RuntimeError, match="already exist"):
        write_history(admin_engine, history, snapshot=False)
    assert callable(reset_simulated)
