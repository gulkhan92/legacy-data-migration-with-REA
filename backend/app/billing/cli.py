"""Operator commands for billing.

    python -m app.billing.cli history [--seed 42] [--end 2026-10-22] [--reset]   # the demonstration history (table owner)
    python -m app.billing.cli invoices --month 2026-09                           # the invoice run for a month
    python -m app.billing.cli snapshot [--date 2026-10-22]                       # one day of metrics (default: the nightly pair)

`history` writes as the table owner (it creates organizations); the others use the application role like the workers do.
"""

import argparse
import json
from datetime import UTC, date, datetime

from sqlalchemy import create_engine

from app.billing import history, invoicing, snapshots
from app.core.config import get_settings


def _day(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.billing.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("history", help="write the simulated 24 month history")
    h.add_argument("--seed", type=int, default=42)
    h.add_argument("--end", type=_day, default=None, help="last day of the history (default: today)")
    h.add_argument("--reset", action="store_true", help="remove the simulated organizations first")
    i = sub.add_parser("invoices", help="run the invoices of a month")
    i.add_argument("--month", type=lambda v: date.fromisoformat(v + "-01"), required=True, help="YYYY-MM")
    s = sub.add_parser("snapshot", help="take the metrics snapshot of a day, or the nightly pair")
    s.add_argument("--date", type=_day, default=None)
    args = parser.parse_args(argv)

    if args.command == "history":
        engine = create_engine(get_settings().database_admin_url)
        print(json.dumps(history.seed_history(engine, args.seed, args.end, reset=args.reset), indent=2))
    elif args.command == "invoices":
        run = invoicing.run_invoices(args.month)
        print(json.dumps({"period": [run.period_start.isoformat(), run.period_end.isoformat()], "created": run.created, "skipped": run.skipped, "total_cents": run.total_cents, "failed": run.failed}, indent=2))
    else:
        days = [args.date] if args.date else snapshots.nightly(datetime.now(UTC))
        if args.date:
            snapshots.take_snapshot(args.date)
        print("snapshots taken for", ", ".join(d.isoformat() for d in days))


if __name__ == "__main__":
    main()
