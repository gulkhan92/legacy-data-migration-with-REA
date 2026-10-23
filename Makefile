.PHONY: datasets datasets-quick datasets-verify

PYTHON ?= python

# Rebuild every dataset fixture from scratch. Downloads are cached and checksum verified.
datasets:
	$(PYTHON) datasets/scripts/build_all.py --clean

# Rebuild reusing the generated Synthea output.
datasets-quick:
	$(PYTHON) datasets/scripts/build_all.py

# Check that the committed fixtures match datasets/checksums.json.
datasets-verify:
	$(PYTHON) datasets/scripts/verify_fixtures.py

# Background workers (Phase 9). Needs Redis: docker compose up -d redis
.PHONY: worker
worker:
	cd backend && uv run celery -A app.jobs.celery_app:celery_app worker -Q legacylift --loglevel=INFO --concurrency=2

# Billing (Phase 10). Two years of simulated customers, usage, invoices and metrics (deterministic, seed 42).
.PHONY: seed-billing
seed-billing:
	cd backend && uv run python -m app.billing.cli history --reset
