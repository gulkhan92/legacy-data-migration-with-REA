"""Fail when the backend line coverage of a group of packages is under its target.

    cd backend && uv run pytest --cov=app --cov-report=json:var/coverage.json
    python scripts/coverage_gate.py backend/var/coverage.json

The plan asks for 80 percent on three groups: `pipeline` (the stages a job goes through: discovery, profiling, mapping, transform, the job
orchestration and the scoring used to evaluate them), `llm` (the router, the providers, the budget and the cache) and `core` (settings,
security, crypto, rate limits, middleware). The figures are printed for every package so that a drop is visible before it crosses the line.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

GROUPS = {
    "pipeline": ["discovery", "profiling", "mapping", "transform", "jobs", "evaluation"],
    "llm": ["llm"],
    "core": ["core"],
}
TARGET = 80.0


def package_totals(report: dict) -> dict[str, tuple[int, int]]:
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for name, data in report["files"].items():
        parts = name.replace("\\", "/").split("/")
        package = parts[1] if len(parts) > 2 else parts[-1]
        totals[package][0] += data["summary"]["covered_lines"]
        totals[package][1] += data["summary"]["num_statements"]
    return {k: (v[0], v[1]) for k, v in totals.items()}


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/var/coverage.json")
    totals = package_totals(json.loads(path.read_text(encoding="utf-8")))
    for package, (covered, statements) in sorted(totals.items()):
        if statements:
            print(f"  {package:12s} {100 * covered / statements:5.1f}%  ({covered} of {statements} lines)")
    failed = False
    for group, packages in GROUPS.items():
        covered = sum(totals.get(p, (0, 0))[0] for p in packages)
        statements = sum(totals.get(p, (0, 0))[1] for p in packages)
        percent = 100 * covered / statements if statements else 0.0
        status = "ok" if percent >= TARGET else "UNDER TARGET"
        print(f"{group:9s} {percent:5.1f}%  target {TARGET:.0f}%  {status}")
        failed |= percent < TARGET
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
