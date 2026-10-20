#!/usr/bin/env python3
"""Inject controlled data quality problems, with a fixed seed, and report them.

Defect types and the share of rows affected in each table that supports them:

    duplicate       2%   an exact copy of an existing row is appended
    null_required   3%   a required field is emptied
    invalid_date    3%   a date or timestamp is replaced with a value that cannot be a date
    format_noise    3%   trailing spaces or mixed casing (clinic: casing only, because fixed width
                         storage pads text with spaces anyway)
    out_of_range    2%   a numeric value outside the plausible range of its column
    orphan_fk       1%   a foreign key that points at no existing row

Every affected row gets at most one defect, so scoring is unambiguous. Each entry records what the
platform should do with the row (`expected_outcome`): quarantine, deduplicate or normalize.

Used by build_legacy_commerce.py and prepare_clinic_inputs.py. Not run directly.
"""

import copy
import sqlite3
from pathlib import Path
from typing import Any, Callable

from common import rng

RATES = {
    "duplicate": 0.02,
    "null_required": 0.03,
    "invalid_date": 0.03,
    "format_noise": 0.03,
    "out_of_range": 0.02,
    "orphan_fk": 0.01,
}
ORDER = ["duplicate", "null_required", "invalid_date", "format_noise", "out_of_range", "orphan_fk"]
OUTCOME = {
    "duplicate": ("deduplicate", None),
    "null_required": ("quarantine", "required_null"),
    "invalid_date": ("quarantine", "invalid_date"),
    "out_of_range": ("quarantine", "out_of_range"),
    "orphan_fk": ("quarantine", "orphan_fk"),
    "trailing_space": ("normalize", None),
    "mixed_case": ("normalize", None),
}


def count_for(rate: float, n: int) -> int:
    return int(rate * n + 0.5)


class Pool:
    """Shuffled row identifiers handed out once each, so defects never overlap on a row."""

    def __init__(self, ids: list[Any], random_source) -> None:  # type: ignore[no-untyped-def]
        self.ids = list(ids)
        random_source.shuffle(self.ids)

    def take(self, n: int, eligible: Callable[[Any], bool] = lambda _: True) -> list[Any]:
        taken: list[Any] = []
        rest: list[Any] = []
        for item in self.ids:
            if len(taken) < n and eligible(item):
                taken.append(item)
            else:
                rest.append(item)
        self.ids = rest
        return taken


def noisy(value: str, kind: str, r) -> str:  # type: ignore[no-untyped-def]
    if kind == "trailing_space":
        return value + " " * r.choice([1, 2, 3])
    candidates = [value.upper(), value.lower(), value.swapcase()]
    candidates = [c for c in candidates if c != value]
    return r.choice(candidates)


def _entry(counter: list[int], scenario: str, kind: str, table: str, **fields: Any) -> dict[str, Any]:
    counter[0] += 1
    outcome, reason = OUTCOME[kind]
    entry: dict[str, Any] = {
        "id": f"{scenario}-{counter[0]:04d}",
        "type": kind,
        "table": table,
        "expected_outcome": outcome,
        "reason_code": reason,
    }
    entry.update(fields)
    return entry


# ---------------------------------------------------------------------------------------------
# Commerce (SQLite, rows identified by rowid)
# ---------------------------------------------------------------------------------------------

INVALID_DATES = {
    "MM/DD/YYYY": ["13/45/2011", "00/00/0000", "02/30/2010"],
    "YYYYMMDD": ["20111345", "00000000", "20100230"],
    "DD-Mon-YY": ["31-Feb-11", "45-Jan-10", "00-Xyz-12"],
    "other": ["N/A", "unknown", "??"],
}

COMMERCE_RULES: dict[str, dict[str, Any]] = {
    "CUST_MST": {"required": ["l_nm", "email_adr"], "noise": ["f_nm", "l_nm", "city_nm", "ctry_nm"]},
    "PROD_MST": {
        "required": ["prod_nm"],
        "noise": ["prod_nm", "cat_nm"],
        "range": {"unit_prc": ["-1.99", "5000000.00"]},
    },
    "INV_HDR": {
        "required": ["cust_no", "inv_dt"],
        "date": "inv_dt",
        "noise": ["bill_city"],
        "range": {"inv_tot": ["-13.86", "99999999.00"]},
        "orphan": ["cust_no"],
    },
    "INV_DTL": {
        "required": ["inv_no", "prod_no", "qty"],
        "range": {"qty": [-3, 9999], "unit_prc": ["-0.99", "250000.00"]},
        "orphan": ["inv_no", "prod_no"],
    },
}


