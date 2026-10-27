# Security review

This is the security pass of Phase 13. It checks the platform against the chapters of the OWASP Application Security Verification Standard (version 4.0.3, the controls that apply to a web application and its API), records what the scans found and what was fixed, and lists what remains open. The controls themselves are described in [security.md](security.md); this document is the review of them.

How to read the evidence: a control is marked **Met** only when a test in this repository fails if the control is removed, and the test is named. **Partly** says what is missing. **Not applicable** says why. Nothing is marked Met on the strength of a design intention.

## Scans

| Check | Tool | Result |
|---|---|---|
| Python dependencies (locked set, no development packages) | `pip-audit` | No known vulnerabilities, after upgrading `certifi` (advisory PYSEC-2026-4320) |
| Frontend dependencies | `npm audit` | No vulnerabilities, after upgrading `zod` (memory exhaustion on oversized collections) and `react-router-dom` (open redirect through a backslash, and an unsafe deserialization path used only by server rendering) |
| REA runner dependencies | `npm audit` | No vulnerabilities, after `npm audit fix` for `fast-uri` and an `overrides` entry that raises the `zod` that the pinned `rea-agents` 6.7.0 requires exactly |
| Secrets, working tree and all commits | `scripts/secrets_scan.py --history` | No findings. Keys, private key blocks, signed tokens, URLs with credentials and secrets assigned to names that say what they are are searched for; the development placeholders are allowed by name in the script, each with its reason |
| Files and Dockerfiles | Trivy `fs`, vulnerabilities, secrets and misconfiguration, HIGH and CRITICAL | 0 findings after fixing one Dockerfile (see Trivy) |
| Images | Trivy `image` (backend, frontend, REA runner) | 0 findings after fixing the base image of the frontend and removing npm from the REA runner (see Trivy) |
| Logs | `backend/tests/test_log_hygiene.py` | A whole session runs at DEBUG level; the password, session tokens, a provider key, the encryption key, the signing secret, email addresses and names of the migrated data, and phone and tax numbers never appear in a log or on the console |
| Hostile input | `backend/tests/test_security_probes.py` | Injection strings, wildcards, null characters, path traversal, oversized and unusual text in every search parameter; privilege escalation; extra fields in requests; forged and unsigned tokens; error bodies without internals |

## Findings of this pass

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | The database session of a request was committed after the response had been sent. A client that read right after a write (every page that reloads its list when a save succeeds) could read the old data, and a write could be acknowledged and then fail to commit without the client knowing. Found by the console tests: overriding a mapping did not show in the table that reloaded at once. | High (integrity: an acknowledged write could be unseen, or lost) | Fixed. Every route declares the session with `scope="function"`, so the commit happens before the response is sent and a failed commit becomes an error response. `test_read_your_writes.py` fails if the commit comes after the response starts, or if a route forgets the scope |
| 2 | A null character in any text parameter reached PostgreSQL, which refuses it, and the API answered 500 and logged a stack trace. | Medium (availability and log noise, no data exposure) | Fixed. The error handler turns a value the database cannot store into a 422 that does not echo the value. Covered for all search parameters by `test_security_probes.py` |
| 3 | `zod` below 4.6.6 (frontend and REA runner): memory exhaustion when validating very large collections. | High (advisory) | Fixed by upgrading. The forms never validate collections from untrusted input, so the practical exposure was low |
| 4 | `react-router` below 7.18: open redirect through a backslash in a link or navigation. The site already accepts only same-site paths after sign in. | Medium (advisory) | Fixed by upgrading to the 7.x line. Behaviour verified by the unit tests and the browser tests |
| 5 | `certifi` 2026.10.18: advisory PYSEC-2026-4320. | Low to medium | Fixed by upgrading to 2026.10.23 |
| 6 | A table that scrolls sideways contains visually hidden text; on a phone it made the whole page scroll sideways. | Low (usability, accessibility) | Fixed. Covered by a browser test on every console page at 360 and 768 px |

No finding of high severity is open.

## OWASP ASVS 4.0.3

### V1 Architecture, design and threat modelling

