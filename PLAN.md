# LegacyLift: Legacy Data Migration Platform

Development plan for a full stack web application that takes a legacy desktop application (or its data file), discovers how its data is stored, maps it to a modern target schema, migrates it into PostgreSQL, proves the result with reconciliation, and reports usage and revenue in professional dashboards.

Working product name: LegacyLift (change freely, confirm it is not a registered brand before publishing).

## 1. Team Standards (apply to code, docs, UI copy, commits, and this plan)

- Use professional, neutral wording everywhere. Do not mention AI coding assistants or the tools used to generate code anywhere in the repository, including comments, commit messages, README, UI text, and test names.
- Never use the long dash character in any file, comment, UI string, or document. Use a comma, colon, parentheses, or a hyphen.
- Conventional Commits, small pull requests, one feature branch per phase task.
- Python: ruff, black, mypy (strict on `app/`), bandit. TypeScript: strict mode, ESLint, Prettier. Pre-commit hooks enforce all of these plus a check that rejects the long dash character.
- Only free and open source tools. No paid services. LLM usage stays within free tiers.

## 2. Scope

Input kinds supported in the MVP:

| Kind | How it is discovered | REA used |
|---|---|---|
| Legacy SQLite database | Schema, indexes, sample rows | Yes (SQLite inspection) |
| .NET desktop assembly plus its binary data file | Record layout recovered from metadata and CIL, then validated against the data file | Yes (.NET assembly analysis) |
| CSV or delimited export | Profiling only | No |

Stretch goals (only after Phase 13): Electron app local store, native binary via Ghidra.

REA is the open source reverse engineering tool at https://github.com/morluto/rea (MIT). The platform calls its CLI with JSON output. No agent setup is needed.

## 3. Tech Stack (all free)

| Layer | Choice |
|---|---|
| Language and API | Python 3.12, FastAPI, Pydantic v2, Uvicorn |
| ORM and migrations | SQLAlchemy 2.0, Alembic, psycopg 3 |
| Database | PostgreSQL 16 with pgvector (image `pgvector/pgvector:pg16`), HNSW cosine index |
| Embeddings | Hugging Face `BAAI/bge-small-en-v1.5` (MIT, 384 dimensions) through `sentence-transformers`, CPU, baked into the Docker image |
| LLM | Groq `openai/gpt-oss-20b` (primary) and Google Gemini Flash class model (fallback), behind one router |
| Reverse engineering | `rea-agents` npm package pinned to one version, Node.js 24.15 or newer (needed for SQLite analysis) |
| Background jobs | Celery with Redis, progress streamed to the browser with Server-Sent Events |
| Data processing | pandas, pyarrow, rapidfuzz, python-dateutil, pandera |
| Auth | argon2-cffi, PyJWT, httpOnly cookies, RBAC, Postgres Row Level Security |
| Security tooling | slowapi, cryptography (AES-256-GCM), pip-audit, npm audit, Trivy, bandit |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui (Radix), TanStack Query and Table, React Router, React Hook Form with Zod, Apache ECharts (echarts-for-react), Framer Motion, Lucide icons, `@fontsource/inter` |
| Testing | pytest, httpx, Testcontainers, Vitest, Testing Library, Playwright |
| DevOps | Docker Compose, GitHub Actions, Caddy (automatic TLS in any hosted demo) |

## 4. Architecture and Pipeline

Job state machine: `CREATED, UPLOADED, DISCOVERING, PROFILED, MAPPED, AWAITING_REVIEW, DRY_RUN, READY, LOADING, RECONCILING, COMPLETED, FAILED, ROLLED_BACK`.

Pipeline stages:

1. **Discover** (REA plus deterministic extractors): produce a source schema or a binary layout spec with evidence.
2. **Profile** (pandas): types, null rates, patterns, cardinality, PII flags, quality issues.
3. **Map** (embeddings plus pgvector): rank target fields for every source column. LLM only for ambiguous columns.
4. **Review** (UI): analyst accepts or overrides mappings. Approved mappings are saved as mapping memory.
5. **Transform and dry run**: safe transform rules applied to a sample, with before and after preview.
6. **Load and reconcile**: staged load, validated insert, quarantine of bad rows, reconciliation report, rollback by job id.

