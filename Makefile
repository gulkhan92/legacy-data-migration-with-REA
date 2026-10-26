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

# Frontend (Phase 11). Needs Node 22 or newer.
.PHONY: frontend-install frontend-dev frontend-check frontend-e2e
frontend-install:
	cd frontend && npm ci

frontend-dev:
	cd frontend && npm run dev

# Lint, typecheck, unit tests and build.
frontend-check:
	cd frontend && npm run lint && npm run typecheck && npm test && npm run build

# Browser tests against the built site (start the API, or the mock, first: see frontend/README.md).
frontend-e2e:
	cd frontend && npm run build && npx playwright test
