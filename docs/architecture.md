# Architecture

This document records design decisions as each phase lands. It currently covers the data model basics
(Phase 1), the request security model (Phase 2, see `security.md`), discovery (Phase 4), profiling with data
quality (Phase 5), embedding based mapping (Phase 6) and the LLM router (Phase 7).

## Pipeline

Job states: `CREATED, UPLOADED, DISCOVERING, PROFILED, MAPPED, AWAITING_REVIEW, DRY_RUN, READY, LOADING,
RECONCILING, COMPLETED, FAILED, ROLLED_BACK`. Stages: discover, profile, map, review, transform and dry run,
load and reconcile. Discovery (below) moves a job from `UPLOADED` to `DISCOVERING` and stores its results;
profiling (next phase) moves it on to `PROFILED`.

## Discovery (Phase 4)

Discovery turns an uploaded legacy system into an internal description of how its data is stored:

| Input | Result model | Method |
|---|---|---|
| SQLite database | `SourceSchema`: tables, columns with declared types, indexes, views, triggers, row samples | REA SQLite inspection |
| .NET assembly plus its binary data file | `LayoutSpec`: header, table directory, per table the fields with name, offset, length, type, encoding, byte order | REA managed code analysis, validated against the data file |
| Binary data file alone | `LayoutSpec` with lower confidence | statistical inference |
| CSV | none (profiled directly in the next phase) | no REA |

### REA commands

The platform pins `rea-agents` **6.7.0** (`rea-runner/package.json` and `package-lock.json`). It needs Node.js
24.15 or newer (SQLite inspection uses `DatabaseSync.limits`). The commands were chosen from the REA
documentation (`docs/cli.md`, `docs/sqlite-databases.md`, `docs/managed-code-analysis.md` and the tool catalog
in `docs/mcp-contracts.md`) and by running them on the fixtures. Only these three are whitelisted by the runner:

| Command | Used for | Notes |
|---|---|---|
| `inspect-sqlite-database FILE --schema-scope database` | full schema: tables, columns, declared types, indexes, views, triggers, engine and encoding | one call per database |
| `inspect-sqlite-database FILE --table T --row-limit N` | bounded row sample of one ordinary table (default 5 rows) | one call per table, values come back tagged by type, integers as decimal strings |
| `inspect-managed-artifact FILE` | confirms a plain managed PE/CLI assembly (`classification.status` is `managed`) | single file hosts, IL2CPP and NativeAOT are not supported by REA's managed parser and are reported, not guessed |
| `inspect-managed-members FILE` | methods with decoded CIL anchors (calls, string literals, constants in order), member references, user strings | about 1 MB of JSON for the sample assembly |

Every command is invoked with `--json` and returns REA Evidence JSON. The runner never exposes runtime capture,
debugging, browser or device commands. The Evidence of every call is stored unchanged in `discovery_evidence`
together with the exact command, the REA version and the duration.

### Runner and isolation

`rea-runner/wrapper.mjs` accepts one command name, one absolute file path and a few whitelisted options, runs
the REA CLI without a shell and with a minimal environment, enforces a timeout (120 seconds) and an output
limit, and prints one JSON envelope: `{ok, command, rea_version, duration_ms, result | error}`. Errors have a
stable `kind` (`unknown_command`, `invalid_arguments`, `file_not_found`, `path_not_allowed`, `timeout`,
`output_too_large`, `analysis_failed`, `invalid_output`); REA's own error code is passed on when present.

The backend adapter (`app/discovery/rea_adapter.py`) starts the runner as a separate process and turns
envelopes into `ReaRun` results or `ReaError`s. Two deployments:

* **Local**: `node rea-runner/wrapper.mjs` (the default; run `npm ci` in `rea-runner` once).
* **Container**: `legacylift-rea-runner:6.7.0` (`docker build -t legacylift-rea-runner:6.7.0 rea-runner`), run with
  `--network none --read-only --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges`, CPU, memory and
  process limits, and a read only mount of a private folder holding the decrypted file. The command template and
  the in-container path are configured with `REA_COMMAND` and `REA_CONTAINER_WORKDIR` (`.env.example`). The tests
  exercise this mode when Docker and the image are present.

The uploaded file is decrypted into a private temporary folder (mode 0700) for the duration of one analysis and
removed afterwards. It is only ever an argument of the runner; nothing loads or executes it. The image installs
REA with `npm ci --ignore-scripts`, so the runtime capture dependencies are not set up at all. A test keeps the
adapter the only module allowed to start a process.

### SQLite discovery

`sqlite_discovery.py` converts the schema evidence and the row samples into a `SourceSchema`. Samples keep
REA's value typing (integers, text, null, blobs as hex). Row counts are not part of REA's output and are computed
by profiling.

### .NET layout discovery

A legacy application that writes fixed records does it with a sequence of `BinaryWriter.Write` calls, and REA
exposes that sequence in order. `cil_layout.py` reads it deterministically:

1. **Record writers**: methods with at least three `BinaryWriter.Write` calls that are called by another such
   method (the top level writer), in the order the top level method calls them.
2. **Fields**: each `Write(int)`, `Write(ushort)`, `Write(long)` and so on is one field of that type. A
   `Write(byte[])` is a fixed width text field when a helper that allocates, copies with `Math.Min` and pads
   was called before it, and the width is the constant passed to that helper. When the bytes come from
   `BinaryPrimitives.Write*BigEndian` or `IPAddress.HostToNetworkOrder` the field is a number with that byte
   order. The padding byte is the constant found in the helper.
3. **Encoding**: the code page passed to `Encoding.GetEncoding` (1252 becomes `windows-1252`), or a named
   encoding getter.
4. **Source column hints**: the string literal read before each write is the name of the input column the field
   is filled from. It is kept as `source_hint`, and used as the field name when no better name exists.
5. **Declared specification**: when the application keeps its record specification as data, the constructor
   calls in its type initialiser give ordered field names, types, sizes, semantics (such as `epoch_days`), encoding
   and byte order. Only structural facts are taken. A declared table is used only when it lines up with the
   writer (same count, types and sizes); the evidence records whether declared and derived values agree.
6. **Widths below nine**: REA does not list the small constants `ldc.i4.0` to `ldc.i4.8` as anchors. A single
   unknown width is resolved from the record size in the table directory, with lower confidence; more than one
   unknown becomes an ambiguity.

### File structure and validation (no trust in the analysis)

`binary_structure.py` looks for a table directory: entries of (table id, record size, record count, data
offset) whose offsets chain so that `header + sum(record_count * record_size) == file size`. Random data
essentially never satisfies this. It also names the fixed header fields from their values.

`layout_validation.py` then checks the recovered layout against the file: the size rule, the directory against
the layout, and the first 200 and last 50 records of every table. Fixed text must decode to printable text with
trailing padding only, fields marked as dates must hold plausible day or minute counts (or the unreadable date
marker), and key fields must count up from one. A layout passes when every check reaches 95 percent.

### Fallback

If validation fails, the assembly cannot be analysed, or only the data file was uploaded, `statistical.py`
infers a layout from the bytes alone: the directory, or failing that the record size that makes the byte
columns most regular, then byte class and entropy per offset to split text and integer fields and guess byte
order. Names are generated (`f012`), confidence is capped at 0.6, and a rejected analysis result is kept as
evidence (`result:rejected_layout`).

### Ambiguities and the LLM

What the extractor cannot settle (an unrecognised byte write, an unsupported `Write` overload, an unknown width)
becomes an `Ambiguity` with a compact evidence excerpt capped at 1,500 tokens. `discover_layout` accepts an
optional resolver, which is where the `interpret_layout` task of the LLM router (Phase 7) plugs in. Without a
resolver the ambiguity is only recorded and the validation decides whether the layout is still trustworthy. The
sample assembly has no ambiguities, so discovery costs zero tokens.

