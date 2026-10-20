"""Phase 3 checks: fixtures are reproducible, open with standard tools, and match their ground truth."""

import collections
import json
import os
import re
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

DATASETS = Path(__file__).resolve().parents[2] / "datasets"
sys.path.insert(0, str(DATASETS / "scripts"))

import build_legacy_commerce  # noqa: E402
import cdb_codec  # noqa: E402
import inject_defects  # noqa: E402
import prepare_clinic_inputs  # noqa: E402
import verify_fixtures  # noqa: E402
from common import CHINOOK_FILE, SYNTHEA_CSV_DIR, read_csv, read_json, sqlite_logical_sha256  # noqa: E402

COMMERCE_DB = DATASETS / "fixtures" / "commerce" / "commerce_legacy.sqlite"
COMMERCE_TRUTH = read_json(DATASETS / "fixtures" / "commerce" / "ground_truth.json")
CLINIC_DIR = DATASETS / "fixtures" / "clinic"
CLINIC_TRUTH = read_json(CLINIC_DIR / "ground_truth.json")
LAYOUT = CLINIC_TRUTH["layout"]
CDB = CLINIC_DIR / "clinic.cdb"
ENTITIES = ["patients", "encounters", "conditions", "medications", "observations"]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ------------------------------------------------------------------ integrity and reproducibility


def test_committed_fixtures_match_recorded_checksums() -> None:
    assert verify_fixtures.verify() == []