def inject_commerce(db_path: Path, columns: dict[str, dict]) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    defects: list[dict[str, Any]] = []
    counter = [0]
    try:
        for table, rules in COMMERCE_RULES.items():
            names = [c[0] for c in columns[table]["columns"]]
            rows = {r["rowid"]: dict(r) for r in conn.execute(f"SELECT rowid, * FROM {table} ORDER BY rowid")}
            n = len(rows)
            r = rng(f"commerce:defects:{table}")
            pool = Pool(list(rows), r)
            next_rowid = max(rows) + 1

            def update(rowid: int, column: str, value: Any) -> None:
                conn.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (value, rowid))

            for kind in ORDER:
                want = count_for(RATES[kind], n)
                if kind == "duplicate":
                    for rowid in sorted(pool.take(want)):
                        values = [rows[rowid][c] for c in names]
                        conn.execute(
                            f"INSERT INTO {table} (rowid, {','.join(names)}) VALUES (?, {','.join('?' * len(names))})",
                            [next_rowid, *values],
                        )
                        defects.append(
                            _entry(counter, "commerce", "duplicate", table, rowid=next_rowid,
                                   duplicate_of_rowid=rowid, column=None)
                        )  # fmt: skip
                        next_rowid += 1
                elif kind == "null_required":
                    for rowid in sorted(pool.take(want)):
                        column = r.choice(rules["required"])
                        defects.append(
                            _entry(counter, "commerce", kind, table, rowid=rowid, column=column,
                                   original=rows[rowid][column], injected=None)
                        )  # fmt: skip
                        update(rowid, column, None)
                elif kind == "invalid_date" and "date" in rules:
                    column = rules["date"]
                    for rowid in sorted(pool.take(want)):
                        original = rows[rowid][column]
                        family = next(
                            (f for f in ("MM/DD/YYYY", "YYYYMMDD", "DD-Mon-YY") if _matches_format(original, f)),
                            "other",
                        )
                        bad = r.choice(INVALID_DATES[family] + INVALID_DATES["other"])
                        defects.append(
                            _entry(counter, "commerce", kind, table, rowid=rowid, column=column,
                                   original=original, injected=bad)
                        )  # fmt: skip
                        update(rowid, column, bad)
                elif kind == "format_noise" and "noise" in rules:
                    def has_text(rowid: int) -> bool:
                        return any(isinstance(rows[rowid][c], str) and any(ch.isalpha() for ch in rows[rowid][c])
                                   for c in rules["noise"])

                    for rowid in sorted(pool.take(want, has_text)):
                        column = r.choice([c for c in rules["noise"]
                                           if isinstance(rows[rowid][c], str) and any(ch.isalpha() for ch in rows[rowid][c])])
                        noise_kind = r.choice(["trailing_space", "mixed_case"])
                        new = noisy(rows[rowid][column], noise_kind, r)
                        defects.append(
                            _entry(counter, "commerce", noise_kind, table, rowid=rowid, column=column,
                                   original=rows[rowid][column], injected=new, expected_value=rows[rowid][column])
                        )  # fmt: skip
                        update(rowid, column, new)
                elif kind == "out_of_range" and "range" in rules:
                    for rowid in sorted(pool.take(want)):
                        column = r.choice(list(rules["range"]))
                        bad = r.choice(rules["range"][column])
                        defects.append(
                            _entry(counter, "commerce", kind, table, rowid=rowid, column=column,
                                   original=rows[rowid][column], injected=bad)
                        )  # fmt: skip
                        update(rowid, column, bad)
                elif kind == "orphan_fk" and "orphan" in rules:
                    for position, rowid in enumerate(sorted(pool.take(want))):
                        column = r.choice(rules["orphan"])
                        bad = 900000 + position
                        defects.append(
                            _entry(counter, "commerce", kind, table, rowid=rowid, column=column,
                                   original=rows[rowid][column], injected=bad)
                        )  # fmt: skip
                        update(rowid, column, bad)
        conn.commit()
    finally:
        conn.close()
    return defects


def _matches_format(value: str, fmt: str) -> bool:
    if fmt == "MM/DD/YYYY":
        return len(value) == 10 and value[2] == "/" and value[5] == "/"
    if fmt == "YYYYMMDD":
        return len(value) == 8 and value.isdigit()
    return len(value) == 9 and value[2] == "-" and value[6] == "-"


# ---------------------------------------------------------------------------------------------
# Clinic (CSV rows, identified by 0-based data row index, which is also the record index)
# ---------------------------------------------------------------------------------------------

INVALID_CLINIC_DATES = ["2023-13-45", "0000-00-00", "2023-02-30", "N/A", "unknown"]
INVALID_CLINIC_STAMPS = ["2023-13-45T10:00:00Z", "0000-00-00T00:00:00Z", "2023-02-30T10:00:00Z", "N/A", "unknown"]