### Confidence

Per field: 0.95 when the declared and derived values agree, 0.85 when only the code analysis gives it, 0.7 when
the width came from the record size, scaled down by the share of sampled values that failed validation. An
unvalidated layout (no data file) is capped at 0.8. Statistical layouts are capped at 0.6.

### Persistence and API

Results are stored in `discovery_evidence` (one row per REA command with the raw JSON, one row per column or
field, the result row `result:schema` or `result:layout`) and `source_columns` (one row per column or field with
type, semantic type, confidence and its location in the file). Running discovery again for the same job replaces
the earlier rows. `jobs.data_source_id` points at the data file of a two part source.

| Route | Role | Purpose |
|---|---|---|
| `POST /api/v1/sources/{id}/discover` | analyst, admin | creates a job, runs discovery, optional `data_source_id` for the data file |
| `GET /api/v1/jobs/{id}/discovery` | any | the `SourceSchema` or `LayoutSpec` |
| `GET /api/v1/jobs/{id}/discovery/evidence?subject=PATIENT.LastNm` | any | what REA found for one field: instruction offsets, calls, declared specification |

Discovery runs inside the request for now (a few seconds). Phase 9 moves it to a background task.

### Measured results on the sample systems

| System | Method | True fields recovered (name, type, size) | Confidence |
|---|---|---|---|
| Commerce SQLite (tables, columns, indexes, view, trigger) | REA SQLite inspection | 100 percent | 1.0 |
| Clinic assembly plus data file (49 tables and fields) | CIL analysis, validated | 100 percent | 0.95 |
| Clinic data file alone | statistical inference | about 80 percent of field boundaries, type family and size (names are generated) | below 0.6 |

Scoring code: `app/evaluation/discovery_score.py`. The platform never reads the ground truth files.

### Limits

* REA's managed parser requires PE/CLI metadata. Single file hosts, IL2CPP and NativeAOT assemblies are not
  analysed (REA says so, and discovery falls back to the data file alone).
* Writers that use variable length encodings (`Write(string)`), loops over fields or reflection are reported as
  ambiguities, not interpreted.
* SQLite inspection reads a copy of the file and does not read WAL content that is not committed.

## Profiling and quality (Phase 5)

Profiling reads the source itself and describes every column and every data problem. It runs in two streaming passes
over the source in chunks of 50,000 rows (pandas), so memory does not grow with the number of rows.

| Source | Reader | Row identifier |
|---|---|---|
| SQLite | `sqlite3`, opened read only and immutable, `query_only` on and the schema trust off, so views and triggers of the file never run | `rowid` |
| Binary data file | numpy structured records decoded with the layout that discovery recovered (offset, length, type, byte order, code page) | zero based record index |
| Delimited text | `pandas.read_csv` with delimiter and encoding detection | zero based data line |

### Pass 1: the profile

Per column (`ColumnProfile`): inferred type (integer, decimal, boolean, date, timestamp, text), null and blank counts,
null rate, distinct count (exact up to 200,000 values, then a lower bound), unique ratio, minimum and maximum, length
statistics, the five most common patterns (digits become `9`, letters `A` or `a`, runs collapsed), the five most common
values, and:

* **Date format candidates**: for each known format (ISO, `MM/DD/YYYY`, `DD/MM/YYYY`, `YYYYMMDD`, `DD-Mon-YY`, and more)
  how many values look like it and how many are really valid dates. A column is a date when 90 percent of its values
  read as dates under some format. The accepted formats are kept, a day first and month first tie is reported as
  ambiguous, and the minimum and maximum come only from the accepted formats.
* **Numeric format candidates**: plain, currency (`$`, euro, pound), thousands separators, decimal comma, percent and
  accounting negatives, with the symbols seen, the quantiles (1, 5, 95 and 99 percent) and the share of negatives.