| Control | Status | Evidence |
|---|---|---|
| Components and trust boundaries are defined; the untrusted input (uploaded software and data) is isolated from the application | Met | Static analysis runs only in the `rea-runner` container (no network, read-only filesystem, no capabilities, resource limits). `test_hardening_and_seed.py::test_rea_runner_sandbox_is_locked_down`, `test_application_never_executes_uploaded_files` |
| Tenant isolation is enforced by the data layer, not by application filters | Met | PostgreSQL Row Level Security; the application role is not the owner and cannot bypass it. `test_rls_isolation.py` (14 tests) |
| Cross-organization reads go through narrow, reviewed interfaces | Met | `SECURITY DEFINER` functions with a fixed search path, EXECUTE revoked from PUBLIC. `test_analytics_api.py`, `test_rls_isolation.py::test_application_role_cannot_create_organizations_or_edit_plans` |
| A written threat model | Partly | The controls and known limits are written down in `security.md`; a formal STRIDE document is not part of this repository |

### V2 Authentication

| Control | Status | Evidence |
|---|---|---|
| Passwords hashed with a memory-hard function; policy of length and composition; the account name may not be part of it | Met | argon2id. `test_security_unit.py` |
| No user enumeration; a locked account refuses even the right password | Met | `test_auth_flow.py::test_unknown_user_and_wrong_password_are_indistinguishable`, `test_account_locks_after_repeated_failures` |
| Brute force limited | Met | Five failures lock the account; five sign ins a minute per address. `test_login_is_rate_limited` |
| Multi-factor authentication | Not applicable to the demonstration scope | Not implemented; listed under open items |
| Credential recovery | Not applicable | There is no self-service reset; users are created by an administrator |

### V3 Session management

| Control | Status | Evidence |
|---|---|---|
| Tokens in cookies that scripts cannot read, only over TLS, same-site | Met | `HttpOnly`, `Secure`, `SameSite=Lax`. `test_login_sets_hardened_cookies_and_returns_profile` |
| Short-lived access token, rotating refresh token, reuse detection, server-side revocation, hash stored | Met | `test_refresh_rotates_and_old_token_is_rejected`, `test_refresh_tokens_are_stored_hashed`, `test_logout_revokes_the_refresh_token`, `test_refresh_fails_after_membership_is_removed` |
| Forged, unsigned or expired tokens are rejected | Met | `test_expired_tampered_and_foreign_tokens_are_rejected`, `test_security_probes.py::test_a_forged_or_stripped_token_is_not_a_session` (altered claims, `alg: none`, empty) |
| Cross-site request forgery | Met | The `X-Requested-With` header on every state-changing request. `test_csrf_header_is_required_on_every_state_changing_request` |
| An access token cannot be revoked before it expires | Partly | 15 minutes; documented under known limits |

### V4 Access control

| Control | Status | Evidence |
|---|---|---|
| Every route requires authentication unless it is listed as public; roles are checked on the server | Met | `test_roles.py::test_every_api_route_is_guarded`, `test_api_surface.py` (every route against every role) |
| The interface hides what a role cannot use, and the server refuses the same requests | Met | Browser tests: `console.spec.ts` "pages are restricted by role" asserts the navigation, the message and the 403 from the API |
| No insecure direct object references across organizations | Met | `test_org_a_cannot_read_org_b_rows`, `test_direct_lookup_of_another_orgs_row_returns_nothing`, `test_console_api.py::test_the_overview_is_private_to_the_organization`, `test_switching_to_an_organization_you_do_not_belong_to_is_refused` |
| A user cannot raise their own privileges | Met | `test_security_probes.py::test_an_ordinary_user_cannot_make_themselves_an_administrator`, `test_extra_fields_in_a_request_cannot_move_data_to_another_organization` |
| A platform operator role separate from organization administrators | Partly | Revenue views are open to any organization administrator, with live customers masked. Documented in `security.md` |

### V5 Validation, sanitization and encoding

