"""Phase 5 acceptance: profiling and quality on both sample systems.

Injected defects must be detected with a recall of at least 90 percent. The binary system is profiled with the layout
from the ground truth (always) and with the layout that REA based discovery recovered (when REA is installed).
"""

import json
import shutil
import tracemalloc
from pathlib import Path

import pytest

from app.discovery.models import FieldLayout, LayoutSpec, RecordLayout
from app.evaluation.quality_score import CORE_TYPES, aliases_from_layout, score_quality
from app.profiling import engine
from app.profiling.readers import BinaryReader, CsvReader, SqliteReader

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "datasets" / "fixtures"
COMMERCE_DB = FIXTURES / "commerce" / "commerce_legacy.sqlite"
CDB = FIXTURES / "clinic" / "clinic.cdb"
REA_INSTALLED = (ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")


def truth(name: str) -> dict:
    return json.loads((FIXTURES / name / "ground_truth.json").read_text(encoding="utf-8"))


def true_layout() -> LayoutSpec:
    tables = [
        RecordLayout(
            name=t["name"], entity_hint=t["entity"], table_id=t["id"], record_size=t["record_size"], record_count=t["record_count"],
            data_offset=t["data_offset"], confidence=0.95,
            fields=[FieldLayout(name=f["name"], offset=f["offset"], length=f["length"], type=f["type"], semantic=f.get("semantic"),
                                encoding="windows-1252" if f["type"] == "char" else None, endianness=f.get("endianness"), confidence=0.95)
                    for f in t["fields"]],
        )  # fmt: skip
        for t in truth("clinic")["layout"]["tables"]
    ]
    return LayoutSpec(method="cil", confidence=0.95, validated=True, tables=tables)


@pytest.fixture(scope="module")
def commerce():  # type: ignore[no-untyped-def]
    return engine.profile_and_check(SqliteReader(COMMERCE_DB).tables(), "sqlite", chunk_size=500)


@pytest.fixture(scope="module")
def clinic():  # type: ignore[no-untyped-def]
    return engine.profile_and_check(BinaryReader(CDB, true_layout()).tables(), "dotnet_binary", chunk_size=2000)


def column(result, table: str, name: str):  # type: ignore[no-untyped-def]
    return next(c for t in result.tables if t.name == table for c in t.columns if c.name == name)


def assert_recall(report, defects, names=None, aliases=None, check_columns=True) -> None:  # type: ignore[no-untyped-def]
    score = score_quality(report, defects, table_names=names, column_aliases=aliases)
    assert score.recall >= 0.9, {k: (v.injected, v.detected) for k, v in score.by_type.items()}
    for kind in CORE_TYPES:
        s = score.by_type[kind]
        assert s.recall >= 0.9, (kind, s.injected, s.detected, s.missed[:5])
        if check_columns:
            assert s.column_matched >= 0.9 * s.injected, (kind, "column", s.column_matched, s.injected)
        assert s.precision >= 0.8 if kind in ("duplicate", "invalid_date", "orphan_fk") else True


# ------------------------------------------------------------------ commerce


def test_commerce_defects_are_detected_with_recall_of_at_least_90_percent(commerce) -> None:  # type: ignore[no-untyped-def]
    _, report = commerce
    assert_recall(report, truth("commerce")["defects"])
    score = score_quality(report, truth("commerce")["defects"])
    assert score.recall >= 0.99  # this fixture is almost fully detectable


def test_commerce_types_and_semantics(commerce) -> None:  # type: ignore[no-untyped-def]
    profile, _ = commerce
    expected = {
        ("CUST_MST", "cust_no"): ("integer", "identifier"), ("CUST_MST", "f_nm"): ("text", "person_name"),
        ("CUST_MST", "email_adr"): ("text", "email"), ("CUST_MST", "ph_no"): ("text", "phone"),
        ("CUST_MST", "fax_no"): ("text", "phone"), ("CUST_MST", "zip_cd"): ("text", "postal_code"),
        ("CUST_MST", "addr1"): ("text", "address"), ("PROD_MST", "unit_prc"): ("decimal", "currency"),
        ("PROD_MST", "cat_nm"): ("text", "enumerated_code"), ("INV_HDR", "inv_dt"): ("date", "date"),
        ("INV_HDR", "inv_tot"): ("decimal", "currency"), ("INV_HDR", "stat_cd"): ("integer", "enumerated_code"),
        ("INV_HDR", "cust_no"): ("integer", "reference"), ("INV_DTL", "qty"): ("integer", "enumerated_code"),
        ("INV_DTL", "prod_no"): ("integer", "reference"), ("INV_DTL", "unit_prc"): ("decimal", "currency"),
    }  # fmt: skip
    for (table, name), (type_, semantic) in expected.items():
        c = column(profile, table, name)
        assert (c.inferred_type, c.semantic_type) == (type_, semantic), (table, name, c.inferred_type, c.semantic_type)


def test_commerce_formats_are_recognised(commerce) -> None:  # type: ignore[no-untyped-def]
    profile, _ = commerce
    dates = column(profile, "INV_HDR", "inv_dt")
    assert set(dates.date.dominant) == {"%m/%d/%Y", "%Y%m%d", "%d-%b-%y"} and not dates.date.ambiguous
    assert dates.date.min.startswith("2009-01") and dates.date.max.startswith("2013-12") and dates.null_rate > 0
    money = column(profile, "PROD_MST", "unit_prc")
    assert money.numeric.currency_symbols == ["$"] and set(money.numeric.formats) == {"plain", "currency"}
    share = money.numeric.formats["currency"] / money.numeric.parsed
    assert 0.2 < share < 0.3  # about a quarter of the values carry the symbol
    assert money.numeric.min < 0 and money.numeric.max > 1000  # the injected out of range prices are visible in the range


def test_commerce_pii_flags(commerce) -> None:  # type: ignore[no-untyped-def]
    profile, _ = commerce
    flagged = {(t.name, c.name) for t in profile.tables for c in t.columns if c.pii}
    assert {("CUST_MST", n) for n in ("f_nm", "l_nm", "email_adr", "ph_no", "fax_no", "addr1", "zip_cd")} <= flagged
    assert ("INV_HDR", "bill_addr1") in flagged
    assert not flagged & {("CUST_MST", "city_nm"), ("CUST_MST", "ctry_nm"), ("PROD_MST", "prod_nm"), ("INV_HDR", "inv_tot"), ("INV_DTL", "qty")}
    for t in profile.tables:
        for c in t.columns:
            if c.pii:
                assert c.top_values == [] and c.min is None and c.max is None


def test_commerce_foreign_keys_and_row_numbers(commerce) -> None:  # type: ignore[no-untyped-def]
    profile, _ = commerce
    assert {(fk.child, fk.parent) for fk in profile.foreign_keys} == {
        ("INV_HDR.cust_no", "CUST_MST.cust_no"), ("INV_DTL.inv_no", "INV_HDR.inv_no"), ("INV_DTL.prod_no", "PROD_MST.prod_no")
    }
    assert {t.name: t.sequence_columns for t in profile.tables} == {
        "CUST_MST": ["cust_no"], "PROD_MST": ["prod_no"], "INV_HDR": ["inv_no"], "INV_DTL": ["dtl_no"], "SYS_LOG": []
    }


def test_commerce_null_rates_reflect_the_injected_nulls(commerce) -> None:  # type: ignore[no-untyped-def]
    profile, _ = commerce
    assert 0.015 < column(profile, "CUST_MST", "email_adr").null_rate < 0.06
    assert 0.02 < column(profile, "PROD_MST", "prod_nm").null_rate < 0.05
    assert column(profile, "CUST_MST", "cust_no").null_rate == 0


def test_quality_score_before_is_below_a_clean_run(commerce, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import sys

    sys.path.insert(0, str(ROOT / "datasets" / "scripts"))
    import build_legacy_commerce as builder

    if not builder.CHINOOK_FILE.exists():
        pytest.skip("Chinook download not present")
    monkeypatch.setattr(builder, "OUT_DIR", tmp_path)
    monkeypatch.setattr(builder, "OUT_DB", tmp_path / "clean.sqlite")
    monkeypatch.setattr(builder, "EXPECTED", tmp_path / "expected")
    builder.build(with_defects=False)
    _, clean = engine.profile_and_check(SqliteReader(tmp_path / "clean.sqlite").tables(), "sqlite", chunk_size=500)
    _, dirty = commerce
    assert clean.score >= 99.0 and clean.issue_counts.get("duplicate", 0) == 0
    assert dirty.score < clean.score - 5 and 0 <= dirty.score <= 100
    assert not {"invalid_date", "out_of_range", "orphan_fk"} & set(clean.issue_counts)
    assert clean.issue_counts.get("required_null", 0) <= 2  # the source itself has the odd missing value


# ------------------------------------------------------------------ clinic (binary)


def test_clinic_defects_are_detected_with_recall_of_at_least_90_percent(clinic) -> None:  # type: ignore[no-untyped-def]
    _, report = clinic
    names = {t["entity"]: t["name"] for t in truth("clinic")["layout"]["tables"]}
    assert_recall(report, truth("clinic")["defects"], names, check_columns=False)  # the true layout carries no input column names


def test_clinic_types_dates_and_markers(clinic) -> None:  # type: ignore[no-untyped-def]
    profile, _ = clinic
    birth = column(profile, "PATIENT", "BirthDt")
    assert (birth.inferred_type, birth.semantic_type) == ("date", "date_of_birth") and birth.date.epoch == "1900-01-01"
    assert birth.null_markers == [0] and birth.invalid_markers == [-1] and birth.pii and birth.min is None
    start = column(profile, "ENCNTR", "StartMin")
    assert (start.inferred_type, start.date.unit, start.date.epoch) == ("timestamp", "minutes", "1900-01-01")
    assert column(profile, "ENCNTR", "StopMin" if False else "EndMin").null_rate > 0.0  # open encounters have no end
    assert column(profile, "COND", "ResolvDt").null_rate > 0.3 and column(profile, "PATIENT", "DeathDt").null_rate > 0.5
    zip_code = column(profile, "PATIENT", "ZipCd")
    assert (zip_code.inferred_type, zip_code.semantic_type) == ("text", "postal_code") and zip_code.pii
    assert column(profile, "ENCNTR", "ClassCd").semantic_type == "enumerated_code"
    assert column(profile, "ENCNTR", "CostCents").semantic_type == "currency"
    assert column(profile, "PATIENT", "Flags").semantic_type == "flags"
    assert column(profile, "COND", "DxCd").inferred_type == "text"  # codes made of digits are not numbers


def test_clinic_pii_and_foreign_keys(clinic) -> None:  # type: ignore[no-untyped-def]
    profile, _ = clinic
    flagged = {c.name for t in profile.tables if t.name == "PATIENT" for c in t.columns if c.pii}
    assert flagged == {"LastNm", "FirstNm", "BirthDt", "DeathDt", "Ssn", "Addr1", "ZipCd"}
    assert {(fk.child, fk.parent) for fk in profile.foreign_keys} == {
        ("ENCNTR.PatNo", "PATIENT.PatNo"), ("COND.PatNo", "PATIENT.PatNo"), ("COND.EncNo", "ENCNTR.EncNo"),
        ("MEDS.PatNo", "PATIENT.PatNo"), ("MEDS.EncNo", "ENCNTR.EncNo"), ("OBS.PatNo", "PATIENT.PatNo"), ("OBS.EncNo", "ENCNTR.EncNo"),
    }
    assert {t.name: t.sequence_columns for t in profile.tables} == {
        "PATIENT": ["PatNo"], "ENCNTR": ["EncNo"], "COND": ["CondNo"], "MEDS": ["MedNo"], "OBS": ["ObsNo"]
    }


def test_clinic_report_scores_and_issue_values(clinic) -> None:  # type: ignore[no-untyped-def]
    _, report = clinic
    assert 0 <= report.score < 100 and {t.table for t in report.tables} == {"PATIENT", "ENCNTR", "COND", "MEDS", "OBS"}
    pii_columns = {"LastNm", "FirstNm", "BirthDt", "DeathDt", "Ssn", "Addr1", "ZipCd"}
    assert all(i.value is None for i in report.issues if i.table == "PATIENT" and i.column in pii_columns)
    assert report.issue_counts["duplicate"] > 400 and not report.issues_truncated


@pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed")
def test_clinic_profile_with_the_layout_recovered_by_discovery() -> None:
    from app.discovery.dotnet_discovery import discover_layout
    from app.discovery.rea_adapter import get_rea_runner

    layout = discover_layout(get_rea_runner(), FIXTURES / "clinic" / "ClinicDesk.dll", CDB).layout
    assert layout.validated
    profile, report = engine.profile_and_check(BinaryReader(CDB, layout).tables(), "dotnet_binary", chunk_size=2000)
    names = {t["entity"]: t["name"] for t in truth("clinic")["layout"]["tables"]}
    # the recovered layout also says which input column each field is read from, so defects can be matched by column
    assert_recall(report, truth("clinic")["defects"], names, aliases_from_layout(layout))
    assert {n.name for n in profile.tables} == set(names.values())


# ------------------------------------------------------------------ streaming a large file


def test_a_large_csv_is_profiled_in_chunks_with_bounded_memory(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "big.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("id,name,amount,when\n")
        for i in range(120_000):
            handle.write(f"{i},Name{i % 500},{(i % 97) / 10:.2f},2020-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}\n")
    tracemalloc.start()
    profile, report = engine.profile_and_check(CsvReader(path, "big").tables(), "csv", chunk_size=10_000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert profile.rows_total == 120_000 and profile.tables[0].rows == 120_000
    assert column(profile, "big", "when").inferred_type == "date" and column(profile, "big", "amount").inferred_type == "decimal"
    assert report.score > 90
    assert peak < 250 * 1024 * 1024, f"peak {peak / 1e6:.0f} MB"
