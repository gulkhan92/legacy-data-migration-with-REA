"""Profiler unit tests on small synthetic sources (no database, no REA)."""

import sqlite3
from collections.abc import Iterator

import pandas as pd
import pytest

from app.profiling import column_profile, engine, llm_view, pii, relations, semantic
from app.profiling.column_profile import ColumnAccumulator, shape
from app.profiling.models import ColumnProfile
from app.profiling.readers import ColumnHint, CsvReader, SqliteReader, TableSource


def table(name: str, data: dict[str, list], hints: dict[str, ColumnHint] | None = None, chunk_rows: int = 1000) -> TableSource:
    frame = pd.DataFrame(data).astype(object)
    frame.index = pd.Index(range(1, len(frame) + 1), name="row")
    columns = [(hints or {}).get(c, ColumnHint(c)) for c in frame.columns]

    def chunks(size: int) -> Iterator[pd.DataFrame]:
        step = min(size, chunk_rows)
        for start in range(0, len(frame), step):
            yield frame.iloc[start : start + step]

    return TableSource(name, columns, len(frame), chunks)


def profile_one(values: list, name: str = "col", hint: ColumnHint | None = None) -> ColumnProfile:
    acc = ColumnAccumulator(name, hint or ColumnHint(name))
    acc.update(pd.Series(values, dtype=object))
    column = acc.finalize("t", 0)
    semantic.apply_layout_hints(column)
    semantic.classify(column)
    pii.apply_pii(column)
    return column


# ------------------------------------------------------------------ basic statistics


def test_nulls_blanks_distinct_and_lengths() -> None:
    p = profile_one(["alpha", "beta", None, "  ", "alpha", "gamma delta", ""] * 10)
    assert (p.rows, p.null_count, p.blank_count) == (70, 10, 20)
    assert p.null_rate == pytest.approx(30 / 70, abs=1e-4)
    assert p.distinct_count == 3 and p.unique_ratio == pytest.approx(3 / 40, abs=1e-4)
    assert p.length.min == 4 and p.length.max == 11 and p.inferred_type == "text"
    assert [t.pattern for t in p.top_patterns][:2] == ["a+", "a+ a+"]
    assert p.top_values[0].value == "alpha" and p.top_values[0].count == 20


def test_shapes() -> None:
    assert shape("12/31/2010") == "99/99/9999" and shape("01-Jan-09") == "99-Aa+-99" and shape("A1b2") == "A9a9"
    assert shape("1234567890") == "9+" and shape("x@y.com") == "a@a.a+"


def test_integer_decimal_and_money_text() -> None:
    assert profile_one(["1", "2", "3", "40"] * 10).inferred_type == "integer"
    assert profile_one(["1.5", "2.25", "3"] * 10).inferred_type == "decimal"
    money = profile_one(["$0.99", "1.99", "$2.50", "0.99"] * 10, "unit_prc")
    assert money.inferred_type == "decimal" and money.semantic_type == "currency"
    assert money.numeric.currency_symbols == ["$"] and money.numeric.formats["currency"] == 20 and money.numeric.formats["plain"] == 20
    assert money.numeric.min == 0.99 and money.numeric.max == 2.5 and money.numeric.negative_share == 0
    european = profile_one(["1,50", "2,75", "10,00"] * 10)
    assert european.numeric.formats == {"decimal_comma": 30}
    thousands = profile_one(["1,200.50", "30,000.00", "5.00"] * 10)
    assert thousands.numeric.formats["thousands"] == 20 and thousands.numeric.max == 30000.0


def test_native_numbers_and_quantiles() -> None:
    p = profile_one(list(range(1, 101)), "qty")
    assert p.inferred_type == "integer" and p.numeric.min == 1 and p.numeric.max == 100
    assert p.numeric.p05 == pytest.approx(5.95) and p.numeric.p95 == pytest.approx(95.05)
    assert profile_one([0, 1, 1, 0] * 10, "flag").inferred_type == "boolean"


