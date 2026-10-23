"""Seed two years of simulated customers, usage, invoices and metrics (deterministic, seed 42).

    uv run --project backend python scripts/seed_billing_history.py [--seed 42] [--end 2026-10-22] [--reset]

Everything written is marked is_simulated. Needs the migrated database and the table owner's URL (DATABASE_ADMIN_URL).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.billing.cli import main  # noqa: E402

if __name__ == "__main__":
    main(["history", *sys.argv[1:]])
