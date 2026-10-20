#!/usr/bin/env python3
"""Build the legacy style commerce fixture from Chinook.

Chinook is copied into `commerce_legacy.sqlite` and made to look like an old in house system:
cryptic table and column names, no constraints, dates stored as text in three formats, money
stored as text (some with a currency symbol), and status codes as integers with no lookup table.
Controlled defects are then injected (see inject_defects.py). The true rename map, the true
field mapping and the defect list are written to `fixtures/commerce/ground_truth.json`, and the
clean reference output to `expected/commerce/`.

    python build_legacy_commerce.py [--no-defects]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import inject_defects  # noqa: E402
from common import (  # noqa: E402
    CHINOOK_FILE,
    CHINOOK_VERSION,
    EXPECTED,
    FIXTURES,
    rng,
    sha256_file,
    sqlite_logical_sha256,
    write_csv,
    write_json,
)

OUT_DIR = FIXTURES / "commerce"
OUT_DB = OUT_DIR / "commerce_legacy.sqlite"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DATE_FORMATS = ["MM/DD/YYYY", "YYYYMMDD", "DD-Mon-YY"]
STATUS_CODES = {"1": "completed", "2": "pending", "3": "cancelled"}
STATUS_WEIGHTS = [85, 10, 5]
DOLLAR_PREFIX_SHARE = 0.25

# (legacy column, original Chinook column or expression, SQLite type, role, target field, transform)
# role: key (legacy primary key, becomes source_key), fk (reference to another legacy table),
# field (mapped to a target field), unmapped (no counterpart in the target schema).
COLUMNS: dict[str, dict] = {
    "CUST_MST": {
        "original_table": "Customer",
        "target_table": "customers",
        "columns": [
            ("cust_no", "CustomerId", "INTEGER", "key", "source_key", None),
            ("f_nm", "FirstName", "TEXT", "field", "first_name", "trim"),
            ("l_nm", "LastName", "TEXT", "field", "last_name", "trim"),
            ("co_nm", "Company", "TEXT", "field", "company", "trim"),
            ("email_adr", "Email", "TEXT", "field", "email", "trim,case(lower)"),
            ("ph_no", "Phone", "TEXT", "field", "phone", "normalize_phone"),
            ("addr1", "Address", "TEXT", "field", "address_line1", "trim"),
            ("city_nm", "City", "TEXT", "field", "city", "trim"),
            ("st_cd", "State", "TEXT", "field", "state", "trim"),
            ("ctry_nm", "Country", "TEXT", "field", "country", "trim"),
            ("zip_cd", "PostalCode", "TEXT", "field", "postal_code", "trim"),
            ("fax_no", "Fax", "TEXT", "unmapped", None, None),
        ],
    },
    "PROD_MST": {
        "original_table": "Track",
        "target_table": "products",
        "columns": [
            ("prod_no", "TrackId", "INTEGER", "key", "source_key", None),
            ("prod_nm", "Name", "TEXT", "field", "name", "trim"),
            ("cat_nm", "Genre.Name", "TEXT", "field", "category", "trim"),
            ("unit_prc", "UnitPrice", "TEXT", "field", "unit_price", "cast_decimal(strip $)"),
            ("comp_nm", "Composer", "TEXT", "unmapped", None, None),
            ("len_ms", "Milliseconds", "INTEGER", "unmapped", None, None),
            ("sz_bytes", "Bytes", "INTEGER", "unmapped", None, None),
        ],
    },
    "INV_HDR": {
        "original_table": "Invoice",
        "target_table": "orders",
        "columns": [
            ("inv_no", "InvoiceId", "INTEGER", "key", "source_key", None),
            ("cust_no", "CustomerId", "INTEGER", "fk", "customer_id", "references customers.source_key"),
            ("inv_dt", "InvoiceDate", "TEXT", "field", "order_date", "parse_date(" + "|".join(DATE_FORMATS) + ")"),
            ("bill_addr1", "BillingAddress", "TEXT", "unmapped", None, None),
            ("bill_city", "BillingCity", "TEXT", "field", "billing_city", "trim"),
            ("bill_ctry", "BillingCountry", "TEXT", "field", "billing_country", "trim"),
            ("inv_tot", "Total", "TEXT", "field", "total_amount", "cast_decimal(strip $)"),
            ("stat_cd", "(generated)", "INTEGER", "field", "status", "map_codes(1=completed,2=pending,3=cancelled)"),
        ],
    },
    "INV_DTL": {
        "original_table": "InvoiceLine",
        "target_table": "order_items",
        "columns": [
            ("dtl_no", "InvoiceLineId", "INTEGER", "key", "source_key", None),
            ("inv_no", "InvoiceId", "INTEGER", "fk", "order_id", "references orders.source_key"),
            ("prod_no", "TrackId", "INTEGER", "fk", "product_id", "references products.source_key"),
            ("qty", "Quantity", "INTEGER", "field", "quantity", "cast_int"),
            ("unit_prc", "UnitPrice", "TEXT", "field", "unit_price", "cast_decimal(strip $)"),
        ],
    },
}

SYSTEM_TABLE = ("SYS_LOG", "CREATE TABLE SYS_LOG (tbl_nm TEXT, op_cd TEXT)")


def fmt_date(iso: str, fmt: str) -> str:
    year, month, day = int(iso[0:4]), int(iso[5:7]), int(iso[8:10])
    if fmt == "MM/DD/YYYY":
        return f"{month:02d}/{day:02d}/{year:04d}"
    if fmt == "YYYYMMDD":
        return f"{year:04d}{month:02d}{day:02d}"
    return f"{day:02d}-{MONTHS[month - 1]}-{year % 100:02d}"


def fmt_money(value: float | int | str, with_symbol: bool) -> str:
    text = f"{float(value):.2f}"
    return f"${text}" if with_symbol else text


def create_statement(table: str) -> str:
    cols = ", ".join(f"{name} {sql_type}" for name, _, sql_type, *_ in COLUMNS[table]["columns"])
    return f"CREATE TABLE {table} ({cols})"


def read_chinook(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    q = conn.execute
    return {
        "CUST_MST": q(
            "SELECT CustomerId, FirstName, LastName, Company, Email, Phone, Address, City, State, "
            "Country, PostalCode, Fax FROM Customer ORDER BY CustomerId"
        ).fetchall(),
        "PROD_MST": q(
            "SELECT t.TrackId, t.Name, g.Name, t.UnitPrice, t.Composer, t.Milliseconds, t.Bytes "
            "FROM Track t LEFT JOIN Genre g ON g.GenreId = t.GenreId ORDER BY t.TrackId"
        ).fetchall(),
        "INV_HDR": q(
            "SELECT InvoiceId, CustomerId, InvoiceDate, BillingAddress, BillingCity, BillingCountry, Total "
            "FROM Invoice ORDER BY InvoiceId"
        ).fetchall(),
        "INV_DTL": q(
            "SELECT InvoiceLineId, InvoiceId, TrackId, Quantity, UnitPrice FROM InvoiceLine ORDER BY InvoiceLineId"
        ).fetchall(),
    }


def to_legacy(chinook: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
    """Apply the legacy storage conventions. Returns rows in legacy column order."""
    r = rng("commerce:legacy-formatting")
    out: dict[str, list[tuple]] = {}
    out["CUST_MST"] = [tuple(row) for row in chinook["CUST_MST"]]
    out["PROD_MST"] = [
        (no, name, genre, fmt_money(price, r.random() < DOLLAR_PREFIX_SHARE), composer, ms, size)
        for no, name, genre, price, composer, ms, size in chinook["PROD_MST"]
    ]
    inv: list[tuple] = []
    for no, cust, date, addr, city, country, total in chinook["INV_HDR"]:
        fmt = DATE_FORMATS[r.randrange(len(DATE_FORMATS))]
        status = int(r.choices(list(STATUS_CODES), weights=STATUS_WEIGHTS)[0])
        inv.append(
            (no, cust, fmt_date(date[:10], fmt), addr, city, country,
             fmt_money(total, r.random() < DOLLAR_PREFIX_SHARE), status)
        )  # fmt: skip
    out["INV_HDR"] = inv
    out["INV_DTL"] = [
        (no, inv_no, prod_no, qty, fmt_money(price, r.random() < DOLLAR_PREFIX_SHARE))
        for no, inv_no, prod_no, qty, price in chinook["INV_DTL"]
    ]
    return out


def write_database(path: Path, legacy: dict[str, list[tuple]]) -> None:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        for table, rows in legacy.items():
            conn.execute(create_statement(table))
            marks = ",".join("?" * len(COLUMNS[table]["columns"]))
            conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
        conn.execute(SYSTEM_TABLE[1])
        conn.commit()
    finally:
        conn.close()


def finalize_database(path: Path) -> None:
    """Indexes, a view and a trigger, added last so injected duplicates do not fire the trigger."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE INDEX IX_INV_CUST ON INV_HDR (cust_no);
            CREATE INDEX IX_DTL_INV ON INV_DTL (inv_no);
            CREATE INDEX IX_CUST_NM ON CUST_MST (l_nm, f_nm);
            CREATE VIEW V_CUST_LIST AS SELECT cust_no, f_nm || ' ' || l_nm AS full_nm, email_adr FROM CUST_MST;
            CREATE TRIGGER TRG_INV_LOG AFTER INSERT ON INV_HDR
            BEGIN INSERT INTO SYS_LOG (tbl_nm, op_cd) VALUES ('INV_HDR', 'I'); END;
            PRAGMA user_version = 3;
            """
        )
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def expected_tables(chinook: dict[str, list[tuple]], legacy: dict[str, list[tuple]]) -> dict[str, tuple[list[str], list[list]]]:
    """Clean reference rows in the target schema. Foreign key columns hold the referenced source_key."""
    status_by_invoice = {row[0]: STATUS_CODES[str(row[7])] for row in legacy["INV_HDR"]}
    customers = [
        [c[0], c[1], c[2], c[3], (c[4] or "").lower() or None, c[5], c[6], c[7], c[8], c[9], c[10]]
        for c in chinook["CUST_MST"]
    ]
    products = [[p[0], p[1], p[2], f"{float(p[3]):.2f}"] for p in chinook["PROD_MST"]]
    orders = [
        [i[0], i[1], i[2][:10], status_by_invoice[i[0]], f"{float(i[6]):.2f}", i[4], i[5]]
        for i in chinook["INV_HDR"]
    ]
    items = [[d[0], d[1], d[2], d[3], f"{float(d[4]):.2f}"] for d in chinook["INV_DTL"]]
    return {
        "customers": (
            ["source_key", "first_name", "last_name", "company", "email", "phone", "address_line1",
             "city", "state", "postal_code", "country"],
            [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[10], r[9]] for r in customers],
        ),  # fmt: skip
        "products": (["source_key", "name", "category", "unit_price"], products),
        "orders": (
            ["source_key", "customer_id", "order_date", "status", "total_amount", "billing_city", "billing_country"],
            orders,
        ),
        "order_items": (["source_key", "order_id", "product_id", "quantity", "unit_price"], items),
    }


def ground_truth_base(chinook_sha: str) -> dict:
    tables = {}
    for legacy_table, spec in COLUMNS.items():
        tables[legacy_table] = {
            "original_table": spec["original_table"],
            "target_table": spec["target_table"],
            "columns": [
                {
                    "name": name,
                    "original": original,
                    "legacy_type": sql_type,
                    "role": role,
                    "target_field": target,
                    "transform": transform,
                }
                for name, original, sql_type, role, target, transform in spec["columns"]
            ],
        }
    return {
        "scenario": "commerce",
        "seed": 42,
        "note": "The platform never reads this file during a run. Only the evaluation harness does.",
        "source": {"name": "Chinook", "version": CHINOOK_VERSION, "file_sha256": chinook_sha},
        "legacy_tables": tables,
        "system_tables": {"SYS_LOG": "Trigger log, no counterpart in the target schema"},
        "formats": {
            "date_column": "INV_HDR.inv_dt",
            "date_formats": DATE_FORMATS,
            "money_columns": ["PROD_MST.unit_prc", "INV_HDR.inv_tot", "INV_DTL.unit_prc"],
            "money_currency_prefix": "$",
            "money_currency_prefix_share": DOLLAR_PREFIX_SHARE,
        },
        "status_codes": STATUS_CODES,
    }


def build(with_defects: bool = True) -> dict:
    if not CHINOOK_FILE.exists():
        raise SystemExit(f"Missing {CHINOOK_FILE}. Run datasets/scripts/fetch_chinook.sh first.")
    src = sqlite3.connect(f"file:{CHINOOK_FILE.as_posix()}?mode=ro", uri=True)
    try:
        chinook = read_chinook(src)
    finally:
        src.close()

    legacy = to_legacy(chinook)
    write_database(OUT_DB, legacy)
    truth = ground_truth_base(sha256_file(CHINOOK_FILE))
    truth["row_counts_clean"] = {t: len(rows) for t, rows in legacy.items()}

    for table, (header, rows) in expected_tables(chinook, legacy).items():
        write_csv(EXPECTED / "commerce" / f"{table}.csv", header, [["" if v is None else v for v in r] for r in rows])

    truth["defects"] = inject_defects.inject_commerce(OUT_DB, COLUMNS) if with_defects else []
    finalize_database(OUT_DB)

    conn = sqlite3.connect(OUT_DB)
    truth["row_counts"] = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in legacy}
    conn.close()
    truth["checksum_logical_sha256"] = sqlite_logical_sha256(OUT_DB)
    write_json(OUT_DIR / "ground_truth.json", truth)
    return truth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--no-defects", action="store_true", help="build the clean legacy fixture only")
    args = parser.parse_args()
    truth = build(with_defects=not args.no_defects)
    print(f"commerce_legacy.sqlite: {truth['row_counts']} defects={len(truth['defects'])}")


if __name__ == "__main__":
    main()
