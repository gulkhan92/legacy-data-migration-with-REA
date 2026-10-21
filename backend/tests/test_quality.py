"""Quality engine unit tests (pandera checks, duplicates, orphans, score) on synthetic sources."""

from collections import Counter

import pytest

from app.profiling import engine
from app.profiling.quality import BLOCKING, FORMULA, build_schema, derive_rules
from app.profiling.readers import ColumnHint
from tests.test_profiling_columns import table


def check(tables, chunk_size: int = 1000):  # type: ignore[no-untyped-def]
    return engine.profile_and_check(tables, "sqlite", chunk_size=chunk_size)


def rows_with(report, kind: str, column: str | None = None) -> set[int]:  # type: ignore[no-untyped-def]
    return {i.row for i in report.issues if i.type == kind and (column is None or i.column == column)}


def ids(n: int) -> list[int]:
    return list(range(1, n + 1))


def test_required_nulls_only_where_nearly_every_row_has_a_value() -> None:
    values = [f"Name{i}" for i in range(100)]
    values[10] = None
    values[55] = "  "  # blank counts as missing
    optional = [None if i % 3 else f"c{i}" for i in range(100)]  # two thirds empty: an optional column
    _, report = check([table("T", {"id": ids(100), "name": values, "company": optional})])
    assert rows_with(report, "required_null", "name") == {11, 56}  # row ids are 1 based here
    assert rows_with(report, "required_null", "company") == set()


def test_invalid_dates_are_found_in_every_format_of_a_mixed_column() -> None:
    good = ["01/31/2010", "20110215", "05-Mar-12", "12/01/2009"] * 30
    bad = {7: "13/45/2011", 20: "20111345", 33: "31-Feb-11", 50: "N/A", 77: "00000000", 90: "02/30/2010"}
    for i, v in bad.items():
        good[i] = v
    _, report = check([table("T", {"id": ids(120), "d": good})])
    assert rows_with(report, "invalid_date") == {i + 1 for i in bad}
    assert all(not i.blocking for i in report.issues if i.type in ("whitespace", "case_anomaly"))


def test_dates_outside_a_plausible_range_are_invalid() -> None:
    values = ["2010-05-17"] * 100 + ["1650-01-01", "2999-12-31"]
    _, report = check([table("T", {"id": ids(102), "d": values})])
    assert rows_with(report, "invalid_date") == {101, 102}


def test_out_of_range_numbers() -> None:
    prices = ["0.99", "1.99", "$0.99", "$1.99"] * 50
    prices[3], prices[60], prices[120] = "-0.99", "5000000.00", "250000.00"
    quantities = [1, 2] * 100
    quantities[5], quantities[44] = -3, 9999
    _, report = check([table("T", {"id": ids(200), "unit_prc": prices, "qty": quantities})])
    assert rows_with(report, "out_of_range", "unit_prc") == {4, 61, 121}
    assert rows_with(report, "out_of_range", "qty") == {6, 45}
    assert not rows_with(report, "out_of_range", "id")  # keys are not range checked


def test_a_column_with_many_negative_values_has_no_lower_limit() -> None:
    balance = [-50.0, 20.0, -5.5, 10.0, -200.0] * 40
    _, report = check([table("T", {"id": ids(200), "balance": balance})])
    assert rows_with(report, "out_of_range") == set()


def test_text_in_a_numeric_column_is_an_invalid_number() -> None:
    prices = ["0.99", "1.99"] * 100
    prices[17] = "call us"
    _, report = check([table("T", {"id": ids(200), "price": prices})])
    assert rows_with(report, "invalid_number") == {18}


def test_orphan_keys() -> None:
    parent = table("P", {"id": ids(100)})
    refs = [(i % 100) + 1 for i in range(400)]
    refs[3], refs[250] = 9001, 8002
    child = table("C", {"id": ids(400), "p_id": refs})
    result, report = check([parent, child])
    assert rows_with(report, "orphan_fk", "p_id") == {4, 251}
    assert report.config["C"]["references"] == {"p_id": "P.id"}


def test_duplicates_across_chunks_keep_the_first_row_and_ignore_row_numbers() -> None:
    names = [f"Name{i}" for i in range(100)]
    names.append(names[3])  # an exact copy of row 4, except for its row number
    names.append(names[90])
    data = {"id": ids(102), "name": names, "city": [f"City{i % 9}" for i in range(100)] + ["City3", "City0"]}
    data["name"] = names
    _, report = check([table("T", data)], chunk_size=16)  # the copies fall in different chunks from the originals
    assert rows_with(report, "duplicate") == {101, 102}