CLINIC_RULES: dict[str, dict[str, Any]] = {
    "patients": {
        "required": ["LAST", "BIRTHDATE"],
        "date": ("BIRTHDATE", INVALID_CLINIC_DATES),
        "noise": ["FIRST", "LAST", "CITY"],
        "range": {"HEALTHCARE_EXPENSES": ["-5000.00", "90000000.00"]},
    },
    "encounters": {
        "required": ["PATIENT", "START"],
        "date": ("START", INVALID_CLINIC_STAMPS),
        "noise": ["DESCRIPTION"],
        "range": {"TOTAL_CLAIM_COST": ["-250.00", "9000000.00"]},
        "orphan": {"PATIENT": "patients"},
    },
    "conditions": {
        "required": ["PATIENT", "CODE"],
        "date": ("START", INVALID_CLINIC_DATES),
        "noise": ["DESCRIPTION"],
        "orphan": {"PATIENT": "patients"},
    },
    "medications": {
        "required": ["PATIENT", "CODE"],
        "date": ("START", INVALID_CLINIC_STAMPS),
        "noise": ["DESCRIPTION"],
        "range": {"TOTALCOST": ["-250.00", "9000000.00"]},
        "orphan": {"PATIENT": "patients"},
    },
    "observations": {
        "required": ["PATIENT", "CODE"],
        "date": ("DATE", INVALID_CLINIC_STAMPS),
        "noise": ["DESCRIPTION"],
        "orphan": {"PATIENT": "patients"},
    },
}


def inject_clinic(clean: dict[str, list[dict[str, str]]]) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, Any]]]:
    """Return (rows with defects, defect list). The input rows are not modified."""
    data = copy.deepcopy(clean)
    defects: list[dict[str, Any]] = []
    counter = [0]
    for table, rules in CLINIC_RULES.items():
        rows = data[table]
        n = len(rows)
        r = rng(f"clinic:defects:{table}")
        pool = Pool(list(range(n)), r)
        appended: list[dict[str, str]] = []
        for kind in ORDER:
            want = count_for(RATES[kind], n)
            if kind == "duplicate":
                for index in sorted(pool.take(want)):
                    appended.append(dict(rows[index]))
                    defects.append(
                        _entry(counter, "clinic", kind, table, row_index=n + len(appended) - 1,
                               duplicate_of_row_index=index, column=None)
                    )  # fmt: skip
            elif kind == "null_required":
                for index in sorted(pool.take(want)):
                    column = r.choice(rules["required"])
                    defects.append(
                        _entry(counter, "clinic", kind, table, row_index=index, column=column,
                               original=rows[index][column], injected="")
                    )  # fmt: skip
                    rows[index][column] = ""
            elif kind == "invalid_date" and "date" in rules:
                column, bad_values = rules["date"]
                for index in sorted(pool.take(want, lambda i: bool(rows[i][column]))):
                    bad = r.choice(bad_values)
                    defects.append(
                        _entry(counter, "clinic", kind, table, row_index=index, column=column,
                               original=rows[index][column], injected=bad)
                    )  # fmt: skip
                    rows[index][column] = bad
            elif kind == "format_noise":
                def has_text(i: int) -> bool:
                    return any(any(ch.isalpha() for ch in rows[i][c]) for c in rules["noise"])

                for index in sorted(pool.take(want, has_text)):
                    column = r.choice([c for c in rules["noise"] if any(ch.isalpha() for ch in rows[index][c])])
                    new = noisy(rows[index][column], "mixed_case", r)
                    defects.append(
                        _entry(counter, "clinic", "mixed_case", table, row_index=index, column=column,
                               original=rows[index][column], injected=new, expected_value=rows[index][column])
                    )  # fmt: skip
                    rows[index][column] = new
            elif kind == "out_of_range" and "range" in rules:
                for index in sorted(pool.take(want)):
                    column = r.choice(list(rules["range"]))
                    bad = r.choice(rules["range"][column])
                    defects.append(
                        _entry(counter, "clinic", kind, table, row_index=index, column=column,
                               original=rows[index][column], injected=bad)
                    )  # fmt: skip
                    rows[index][column] = bad
            elif kind == "orphan_fk" and "orphan" in rules:
                for position, index in enumerate(sorted(pool.take(want))):
                    column = r.choice(list(rules["orphan"]))
                    bad = f"00000000-0000-4000-8000-{position:012d}"
                    defects.append(
                        _entry(counter, "clinic", kind, table, row_index=index, column=column,
                               original=rows[index][column], injected=bad)
                    )  # fmt: skip
                    rows[index][column] = bad
        rows.extend(appended)
    return data, defects
