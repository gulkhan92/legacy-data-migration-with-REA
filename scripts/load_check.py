"""Load sanity: a delimited file of 200,000 rows migrates without memory growing past a set limit.

    cd backend && uv run python ../scripts/load_check.py                 # 50,000 and 200,000 rows
    cd backend && uv run python ../scripts/load_check.py --rows 200000 --limit-mb 700

The file is generated here (deterministic, synthetic, with injected defects so that rows are quarantined), uploaded through the API, and
taken through profiling, mapping, the rules, the dry run and the load. The memory of this process (the API runs inside it) is sampled
every 100 ms. The check passes when, for the largest size,

* the growth of the resident set over the level before the run stays under `--limit-mb`, and
* the growth is not proportional to the rows: it is at most `--scale` times the growth of the smallest size, because the data is
  read, transformed and written in chunks, so a four times larger file must not need four times the memory.

The result is written to docs/load-check.md. Exit status 1 when a limit is exceeded.
"""

import argparse
import csv
import os
import random
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_run  # noqa: E402  (sets the environment the application needs before it is imported)

import psutil  # noqa: E402

ROOT = eval_run.ROOT
HEADER = ["cust_no", "f_nm", "l_nm", "co_nm", "email_adr", "ph_no", "addr1", "city_nm", "st_cd", "ctry_nm", "zip_cd"]
FIRST = ["Ana", "Ben", "Chen", "Dara", "Eli", "Farah", "Goran", "Hana", "Ivo", "Jun"]
LAST = ["Silva", "Okafor", "Novak", "Haddad", "Larsen", "Mori", "Reyes", "Kaur", "Weber", "Costa"]
CITIES = [("Lisbon", "PT"), ("Lagos", "NG"), ("Brno", "CZ"), ("Beirut", "LB"), ("Oslo", "NO"), ("Osaka", "JP")]


