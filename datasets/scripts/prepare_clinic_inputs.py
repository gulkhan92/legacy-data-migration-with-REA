#!/usr/bin/env python3
"""Prepare the clinic scenario from the Synthea CSV output.

    python prepare_clinic_inputs.py prepare    # select rows, inject defects, write the import CSV files
    python prepare_clinic_inputs.py finalize   # after ClinicDesk ran: write ground_truth.json and expected/

Subset (keeps the committed fixtures small and the demo fast): the first PATIENT_LIMIT patients in the
Synthea file and every encounter, condition and medication that belongs to them, plus at most
OBSERVATIONS_PER_PATIENT observations per patient (the first ones in file order).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cdb_codec  # noqa: E402
import clinic_mapping  # noqa: E402
import inject_defects  # noqa: E402
from common import (  # noqa: E402
    CLINIC_CLEAN_DIR,
    CLINIC_IMPORT_DIR,
    EXPECTED,
    FIXTURES,
    RAW,
    SYNTHEA_CSV_DIR,
    read_csv,
    read_json,
    sha256_file,
    write_csv,
    write_json,
)

PATIENT_LIMIT = 300
OBSERVATIONS_PER_PATIENT = 20
SYNTHEA_VERSION = "v4.0.0"
# Synthea stamps an ongoing encounter with the wall clock time of the run, which makes the output differ
# between runs. A STOP value after the simulation reference date (see generate_synthea.sh) is therefore
# cleared, meaning "still open".
REFERENCE_DATE_ISO = "2026-01-01"
OUT_DIR = FIXTURES / "clinic"

COLUMNS = {
    "patients": ["Id", "BIRTHDATE", "DEATHDATE", "SSN", "FIRST", "LAST", "MARITAL", "RACE", "ETHNICITY", "GENDER",
                 "ADDRESS", "CITY", "STATE", "ZIP", "HEALTHCARE_EXPENSES", "INCOME"],
    "encounters": ["Id", "START", "STOP", "PATIENT", "ENCOUNTERCLASS", "CODE", "DESCRIPTION", "TOTAL_CLAIM_COST"],
    "conditions": ["START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION"],
    "medications": ["START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "TOTALCOST"],
    "observations": ["DATE", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "VALUE", "UNITS"],
}  # fmt: skip


def select_rows() -> dict[str, list[dict[str, str]]]:
    if not (SYNTHEA_CSV_DIR / "patients.csv").exists():
        raise SystemExit(f"Missing Synthea output in {SYNTHEA_CSV_DIR}. Run generate_synthea.sh first.")
    _, patients = read_csv(SYNTHEA_CSV_DIR / "patients.csv")
    patients = patients[:PATIENT_LIMIT]
    keep = {p["Id"] for p in patients}
    selected: dict[str, list[dict[str, str]]] = {"patients": patients}
    for entity in ("encounters", "conditions", "medications"):
        _, rows = read_csv(SYNTHEA_CSV_DIR / f"{entity}.csv")
        selected[entity] = [r for r in rows if r["PATIENT"] in keep]
    _, observations = read_csv(SYNTHEA_CSV_DIR / "observations.csv")
    per_patient: dict[str, int] = {}
    kept: list[dict[str, str]] = []
    for row in observations:
        pid = row["PATIENT"]
        if pid in keep and per_patient.get(pid, 0) < OBSERVATIONS_PER_PATIENT:
            per_patient[pid] = per_patient.get(pid, 0) + 1
            kept.append(row)
    selected["observations"] = kept
    for entity in ("encounters", "conditions", "medications"):
        for row in selected[entity]:
            if row["STOP"] > REFERENCE_DATE_ISO:
                row["STOP"] = ""
    # Keep only references that resolve, so the clean data has no orphans of its own.
    encounter_ids = {r["Id"] for r in selected["encounters"]}
    for entity in ("conditions", "medications", "observations"):
        for row in selected[entity]:
            if row["ENCOUNTER"] and row["ENCOUNTER"] not in encounter_ids:
                row["ENCOUNTER"] = ""
    return {entity: [{c: row[c] for c in COLUMNS[entity]} for row in rows] for entity, rows in selected.items()}


def prepare() -> None:
    clean = select_rows()
    dirty, defects = inject_defects.inject_clinic(clean)
    for entity, columns in COLUMNS.items():
        write_csv(CLINIC_CLEAN_DIR / f"{entity}.csv", columns, [[r[c] for c in columns] for r in clean[entity]])
        write_csv(CLINIC_IMPORT_DIR / f"{entity}.csv", columns, [[r[c] for c in columns] for r in dirty[entity]])
    write_json(RAW / "clinic_defects.json", defects)
    print({e: len(r) for e, r in dirty.items()}, "defects:", len(defects))


def finalize() -> None:
    layout = read_json(OUT_DIR / "layout.json")
    # The legacy application knows nothing about the target schema. The true mapping is merged in here.
    for table in layout["tables"]:
        for field in table["fields"]:
            target, transform = clinic_mapping.MAPPING.get(f"{table['entity']}.{field['name']}", (None, None))
            if target:
                field["target"] = target
            if transform:
                field["transform"] = transform
    defects = read_json(RAW / "clinic_defects.json")
    clean = {e: read_csv(CLINIC_CLEAN_DIR / f"{e}.csv")[1] for e in COLUMNS}

    patients, encounters = cdb_codec.reference_maps(clean)
    for entity, rows in clean.items():
        out = []
        for i, row in enumerate(rows):
            raw = cdb_codec.encode_row(entity, i + 1, row, patients, encounters)
            out.append(cdb_codec.to_target(entity, raw))
        write_csv(EXPECTED / "clinic" / f"{entity}.csv", cdb_codec.TARGET_COLUMNS[entity], out)

    cdb = OUT_DIR / "clinic.cdb"
    truth = {
        "scenario": "clinic",
        "seed": 42,
        "note": "The platform never reads this file during a run. Only the evaluation harness does.",
        "source": {
            "name": "Synthea",
            "version": SYNTHEA_VERSION,
            "command": "run_synthea -p 500 -s 42 -cs 42 -r 20260101 Massachusetts --exporter.csv.export=true "
                       "--exporter.years_of_history=3",
            "subset": {"patients": PATIENT_LIMIT, "observations_per_patient": OBSERVATIONS_PER_PATIENT},
        },
        "layout": layout,
        "row_counts_clean": {e: len(rows) for e, rows in clean.items()},
        "row_counts": {t["entity"]: t["record_count"] for t in layout["tables"]},
        "defects": defects,
        "checksums": {"clinic.cdb": sha256_file(cdb), "ClinicDesk.dll": sha256_file(OUT_DIR / "ClinicDesk.dll")},
    }
    write_json(OUT_DIR / "ground_truth.json", truth)
    print("clinic ground truth written:", truth["row_counts"])


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        prepare()
    elif command == "finalize":
        finalize()
    else:
        raise SystemExit(__doc__)