| Control | Status | Evidence |
|---|---|---|
| Input validated by type and range on the server | Met | Pydantic models on every body; query parameters constrained. `test_openapi.py`, `test_api_surface.py` |
| No SQL built from input | Met | SQLAlchemy parameters throughout. `test_security_probes.py::test_hostile_search_text_never_causes_an_internal_error` (six search parameters, twelve hostile strings), `test_an_injection_string_matches_nothing_instead_of_everything` |
| Output encoding; no HTML built from data | Met | React escapes; the chart tooltips escape every label (`transforms.test.ts` "escapes what goes into a tooltip", `builders.test.ts`); the strict Content-Security-Policy forbids inline script |
| Spreadsheet formula injection in exports | Met | Cells starting with `=`, `+`, `-`, `@` are prefixed. `test_jobs_flow.py::test_csv_cells_cannot_become_formulas`, `test_analytics_api.py::test_csv_text_cells_cannot_become_formulas` |
| Unexpected characters do not cause server errors | Met after this pass | Finding 2 |

### V6 Stored cryptography

| Control | Status | Evidence |
|---|---|---|
| Uploaded files encrypted at rest with an authenticated mode; the key is not in the code outside development | Met | AES-256-GCM, nonce per file, ciphertext bound to its path. `test_uploads.py::test_encryption_round_trip_and_tamper_detection`, `test_upload_never_leaves_plaintext_on_disk`; `test_production_refuses_development_secrets` |
| Secure random values for tokens | Met | `secrets` module; `test_security_unit.py` |
| Key rotation | Not implemented | Listed under open items |

### V7 Error handling and logging

| Control | Status | Evidence |
|---|---|---|
| Errors do not reveal internals | Met | `test_security_probes.py::test_errors_do_not_show_how_the_server_is_built` (no stack trace, path, driver or library name) |
| Sensitive data is not logged | Met | `test_log_hygiene.py` (two tests) |
| Security events are recorded and cannot be altered by the application | Met | Sign ins, uploads, rejected uploads, mapping approvals, loads, role changes, settings. `test_security_events_are_audited_and_scoped_to_the_organization`, `test_audit_log_is_append_only_and_scoped` |

### V8 Data protection

| Control | Status | Evidence |
|---|---|---|
| Personal data is identified and kept out of what leaves the system | Met | Profiling flags personal columns and keeps no values for them; the model's view of a column is a pattern class and statistics, and a payload guard refuses anything that looks like personal data or is too large. `test_profiling_columns.py`, `test_llm_router.py::test_the_guard_accepts_masked_values_and_refuses_oversized_payloads` |
| Retention | Met | A nightly purge removes the encrypted copy of uploads past the period set by an administrator. `test_console_api.py::test_the_nightly_purge_deletes_only_expired_files_of_finished_work` |
| Responses are not cached by the browser | Met | `Cache-Control: no-store`. `test_security_headers_and_cors_allowlist` |

### V9 Communication

| Control | Status | Evidence |
|---|---|---|
| HSTS, TLS only for cookies | Met | `test_security_headers_and_cors_allowlist` |
| TLS termination | Not applicable in this repository | Delivered by the reverse proxy of a deployment; the nginx configuration of the site is in `frontend/nginx.conf` |

### V10 Malicious code

| Control | Status | Evidence |
|---|---|---|
| Uploaded files are never executed or loaded | Met | `test_application_never_executes_uploaded_files`, `test_the_rea_adapter_never_uses_a_shell_and_never_runs_the_upload` |
| Dependencies are checked and locked | Met | Lock files for all three package sets; the scans above run in CI on every push and weekly |

### V11 Business logic

| Control | Status | Evidence |
|---|---|---|
| A load cannot partly succeed | Met | Every check must pass or the load is rolled back. `test_transform_load.py`, `docs/evaluation.md` (reconciliation) |
| Limits on use | Met | Token budgets per job and per day; upload size; rate limits. `test_llm_router.py`, `test_oversized_upload_is_rejected` |

### V12 Files and resources

| Control | Status | Evidence |
|---|---|---|
| File type is checked by content, size is capped, names are sanitized, paths cannot escape | Met | `test_uploads.py` (12 tests); `test_security_probes.py::test_a_sample_name_is_never_used_as_a_path` |
| Large inputs do not exhaust memory | Met | The data is read in chunks. `docs/load-check.md` (200,000 rows) |