def write_csv(path: Path, rows: int) -> None:
    """Synthetic customers in the style of a legacy master file: cryptic headers, some missing emails and some repeated numbers."""
    rng = random.Random(42)
    with path.open("w", encoding="utf-8", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(HEADER)
        for i in range(1, rows + 1):
            first, last = rng.choice(FIRST), rng.choice(LAST)
            city, country = rng.choice(CITIES)
            email = "" if i % 53 == 0 else f"{first}.{last}{i}@example.org".lower()
            number = i - 1 if i % 997 == 0 else i  # a repeated customer number now and then
            out.writerow([number, first, last, "" if i % 3 else f"{last} Trading", email, f"+{rng.randint(10, 99)} {rng.randint(100000, 999999)}", f"{rng.randint(1, 999)} {last} Street", city, "", country, f"{rng.randint(10000, 99999)}"])


class Sampler:
    """Resident memory of this process, sampled in the background; remembers the peak and the peak of each phase."""

    def __init__(self) -> None:
        self.process = psutil.Process()
        self.peak = self.process.memory_info().rss
        self.phase_peak: dict[str, int] = {}
        self.phase = "idle"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            rss = self.process.memory_info().rss
            self.peak = max(self.peak, rss)
            self.phase_peak[self.phase] = max(self.phase_peak.get(self.phase, 0), rss)
            time.sleep(0.1)

    def __enter__(self) -> "Sampler":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


MB = 1024 * 1024


def migrate(ev: "eval_run.Evaluation", rows: int, workdir: Path) -> dict[str, object]:
    path = workdir / f"customers_{rows}.csv"
    write_csv(path, rows)
    size = path.stat().st_size
    import gc

    gc.collect()
    before = psutil.Process().memory_info().rss
    seconds: dict[str, float] = {}
    with Sampler() as sampler:

        def phase(name: str, fn):  # type: ignore[no-untyped-def]
            sampler.phase = name
            started = time.perf_counter()
            result = fn()
            seconds[name] = round(time.perf_counter() - started, 2)
            return result

        with path.open("rb") as handle:
            source = phase("upload", lambda: ev.call("POST", "/sources/upload", files={"file": (path.name, handle, "text/csv")}))
        job = ev.call("POST", "/jobs", json={"source_id": source["id"], "target_schema": "commerce"})
        for stage in ("profile", "map", "rules"):
            phase(stage, lambda s=stage: ev.call("POST", f"/jobs/{job['id']}/stages/{s}", json={}))
        ev.call("POST", f"/jobs/{job['id']}/mappings/approve-decided")
        dry = phase("dry_run", lambda: ev.call("POST", f"/jobs/{job['id']}/dry-run"))
        if not dry["ready"]:
            raise SystemExit(f"the dry run is blocked: {dry['blocking']}")
        phase("load", lambda: ev.call("POST", f"/jobs/{job['id']}/stages/load", json={}))
        report = ev.call("GET", f"/jobs/{job['id']}/reconciliation")
    return {
        "rows": rows, "file_mb": round(size / MB, 1), "seconds": seconds, "total_seconds": round(sum(seconds.values()), 1),
        "rows_per_second": int(rows / max(0.1, seconds["load"])), "before_mb": round(before / MB), "peak_mb": round(sampler.peak / MB),
        "growth_mb": round((sampler.peak - before) / MB), "phase_peak_mb": {k: round(v / MB) for k, v in sampler.phase_peak.items()},
        "reconciliation_passed": report["passed"], "loaded": report["loaded_rows"], "quarantined": report["quarantined_rows"], "source_rows": report["source_rows"],
    }  # fmt: skip


def render(results: list[dict[str, object]], limit_mb: int, scale: float, verdict: list[str]) -> str:
    L = ["# Load check", "", f"Generated by `scripts/load_check.py` on {datetime.now(UTC).strftime('%Y-%m-%d')}. It migrates a synthetic delimited file through the API (upload, profile, map, rules, dry run, load, reconciliation) and samples the resident memory of the process every 100 ms.", ""]
    L += ["| Rows | File (MB) | Load (s) | Rows per second (load) | Memory before (MB) | Peak (MB) | Growth (MB) | Quarantined | Reconciliation |", "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        L.append(f"| {r['rows']:,} | {r['file_mb']} | {r['seconds']['load']} | {r['rows_per_second']:,} | {r['before_mb']} | {r['peak_mb']} | {r['growth_mb']} | {r['quarantined']:,} | {'passed' if r['reconciliation_passed'] else 'failed'} |")  # type: ignore[index]
    L += ["", f"Limits: growth of the largest run at most {limit_mb} MB, and at most {scale} times the growth of the smallest run.", ""]
    L += [f"- {line}" for line in verdict]
    L += ["", "Peak memory by phase, largest run (MB): " + ", ".join(f"{k} {v}" for k, v in results[-1]["phase_peak_mb"].items()) + ".", "", "The memory is that of the process that runs the API, the pipeline and this script (the model of the mapping included). The memory of PostgreSQL, which runs separately, is not in these figures. The machine, the data and the process differ between runs; the point is the shape (growth does not follow the row count), not the absolute numbers.", ""]  # type: ignore[union-attr]
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--small", type=int, default=50_000)
    parser.add_argument("--limit-mb", type=int, default=700)
    parser.add_argument("--scale", type=float, default=2.5)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    from app.mapping.embedding import get_embedding_service

    get_embedding_service().encode(["warm up"])  # the model is loaded before the baseline, it is not a cost of the data
    ev = eval_run.Evaluation()
    results: list[dict[str, object]] = []
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            for rows in (args.small, args.rows):
                print(f"migrating {rows:,} rows ...", flush=True)
                results.append(migrate(ev, rows, Path(tmp)))
                print(results[-1], flush=True)
    finally:
        ev.close()

    small, big = results[0], results[-1]
    problems: list[str] = []
    verdict: list[str] = []
    if int(big["growth_mb"]) > args.limit_mb:  # type: ignore[call-overload]
        problems.append(f"growth {big['growth_mb']} MB is over the limit of {args.limit_mb} MB")
    else:
        verdict.append(f"Growth of {big['growth_mb']} MB for {big['rows']:,} rows is under the limit of {args.limit_mb} MB.")
    allowed = max(1.0, float(small["growth_mb"])) * args.scale  # type: ignore[arg-type]
    if float(big["growth_mb"]) > allowed:  # type: ignore[arg-type]
        problems.append(f"growth {big['growth_mb']} MB for {big['rows']:,} rows is more than {args.scale} times the {small['growth_mb']} MB of {small['rows']:,} rows")
    else:
        verdict.append(f"{big['rows']:,} rows grew the process by {big['growth_mb']} MB against {small['growth_mb']} MB for {small['rows']:,} rows, so memory does not follow the row count.")
    for r in results:
        if not r["reconciliation_passed"]:
            problems.append(f"reconciliation failed for {r['rows']:,} rows")
    if not problems:
        verdict.append("Reconciliation passed for every size: loaded plus quarantined rows equal the source rows.")
    document = render(results, args.limit_mb, args.scale, verdict + [f"PROBLEM: {p}" for p in problems])
    if not args.no_write:
        (ROOT / "docs" / "load-check.md").write_text(document, encoding="utf-8")
        print("wrote docs/load-check.md")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