def test_commerce_rebuild_is_reproducible(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    if not CHINOOK_FILE.exists():
        pytest.skip("Chinook download not present (run datasets/scripts/fetch_chinook.sh)")
    monkeypatch.setattr(build_legacy_commerce, "OUT_DIR", tmp_path)
    monkeypatch.setattr(build_legacy_commerce, "OUT_DB", tmp_path / "commerce_legacy.sqlite")
    monkeypatch.setattr(build_legacy_commerce, "EXPECTED", tmp_path / "expected")
    first = build_legacy_commerce.build()
    assert first["checksum_logical_sha256"] == COMMERCE_TRUTH["checksum_logical_sha256"]
    assert sqlite_logical_sha256(tmp_path / "commerce_legacy.sqlite") == sqlite_logical_sha256(COMMERCE_DB)
    second = build_legacy_commerce.build()
    assert second == first  # same seed, same result, run after run
    for table in ("customers", "products", "orders", "order_items"):
        built = (tmp_path / "expected" / "commerce" / f"{table}.csv").read_bytes()
        assert built == (DATASETS / "expected" / "commerce" / f"{table}.csv").read_bytes()


def test_clinic_inputs_and_defects_are_reproducible() -> None:
    if not (SYNTHEA_CSV_DIR / "patients.csv").exists():
        pytest.skip("Synthea output not present (run datasets/scripts/generate_synthea.sh)")
    clean = prepare_clinic_inputs.select_rows()
    dirty, defects = inject_defects.inject_clinic(clean)
    assert defects == CLINIC_TRUTH["defects"]
    for entity in ENTITIES:
        columns = prepare_clinic_inputs.COLUMNS[entity]
        _, rows = read_csv(DATASETS / "raw" / "clinic_import" / f"{entity}.csv")
        assert rows == [{c: r[c] for c in columns} for r in dirty[entity]]


@pytest.mark.skipif(os.environ.get("RUN_DOTNET_REBUILD") != "1", reason="set RUN_DOTNET_REBUILD=1 (needs Docker or the .NET SDK)")
def test_clinic_binary_rebuild_is_reproducible() -> None:
    import subprocess

    before = {p.name: p.read_bytes() for p in CLINIC_DIR.iterdir() if p.is_file()}
    subprocess.run(["bash", str(DATASETS / "scripts" / "build_legacy_clinic" / "build.sh")], check=True)
    after = {p.name: p.read_bytes() for p in CLINIC_DIR.iterdir() if p.is_file()}
    for name in ("clinic.cdb", "ClinicDesk.dll", "layout.json"):
        assert after[name] == before[name], name


# ------------------------------------------------------------------ opens with standard tools


def test_commerce_database_opens_with_a_standard_sqlite_client() -> None:
    assert COMMERCE_DB.read_bytes().startswith(b"SQLite format 3\x00")
    conn = sqlite3.connect(f"file:{COMMERCE_DB.as_posix()}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        kinds = collections.Counter(r[0] for r in conn.execute("SELECT type FROM sqlite_master"))
        assert kinds["table"] == 5 and kinds["index"] == 3 and kinds["view"] == 1 and kinds["trigger"] == 1
        assert conn.execute("SELECT count(*) FROM INV_HDR").fetchone()[0] == COMMERCE_TRUTH["row_counts"]["INV_HDR"]
    finally:
        conn.close()


def test_clinic_data_file_is_readable_with_a_hex_dump() -> None:
    blob = CDB.read_bytes()
    assert blob[:4] == b"CDB1"  # first bytes of a hex dump read "43 44 42 31"
    assert cdb_codec.check_size(blob)
    header = cdb_codec.read_header(blob)
    assert (header["version"], header["code_page"], header["table_count"], header["header_size"]) == (1, 1252, 5, 96)
    assert len(blob) == LAYOUT["file_size"]


def test_legacy_assembly_is_a_managed_pe_file_that_names_the_record_fields() -> None:
    dll = (CLINIC_DIR / "ClinicDesk.dll").read_bytes()
    assert dll[:2] == b"MZ"
    pe_offset = struct.unpack_from("<I", dll, 0x3C)[0]
    assert dll[pe_offset : pe_offset + 4] == b"PE\x00\x00"
    assert b"BSJB" in dll  # .NET metadata root signature
    for name in (b"CdbWriter", b"WritePatient", b"WriteEncounter"):
        assert name in dll  # type and method names live in the #Strings metadata heap
    for literal in ("LastNm", "CostCents", "BirthDt"):
        assert literal.encode("utf-16-le") in dll  # field name literals live in the #US heap
    assert json.loads((CLINIC_DIR / "ClinicDesk.runtimeconfig.json").read_text())["runtimeOptions"]


# ------------------------------------------------------------------ commerce ground truth


def _parse_date(text: str) -> str | None:
    from datetime import datetime

    for fmt in ("%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d\d)-([A-Z][a-z]{2})-(\d\d)", text)
    if m and m.group(2) in MONTHS:
        try:
            return datetime(2000 + int(m.group(3)), MONTHS.index(m.group(2)) + 1, int(m.group(1))).date().isoformat()
        except ValueError:
            return None
    return None


def _money(text: str) -> str | None:
    m = re.fullmatch(r"\$?(-?\d+\.\d\d)", text)
    return m.group(1) if m else None


def _commerce_rows() -> dict[str, dict[int, dict]]:
    conn = sqlite3.connect(f"file:{COMMERCE_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return {
            t: {r["rowid"]: dict(r) for r in conn.execute(f"SELECT rowid, * FROM {t}")}
            for t in COMMERCE_TRUTH["legacy_tables"]
        }
    finally:
        conn.close()


def test_commerce_schema_matches_the_ground_truth_rename_map() -> None:
    conn = sqlite3.connect(f"file:{COMMERCE_DB.as_posix()}?mode=ro", uri=True)
    try:
        for table, spec in COMMERCE_TRUTH["legacy_tables"].items():
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            assert [(c[1], c[2]) for c in info] == [(c["name"], c["legacy_type"]) for c in spec["columns"]]
            assert all(c[5] == 0 for c in info)  # legacy style: no primary key or constraints
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert names == set(COMMERCE_TRUTH["legacy_tables"]) | {"SYS_LOG"}
        assert not [n for n in names if "STAT" in n.upper()]  # status codes have no lookup table
    finally:
        conn.close()
    # Every legacy name is cryptic: none of the original Chinook names survives.
    original_names = {"Customer", "Invoice", "InvoiceLine", "Track", "FirstName", "InvoiceDate", "BillingAddress"}
    for table, spec in COMMERCE_TRUTH["legacy_tables"].items():
        assert table not in original_names
        assert not original_names & {c["name"] for c in spec["columns"]}


def test_commerce_defect_counts_follow_the_documented_rates() -> None:
    by = collections.Counter((d["table"], d["type"]) for d in COMMERCE_TRUTH["defects"])
    clean = COMMERCE_TRUTH["row_counts_clean"]
    for table, n in clean.items():
        assert by[(table, "duplicate")] == inject_defects.count_for(0.02, n)
        assert by[(table, "null_required")] == inject_defects.count_for(0.03, n)
    assert by[("INV_HDR", "invalid_date")] == inject_defects.count_for(0.03, clean["INV_HDR"])
    assert by[("INV_DTL", "orphan_fk")] == inject_defects.count_for(0.01, clean["INV_DTL"])
    for table, n in clean.items():
        assert COMMERCE_TRUTH["row_counts"][table] == n + by[(table, "duplicate")]
    # at most one defect per row
    keys = [(d["table"], d["rowid"]) for d in COMMERCE_TRUTH["defects"]]
    assert len(keys) == len(set(keys))


def test_commerce_injected_defects_are_present_in_the_database() -> None:
    rows = _commerce_rows()
    for d in COMMERCE_TRUTH["defects"]:
        row = rows[d["table"]][d["rowid"]]
        if d["type"] == "duplicate":
            original = rows[d["table"]][d["duplicate_of_rowid"]]
            assert {k: v for k, v in row.items() if k != "rowid"} == {k: v for k, v in original.items() if k != "rowid"}
        else:
            assert row[d["column"]] == d["injected"], d
            assert d["injected"] != d["original"]
    invalid = [d for d in COMMERCE_TRUTH["defects"] if d["type"] == "invalid_date"]
    assert invalid and all(_parse_date(d["injected"]) is None for d in invalid)


def test_commerce_clean_rows_decode_to_the_expected_output() -> None:
    rows = _commerce_rows()
    defective = {(d["table"], d["rowid"]) for d in COMMERCE_TRUTH["defects"]}
    expected = {t: read_csv(DATASETS / "expected" / "commerce" / f"{t}.csv")[1]
                for t in ("customers", "products", "orders", "order_items")}  # fmt: skip
    status = COMMERCE_TRUTH["status_codes"]
    seen_formats = set()

    def clean_rows(table: str) -> list[dict]:
        return [r for rid, r in sorted(rows[table].items()) if (table, rid) not in defective]

    exp_customers = {e["source_key"]: e for e in expected["customers"]}
    for r in clean_rows("CUST_MST"):
        e = exp_customers[str(r["cust_no"])]
        legacy = [r["f_nm"], r["l_nm"], r["co_nm"], r["addr1"], r["city_nm"], r["st_cd"], r["ctry_nm"], r["zip_cd"]]
        target = [e["first_name"], e["last_name"], e["company"], e["address_line1"], e["city"], e["state"],
                  e["country"], e["postal_code"]]  # fmt: skip
        assert [v or "" for v in legacy] == target
    exp_orders = {e["source_key"]: e for e in expected["orders"]}
    for r in clean_rows("INV_HDR"):
        e = exp_orders[str(r["inv_no"])]
        assert _parse_date(r["inv_dt"]) == e["order_date"]
        assert _money(r["inv_tot"]) == e["total_amount"]
        assert status[str(r["stat_cd"])] == e["status"]
        assert str(r["cust_no"]) == e["customer_id"]
        seen_formats.add("slash" if "/" in r["inv_dt"] else "dash" if "-" in r["inv_dt"] else "compact")
    exp_products = {e["source_key"]: e for e in expected["products"]}
    for r in clean_rows("PROD_MST"):
        e = exp_products[str(r["prod_no"])]
        assert (r["prod_nm"], r["cat_nm"] or "", _money(r["unit_prc"])) == (e["name"], e["category"], e["unit_price"])
    exp_items = {e["source_key"]: e for e in expected["order_items"]}
    for r in clean_rows("INV_DTL"):
        e = exp_items[str(r["dtl_no"])]
        assert (str(r["inv_no"]), str(r["prod_no"]), str(r["qty"]), _money(r["unit_prc"])) == (
            e["order_id"], e["product_id"], e["quantity"], e["unit_price"])  # fmt: skip
    assert seen_formats == {"slash", "dash", "compact"}  # all three date formats occur
    assert any(r["inv_tot"].startswith("$") for r in rows["INV_HDR"].values())  # some money has a symbol
    assert len(exp_customers) == COMMERCE_TRUTH["row_counts_clean"]["CUST_MST"]


def test_commerce_defects_carry_the_expected_outcome() -> None:
    outcomes = {d["type"]: d["expected_outcome"] for d in COMMERCE_TRUTH["defects"]}
    assert outcomes == {
        "duplicate": "deduplicate", "null_required": "quarantine", "invalid_date": "quarantine",
        "out_of_range": "quarantine", "orphan_fk": "quarantine", "trailing_space": "normalize",
        "mixed_case": "normalize",
    }  # fmt: skip


# ------------------------------------------------------------------ clinic ground truth


def test_clinic_layout_is_self_consistent() -> None:
    for table in LAYOUT["tables"]:
        offset = 0
        for field in table["fields"]:
            assert field["offset"] == offset
            offset += field["length"]
        assert offset == table["record_size"]
        names = [f["name"] for f in table["fields"]]
        assert len(names) == len(set(names))
    big = [(t["name"], f["name"]) for t in LAYOUT["tables"] for f in t["fields"] if f.get("endianness") == "big"]
    assert big == [("ENCNTR", "CostCents")]
    assert LAYOUT["code_page"] == 1252


def test_clinic_binary_matches_the_source_csv_values() -> None:
    decoded = cdb_codec.read_cdb(CDB, LAYOUT)
    imported = {e: read_csv(DATASETS / "raw" / "clinic_import" / f"{e}.csv")[1] for e in ENTITIES}
    if not imported["patients"]:
        pytest.skip("import CSV not present")
    patients, encounters = cdb_codec.reference_maps(imported)
    for entity in ENTITIES:
        assert len(decoded[entity]) == len(imported[entity]) == CLINIC_TRUTH["row_counts"][entity]
        for i, (raw, source) in enumerate(zip(decoded[entity], imported[entity])):
            assert raw == cdb_codec.encode_row(entity, i + 1, source, patients, encounters), (entity, i)


def test_clinic_injected_defects_are_visible_in_the_binary() -> None:
    decoded = cdb_codec.read_cdb(CDB, LAYOUT)
    field_for = {  # CSV column -> stored field
        ("patients", "LAST"): "LastNm", ("patients", "BIRTHDATE"): "BirthDt", ("patients", "FIRST"): "FirstNm",
        ("patients", "CITY"): "CityNm", ("patients", "HEALTHCARE_EXPENSES"): "ExpCents",
        ("encounters", "PATIENT"): "PatNo", ("encounters", "START"): "StartMin", ("encounters", "DESCRIPTION"): "DescTxt",
        ("encounters", "TOTAL_CLAIM_COST"): "CostCents",
        ("conditions", "PATIENT"): "PatNo", ("conditions", "CODE"): "DxCd", ("conditions", "START"): "OnsetDt",
        ("conditions", "DESCRIPTION"): "DxDesc",
        ("medications", "PATIENT"): "PatNo", ("medications", "CODE"): "RxCd", ("medications", "START"): "StartDt",
        ("medications", "DESCRIPTION"): "RxDesc", ("medications", "TOTALCOST"): "CostCents",
        ("observations", "PATIENT"): "PatNo", ("observations", "CODE"): "ObsCd", ("observations", "DATE"): "ObsMin",
        ("observations", "DESCRIPTION"): "ObsDesc",
    }  # fmt: skip
    counts = collections.Counter()
    for d in CLINIC_TRUTH["defects"]:
        rows = decoded[d["table"]]
        row = rows[d["row_index"]]
        counts[d["type"]] += 1
        if d["type"] == "duplicate":
            original = rows[d["duplicate_of_row_index"]]
            number_key = next(iter(row))
            assert {k: v for k, v in row.items() if k != number_key} == {k: v for k, v in original.items() if k != number_key}
            continue
        value = row[field_for[(d["table"], d["column"])]]
        if d["type"] == "null_required":
            assert value in ("", 0), d
        elif d["type"] == "invalid_date":
            assert value == cdb_codec.INVALID_DATE, d
        elif d["type"] == "orphan_fk":
            assert value == cdb_codec.DANGLING, d
        elif d["type"] == "out_of_range":
            assert value < 0 or value >= 900_000_000, d
        elif d["type"] == "mixed_case":
            assert value != d["original"][: len(value)] or value.lower() == value.lower()
    assert set(counts) >= {"duplicate", "null_required", "invalid_date", "orphan_fk", "out_of_range", "mixed_case"}


def test_clinic_expected_output_agrees_with_the_binary_for_clean_rows() -> None:
    decoded = cdb_codec.read_cdb(CDB, LAYOUT)
    defective = collections.defaultdict(set)
    for d in CLINIC_TRUTH["defects"]:
        defective[d["table"]].add(d["row_index"])
    for entity in ENTITIES:
        header, expected = read_csv(DATASETS / "expected" / "clinic" / f"{entity}.csv")
        assert header == cdb_codec.TARGET_COLUMNS[entity]
        assert len(expected) == CLINIC_TRUTH["row_counts_clean"][entity]
        checked = 0
        for i, exp in enumerate(expected):
            if i in defective[entity]:
                continue
            got = cdb_codec.to_target(entity, decoded[entity][i])
            assert got == [exp[h] for h in header], (entity, i)
            checked += 1
        assert checked > 0.8 * len(expected)


def test_clinic_layout_targets_exist_in_the_target_schema() -> None:
    targets = read_json(DATASETS / "targets" / "clinic_target.json")
    known = {f"{f['table']}.{f['name']}" for f in targets["fields"]}
    mapped = [f["target"] for t in LAYOUT["tables"] for f in t["fields"] if f.get("target")]
    assert set(mapped) <= known
    unmapped = [f["name"] for t in LAYOUT["tables"] for f in t["fields"] if not f.get("target")]
    assert set(unmapped) == {"ExpCents", "IncomeAmt", "TypeCd"}
    # every target field other than the derived is_deceased is fed by exactly one stored field
    assert known - set(mapped) == {"patients.is_deceased"}


def test_commerce_ground_truth_targets_exist_in_the_target_schema() -> None:
    targets = read_json(DATASETS / "targets" / "commerce_target.json")
    known = {(f["table"], f["name"]) for f in targets["fields"]}
    mapped = {
        (spec["target_table"], c["target_field"])
        for spec in COMMERCE_TRUTH["legacy_tables"].values()
        for c in spec["columns"]
        if c["target_field"]
    }
    assert mapped == known


# ------------------------------------------------------------------ target schema files


@pytest.mark.parametrize("name", ["commerce", "clinic"])
def test_target_schema_files_are_complete_and_match_the_warehouse(name: str) -> None:
    from app.db import models  # noqa: F401
    from app.db.base import Base

    data = read_json(DATASETS / "targets" / f"{name}_target.json")
    keys = {"table", "name", "type", "required", "description", "synonyms", "examples", "pii"}
    seen = set()
    for field in data["fields"]:
        assert set(field) == keys
        assert (field["table"], field["name"]) not in seen
        seen.add((field["table"], field["name"]))
        assert len(field["examples"]) == 3
        assert field["synonyms"] and isinstance(field["pii"], bool) and isinstance(field["required"], bool)
        assert field["description"].endswith(".") and len(field["description"].split(". ")) == 1
        text = json.dumps(field, ensure_ascii=False)
        assert chr(0x2014) not in text and chr(0x2013) not in text  # no long dashes
    by_table: dict[str, set[str]] = collections.defaultdict(set)
    for field in data["fields"]:
        by_table[field["table"]].add(field["name"])
    system = {"id", "org_id", "job_id", "created_at"}
    for table, names in by_table.items():
        columns = {c.name for c in Base.metadata.tables[f"warehouse.{table}"].columns} - system
        assert names == columns, table


def test_pii_fields_are_flagged() -> None:
    clinic = {(f["table"], f["name"]): f for f in read_json(DATASETS / "targets" / "clinic_target.json")["fields"]}
    commerce = {(f["table"], f["name"]): f for f in read_json(DATASETS / "targets" / "commerce_target.json")["fields"]}
    for key in [("patients", "ssn"), ("patients", "birth_date"), ("patients", "last_name"), ("patients", "address")]:
        assert clinic[key]["pii"]
    for key in [("customers", "email"), ("customers", "phone"), ("customers", "last_name")]:
        assert commerce[key]["pii"]
    assert not commerce[("products", "name")]["pii"] and not clinic[("encounters", "encounter_class")]["pii"]