### Token minimization rules (mandatory)

- Deterministic first. LLM is called only when rules, abbreviation expansion, mapping memory, and embedding similarity cannot decide.
- Target at most 2 LLM calls per job and under 6,000 total tokens. The job budget is enforced in code and recorded.
- One batched call per task, never one call per column. Chunk size about 12 columns so every request stays under 5,000 tokens (Groq free tier allows 8,000 tokens per minute for `gpt-oss-20b`).
- Send metadata only: normalized column name, inferred type, pattern class, at most 5 distinct masked sample values, top 3 candidate targets. Never send full rows. Never send values from columns flagged as PII, send only the pattern class.
- Constant system prompt prefix placed first so provider prompt caching applies (cached tokens do not count toward Groq rate limits).
- `temperature` 0, low reasoning effort for the Groq model, `max_tokens` cap of 800, strict JSON schema output validated with Pydantic, one repair retry maximum.
- Cache every LLM answer in `llm_cache` keyed by SHA-256 of the normalized request. Reuse approved mappings through `mapping_memory`, so repeated schemas cost zero tokens.
- The final report is generated from templates. An LLM executive summary is optional, off by default, capped at 400 tokens.

### LLM router behavior

- Common interface `complete_json(task, payload, schema)` with `GroqProvider` and `GeminiProvider`.
- Admin setting chooses primary provider. Default order: Groq, then Gemini.
- Local token bucket per provider seeded from Groq response headers (`x-ratelimit-remaining-tokens`, `x-ratelimit-remaining-requests`) and from configured Gemini limits.
- Failover on HTTP 429, 5xx, timeout, or schema failure after one repair attempt. Exponential backoff with jitter. Circuit breaker opens for 60 seconds after 3 consecutive failures.
- Gemini model name comes from `GEMINI_MODEL` env var. Free tier limits and eligible models change often and published figures disagree, so confirm the current free models and quotas in Google AI Studio before choosing a default (a Flash-Lite class model is the safest start).
- Free tier content may be used by the provider to improve its products. Send only synthetic or masked data. State this in the README.
- Every call writes a row in `llm_calls` (provider, model, task, tokens in, tokens out, cached tokens, latency, status, cache hit, job id).

## 5. Dataset (dedicated section)

All data is open or synthetic. Nothing real or personal is used. Create the top level folder `datasets/` and commit only small files. Large files are downloaded or generated by scripts.

```
datasets/
  README.md                     provenance, licenses, regeneration steps
  scripts/
    fetch_chinook.sh            downloads and verifies the Chinook SQLite file
    generate_synthea.sh         generates synthetic patients as CSV
    build_legacy_commerce.py    makes the legacy SQLite fixture from Chinook
    build_legacy_clinic/        small .NET app plus build script (see below)
    inject_defects.py           adds controlled data quality problems
  raw/                          git-ignored downloads
  fixtures/
    commerce_legacy.sqlite      legacy style SQLite file
    clinic/ClinicDesk.dll       compiled legacy .NET assembly
    clinic/clinic.cdb           proprietary binary data file
    clinic/ground_truth.json    true layout and true field mapping
    commerce/ground_truth.json  true column rename map and defects list
  targets/
    commerce_target.json        canonical target schema with field descriptions
    clinic_target.json          canonical target schema with field descriptions
  expected/                     reference outputs used by the evaluation harness
```

### Source 1: Chinook (retail and media store)

- What: sample database of artists, albums, tracks, invoices, customers, employees. Customer and employee records are fictitious, sales are randomly generated.
- Where: https://github.com/lerocha/chinook-database, download the SQLite asset from the latest release. Read `LICENSE.md` in that repository and copy its terms into `datasets/README.md`.
- How it is used: `build_legacy_commerce.py` copies Chinook into `commerce_legacy.sqlite` and makes it look like a real legacy system:
  - rename tables and columns to cryptic legacy names (for example `CUST_MST`, `cust_nm`, `inv_dt`, `bill_addr1`), storing the rename map in `ground_truth.json`
  - store dates as text in mixed formats (`MM/DD/YYYY`, `YYYYMMDD`, `DD-Mon-YY`)
  - store money as text with currency symbols in some rows
  - add status codes as integers with no lookup table