* **Integer dates** in binary files (days or minutes): the epoch is the first of 1970-01-01, 1900-01-01 and 1899-12-30
  under which the largest stored value is not in the future. `0` is a null marker and `-1` an unreadable date marker.
  Digit strings with leading zeros, or in a code, zip or phone column, stay text.

Then, using only the profile:

* **Semantic types** (`semantic.py`): email, phone, ssn, postal code, date, timestamp, date of birth, currency,
  identifier, reference, enumerated code, boolean, person name, address, organization name, free text, flags, and
  plain integer, decimal and text. Rules combine value signals (regular expressions over the distinct values), type,
  uniqueness, cardinality, the declared semantic of a binary field, and the words of the column name after
  abbreviation expansion (`vocabulary.py`: `cust_no` is customer number, `l_nm` is last name).
* **Keys and foreign keys** (`relations.py`): unique integer columns are keys. A column points at a key when at least
  90 percent of its values are found there (orphans do not prevent it) and it has at least 12 distinct values. When
  several keys qualify, the one the column covers most wins, which rejects small code columns that fit inside big key
  ranges by luck.
* **PII** (`pii.py`): rules on semantic type and name (email, phone, national id, person name, street address, date of
  birth, death date, postal code, payment card, ip address, geolocation). A PII column keeps no minimum, maximum, example
  or top value in the stored profile. `llm_view.py` is the only description an LLM may receive: normalized name, type,
  pattern class and at most five masked distinct samples, and for PII columns the pattern class only.

### Pass 2: quality (`quality.py`)

Rules are derived from the profile and run through a pandera `DataFrameSchema` per table (lazy validation, so every
failing row is reported). Rules can be replaced by explicit ones when a target schema supplies them.

| Issue | Rule |
|---|---|
| `required_null` | no value where at most 6 percent of rows are empty (empty text, NULL or the legacy null marker) |
| `invalid_date` | not a real date in any accepted format, an integer date outside 1800 to 2100, or the unreadable marker |
| `out_of_range` | outside the 5 to 95 percent range widened by ten times its spread, and below zero when the column is non negative |
| `invalid_number` | a value in a numeric column that is not a number |
| `orphan_fk` | a reference to a key that does not exist (null markers excluded) |
| `duplicate` | a row equal to an earlier one on every column except row numbers (needs two or more other columns) |
| `whitespace`, `case_anomaly` | warnings: leading or trailing spaces, or a casing style almost no other value in the column uses |

**Score** (0 to 100). Blocking rows are rows with at least one blocking issue (everything except the two warnings).
Warning rows are rows with only warnings.

    table score  = 100 * (1 - min(1, (blocking rows + 0.25 * warning rows) / rows))
    source score = row weighted mean of the table scores

The report also carries the complete issue counts, the issue list (capped at 200,000 entries), the derived rules and the
formula. The score computed here is the "quality before" figure of the reconciliation report.

### Persistence and API

Per column the results go to `source_columns` (inferred and semantic type, pattern class, null rate, distinct count,
`pii`, and the full profile under `profile.stats` next to the discovery facts). The profile and the quality report are
stored as `result:profile` and `result:quality`. Running profiling again replaces them. A job moves from `DISCOVERING`
to `PROFILED`; delimited text passes through `DISCOVERING` since it has nothing to discover.

| Route | Role | Purpose |
|---|---|---|
| `POST /api/v1/jobs/{id}/profile` | analyst, admin | profile a job after discovery |
| `POST /api/v1/sources/{id}/profile` | analyst, admin | create a job for a delimited text file and profile it |
| `GET /api/v1/jobs/{id}/profile` | any | the stored profile |
| `GET /api/v1/jobs/{id}/quality?type=&table=&limit=&offset=` | any | score, per table results, issue counts and a page of issues |