def test_text_columns_of_digits_with_leading_zeros_stay_text() -> None:
    zip_codes = profile_one(["01757", "02134", "10001", "90210"] * 10, "zip_cd")
    assert zip_codes.inferred_type == "text" and zip_codes.semantic_type == "postal_code" and zip_codes.pii
    codes = profile_one(["314529007", "44054006", "15777000"] * 10, "dx_cd")
    assert codes.inferred_type == "text"


# ------------------------------------------------------------------ dates


def test_mixed_date_formats_are_recognised_with_validity_counts() -> None:
    values = ["01/31/2010", "20110215", "05-Mar-12", "12/01/2009", "20100101", "13-Jan-11"] * 20
    p = profile_one(values, "inv_dt")
    assert p.inferred_type == "date" and p.semantic_type == "date"
    assert set(p.date.dominant) == {"%m/%d/%Y", "%Y%m%d", "%d-%b-%y"}
    assert p.date.formats["%m/%d/%Y"].shape == p.date.formats["%m/%d/%Y"].valid == 40
    assert p.date.min.startswith("2009-12-01") and p.date.max.startswith("2012-03-05")


def test_invalid_dates_keep_the_column_a_date_but_are_counted_as_shape_only() -> None:
    values = ["01/31/2010"] * 95 + ["13/45/2011", "02/30/2010", "N/A", "00/00/0000", "99/99/9999"]
    p = profile_one(values)
    assert p.inferred_type == "date"
    assert p.date.formats["%m/%d/%Y"].shape == 99 and p.date.formats["%m/%d/%Y"].valid == 95


def test_day_first_and_month_first_ambiguity_is_reported() -> None:
    both_valid = ["01/02/2010", "03/04/2011", "05/06/2012", "07/08/2013"] * 10
    p = profile_one(both_valid)
    assert p.date.ambiguous and p.date.dominant == ["%m/%d/%Y"]
    dmy = profile_one(["25/12/2010", "30/01/2011", "13/05/2012", "01/02/2010"] * 10)
    assert dmy.date.dominant == ["%d/%m/%Y"] and not dmy.date.ambiguous


def test_eight_digit_ids_are_not_dates_unless_they_parse_as_dates() -> None:
    assert profile_one([str(10000000 + i * 7919) for i in range(60)]).inferred_type != "date"
    assert profile_one(["20100101", "20110203", "20121231"] * 10).inferred_type == "date"


def test_timestamps() -> None:
    p = profile_one(["2023-06-12T09:28:29Z", "2024-01-05T14:10:00Z"] * 15)
    assert p.inferred_type == "timestamp" and p.semantic_type == "timestamp" and p.date.has_time


def test_integer_dates_pick_the_epoch_that_is_not_in_the_future() -> None:
    hint = ColumnHint("BirthDt", "int32", "epoch_days", "int")
    values = [18000 + i * 100 for i in range(150)] + [0] * 10 + [-1] * 3
    p = profile_one(values, "BirthDt", hint)
    assert p.inferred_type == "date" and p.date.epoch == "1900-01-01" and p.date.unit == "days"
    assert p.null_markers == [0] and p.invalid_markers == [-1]
    assert p.min is None and p.pii and "date_of_birth" in p.pii_kinds  # a birth date keeps no values in its profile
    start = profile_one(values, "StartDt", hint)
    assert start.min.startswith("1949") and not start.pii and start.semantic_type == "date"
    minutes = profile_one([60_000_000 + i * 1000 for i in range(100)], "StartMin", ColumnHint("StartMin", "int32", "epoch_minutes", "int"))
    assert minutes.inferred_type == "timestamp" and minutes.date.unit == "minutes" and minutes.date.epoch == "1900-01-01"


# ------------------------------------------------------------------ semantic types and PII


@pytest.mark.parametrize(
    "name,values,semantic_type,pii_kind",
    [
        ("email_adr", ["luis@embraer.com.br", "leonie@gmx.de", "a.b@c.org"] * 10, "email", "email"),
        ("ph_no", ["+55 (12) 3923-5555", "+49 0711 2842222", "+1 (650) 253-0000"] * 10, "phone", "phone"),
        ("Ssn", ["999-10-8066", "999-23-4411", "999-77-0102"] * 10, "ssn", "national_id"),
        ("zip_cd", ["12227-000", "70174", "94043"] * 10, "postal_code", "postal_code"),
        ("l_nm", ["Holy", "Philips", "Mitchell", "Gruber"] * 10, "person_name", "person_name"),
        ("addr1", ["696 Osborne Street", "1 Microsoft Way"] * 20, "address", "street_address"),
    ],
)
def test_semantic_type_and_pii_rules(name: str, values: list, semantic_type: str, pii_kind: str) -> None:
    p = profile_one(values, name)
    assert p.semantic_type == semantic_type and p.pii and pii_kind in p.pii_kinds


