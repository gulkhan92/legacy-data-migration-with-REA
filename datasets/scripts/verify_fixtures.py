#!/usr/bin/env python3
"""Check that the committed fixtures still match datasets/checksums.json. Exit code 1 on any difference."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATASETS, read_json, sha256_file, sqlite_logical_sha256  # noqa: E402


def verify() -> list[str]:
    recorded = read_json(DATASETS / "checksums.json")
    problems = []
    for rel, expected in recorded["files"].items():
        path = DATASETS / rel
        if not path.exists():
            problems.append(f"missing {rel}")
        elif sha256_file(path) != expected:
            problems.append(f"changed {rel}")
    for rel, expected in recorded["sqlite_logical"].items():
        if sqlite_logical_sha256(DATASETS / rel) != expected:
            problems.append(f"logical content changed {rel}")
    return problems


if __name__ == "__main__":
    issues = verify()
    for issue in issues:
        print(issue, file=sys.stderr)
    print("fixtures verified" if not issues else f"{len(issues)} problem(s)")
    sys.exit(1 if issues else 0)
