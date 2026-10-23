"""Every metric formula, on small hand-made series. No database."""

from datetime import date, timedelta

import pytest

from app.billing import analytics
from app.billing.analytics import Day, bucket_start, compare, net_new, percent_change, previous_range, series, summarize, weighted


def day(d: int, **kw) -> Day:  # type: ignore[no-untyped-def]
    return Day(date(2026, 3, d), **kw)


# A week of a small business. MRR moves 1000 -> 1300 -> 1600 -> 1500 -> 1450 -> 1450.
WEEK = [
    day(1, mrr_cents=1_000, active_orgs=3, paying_orgs=2, new_orgs=0, jobs=2, success_rate=1.0, avg_duration_ms=1_000, quality_score=90.0, subscription_revenue_cents=0),
    day(2, mrr_cents=1_300, active_orgs=4, paying_orgs=3, new_orgs=1, new_mrr_cents=300, jobs=3, success_rate=2 / 3, avg_duration_ms=4_000, quality_score=96.0),
    day(3, mrr_cents=1_600, active_orgs=4, paying_orgs=3, expansion_mrr_cents=300, jobs=0),
    day(4, mrr_cents=1_500, active_orgs=4, paying_orgs=3, contraction_mrr_cents=100, jobs=5, success_rate=0.8, avg_duration_ms=2_000, quality_score=93.0),
    day(5, mrr_cents=1_450, active_orgs=3, paying_orgs=2, churned_mrr_cents=50, usage_revenue_cents=70, subscription_revenue_cents=1_450),
    day(6, mrr_cents=1_450, active_orgs=3, paying_orgs=2),
]  # fmt: skip
# Day 1 carries its own movement so the MRR before the period is known: 1000 - (+0) = 1000.


def test_net_new_mrr_is_new_plus_expansion_minus_contraction_minus_churn() -> None:
    assert net_new(day(1, new_mrr_cents=500, expansion_mrr_cents=200, contraction_mrr_cents=80, churned_mrr_cents=20)) == 600


def test_mrr_at_the_end_is_the_start_plus_net_new() -> None:
    s = summarize(WEEK)
    assert (s["mrr_start_cents"], s["net_new_mrr_cents"], s["mrr_cents"]) == (1_000, 450, 1_450)
    assert s["mrr_movements_add_up"] is True
    assert (s["new_mrr_cents"], s["expansion_mrr_cents"], s["contraction_mrr_cents"], s["churned_mrr_cents"]) == (300, 300, 100, 50)
    broken = [*WEEK[:-1], day(6, mrr_cents=9_999)]
    assert summarize(broken)["mrr_movements_add_up"] is False  # a hole in the data is noticed, not hidden


def test_gross_churn_is_churned_mrr_over_the_mrr_at_the_start() -> None:
    s = summarize(WEEK)
    assert s["gross_churn_rate"] == pytest.approx(50 / 1_000, abs=1e-4)
    assert summarize([day(1, mrr_cents=0)])["gross_churn_rate"] is None  # nothing to churn from


def test_arpa_is_mrr_over_paying_accounts_and_arr_is_twelve_times_mrr() -> None:
    s = summarize(WEEK)
    assert s["arpa_cents"] == 725 and s["arr_cents"] == 17_400 and s["paying_orgs"] == 2
    assert summarize([day(1, mrr_cents=500, paying_orgs=0)])["arpa_cents"] is None


def test_net_revenue_retention_is_the_value_on_the_last_day() -> None:
    rows = [day(1, mrr_cents=1, nrr_12m=0.9), day(2, mrr_cents=1, nrr_12m=1.0734)]
    assert summarize(rows)["net_revenue_retention_12m"] == 1.0734
    assert summarize([day(1, mrr_cents=1)])["net_revenue_retention_12m"] is None


def test_revenue_adds_subscription_and_usage_and_usage_share_is_its_part() -> None:
    s = summarize(WEEK)
    assert (s["subscription_revenue_cents"], s["usage_revenue_cents"], s["total_revenue_cents"]) == (1_450, 70, 1_520)
    assert s["usage_share"] == pytest.approx(70 / 1_520, abs=1e-4)


def test_ratios_are_weighted_by_the_jobs_of_each_day() -> None:
    # (1.0 * 2 + 2/3 * 3 + 0.8 * 5) / 10 = 0.8 ; days without jobs count for nothing
    assert weighted(WEEK, "success_rate") == pytest.approx(0.8)
    s = summarize(WEEK)
    assert s["success_rate"] == 0.8 and s["jobs"] == 10
    assert s["avg_duration_ms"] == round((1_000 * 2 + 4_000 * 3 + 2_000 * 5) / 10) == 2_400
    assert s["quality_score"] == round((90 * 2 + 96 * 3 + 93 * 5) / 10, 2) == 93.3
    assert summarize([day(1, mrr_cents=1)])["success_rate"] is None  # no jobs, no rate