def test_non_pii_columns() -> None:
    category = profile_one(["Rock", "Jazz", "Metal", "Latin"] * 25, "cat_nm")
    assert category.semantic_type == "enumerated_code" and not category.pii
    key = profile_one(list(range(1, 201)), "cust_no")
    assert key.semantic_type == "identifier" and key.key_candidate and not key.pii
    notes = [f"Customer {i} asked about the delivery of the order placed last week" for i in range(60)]
    assert profile_one(notes, "notes").semantic_type == "free_text"
    assert not profile_one(["Berlin", "Oslo", "Paris", "Rome", "Lyon", "Bonn", "Graz", "Linz", "Nice", "Kiev", "Riga", "Bern"] * 3, "city_nm").pii


def test_pii_profiles_hold_no_values() -> None:
    p = profile_one(["luis@embraer.com.br", "leonie@gmx.de", "a.b@c.org"] * 10, "email_adr")
    assert p.pii and p.top_values == [] and p.min is None and p.max is None
    birth = profile_one([18000 + i for i in range(100)], "BirthDt", ColumnHint("BirthDt", "int32", "epoch_days", "int"))
    assert birth.pii and birth.date.min is None and birth.numeric.min is None and birth.numeric.p95 is None


def test_llm_view_never_contains_pii_values() -> None:
    emails = ["luis@embraer.com.br", "leonie@gmx.de", "a.b@c.org"] * 10
    view = llm_view.llm_column_view(profile_one(emails, "email_adr"))
    assert view["samples"] == [] and view["pii"] is True and view["pattern_class"] == "a+@a+.a+" or view["pattern_class"]
    assert not any(email in str(view) for email in emails)
    names = profile_one(["Holy", "Philips"] * 20, "f_nm")
    assert llm_view.llm_column_view(names)["samples"] == []
    plain = llm_view.llm_column_view(profile_one(["Rock", "Jazz", "Metal"] * 20, "cat_nm"))
    assert plain["samples"] == ["Rock", "Jazz", "Metal"] and plain["name"] == "category name" and len(plain["samples"]) <= 5
    masked = llm_view.llm_column_view(profile_one(["ACCT-12345678901", "ACCT-98765432101"] * 20, "acct"))
    assert all("12345678901" not in s and "98765432101" not in s for s in masked["samples"])


# ------------------------------------------------------------------ relations


def _numbers(n: int, start: int = 1) -> list[int]:
    return list(range(start, start + n))


def test_foreign_keys_are_found_by_containment_and_decoys_are_ignored() -> None:
    customers = table("CUST", {"cust_no": _numbers(60), "nm": [f"n{i}" for i in range(60)]})
    products = table("PROD", {"prod_no": _numbers(500)})
    invoices = table("INV", {"inv_no": _numbers(300), "cust_no": [(i % 60) + 1 for i in range(300)], "stat": [1, 2, 3] * 100,
                             "qty": [1, 2] * 150})  # fmt: skip
    lines = table("LINE", {"line_no": _numbers(900), "inv_no": [(i % 300) + 1 for i in range(900)], "prod_no": [(i * 7 % 400) + 1 for i in range(900)]})
    result, counters = engine.profile_tables([customers, products, invoices, lines], "sqlite")
    found = {(fk.child, fk.parent) for fk in result.foreign_keys}
    assert found == {("INV.cust_no", "CUST.cust_no"), ("LINE.inv_no", "INV.inv_no"), ("LINE.prod_no", "PROD.prod_no")}
    by = {(t.name, c.name): c for t in result.tables for c in t.columns}
    assert by[("LINE", "prod_no")].semantic_type == "reference" and by[("LINE", "prod_no")].references == "PROD.prod_no"
    assert by[("INV", "qty")].references is None and by[("INV", "stat")].references is None  # small code columns are not references


