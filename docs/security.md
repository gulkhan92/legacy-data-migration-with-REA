# Security

This document describes the security controls implemented so far (Phase 2). Each statement
below is covered by an automated test in `backend/tests`.

## Authentication

- Registration is disabled. Users are created by `python -m app.seed` (local demo) or by an
  administrator with database access.
- Passwords are hashed with argon2id. Policy: 12 to 128 characters with a lowercase letter, an
  uppercase letter and a digit, and not containing the name part of the email address.
- Five failed logins lock the account for 15 minutes. A locked account refuses even the correct
  password. Unknown accounts and wrong passwords return the same response and take similar time.
- The access token (15 minutes, signed, HS256) and the rotating refresh token (7 days) are sent
  in `HttpOnly`, `Secure`, `SameSite=Lax` cookies. The refresh cookie is scoped to
  `/api/v1/auth`. Only the SHA-256 hash of a refresh token is stored.
- Refresh rotates the token. Presenting an already used refresh token revokes every session of
  that user. Refresh re-checks that the user is active and still a member of the organization.
- Outside `local` and `test` environments the application refuses to start with the built in
  development `JWT_SECRET` or `UPLOAD_ENCRYPTION_KEY`.

## Authorization

- Roles: `admin` (everything), `analyst` (create and run migrations, review mappings), `viewer`
  (read only). `require_role` guards each route and admin is always permitted. The backend is the
  authority, the frontend only hides what a role cannot use.
- A test fails if a route is added without an authentication dependency.
- Tenant isolation is enforced by PostgreSQL Row Level Security (see the Phase 1 migration). The
  application connects as a role that is not a superuser, does not own the tables and cannot
  bypass RLS. Each request runs with `app.org_id` taken from the verified token.
- During login no organization is known yet, so a second policy lets a caller read only their own
  membership rows through `app.user_id`.

## Request protection

- CSRF: every state changing request must carry `X-Requested-With: LegacyLift`. A cross site
  form or simple request cannot add it, and cross origin scripts are stopped by the CORS
  allowlist.
- CORS allows only the origins in `CORS_ORIGINS`, with credentials.
- Rate limits (slowapi): login 5 per minute per address, refresh 30, upload 20, default 300.
  Behind a reverse proxy the client address must come from a trusted forwarded header.
- Security headers on every response: CSP, HSTS, `X-Content-Type-Options`, `Referrer-Policy`,
  `X-Frame-Options`, `Permissions-Policy`, `Cache-Control: no-store`.

## Uploads

- Extension allowlist, size cap (default 50 MB, enforced while streaming), and magic byte checks:
  SQLite header, PE header for assemblies, text check for delimited files, and a refusal of
  executable content disguised as a data file.
- Files are encrypted with AES-256-GCM (random nonce per file, ciphertext bound to its storage
  path) under a key from `UPLOAD_ENCRYPTION_KEY` and stored under a random name. The original
  filename is kept only as sanitized metadata.
- Uploaded software is never executed. The application contains no dynamic import or evaluation code, and the
  only module that starts a process is the REA adapter, which runs the fixed runner command without a shell and
  hands it the file as an argument (both checked by tests). Static analysis runs only in the `rea-runner`
  container: no network, read only root filesystem and mounts, non root user, all capabilities
  dropped, CPU, memory and process limits, 120 second timeout. Runtime capture is not exposed.

## Profiling and PII

Profiling opens SQLite uploads read only and immutable with query_only on and the schema trust off, and decodes
binary files with a layout from discovery; neither runs anything from the file. PII columns (rules in
`app/profiling/pii.py`) keep no values in the stored profile, issue lists show no value for them, and the LLM view of a
column (`app/profiling/llm_view.py`) contains only its pattern class. Tests check the database content for leaked values.

## LLM calls

Provider keys come from the environment (`GROQ_API_KEY`, `GEMINI_API_KEY`) as secret values: they are not stored in the database,
not returned by any route and not logged. Nothing leaves the system without passing the payload guard (no rows, no values that look
like personal data, size limit) and the PII-free profile views. Free tier providers may use prompts to improve their products,
which is why only synthetic or masked data is sent. Every request is recorded (`llm_calls`) without its content. Token budgets per
job and per organization and a per job call limit bound what a runaway job can send. The tests fail on any real network call.

## Audit log

Login, failed login, lockout, logout, refresh token reuse, upload and rejected upload are recorded
now. Mapping approval, load, rollback and settings change have their action names reserved and are
recorded when those features are built. The table is append only for the application role, and
organization admins read only their own organization's entries.

## Transform and load

- Transform rules are data validated against a fixed whitelist of operations. There is no `eval`, no import and no user supplied SQL: identifiers in the load SQL come from the target schema, values are bound parameters or `COPY` data.
- Regular expressions in rules are length limited and refused when they nest quantifiers or use backreferences, so a rule cannot hang the worker.
- Staging tables are temporary, per connection, and the final insert goes through the same row level security as everything else: every warehouse and quarantine row carries the organization, and a job of another organization is a 404.
- Mutations (approve, reject, rules, code maps, dry run, load, rollback) need the analyst role; reading rules, reports and quarantine needs any signed in user. Load and rollback are written to the audit log.
- The raw source row of a quarantined record is kept so it can be fixed, which means it may contain personal data. It is protected by the same row level security and is deleted by a rollback of the job.

## Jobs, workers and the public API

- Task payloads contain ids only (job, organization, user), never data and never keys. A worker opens its sessions scoped to the organization in the payload, so Row Level Security applies exactly as in a request. The broker is trusted: Redis listens on localhost only in the compose file, has no persistence, and must not be exposed.
- Celery accepts JSON only, no pickle, so a message cannot carry code.
- A failed stage records the exception class in the job event, not its message, so paths, SQL and source data do not reach users through the event stream.
- The event stream is authenticated by the same cookie as every other route, is scoped to the caller's organization on every poll, and answers 404 for a job of another organization.
- User management is admin only, never reveals whether an address has an account, revokes refresh tokens when a user is removed, refuses removing or demoting the last admin, and is audited.
- Validation errors never include the submitted value, so a mistyped password field is not echoed back or logged by a proxy.
- The public form and metrics are the only routes without sign in. They read no tenant data, return no revenue figures, need the CSRF header, are rate limited, and the form drops bot submissions silently.
- The quarantine CSV export neutralises spreadsheet formulas.
- Sample systems are registered by copying the repository's own fixtures, never from a path chosen by the caller.

## Known limits

- Access tokens are stateless and remain valid until they expire (15 minutes) after logout.
- Two simultaneous refreshes with the same cookie (for example two browser tabs) are
  indistinguishable from token theft and end the session.
- Uploads are encrypted in memory in one piece, which is why the size cap exists.
- Access tokens of a removed user stay valid until they expire; their refresh tokens are revoked at once.
- A running job stage cannot be interrupted, only the stages after it.