- Target: canonical `customers, products, orders, order_items` schema in `commerce_target.json`.

### Source 2: Synthea (synthetic patients for the clinic scenario)

- What: open source synthetic patient generator, Apache 2.0, https://github.com/synthetichealth/synthea. Output is not real patient data.
- How to get data: generate locally. Install a current Java JDK as stated in the Synthea README, then run `./run_synthea -p 1000 -s 42 Massachusetts --exporter.csv.export=true`. CSV export is off by default, so the flag is required. Output includes `patients.csv, encounters.csv, conditions.csv, medications.csv, observations.csv` and others. Keep five files and about 1,000 patients so the demo stays fast. Synthea also publishes ready made sample datasets on its site, use them if generation is inconvenient and record the source in `datasets/README.md`.
- How it is used: the small .NET console app `ClinicDesk` (built with the free .NET SDK, cross platform) imports the CSV files and writes them to a proprietary fixed record binary file `clinic.cdb`: a header, fixed width padded strings in a legacy code page, integer dates as days since an epoch, money stored as integer cents, flags packed into bit fields. The app is compiled to `ClinicDesk.dll`.
- Ground truth: the original CSV values plus `ground_truth.json` containing the real record layout. The platform never reads the ground truth during a run. Only the evaluation harness uses it.
- Target: canonical `patients, encounters, conditions, medications, observations` schema in `clinic_target.json`.

### Defect injection (both scenarios)

`inject_defects.py` with a fixed seed adds: duplicate rows (2%), null required fields (3%), invalid dates, trailing spaces and mixed casing, out of range numerics, orphan foreign keys (1%). The list of injected defects is written to `ground_truth.json` so the quality report can be scored.

### Target schema files

Each target field has: `table`, `name`, `type`, `required`, `description` (one plain sentence), `synonyms` (list), `examples` (3 values), `pii` (boolean). These descriptions are embedded and stored in pgvector.

### Demo experience

Seed script registers both scenarios in the app as selectable "Sample legacy systems" so a visitor can run a full migration with one click. Each scenario must complete end to end in under 3 minutes on a laptop.

## 6. Repository Layout

```
legacylift/
  README.md
  docker-compose.yml
  .env.example
  backend/        app/{api,core,db,services,pipeline,llm,workers}, alembic/, tests/
  rea-runner/     Dockerfile, wrapper service, pinned REA version
  frontend/       src/{site,app,components,charts,lib,styles}, public/images/, e2e/
  datasets/       see section 5
  scripts/        seed_demo.py, seed_billing_history.py, capture_screenshots.ts, eval_run.py
  docs/           architecture.md, api.md, security.md, evaluation.md, images/
  .github/workflows/ci.yml
```

## 7. Data Model (PostgreSQL)

- Identity: `organizations, users, memberships (role), refresh_tokens, audit_log, leads`
- Pipeline: `sources, jobs, job_events, discovery_evidence, source_columns (embedding vector(384)), mappings, transform_rules, quarantined_rows, reconciliation_reports`
- Targets: `target_schemas, target_fields (embedding vector(384)), mapping_memory (embedding vector(384))`, plus a `warehouse` schema holding migrated tables, each row carrying `org_id` and `job_id`
- LLM: `llm_calls, llm_cache`
- Billing and analytics: `plans, subscriptions, usage_events, invoices, invoice_lines, daily_metrics`
- Every tenant table has `org_id` and a Row Level Security policy bound to `current_setting('app.org_id')`.

## 8. Development Phases

Estimates assume one developer working full time. Each phase ends with its acceptance checks passing in CI before the next begins.

### Phase 0: Foundation (2 days)