### V13 API and web service

| Control | Status | Evidence |
|---|---|---|
| A single, documented API; methods restricted by role | Met | `test_api_surface.py` lists every route; `test_openapi.py` |
| Rate limiting and size limits | Met | slowapi; `test_login_is_rate_limited` |
| Server-sent events are authenticated and scoped | Met | `test_jobs_api.py`: every route of another organization's job, the event stream included, answers 404 |

### V14 Configuration

| Control | Status | Evidence |
|---|---|---|
| Security headers; a Content-Security-Policy | Met | `test_security_headers_and_cors_allowlist`; the browser tests run against the policy of `nginx.conf` |
| No development secrets in production | Met | The application refuses to start. `test_production_refuses_development_secrets` |
| Containers run as non-root with limited capabilities | Met | Each Dockerfile sets a numeric non-root `USER` (Trivy rule DS-0002 passes); the REA sandbox settings are asserted by `test_rea_runner_sandbox_is_locked_down`; the running frontend container answers `uid=101` |
| Dependencies and images are kept current | Partly | Scans run in CI and weekly; automatic update pull requests are not configured |

## Trivy

Trivy 0.76 with the vulnerability database of 2026-10-27, severities HIGH and CRITICAL, findings for which a fix exists (`--ignore-unfixed`; a finding that no release fixes yet is not reported). The three images were built from the Dockerfiles of this repository for the first time in this phase.

| Target | Result |
|---|---|
| Files: `backend/uv.lock`, `frontend/package-lock.json`, `rea-runner/package-lock.json` | 0 vulnerabilities |
| Files: secrets in the tracked and new files | 0 |
| Dockerfiles of the backend, the frontend and the REA runner | 0 misconfigurations after the change below |
| Image `legacylift-backend` (Debian 13.7, the Python packages, `uv`) | 0 |
| Image `legacylift-frontend` (Alpine 3.23.4) | 0 |
| Image `legacylift-rea-runner` (Debian 12.15, Node packages) | 0 |

What the first scan found, and what changed:

- `frontend/Dockerfile` declared no `USER` (rule DS-0002, high). The base image runs as the unprivileged `nginx` user already; the Dockerfile now says so (`USER 101:101`), and the running container answers `uid=101(nginx)`.
- The frontend image had 58 findings in operating system packages (`libcrypto3`, `libssl3`, `curl`, `libexpat`, `libpng`, `pcre2`, `zlib` and others) because the pinned `nginx-unprivileged:1.27-alpine` tag was built on Alpine 3.21.3. The base is now `1.29-alpine` (Alpine 3.23) and the build runs `apk upgrade`.
- The REA runner image had four findings (`brace-expansion`, `undici`), all inside npm, which the Node base image bundles. The runner starts `node` directly and needs npm only to install, so the image removes npm after installing. It still runs the analysis of both sample systems inside the sandbox (no network, read-only filesystem, non-root user, no capabilities).
- The frontend image, started with an `api` host name added (it is the proxy target in `nginx.conf`, so nginx refuses to start without one), serves the site and its security headers and answers the client side routes with the page shell. It was run with its default settings; the read-only and capability settings of the compose file were not exercised in this review.

## Open items and accepted risks

- Multi-factor authentication, self-service credential recovery and key rotation are not implemented.
- Access tokens cannot be revoked before they expire (15 minutes). Refresh tokens can, and are, at once.
- Revenue and invoice views of all organizations are readable by any organization administrator until a platform operator role exists. Live organizations are masked.
- The pinned `rea-agents` 6.7.0 asks for `zod` 4.4.3 exactly. The override to 4.6.7 is outside what its authors tested; the REA test suite (`test_discovery.py`, 32 tests) passes with it, and the runner is isolated and only ever reads an uploaded file as data. Revisit when a newer `rea-agents` is pinned.
- Findings for which no fixed release exists are not reported by the image scan and are not tracked here; the weekly scan in CI reports them when a fix appears.
- A formal threat model document and automatic dependency update pull requests are not part of this repository.
