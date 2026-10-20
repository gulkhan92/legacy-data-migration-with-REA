"""Shared helpers for the dataset scripts. Standard library only."""

import csv
import hashlib
import json
import random
import sqlite3
from pathlib import Path
from typing import Any

SEED = 42
DATASETS = Path(__file__).resolve().parent.parent
RAW = DATASETS / "raw"
FIXTURES = DATASETS / "fixtures"
TARGETS = DATASETS / "targets"
EXPECTED = DATASETS / "expected"

CHINOOK_VERSION = "v1.4.5"
CHINOOK_FILE = RAW / "Chinook_Sqlite.sqlite"
SYNTHEA_CSV_DIR = RAW / "synthea" / "output" / "csv"
CLINIC_CLEAN_DIR = RAW / "clinic_import_clean"
CLINIC_IMPORT_DIR = RAW / "clinic_import"


def rng(name: str, seed: int = SEED) -> random.Random:
    """A deterministic generator that is independent per purpose (stable across Python versions)."""
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sqlite_logical_sha256(path: Path) -> str:
    """Checksum of the logical content (schema and rows with rowids, in a fixed order).

    Unlike the raw file hash this does not depend on the SQLite library version that wrote the
    file, so it is the value used to prove reproducibility.
    """
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        h = hashlib.sha256()
        objects = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        for obj in objects:
            h.update(repr(obj).encode())
        for obj in objects:
            if obj[0] != "table":
                continue
            h.update(obj[1].encode())
            for row in conn.execute(f'SELECT rowid, * FROM "{obj[1]}" ORDER BY rowid'):
                h.update(repr(row).encode())
        return h.hexdigest()
    finally:
        conn.close()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_bytes(text.encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        return list(reader.fieldnames or []), rows