- Create the repository layout, Docker Compose services: `api, worker, db (pgvector), redis, rea-runner, frontend`.
- Configure ruff, black, mypy, bandit, pre-commit, ESLint, Prettier, commit linting, the long dash check, and a GitHub Actions workflow running lint, type check, tests, `pip-audit`, `npm audit`, and Trivy.
- Config management with Pydantic Settings and `.env.example` documenting every variable. No secrets in git.
- Structured JSON logging (structlog) with request ids. Health endpoints `/health` and `/ready`.
- Acceptance: `docker compose up` starts all services, CI is green on an empty test suite.

### Phase 1: Database and Core Models (3 days)

- Alembic migrations for every table in section 7, `CREATE EXTENSION vector`, HNSW indexes with `vector_cosine_ops`.
- Repository layer, tenancy dependency that sets `app.org_id` per request, and Row Level Security policies with tests proving that org A cannot read org B.
- Acceptance: migration up and down works, cross tenant isolation tests pass.

### Phase 2: Authentication, Authorization, and Security Baseline (4 days)

- Registration is disabled in the demo. Users are seeded. Password hashing with argon2id, password policy (12 characters minimum), account lockout after repeated failures.
- Access token (15 minutes) and rotating refresh token (7 days, stored hashed) in httpOnly, Secure, SameSite=Lax cookies. CSRF protection through a custom header check on state changing requests.
- Roles: `admin` (everything, billing and revenue dashboards, settings), `analyst` (create and run migrations, review mappings), `viewer` (read only). A `require_role` dependency guards every route. Frontend hides what the role cannot use, but the backend is the authority.
- Security controls: slowapi rate limits (strict on login), CORS allowlist, security headers (CSP, HSTS, X-Content-Type-Options, Referrer-Policy), upload limits (size cap, extension and magic byte check), uploads encrypted at rest with AES-256-GCM using a key from the environment, audit log entries for login, upload, mapping approval, load, rollback, settings change.
- Uploaded software is never executed. REA runs static analysis only, inside the `rea-runner` container (no internet, read only mount of uploads, non root, CPU and memory limits, 120 second timeout). Runtime capture features are not exposed.
- Acceptance: automated tests for authentication flow, role matrix, lockout, CSRF, rate limit, upload rejection, and tenant isolation.

### Phase 3: Dataset and Legacy Fixtures (4 days)

- Implement everything in section 5: fetch and generate scripts, the legacy commerce builder, the `ClinicDesk` .NET app and binary writer, defect injection, target schema files, and `ground_truth.json` files.
- Add `datasets/README.md` with provenance, licenses, exact commands, and file checksums.
- Add pytest checks that fixtures are reproducible (same seed gives same checksum).
- Acceptance: `make datasets` rebuilds all fixtures from scratch, both fixtures open correctly with standard tools (SQLite client, hex dump), and ground truth matches the generated content.

### Phase 4: REA Integration and Discovery (5 days)

- Build the `rea-runner` image: Node 24.15 or newer, `rea-agents` installed at a pinned version, a thin wrapper that accepts a command name, a file reference, and options, then returns the CLI JSON output. Read the REA docs (`docs/cli.md` and the generated tool catalog in `docs/mcp-contracts.md`) to get the exact CLI command names for SQLite inspection and .NET assembly analysis, because REA changes quickly. Record the chosen commands in `docs/architecture.md`.
- `rea_adapter.py` in the backend: timeouts, structured errors, evidence persisted in `discovery_evidence` (raw JSON, command, REA version, duration).
- SQLite discovery: take tables, columns, indexes, views, triggers, and sample rows from REA output, convert to the internal `SourceSchema` model.
- .NET discovery: from REA metadata and CIL output, a deterministic extractor builds a `LayoutSpec` (header, record size, per field name, offset, length, type, encoding, endianness). It looks for reader and writer call sequences, padding constants, encoding choices, and field names in the record type metadata. Ambiguities are passed to the LLM as a compact evidence excerpt (cap 1,500 tokens).
- Layout validation without trusting the analysis: parse the first N records, check `header + count * record_size == file size`, check printable text in string fields and plausible date ranges, and score confidence. If validation fails, fall back to statistical layout inference (byte class and entropy per offset) and mark the result as lower confidence.
- Acceptance: on both fixtures, discovery recovers at least 95 percent of true fields (name, type, size) compared with `ground_truth.json`, and evidence is viewable per field.