def test_the_other_flows_are_summed() -> None:
    rows = [day(1, mrr_cents=1, tokens_groq=100, tokens_gemini=20, failovers=1, cache_hits=2, records=10, new_orgs=1), day(2, mrr_cents=1, tokens_groq=50, tokens_gemini=5, failovers=0, cache_hits=3, records=5, new_orgs=2)]
    s = summarize(rows)
    assert (s["tokens_groq"], s["tokens_gemini"], s["tokens_total"], s["failovers"], s["cache_hits"], s["records"], s["new_orgs"]) == (150, 25, 175, 1, 5, 15, 3)


@pytest.mark.parametrize("d,gran,expected", [(date(2026, 3, 4), "day", date(2026, 3, 4)), (date(2026, 3, 4), "week", date(2026, 3, 2)), (date(2026, 3, 8), "week", date(2026, 3, 2)),
                                              (date(2026, 3, 9), "week", date(2026, 3, 9)), (date(2026, 3, 31), "month", date(2026, 3, 1))])  # fmt: skip
def test_buckets_start_on_the_day_the_week_or_the_month(d, gran, expected) -> None:  # type: ignore[no-untyped-def]
    assert bucket_start(d, gran) == expected  # ISO weeks begin on Monday: 2 March 2026 is one


def test_a_series_takes_stocks_from_the_end_of_the_bucket_and_sums_flows() -> None:
    weekly = series(WEEK, "week", ("mrr_cents", "new_mrr_cents", "net_new_mrr_cents", "paying_orgs", "jobs"))
    assert len(weekly) == 2  # 1 March 2026 is a Sunday: it forms a week of its own, 2 to 6 March is the next
    assert weekly[0]["period"] == "2026-02-23" and weekly[0]["mrr_cents"] == 1_000
    assert weekly[1]["period"] == "2026-03-02" and weekly[1]["mrr_cents"] == 1_450 and weekly[1]["new_mrr_cents"] == 300 and weekly[1]["jobs"] == 8
    assert [p["mrr_cents"] for p in series(WEEK, "day", ("mrr_cents",))] == [1_000, 1_300, 1_600, 1_500, 1_450, 1_450]
    monthly = series(WEEK, "month", ("mrr_cents", "jobs", "net_new_mrr_cents"))
    assert monthly == [{"period": "2026-03-01", "mrr_cents": 1_450, "jobs": 10, "net_new_mrr_cents": 450}]


def test_the_weekly_buckets_chain_the_mrr_movements() -> None:
    rows = [day(d, mrr_cents=1_000 + 100 * d, new_mrr_cents=100) for d in range(1, 15)]
    points = series(rows, "week", ("mrr_cents", "net_new_mrr_cents"))
    assert [p["period"] for p in points] == ["2026-02-23", "2026-03-02", "2026-03-09"]
    assert points[2]["mrr_cents"] == 2_400 and points[2]["net_new_mrr_cents"] == 600  # 6 days of 100


def test_the_previous_period_has_the_same_length_and_ends_the_day_before() -> None:
    assert previous_range(date(2026, 3, 11), date(2026, 3, 20)) == (date(2026, 3, 1), date(2026, 3, 10))
    assert previous_range(date(2026, 3, 1), date(2026, 3, 1)) == (date(2026, 2, 28), date(2026, 2, 28))
    start, end = date(2026, 1, 1), date(2026, 3, 31)
    p_start, p_end = previous_range(start, end)
    assert (p_end - p_start) == (end - start) and p_end == start - timedelta(days=1)


def test_change_against_the_previous_period() -> None:
    cur, prev = {"mrr_cents": 1_500, "jobs": 10, "rate": None}, {"mrr_cents": 1_000, "jobs": 0, "rate": 0.9}
    result = compare(cur, prev, ["mrr_cents", "jobs", "rate"])
    assert result["mrr_cents"] == {"current": 1_500, "previous": 1_000, "delta": 500, "change": 0.5}
    assert result["jobs"]["change"] is None  # growth from zero has no percentage
    assert result["rate"]["delta"] is None and result["rate"]["change"] is None
    assert percent_change(90, 100) == -0.1 and percent_change(5, None) is None and percent_change(None, 5) is None


def test_the_summary_of_one_day_is_well_defined() -> None:
    s = summarize([day(1, mrr_cents=500, new_mrr_cents=500, paying_orgs=1)])
    assert s["days"] == 1 and s["mrr_start_cents"] == 0 and s["mrr_cents"] == 500 and s["mrr_growth_rate"] is None and s["mrr_movements_add_up"] is True


def test_the_dimensions_are_the_ones_the_snapshot_stores() -> None:
    assert set(analytics.DIMENSIONS) == {"plan", "industry", "region"}