### Measured results on the sample systems

| System | Defects injected | Detected | Notes |
|---|---|---|---|
| Commerce SQLite | 590 | 99.8 percent | duplicates, null required, invalid dates, out of range and orphan keys all at 100 percent |
| Clinic binary file | 3,064 | 94 percent | the five core types at 100 percent; casing noise 76 percent |

Casing noise is found only in columns with a dominant style. Drug names in the clinic data are naturally lower case
first, so injected casing there cannot be told from the data. Precision is high for the core types (about 1.0 for
duplicates, dates and orphans), lower for the warnings, which also report genuine oddities. Scoring code:
`app/evaluation/quality_score.py`.

### Limits

* Rules are inferred until a target schema exists. A rarely empty optional column is reported as `required_null`.
* A table with fewer than two columns besides its row number has no duplicate check.
* SQLite files are parsed in the API process. Moving profiling into the sandbox is possible later because readers take
  a file path only.

## Embedding and mapping (Phase 6)

Mapping decides, for every source column, which target field it feeds. Rules and embeddings do the work; an LLM is only
asked about what they cannot settle.

### Embeddings

`BAAI/bge-small-en-v1.5` (MIT, 384 dimensions) runs on the CPU through `sentence-transformers`
(`app/mapping/embedding.py`). The model is loaded once per process, on first use, and every call encodes a batch of 32.
Vectors are normalised, so cosine similarity is a dot product and pgvector's cosine distance is `1 - similarity`.
`backend/Dockerfile` installs the CPU build of PyTorch and runs `python -m app.mapping.download_model` while building,
so the image carries the model and a worker starts without downloading anything.

What is embedded (`app/mapping/texts.py`):

* **Source column**: `table <expanded table name> | column <expanded column name> | type <semantic type in words> |
  refers to <parent table> | <note found next to a binary field> | examples a, b, c`. Names are expanded with the
  abbreviation dictionary (`cust_no` is customer number, `l_nm` last name). Examples come from the same metadata view an
  LLM may see: at most three masked values and none for a PII column, so stored texts and vectors hold no PII. Queries are
  prefixed with the instruction the bge models are trained with.
* **Target field**: `table | field | type | description | also called <synonyms>`.

Target schemas are loaded and embedded by `python -m app.mapping.seed_targets` (built in rows, `org_id` NULL, stored in
`target_fields.embedding`, HNSW cosine index). Source column vectors are stored in `source_columns.embedding`.

### The decision for each column (`decide.py`, `service.py`)

1. **Memory**: the nearest approved mapping of the organization (pgvector on `mapping_memory`) is reused when its cosine
   distance is under `memory_distance`, its field belongs to the chosen schema and the types are compatible. Zero tokens.
2. **Type gate** (`compat.py`): fields whose type cannot hold the column are removed before ranking. A date never feeds
   a numeric field, a reference only feeds references or integers, a key feeds the legacy identifier of a record.
3. **Target table**: the target table whose compatible fields fit the source table's columns best (mean of the best
   similarity per column) is chosen for the whole table.
4. **One to one matching** inside it, best pairs first, never below `reject_similarity`. A field belongs to the column
   that fits it best, which is what keeps `inv_no` on `orders.source_key` and sends `dtl_no` elsewhere.
5. **Accept or ask**: a match is accepted when its similarity reaches `auto_similarity` and its margin over the best field
   that is still free reaches `auto_margin`. Otherwise the column goes to the LLM step with its top three candidates.
   A column left without a field is unmapped.
6. **LLM step** (`llm_hook.py`, served by the router of Phase 7): one batched call per 12 columns and under 5,000 tokens, metadata only,
   the answer must be one of the offered candidates or none. A confident answer is accepted, anything under
   `llm_min_confidence` goes to human review.