### Phase 5: Profiling and Quality Engine (3 days)

- Stream the source into pandas chunks. Per column: inferred type, null rate, distinct count, min and max, top patterns, date format candidates, numeric format candidates, length stats.
- Deterministic semantic typing with regex and rules (email, phone, postal code, date, currency, identifier, enumerated code).
- PII detector (rules) that sets `pii=true` and controls what is ever sent to an LLM.
- Quality checks with pandera: duplicates, required nulls, invalid dates, out of range values, orphan keys. Produce a data quality score (0 to 100) with a documented formula.
- Acceptance: injected defects are detected with recall of at least 90 percent on both fixtures.

### Phase 6: Embedding and Mapping Engine (4 days)

- Embedding service wrapping `sentence-transformers` with `BAAI/bge-small-en-v1.5`, normalized vectors, batch encoding, model loaded once per worker.
- Abbreviation dictionary (cust, nm, addr, dt, amt, qty, ph, ...) expanding cryptic legacy names before embedding. Embedding text for a source column: expanded name, semantic type, pattern class, short masked examples. Embedding text for a target field: table, name, description, synonyms, type.
- Mapping decision per source column:
  1. exact match in `mapping_memory` (embedding distance below threshold): reuse, zero tokens
  2. pgvector top 3 targets; auto accept if similarity and margin over the second candidate exceed tuned thresholds and the type is compatible
  3. otherwise add to the ambiguous batch for one LLM call (Phase 7)
  4. LLM answer below confidence goes to human review
- Type compatibility gate prevents mapping a date column to a numeric field regardless of similarity.
- Thresholds are tuned on a held out portion of fixtures and stored in config, not hard coded.
- Acceptance: at least 85 percent of columns mapped correctly without any LLM call, and at least 95 percent after the LLM step, measured against ground truth.

### Phase 7: LLM Router and Token Budget (3 days)

- Implement `LLMRouter`, providers, rate limit tracking, failover, circuit breaker, `llm_cache`, `llm_calls`, per job and per organization daily token budgets, and the prompt templates for the two tasks: `resolve_mappings` (ambiguous columns, optional transform hint) and `interpret_layout` (ambiguous binary layout evidence).
- Prompts are versioned files with a constant prefix, JSON schema outputs, and unit tests with recorded responses so CI never calls a real provider.
- Admin UI hook: provider preference, budget limits, and a test connection button (Phase 12).
- Acceptance: simulated 429 and timeout trigger failover without losing the job, budgets stop a runaway job, a repeated run of the same fixture uses zero tokens, a first run stays under 6,000 tokens.

### Phase 8: Transform, Load, and Reconciliation (5 days)

- Safe transform DSL (no `eval`, whitelist only): `cast_int, cast_decimal, cents_to_decimal, parse_date(formats), epoch_days_to_date, normalize_phone, trim, case, map_codes, split, concat, default, regex_extract`. Rules are generated deterministically from profile and mapping, with an LLM hint only for unusual formats, and always validated against a sample before use.
- Dry run on a sample (for example 500 rows) with before and after preview and per rule failure counts.
- Load: write to a staging table with `COPY`, validate required fields, types, and foreign keys, move valid rows to `warehouse` tables in one transaction, send invalid rows to `quarantined_rows` with reason codes.
- Idempotency by job id, and rollback that deletes rows by `job_id`.
- Reconciliation report: row counts (source, loaded, quarantined), distinct key counts, null rate comparison, numeric sum checksums, sampled row level hash comparison, and the quality score before and after.
- Acceptance: both fixtures load with no unexplained difference, quarantined rows match the injected defects, rollback restores the empty state.

### Phase 9: Job Orchestration and API (4 days)

