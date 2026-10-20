#!/usr/bin/env python3
"""Rebuild every dataset fixture. This is what `make datasets` runs.

    python build_all.py            # reuse downloads and the generated Synthea output when present
    python build_all.py --clean    # delete all generated files first (downloads stay, they are verified)

Steps: fetch Chinook, generate Synthea CSV, write target schemas, build the legacy commerce database,
prepare the clinic import files, build ClinicDesk and the clinic data file, write ground truth and
expected output, then record checksums in datasets/checksums.json.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_legacy_commerce  # noqa: E402
import make_targets  # noqa: E402
import prepare_clinic_inputs  # noqa: E402
from common import (  # noqa: E402
    CHINOOK_FILE,
    DATASETS,
    EXPECTED,
    FIXTURES,
    RAW,
    SYNTHEA_CSV_DIR,
    TARGETS,
    sha256_file,
    sqlite_logical_sha256,
    write_json,
)

CHECKSUMS = DATASETS / "checksums.json"
INPUTS = {
    "Chinook_Sqlite.sqlite": RAW / "Chinook_Sqlite.sqlite",
    "synthea-with-dependencies.jar": RAW / "synthea-with-dependencies.jar",
}


def bash(script: Path) -> None:
    subprocess.run(["bash", str(script)], check=True)


def clean() -> None:
    for path in (FIXTURES, EXPECTED, TARGETS, RAW / "synthea", RAW / "clinic_import", RAW / "clinic_import_clean"):
        shutil.rmtree(path, ignore_errors=True)
    (RAW / "clinic_defects.json").unlink(missing_ok=True)


def collect_checksums() -> dict:
    files = {}
    for base in (FIXTURES, EXPECTED, TARGETS):
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            files[path.relative_to(DATASETS).as_posix()] = sha256_file(path)
    return {
        "note": "SHA-256 of every generated file. The sqlite file is also listed by logical content, which "
                "does not depend on the SQLite library version. ClinicDesk.dll is reproducible with the "
                "pinned .NET SDK image (see README).",
        "inputs": {name: sha256_file(path) for name, path in INPUTS.items()},
        "files": files,
        "sqlite_logical": {
            "fixtures/commerce/commerce_legacy.sqlite": sqlite_logical_sha256(FIXTURES / "commerce" / "commerce_legacy.sqlite")
        },
    }


def render_readme(checksums: dict) -> None:
    """Write the checksum table into README.md between its markers."""
    lines = ["| File | SHA-256 |", "|---|---|"]
    for name, digest in checksums["inputs"].items():
        lines.append(f"| `raw/{name}` (downloaded input) | `{digest}` |")
    for rel, digest in checksums["files"].items():
        lines.append(f"| `{rel}` | `{digest}` |")
    for rel, digest in checksums["sqlite_logical"].items():
        lines.append(f"| `{rel}` (logical content, independent of the SQLite version) | `{digest}` |")
    readme = DATASETS / "README.md"
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- checksums:start -->", "<!-- checksums:end -->"
    head, rest = text.split(start, 1)
    tail = rest.split(end, 1)[1]
    body = start + "\n" + "\n".join(lines) + "\n" + end
    readme.write_text(head + body + tail, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--clean", action="store_true", help="delete generated files before building")
    args = parser.parse_args()

    if args.clean:
        clean()
    if not CHINOOK_FILE.exists():
        bash(HERE / "fetch_chinook.sh")
    if not (SYNTHEA_CSV_DIR / "patients.csv").exists():
        bash(HERE / "generate_synthea.sh")

    make_targets.main()
    build_legacy_commerce.build(with_defects=True)
    prepare_clinic_inputs.prepare()
    bash(HERE / "build_legacy_clinic" / "build.sh")
    prepare_clinic_inputs.finalize()

    checksums = collect_checksums()
    write_json(CHECKSUMS, checksums)
    render_readme(checksums)
    print(f"checksums written to {CHECKSUMS}")


if __name__ == "__main__":
    main()
