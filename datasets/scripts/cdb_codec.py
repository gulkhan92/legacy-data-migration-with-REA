"""Reference reader and encoder for the clinic data file (.cdb).

This is an independent Python implementation of the format written by ClinicDesk. It is used by the
fixture build (to compute the clean expected output with exactly the same lossy conventions as the
legacy application) and by the tests (to prove that the binary file matches the source CSV values and
the layout in ground_truth.json). The migration platform does not use it.
"""

import struct
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

EPOCH = date(1900, 1, 1)
EPOCH_DT = datetime(1900, 1, 1, tzinfo=timezone.utc)
INVALID_DATE = -1
DANGLING = 9_999_999
INT32_MIN = -(2**31)
INT64_MIN = -(2**63)
ENCOUNTER_CLASSES = {
    "ambulatory": 1, "emergency": 2, "inpatient": 3, "wellness": 4, "urgentcare": 5,
    "outpatient": 6, "virtual": 7, "hospice": 8, "home": 9, "snf": 10,
}  # fmt: skip
ENCOUNTER_CLASS_NAMES = {v: k for k, v in ENCOUNTER_CLASSES.items()}
RACES = {"white": 0, "black": 1, "asian": 2}

STRUCT = {"int32": "i", "uint16": "H", "uint8": "B", "int64": "q", "uint32": "I"}


# ------------------------------------------------------------------ reading


def read_header(blob: bytes) -> dict[str, Any]:
    magic, version, code_page, table_count, header_size = struct.unpack_from("<4sHHHH", blob, 0)
    tables = []
    for i in range(table_count):
        table_id, record_size, count, offset, _ = struct.unpack_from("<HHIII", blob, 16 + i * 16)
        tables.append({"id": table_id, "record_size": record_size, "count": count, "offset": offset})
    return {
        "magic": magic.decode("ascii"), "version": version, "code_page": code_page,
        "table_count": table_count, "header_size": header_size, "tables": tables,
    }  # fmt: skip


def check_size(blob: bytes) -> bool:
    """header + sum(count * record_size) must equal the file size."""
    header = read_header(blob)
    return header["header_size"] + sum(t["count"] * t["record_size"] for t in header["tables"]) == len(blob)