- Celery tasks per stage with retries and idempotent steps, job events written at each transition, Server-Sent Events endpoint streaming progress to the UI.
- REST API (all under `/api/v1`, OpenAPI at `/docs`):
  - auth: `login, refresh, logout, me`
  - sources: upload, list, sample systems, delete
  - jobs: create, list, get, events stream, cancel, rollback
  - mappings: list, update, approve, bulk approve
  - transforms: preview
  - reports: reconciliation, quarantined rows (paginated), CSV export
  - schemas: target schemas and fields
  - admin: users, roles, LLM settings, audit log
  - analytics and billing: see Phase 10
  - public: `public/metrics` (aggregate demo numbers for the marketing site), `leads`
- Consistent error format, pagination, filtering, and request validation.
- Acceptance: a full job runs from API calls only, OpenAPI examples are accurate, API tests cover every route and role.

### Phase 10: Billing Engine, Metrics, and Analytics APIs (5 days)

Revenue figures are simulated for demonstration because there are no real customers. The dashboard logic is real: all numbers are computed from tables, and every migration run in the app creates real usage events that flow into the next invoice and metrics snapshot. Seeded rows carry `is_simulated=true` and the README and UI footnote state it.

- Plans (illustrative): Starter (free, 100,000 records per month), Professional ($499 per month, 2,000,000 records included, $0.20 per 1,000 records over), Enterprise ($2,500 per month, 25,000,000 records included, $0.08 per 1,000 records over).
- Metering: each completed job writes `usage_events` (records loaded, tokens used, duration). A monthly invoice run produces `invoices` and `invoice_lines` (base fee and overage). No payment processor, statuses are `paid, open, overdue, void` set by the seed or a simple simulation.
- Nightly snapshot job writes `daily_metrics` (date, org counts, MRR, new, expansion, contraction, churned MRR, usage revenue, jobs, records, tokens by provider, failovers, cache hits, success rate, average duration, quality score).
- `seed_billing_history.py` (deterministic, seed 42): 24 months of history, about 140 fictional organizations across five industries and four regions, S curve growth, plan mix, monthly churn (higher for Starter, lower for Enterprise), expansion through usage growth, Q4 seasonality, invoices and payments, and historical jobs with plausible token, latency, and failure distributions.
- Metric definitions (document in `docs/architecture.md`): MRR is the sum of base fees of active subscriptions. Usage revenue is tracked separately. Net new MRR equals new plus expansion minus contraction minus churn. Net revenue retention is computed over the trailing 12 months. Gross churn is churned MRR divided by starting MRR. ARPA is MRR divided by active paying accounts.
- Analytics endpoints (admin for revenue, analyst and admin for operations) with date range, granularity (day, week, month), previous period comparison, and CSV export. Cache with Redis for 60 seconds.
- Acceptance: metrics reconcile (sum of invoice lines equals revenue shown, MRR movements add up month over month), tests for every metric formula.

### Phase 11: Frontend Foundation and Public Marketing Site (6 days)

Goal: a public site that looks and feels like a large enterprise software company.

Design system:
- Palette: deep navy `#0B1F3A`, primary blue `#0F62FE`, teal accent `#00A3A3`, neutral grays, success, warning, error. Inter font (self hosted). 8 point spacing grid, maximum content width 1280 px, generous whitespace, consistent 12 column grid.
- Components from shadcn/ui customized into a single token file (colors, radii, shadows, typography scale). Light theme for the public site, light and dark for the console.
- Accessibility WCAG 2.1 AA: focus states, keyboard navigation, aria labels, color contrast, reduced motion support.
- Performance: Lighthouse at least 90 performance and 95 accessibility on desktop, route level code splitting, lazy loaded charts and images, WebP or AVIF images with explicit dimensions.

Global layout:
- Top utility bar (Contact, Documentation, Status) and a main navigation with mega menus: Platform, Solutions, Pricing, Resources, Company. A clear `Sign in` link at the far right and a primary `Open console` button, both routing to `/login`. Sticky header that condenses on scroll, mobile drawer menu.
- Footer: five link columns, newsletter style contact field storing to `leads`, legal links, language and region label, a short portfolio notice line stating that the site is a demonstration with sample data.