`confidence = w * similarity + (1 - w) * min(1, margin / margin_full)` with `w` = 0.5.
Each `mappings` row stores the method (`memory`, `embedding`, `llm`, `human`), the decision (`memory`, `auto`,
`needs_llm`, `review`, `unmapped`), similarity, margin, confidence, the top candidates and the reason.

### Approval and memory

`approve_mapping` marks a mapping approved (or overridden with another field, which makes the method `human`) and
upserts a `mapping_memory` row keyed by a signature of the column (expanded table and column name, semantic type, type,
pattern class): its text, its embedding and an approval counter. A later job with the same legacy system finds the
approved mappings in memory and spends no tokens on them. Memory is tenant data (row level security), so another
organization starts with none. The approval routes themselves arrive with the API phase.

### Thresholds (`config/mapping_thresholds.json`)

Nothing is hard coded: `reject_similarity`, `auto_similarity`, `auto_margin`, `table_min_score`, `memory_distance`,
`llm_min_confidence` and the confidence weights are read from the JSON file (or the file named by
`MAPPING_THRESHOLDS_FILE`). `python -m app.mapping.tuning` searches a grid on a tuning set and writes the file, together
with how it was chosen and what it scored. The held out tables are never seen during the search.

| Set | Source tables | Columns | Correct without the LLM |
|---|---|---|---|
| Tuning | all of commerce, clinic `PATIENT`, `ENCNTR` | 53 | 96.2 percent |
| Held out | clinic `COND`, `MEDS`, `OBS` | 23 | 91.3 percent |

The search maximises the columns mapped correctly and counts a wrong match that was accepted without help twice as bad as
one sent to the LLM.

### Measured results on the sample systems

76 source columns (32 commerce, 44 clinic) scored against `ground_truth.json` (`app/evaluation/mapping_score.py`).
Eight columns have no counterpart in the target schema and are correct when left unmapped.

| Measure | Result |
|---|---|
| Mapped correctly without any LLM call | 72 of 76, **94.7 percent** (target 85 percent) |
| Wrong, accepted without help | 3: `PROD_MST.prod_nm` and `comp_nm` compete for `products.name`, `OBS.ObsUnit` and `ObsVal` swap |
| Waiting for the LLM | 1 (`OBS.ObsVal`) |
| Ceiling after the LLM step (true field among the candidates, or no field) | 73 of 76, **96.1 percent** (target 95 percent) |
| After the LLM step with a deterministic stand in resolver | 96.1 percent, one batched call on the clinic data, none on commerce |

The 95 percent figure after the LLM step is a ceiling plus a stand in, not a measurement of a real model: the router of Phase 7
is built and tested with scripted providers, but no real provider has been called yet. The tests run the real hand over (batching, token cap, answer validation) with the stand
in. First run versus repeat run: after approval, a second job on the same legacy system takes its approved columns from
memory with no tokens.

### Limits

* Matching uses meaning, not data: two columns with similar names and types in one table can swap (see the three errors).
  The review step exists for these.
* A target schema with thousands of fields would need a limit on the pgvector query (now every field of the schema is
  retrieved, which is cheap at this size).
* Transform rules for the mapped fields (casts, date formats, code maps) are produced in the transform phase from the
  profile and the mapping.

## LLM router and token budget (Phase 7)

An LLM is the last resort. The router (`app/llm/`) sits in front of two providers and enforces the plan's token rules in
code. Today two tasks use it: `resolve_mappings` (columns the mapping rules could not settle) and `interpret_layout` (writes
of bytes in a binary file whose type or width the code analysis could not determine). Without a provider key everything
still works; the unsettled items wait for human review.

### Interface and providers

`LLMRouter.complete_json(task, payload, context)` returns a validated Pydantic object. Providers implement one method:

| Provider | Endpoint | Output control |
|---|---|---|
| `GroqProvider` | OpenAI compatible chat completions, model `openai/gpt-oss-20b` | `response_format` of type `json_schema` in strict mode, `reasoning_effort` low, `temperature` 0, `max_completion_tokens` 800 (reasoning tokens count against it) |
| `GeminiProvider` | REST `generateContent`, model from `GEMINI_MODEL` | `responseMimeType` JSON with a `responseSchema` (OpenAPI subset), `temperature` 0, `maxOutputTokens` 800, key in the `x-goog-api-key` header |

The default Gemini model is a Flash-Lite class model. Free models and quotas change often and published figures disagree, so
confirm the current free models in Google AI Studio and set `GEMINI_MODEL` before relying on it. Keys come only from
`GROQ_API_KEY` and `GEMINI_API_KEY`; they are `SecretStr` values, are never stored in the database, never returned by an API
route, and appear in no log or error message.

### What happens to a request

1. **Payload guard** (`guard.py`): refuses fields such as `rows` or `api_key`, anything that looks like an email address, social
   security number, card number, phone number or a long digit run (digits the profile view already masked as `9999` pass), and
   payloads over 5,000 tokens. The payload builders already send metadata only; the guard does not trust them.
2. **Cache** (`llm_cache`, per organization): key is the SHA-256 of the task, the SHA-256 of the prompt file and the normalized
   payload (sorted keys, floats rounded to two places). A hit costs zero tokens, is logged as `cache_hit`, and is re-validated
   against the current request. An exhausted budget still serves cached answers.
3. **Budgets**, checked against a pessimistic estimate (request plus the 800 token output cap) before anything is sent:
   the job's token budget (`jobs.token_budget`, 6,000 by default), the job's call limit (2 by default, counting answered calls)
   and the organization's daily token budget (100,000 by default, summed from `llm_calls`). A request that would exceed a
   budget is not sent. Actual usage is charged to the job after every answered request, including wasted and repair requests,
   so a job can overshoot only by the error of one estimate.
4. **Providers in order** (the organization's setting, default Groq then Gemini). One is skipped when it has no key, its
   circuit breaker is open, or its local rate limit bucket has no room.
5. **Retries and failover**: 429, 5xx and timeouts are retried (two tries per provider) with exponential backoff and jitter,
   waiting at least as long as `Retry-After` when that is 10 seconds or less. A longer wait, or running out of tries, fails over
   to the next provider at once. Other 4xx answers (bad key, bad request) are not retried.
6. **Validation and repair**: the answer must be JSON that matches the task schema (fenced JSON is accepted) and pass the
   task's own checks: one answer per id, and every chosen field must be one of the candidates offered for that column. If not,
   one repair request shows the model what was wrong. If that fails, or the output was cut off at the cap, the next provider is
   tried.
7. **Record**: every request to a provider writes an `llm_calls` row (provider, model, task, tokens in and out, cached tokens,
   latency, status, cache hit, job, error code, prompt version). A good answer is cached and its tokens added to the job.
   Records are written in their own transactions, so they survive a failed request.

If every provider fails the router raises `AllProvidersFailed`; the adapters turn that into "no answer" and the mapping decisions
go to review. The job is never lost.

### Rate limits, circuit breaker, backoff (`resilience.py`)

* **Token bucket per provider**, seeded from the configured limits (Groq 8,000 tokens and 30 requests per minute, Gemini from
  settings) and replaced by the provider's own figures whenever a response carries them (Groq
  `x-ratelimit-remaining-tokens` and `-requests`). Cached prompt tokens do not count against it. A 429 empties the bucket until
  the provider's wait is over.
* **Circuit breaker**: three consecutive failures open it for 60 seconds, then one trial request is let through (half open); a
  success closes it, a failure opens it again.
* **Backoff**: `min(8 s, 0.5 s * 2^attempt)` with a random delay between half of that and all of it.

All three take an injectable clock, so the tests need no real waiting.

### Prompts (`app/llm/prompts/`)