def read_cdb(path: Path, layout: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Decode every record with the layout. Text is decoded and right trimmed, numbers are raw."""
    blob = path.read_bytes()
    header = read_header(blob)
    by_id = {t["id"]: t for t in header["tables"]}
    result: dict[str, list[dict[str, Any]]] = {}
    for table in layout["tables"]:
        entry = by_id[table["id"]]
        if entry["record_size"] != table["record_size"] or entry["count"] != table["record_count"]:
            raise ValueError(f"layout does not match the file directory for {table['name']}")
        rows = []
        for i in range(entry["count"]):
            base = entry["offset"] + i * entry["record_size"]
            row: dict[str, Any] = {}
            for field in table["fields"]:
                start = base + field["offset"]
                raw = blob[start : start + field["length"]]
                if field["type"] == "char":
                    row[field["name"]] = raw.decode("cp1252").rstrip(" ")
                else:
                    order = ">" if field.get("endianness") == "big" else "<"
                    row[field["name"]] = struct.unpack(order + STRUCT[field["type"]], raw)[0]
            rows.append(row)
        result[table["entity"]] = rows
    return result


# ------------------------------------------------------------------ legacy conversions


def days(value: str) -> int:
    if not value.strip():
        return 0
    try:
        return (datetime.strptime(value[:10], "%Y-%m-%d").date() - EPOCH).days if len(value) >= 10 else INVALID_DATE
    except ValueError:
        return INVALID_DATE


def minutes(value: str) -> int:
    if not value.strip():
        return 0
    try:
        stamp = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return INVALID_DATE
    return int((stamp - EPOCH_DT).total_seconds() // 60)


def cents(value: str) -> int:
    if not value.strip():
        return 0
    try:
        amount = Decimal(value.strip())
    except InvalidOperation:
        return INT64_MIN
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cents_int(value: str) -> int:
    c = cents(value)
    return INT32_MIN if c == INT64_MIN or c > 2**31 - 1 or c < INT32_MIN else c


def whole_number(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return 0


def fixed(value: str, width: int) -> str:
    """What a fixed width text field holds once decoded and right trimmed."""
    return value.encode("cp1252", errors="replace")[:width].decode("cp1252").rstrip(" ")


def patient_flags(row: dict[str, str]) -> int:
    flags = 0
    if row["GENDER"].upper() == "M":
        flags |= 1
    if row["DEATHDATE"].strip():
        flags |= 1 << 1
    if row["ETHNICITY"].lower() == "hispanic":
        flags |= 1 << 2
    if row["MARITAL"].upper() == "M":
        flags |= 1 << 3
    flags |= RACES.get(row["RACE"].lower(), 3) << 4
    return flags


def reference(numbers: dict[str, int], value: str) -> int:
    if not value.strip():
        return 0
    return numbers.get(value, DANGLING)


def reference_maps(clean: dict[str, list[dict[str, str]]]) -> tuple[dict[str, int], dict[str, int]]:
    patients: dict[str, int] = {}
    for i, row in enumerate(clean["patients"]):
        patients.setdefault(row["Id"], i + 1)
    encounters: dict[str, int] = {}
    for i, row in enumerate(clean["encounters"]):
        encounters.setdefault(row["Id"], i + 1)
    return patients, encounters


def encode_row(entity: str, number: int, r: dict[str, str], patients: dict[str, int], encounters: dict[str, int]) -> dict[str, Any]:
    """The raw field values the legacy application stores for one CSV row."""
    if entity == "patients":
        return {
            "PatNo": number, "LastNm": fixed(r["LAST"], 30), "FirstNm": fixed(r["FIRST"], 20),
            "BirthDt": days(r["BIRTHDATE"]), "DeathDt": days(r["DEATHDATE"]), "Flags": patient_flags(r),
            "Ssn": fixed(r["SSN"], 11), "Addr1": fixed(r["ADDRESS"], 40), "CityNm": fixed(r["CITY"], 24),
            "StateNm": fixed(r["STATE"], 16), "ZipCd": fixed(r["ZIP"], 10),
            "ExpCents": cents(r["HEALTHCARE_EXPENSES"]), "IncomeAmt": whole_number(r["INCOME"]),
        }  # fmt: skip
    if entity == "encounters":
        return {
            "EncNo": number, "PatNo": reference(patients, r["PATIENT"]), "StartMin": minutes(r["START"]),
            "EndMin": minutes(r["STOP"]), "ClassCd": ENCOUNTER_CLASSES.get(r["ENCOUNTERCLASS"].lower(), 0),
            "TypeCd": fixed(r["CODE"], 16), "DescTxt": fixed(r["DESCRIPTION"], 80),
            "CostCents": cents_int(r["TOTAL_CLAIM_COST"]),
        }  # fmt: skip
    if entity == "conditions":
        return {
            "CondNo": number, "PatNo": reference(patients, r["PATIENT"]), "EncNo": reference(encounters, r["ENCOUNTER"]),
            "DxCd": fixed(r["CODE"], 20), "DxDesc": fixed(r["DESCRIPTION"], 120),
            "OnsetDt": days(r["START"]), "ResolvDt": days(r["STOP"]),
        }  # fmt: skip
    if entity == "medications":
        return {
            "MedNo": number, "PatNo": reference(patients, r["PATIENT"]), "EncNo": reference(encounters, r["ENCOUNTER"]),
            "RxCd": fixed(r["CODE"], 10), "RxDesc": fixed(r["DESCRIPTION"], 100),
            "StartDt": days(r["START"]), "StopDt": days(r["STOP"]), "CostCents": cents_int(r["TOTALCOST"]),
        }  # fmt: skip
    if entity == "observations":
        return {
            "ObsNo": number, "PatNo": reference(patients, r["PATIENT"]), "EncNo": reference(encounters, r["ENCOUNTER"]),
            "ObsCd": fixed(r["CODE"], 10), "ObsDesc": fixed(r["DESCRIPTION"], 80), "ObsVal": fixed(r["VALUE"], 24),
            "ObsUnit": fixed(r["UNITS"], 16), "ObsMin": minutes(r["DATE"]),
        }  # fmt: skip
    raise KeyError(entity)


# ------------------------------------------------------------------ raw values to target values


def iso_date(n: int) -> str:
    return "" if n == 0 else (EPOCH + timedelta(days=n)).isoformat()


def iso_stamp(n: int) -> str:
    return "" if n == 0 else (EPOCH_DT + timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:00Z")


def ref(n: int) -> str:
    return "" if n == 0 else str(n)


TARGET_COLUMNS = {
    "patients": ["source_key", "first_name", "last_name", "birth_date", "death_date", "gender", "ssn",
                 "address", "city", "state", "postal_code", "is_deceased"],
    "encounters": ["source_key", "patient_id", "start_time", "end_time", "encounter_class", "description",
                   "total_cost_cents"],
    "conditions": ["source_key", "patient_id", "encounter_id", "code", "description", "start_date", "stop_date"],
    "medications": ["source_key", "patient_id", "encounter_id", "code", "description", "start_date", "stop_date",
                    "total_cost_cents"],
    "observations": ["source_key", "patient_id", "encounter_id", "code", "description", "value", "units",
                     "observed_at"],
}  # fmt: skip


def to_target(entity: str, raw: dict[str, Any]) -> list[str]:
    """Target field values (as text) for one legacy record, in TARGET_COLUMNS order."""
    if entity == "patients":
        flags = raw["Flags"]
        return [str(raw["PatNo"]), raw["FirstNm"], raw["LastNm"], iso_date(raw["BirthDt"]), iso_date(raw["DeathDt"]),
                "M" if flags & 1 else "F", raw["Ssn"], raw["Addr1"], raw["CityNm"], raw["StateNm"], raw["ZipCd"],
                "true" if flags & 2 else "false"]  # fmt: skip
    if entity == "encounters":
        return [str(raw["EncNo"]), ref(raw["PatNo"]), iso_stamp(raw["StartMin"]), iso_stamp(raw["EndMin"]),
                ENCOUNTER_CLASS_NAMES.get(raw["ClassCd"], ""), raw["DescTxt"], str(raw["CostCents"])]  # fmt: skip
    if entity == "conditions":
        return [str(raw["CondNo"]), ref(raw["PatNo"]), ref(raw["EncNo"]), raw["DxCd"], raw["DxDesc"],
                iso_date(raw["OnsetDt"]), iso_date(raw["ResolvDt"])]  # fmt: skip
    if entity == "medications":
        return [str(raw["MedNo"]), ref(raw["PatNo"]), ref(raw["EncNo"]), raw["RxCd"], raw["RxDesc"],
                iso_date(raw["StartDt"]), iso_date(raw["StopDt"]), str(raw["CostCents"])]  # fmt: skip
    if entity == "observations":
        return [str(raw["ObsNo"]), ref(raw["PatNo"]), ref(raw["EncNo"]), raw["ObsCd"], raw["ObsDesc"], raw["ObsVal"],
                raw["ObsUnit"], iso_stamp(raw["ObsMin"])]  # fmt: skip
    raise KeyError(entity)