Pages and content:
- **Home**: hero with headline (example: "Move decades of legacy data into modern systems, without a rewrite"), supporting sentence, primary and secondary calls to action, and a product screenshot; capability strip with six cards (Discovery, Mapping, Transformation, Validation, Reconciliation, Analytics); animated metric band fed by `public/metrics` and labelled as demo environment figures; "How it works" four step section with the pipeline illustration; industry tiles (Healthcare, Retail and Commerce, Financial Services, Manufacturing, Public Sector) with photos; interactive product tour using real screenshots; security practices summary; illustrative case study cards clearly marked as sample scenarios based on the two datasets; FAQ accordion; closing call to action band.
- **Platform**: the six pipeline stages in depth with diagrams, supported source types, and architecture overview.
- **Solutions** (one page per industry): challenge, approach, sample outcome, linked demo scenario.
- **Pricing**: three plan cards from the plans API, feature comparison table, and a usage cost calculator (records per month to estimated cost).
- **Security**: only implemented practices (tenant isolation, encryption at rest, role based access, audit logging, static analysis only, dependency scanning). Do not claim certifications that do not exist.
- **Resources**: documentation viewer rendering project docs, API reference link, blog style cards for three technical articles written from the project (discovery approach, token efficient mapping, reconciliation method).
- **Company and Contact**: about, contact form (stored in `leads`, visible to admin), request demo form.
- **Legal**: privacy and terms templates clearly labelled as samples.
- **Login**: split layout with branded panel and form, validation, error states, forgot password placeholder, redirect to the console by role.

Imagery:
- Use free licensed photography from Unsplash or Pexels (data center, server room, hospital technology, analytics workspace, warehouse and logistics, banking office, engineering team). Download, convert to WebP, store under `frontend/public/images/`, and list every photographer, source URL, and license in `frontend/public/images/ATTRIBUTION.md`.
- Use unDraw or hand built SVG for diagrams. No real company logos, no real customer names, no stock testimonials presented as real.
- Product screenshots are produced from the running seeded app by `scripts/capture_screenshots.ts` (Playwright), so they are authentic and can be regenerated.
- Open Graph images and favicon set included.

Acceptance: all public pages responsive from 360 to 1920 px, Lighthouse targets met, no layout shift, all links work.

### Phase 12: Authenticated Console (8 days)

Layout: collapsible sidebar, top bar with organization switcher, global command palette (Ctrl K), notifications, user menu, breadcrumbs, skeleton loaders, empty states, error boundaries, toasts.

Pages:
- **Overview**: KPI cards (jobs this month, records migrated, success rate, average duration, tokens used), recent jobs table, active job progress cards, quick start with the two sample systems.
- **Migrations**: filterable and sortable table (status, source kind, date, owner), status badges, row actions.
- **New Migration wizard** (stepper):
  1. Source: upload a file or pick a sample system.
  2. Discovery: live progress through SSE, then a results view with schema or layout table, per field confidence, and an evidence drawer showing what REA found.
  3. Mapping review: table of source column, suggested target, confidence bar, method badge (memory, embedding, LLM), accept, override with searchable target picker, bulk approve for high confidence rows.
  4. Transform preview: before and after side by side, rule failure counts.
  5. Dry run results and cost summary (tokens, estimated time).
  6. Execute and monitor with stage timeline.
  7. Reconciliation report: count cards, checksum table, quality score gauge, quarantined rows table with reason filters, CSV export, rollback button (admin and analyst).
- **Target Schemas** and **Mapping Memory**: browse fields, descriptions, saved mappings.
- **Analytics** (analyst and admin): see below.
- **Revenue** (admin only): see below.
- **Billing**: current plan, usage meter, invoice list and invoice detail view (printable).
- **Team**: users, roles, invite placeholder. **Audit Log**: searchable events. **Settings**: LLM provider order, token budgets, test connection, data retention.

#### Revenue dashboard (ECharts, admin only)

Controls: date range presets (30 days, 90 days, 12 months, 24 months, custom), granularity, compare with previous period, export CSV, print layout.

