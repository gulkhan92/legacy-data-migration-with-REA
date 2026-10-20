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

## Audit log

Login, failed login, lockout, logout, refresh token reuse, upload and rejected upload are recorded
now. Mapping approval, load, rollback and settings change have their action names reserved and are
recorded when those features are built. The table is append only for the application role, and
organization admins read only their own organization's entries.

## Known limits

- Access tokens are stateless and remain valid until they expire (15 minutes) after logout.
- Two simultaneous refreshes with the same cookie (for example two browser tabs) are
  indistinguishable from token theft and end the session.
- Uploads are encrypted in memory in one piece, which is why the size cap exists.