def test_key_columns_that_repeat_in_a_duplicate_row_are_part_of_the_comparison() -> None:
    keys = list(range(1, 101)) + [5]  # not a sequence any more: row 101 repeats key 5
    names = [f"N{i}" for i in range(100)] + ["N4"]
    cities = [f"City{i % 13}" for i in range(100)] + ["City4"]
    _, report = check([table("T", {"k": keys, "n": names, "c": cities})])
    assert rows_with(report, "duplicate") == {101}


def test_whitespace_and_case_are_warnings() -> None:
    cities = [f"Berlin" if i % 2 else "Oslo" for i in range(200)]
    cities[10] = "Berlin  "
    cities[11] = "BERLIN"
    cities[12] = "oslo"
    cities[13] = "oSLO"
    _, report = check([table("T", {"id": ids(200), "city": cities, "ref": [f"R{i}" for i in range(200)]})])
    assert rows_with(report, "whitespace") == {11}
    assert rows_with(report, "case_anomaly") == {12, 13, 14}
    assert not any(i.blocking for i in report.issues)
    assert "whitespace" not in BLOCKING and "case_anomaly" not in BLOCKING


def test_pii_values_never_appear_in_issues() -> None:
    emails = [f"user{i}@example.com" for i in range(100)]
    emails[5] = None
    _, report = check([table("T", {"id": ids(100), "email_adr": emails})])
    flagged = [i for i in report.issues if i.column == "email_adr"]
    assert flagged and all(i.value is None for i in flagged)


def test_quality_score_formula() -> None:
    n = 200
    dates = ["2010-05-17", "2011-06-18"] * 100
    for i in range(8):
        dates[10 + i] = "not a date"
    amounts = [1.5, 2.5] * 100
    for i in range(3):
        amounts[50 + i] = -4.0
    cities = ["Berlin", "Oslo"] * 100
    for i in range(5):
        cities[120 + i] = cities[120 + i] + " "
    result, report = check([table("T", {"id": ids(n), "ref": [f"R{i}" for i in range(n)], "d": dates, "amount": amounts, "city": cities})])
    (t,) = report.tables
    assert (t.blocking_rows, t.warning_rows) == (11, 5)
    assert t.score == pytest.approx(100 * (1 - (11 + 0.25 * 5) / 200), abs=0.01)
    assert report.score == t.score and "0.25" in FORMULA and report.issue_counts["invalid_date"] == 8


def test_overall_score_is_row_weighted() -> None:
    clean = table("A", {"id": ids(300), "ref": [f"R{i}" for i in range(300)], "v": [1.0, 2.0] * 150})
    dirty_dates = ["2010-01-01"] * 90 + ["bad"] * 10
    dirty = table("B", {"id": ids(100), "ref": [f"R{i}" for i in range(100)], "d": dirty_dates})
    _, report = check([clean, dirty])
    scores = {t.table: t for t in report.tables}
    assert scores["A"].score == 100.0 and scores["B"].score == 90.0
    assert report.score == pytest.approx((300 * 100 + 100 * 90) / 400)


def test_a_clean_source_scores_100() -> None:
    _, report = check([table("T", {"id": ids(150), "name": [f"Name{i}" for i in range(150)], "d": ["2010-01-01"] * 150})])
    assert report.score == 100.0 and report.issues == [] and report.issue_counts == {}


def test_rules_are_visible_in_the_report_and_use_pandera() -> None:
    import pandera.pandas as pa

    data = {"id": ids(100), "d": ["2010-01-01"] * 100, "n": [1.0, 2.0] * 50}
    result, counters = engine.profile_tables([table("T", data)], "sqlite")
    rules = derive_rules(result, engine.key_sets(result, counters))
    schema = build_schema(rules[0])
    assert isinstance(schema, pa.DataFrameSchema)
    names = {c.name for col in schema.columns.values() for c in col.checks}
    assert {"required_null", "invalid_date", "out_of_range"} <= names
    _, report = check([table("T", data)])
    assert report.config["T"]["dates"] == ["d"] and "n" in report.config["T"]["ranges"] and report.formula == FORMULA


def test_issue_listing_is_capped_but_counts_are_complete() -> None:
    from app.profiling.quality import run_quality

    data = {"id": ids(300), "d": ["bad"] * 100 + ["2010-01-01"] * 200}
    src = table("T", data)
    result, counters = engine.profile_tables([src], "sqlite")
    # the column is not a date column (two thirds valid), so force explicit rules instead
    rules = derive_rules(result, {})
    rules[0].columns[1].date_formats = ["%Y-%m-%d"]
    report = run_quality([src], rules, chunk_size=100, max_issues=10)
    assert report.issue_counts["invalid_date"] == 100 and len(report.issues) == 10 and report.issues_truncated
    assert Counter(i.type for i in report.issues) == {"invalid_date": 10}
    assert ColumnHint("x").name == "x"