- KPI row with sparkline and delta: MRR, ARR, net new MRR, net revenue retention, gross churn, ARPA, active paying accounts.
- MRR over time (area) with dashed forecast of the next 6 months (simple linear or exponential smoothing, labelled as projection).
- MRR movement (stacked bars: new, expansion, contraction, churn) with a waterfall view toggle.
- Revenue by plan (stacked area), by industry (treemap or donut), by region (horizontal bars).
- Subscription versus usage revenue (stacked columns).
- Cohort retention heatmap (signup month by months since signup).
- Customer growth and ARPA trend (dual axis line).
- Unit economics: revenue per 1,000 records and LLM tokens per 1,000 records.
- Top 10 customers table with trend sparklines. Invoices table with status badges, search, and pagination.
- A footnote on the page states the historical figures are simulated for demonstration.

#### Operations analytics dashboard

- Jobs per day (bars), success rate (line), records migrated (area).
- Mapping automation breakdown (stacked: memory, embedding, LLM, human reviewed).
- LLM tokens per job by provider (stacked), failover count, cache hit rate, token savings from memory and cache.
- Latency percentiles (p50, p95) by pipeline stage (horizontal stacked bars).
- Data quality score trend, error taxonomy (bar), discovery confidence distribution (histogram).
- Same controls as the revenue dashboard.

Chart standards: consistent color mapping per series across all charts, labelled axes with units, readable tooltips with exact values, legends that toggle series, loading and empty states, responsive resize, number formatting (compact currency, thousands separators), accessible text summaries for each chart.

Acceptance: Playwright end to end tests cover login for each role, the full migration wizard on both sample systems, role restricted pages, and both dashboards rendering with seeded data.

### Phase 13: Evaluation, Testing, and Hardening (4 days)

- `scripts/eval_run.py` runs both fixtures end to end and writes `docs/evaluation.md` with: field discovery recall, mapping accuracy (before and after LLM), defect detection recall, reconciliation pass rate, tokens per job by provider, LLM calls per job, wall time per stage. Show first run versus repeat run (memory and cache effect).
- Backend coverage target 80 percent on `pipeline`, `llm`, and `core`. Frontend component tests for wizard steps and chart data transforms. Contract tests for the router with recorded provider responses.
- Security pass: OWASP ASVS style checklist, dependency scans clean, secrets scan, log review confirming no PII or tokens are logged, Trivy scan of images.
- Load sanity: a 200,000 row CSV migrates without memory growth beyond a set limit.
- Acceptance: evaluation document committed, all CI checks green, no high severity findings open.

### Phase 14: Documentation and Release (3 days)

README requirements (clear, professional, no marketing exaggeration, no informal wording):

1. Title, one paragraph overview, status badges.
2. Problem and solution summary.
3. Feature list.
4. Architecture diagram (Mermaid) and pipeline description.
5. Tech stack table.
6. Quick start: prerequisites (Docker, Node, Python versions), `cp .env.example .env`, how to obtain a Groq key and a Gemini key, `docker compose up`, seed commands, URLs.
7. Dataset section: sources, licenses, how fixtures are built, how to regenerate.
8. REA setup notes: pinned version, Node requirement, how the runner is isolated.
9. Demo accounts (admin, analyst, viewer) for local use only, with the instruction to change them outside local use.
10. Usage walkthrough with screenshots for both sample systems.
11. Token efficiency design and measured results table from `docs/evaluation.md`.
12. Security model and tenant isolation summary.
13. Dashboards overview with screenshots and the statement that revenue history is simulated.
14. API reference link, testing commands, project structure.
15. Attributions and licenses (datasets, Hugging Face model, images, REA, other open source).
16. Troubleshooting, limitations, and roadmap.

Also deliver: `docs/architecture.md`, `docs/api.md`, `docs/security.md`, a 2 minute screen recording of the demo, and tagged release `v1.0.0` with a changelog.

Acceptance: a new developer can clone the repository and reach a working demo using only the README.

## 9. Definition of Done

- Both sample systems migrate end to end from the UI with a passing reconciliation report.
- Measured token use is within budget and the evaluation document proves it.
- Role based access, tenant isolation, and audit logging are tested.
- Public site and console meet the design, accessibility, and performance targets.
- Revenue and operations dashboards render with 24 months of history and clearly disclose simulated data.
- Documentation is complete, professional, and free of the wording and character rules in section 1.