Versioned files, `<task>.v1.txt`: a constant system prompt (about 300 tokens, no data) that always comes first so provider
prompt caching applies, followed by the variable JSON payload as the user message. The prompt file's SHA-256 is part of the
cache key, so an edited prompt can never serve answers produced by an older one. Output schemas are Pydantic models turned into
strict JSON schemas (every property required, no extra properties, optional values as a union with null).

### Token minimisation, as built

| Rule of the plan | Where it is enforced |
|---|---|
| Deterministic first, LLM only for what rules cannot decide | mapping rules and memory run first; only `needs_llm` columns and open layout ambiguities are sent |
| At most 2 calls and under 6,000 tokens per job, enforced in code and recorded | job call limit and token budget, `llm_calls`, `jobs.tokens_used` |
| One batched call per task, about 12 columns, every request under 5,000 tokens | `llm_hook.build_requests`, the payload guard; layout ambiguities all go in one request |
| Metadata only, never rows, never PII values | profile views, the payload guard |
| Constant prefix first | the system message is the prompt file, nothing else |
| Temperature 0, low reasoning effort, 800 token cap, strict JSON, one repair | provider request builders, router |
| Cache every answer, reuse approved mappings | `llm_cache`, mapping memory |

Free tier providers may use prompts to improve their products. Only synthetic or masked data is sent, and that statement belongs
in the project README.

### Settings and admin hooks

Organization settings (`org_settings`, row level security like every tenant table) hold the provider order and the three
budgets. New jobs take their token budget from the organization's setting at creation.

| Route (admin only) | Purpose |
|---|---|
| `GET /api/v1/admin/llm-settings` | provider order, budgets, tokens used today, per provider: model, key configured, circuit state, rate limit room |
| `PUT /api/v1/admin/llm-settings` | change them (validated, audited as `settings.change` with old and new values) |
| `POST /api/v1/admin/llm-settings/test-connection` | one tiny request to one provider, no failover, no cache, counted in the daily budget |
| `GET /api/v1/admin/llm-usage?days=7` | tokens, calls, cache hits and failed attempts per day and provider, and the tokens the cache saved |

### Testing without a provider

CI never calls a real provider: a fixture makes any real HTTP request from a test fail. Provider adapters are tested against
recorded response files in `tests/recorded/llm/` (Groq and Gemini success, 429 with and without a long wait, 5xx, bad key,
invalid JSON, wrong target, output cut off, blocked prompt) through a scripted `httpx` transport. The recorded files follow
the providers' documented response shapes and are meant to be replaced by real captures when keys are at hand. The router is
tested with scripted fake providers and a manual clock. `tests/test_llm_live.py` runs one tiny synthetic request per configured
provider when `RUN_LIVE_LLM=1`.

### Results against the acceptance checks

| Check | Result |
|---|---|
| A simulated 429 and timeout trigger failover without losing the request | tested: Groq 429 then timeout, answer from Gemini; and with every provider down the mapping job still completes, the column goes to review |
| Budgets stop a runaway job | tested: a loop of distinct requests stops at the job token budget (never exceeded) or at the call limit, and the refused request never reaches a provider |
| A repeated run of the same fixture uses zero tokens | tested with the clinic fixture: its one unsettled column goes out in one request, a second mapping run adds no tokens and the provider is not asked again (cache hit logged); commerce has no unsettled column and sends nothing |
| A first run stays under 6,000 tokens | the clinic fixture sends one request of about 430 estimated input tokens (264 for the constant prompt, 168 for the column) with an output cap of 800; a worst case of 24 unsettled columns (two batched requests) stays under 6,000 in the test |

### Limits

* No tokenizer is installed: token counts before a call are estimates (characters divided by four, plus 10 percent). Recorded
  usage always comes from the provider.
* The Groq output cap includes reasoning tokens. With low reasoning effort 800 is enough for 12 answers, and a cut off answer is
  treated as a schema failure and repaired.
* The circuit breaker and rate limit buckets are per process. Several workers each keep their own.