def test_orphans_do_not_stop_a_foreign_key_from_being_found() -> None:
    parent = table("P", {"id": _numbers(100)})
    child = table("C", {"id": _numbers(400), "p_id": [(i % 100) + 1 for i in range(396)] + [9001, 9002, 9003, 9004]})
    result, _ = engine.profile_tables([parent, child], "sqlite")
    [fk] = result.foreign_keys
    assert fk.child == "C.p_id" and 0.98 < fk.containment < 1.0


# ------------------------------------------------------------------ streaming and bounds


def test_chunk_size_does_not_change_the_profile() -> None:
    data = {"id": _numbers(500), "name": [f"Name{i % 37}" for i in range(500)], "d": ["01/02/2010", "20110304"] * 250}
    small, _ = engine.profile_tables([table("T", data, chunk_rows=1000)], "sqlite", chunk_size=7)
    large, _ = engine.profile_tables([table("T", data, chunk_rows=1000)], "sqlite", chunk_size=1000)
    assert small.model_dump(exclude={"chunk_size"}) == large.model_dump(exclude={"chunk_size"})


def test_profiling_consumes_the_source_in_bounded_chunks() -> None:
    sizes: list[int] = []

    def chunks(size: int) -> Iterator[pd.DataFrame]:
        for start in range(0, 1000, size):
            frame = pd.DataFrame({"v": list(range(start, min(start + size, 1000)))}).astype(object)
            sizes.append(len(frame))
            yield frame

    source = TableSource("T", [ColumnHint("v")], 1000, chunks)
    result, _ = engine.profile_tables([source], "sqlite", chunk_size=128)
    assert max(sizes) <= 128 and len(sizes) == 8 and result.rows_total == 1000


def test_distinct_counting_is_bounded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(column_profile, "VALUE_CAP", 100)
    p = profile_one([f"v{i}" for i in range(1000)])
    assert p.distinct_approximate and p.distinct_count == 100


# ------------------------------------------------------------------ readers


def test_sqlite_reader_is_read_only_and_does_not_run_triggers_or_views(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE T (a INTEGER, b TEXT); INSERT INTO T VALUES (1,'x'),(2,'y'),(3,NULL);"
        "CREATE TABLE LOG (m TEXT); CREATE VIEW V AS SELECT * FROM T;"
        "CREATE TRIGGER TR AFTER INSERT ON T BEGIN INSERT INTO LOG VALUES ('fired'); END;"
    )
    conn.commit()
    conn.close()
    reader = SqliteReader(path)
    tables = {t.name: t for t in reader.tables()}
    assert set(tables) == {"T", "LOG"}  # the view is not a table to profile
    frame = next(tables["T"].chunks(10))
    assert list(frame.index) == [1, 2, 3] and frame["b"].isna().sum() == 1
    with pytest.raises(sqlite3.OperationalError):
        reader._connect().execute("INSERT INTO T VALUES (9, 'z')")  # query_only
    assert sqlite3.connect(path).execute("SELECT count(*) FROM LOG").fetchone()[0] == 0


def test_csv_reader_handles_delimiters_and_encodings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "export.csv"
    path.write_bytes("id;name;city\n1;Zoë;Köln\n2;Lu\xedis;São Paulo\n".encode("cp1252", errors="replace"))
    [source] = CsvReader(path, "export").tables()
    assert [c.name for c in source.columns] == ["id", "name", "city"] and "delimiter ';'" in source.notes[0]
    frame = next(source.chunks(10))
    assert list(frame["city"]) == ["Köln", "São Paulo"] and list(frame.index) == [0, 1]
    result, _ = engine.profile_tables([source], "csv", chunk_size=1)
    assert [c.inferred_type for c in result.tables[0].columns] == ["integer", "text", "text"]


def test_relations_helpers() -> None:
    assert relations.integer_values(__import__("collections").Counter({1.0: 2, 2.0: 1, 0.0: 5}), [0]) == {1: 2, 2: 1}
    assert relations.integer_values(__import__("collections").Counter({"a": 1}), []) is None
