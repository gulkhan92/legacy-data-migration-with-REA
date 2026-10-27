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

## Transform, load and reconciliation (Phase 8)

After mappings are approved, the job is moved into the warehouse in four steps, each of which can be inspected and none of which changes the warehouse until the last.

### Rules are data, not code (`app/transform/dsl.py`)

A transform rule is a list of steps such as `{"op": "parse_date", "formats": [...]}`. Only the operations in the `OPS` registry exist (text, trim, case, integer and decimal casts, cents to decimal, dates and timestamps, epoch days and minutes, phone numbers, code maps, split, concat, default, regex extract, bit flag, boolean, not-null). Parameters are validated by pydantic models that forbid extra keys, regular expressions are checked for nested quantifiers and backreferences, and nothing is ever passed to `eval`. A rule that is not valid is rejected with a message that names the step. Failed values carry a kind (`invalid_date`, `invalid_number`, `invalid_code`, `invalid_value`) that becomes the quarantine reason.

### Generation (`generate.py`, `rules.py`)

Rules are derived from the column profile and the approved mapping, never from an LLM: the dominant date format, thousands and decimal separators, the null markers of numeric references, flag bits, title-case anomalies. A rule a person wrote is kept on regeneration. Numeric codes whose meaning is not in the data (an order status of 1, 2, 3) produce a rule in state `needs_input`; an analyst supplies the code map (`PUT .../rules/{id}/code-map`) and the dry run and load stay blocked until every required rule is ready. A derived field (`patients.is_deceased` from the death date) is generated from another column of the same table.

### Plan and dry run (`plan.py`, `dry_run.py`)

`build_plan` turns rules into a per-table plan ordered parents first and reports issues that block a load: a target field with no rule, a reference whose parent table is not part of the load, a rule that is invalid or not ready, a table fed from two source tables. The dry run applies the plan to the first 500 rows of every table and returns before and after rows (clean and failing), the quarantine reasons that would occur, and per rule the null, failure and out-of-range counts. A rule that fails on 50 percent or more of the sampled values blocks the load (it is probably wrong), 20 percent warns.

### Load (`load.py`, `service.py`)

1. The source is streamed in chunks, transformed by the same code as the dry run, and written with `COPY` into a temporary staging table per target table, with the reason for rows that failed a rule. The same pass accumulates the numbers the reconciliation needs (row ids, key set, null counts, numeric sums, a deterministic row sample).
2. Inside SQL, the staged rows are checked: references to a parent that does not exist or was quarantined (`orphan_fk`), the same key and content seen again (`duplicate`), the same content under another key (`duplicate`, only when the table has at least five business fields and fewer than 10 percent of rows repeat, otherwise plain names and prices would be mistaken for copies), and the same key with different content (`duplicate_key`).
3. Valid rows are inserted into the warehouse with a `CAST` to the column type and references resolved to parent ids. Rejected rows go to `quarantined_rows` with the source row number, the reason, a human readable detail and the raw source row as JSON.
4. Everything runs in one savepoint inside the job's transaction. If a reconciliation check fails the savepoint is rolled back, the job becomes FAILED and a failed report is stored. A unique `(job_id, source_key)` index on every warehouse table makes a second load of a job impossible, and the service also refuses it explicitly.

### Reconciliation (`reconcile.py`)

The expected values come from the staging pass, the actual values are read back from the warehouse, so a bug in the SQL move cannot hide itself. Per table: row accounting (source = staged = loaded + quarantined), every quarantined row has a reason, distinct keys, null counts per field, sums of numeric fields (exact decimals) and a sample of rows compared field by field. Quality is scored before (from the profile) and after (profiling the warehouse tables). The report is stored per job and served by `GET .../reconciliation`.

### Rollback

`POST .../rollback` deletes everything a job loaded, by job id, plus its quarantine rows and report, and moves the job to ROLLED_BACK. Warehouse rows are tagged with the job id, so nothing else is touched.

### Measured results on the sample systems

Every injected defect on a mapped column is found with the expected reason: all null-required, invalid-date and out-of-range defects (the clinic has a few extra out-of-range values that are genuine outliers beyond the learned bounds: 2 in encounters, 17 in medications), and 36 of 45, 70 of 70, 8 of 8 duplicates in commerce order items, products and orders (the other 9 copies were of rows that were quarantined for another reason and carry that reason). Rows are then either loaded or quarantined: 5,515 loaded and 823 quarantined in commerce. Quality rises from 91.3 to 99.5 on commerce.

### Limits

- The meaning of numeric codes cannot be inferred and always needs a person.
- Case normalisation of title-dominant columns is conservative: only values that look wrong (all caps, all lower, inverted) are rewritten, so McDonald stays.
- Range bounds are learned from the data, so genuinely extreme but valid values (a very expensive medication) are quarantined as out_of_range and need a reviewer to release or widen the bound.
- A defect on a source column that has no target field (for example patient healthcare expenses) is not seen, because the column is not loaded.
- Quarantine includes children of quarantined parents (`orphan_fk`), which is intended but makes the quarantine larger than the number of injected defects.
- The mapping engine can still pair a column wrongly (commerce `comp_nm` with `products.name`); a reviewer rejects or overrides it with `POST .../mappings/{id}/reject` or `/approve`.

## Job orchestration and API (Phase 9)

Every step of a migration can run in the background, report progress while it does, and be repeated safely. The synchronous endpoints of the earlier phases still work; the endpoints below are what a UI uses.

### Stages and workers (`app/jobs/`)

A job moves through six stages: `discover`, `profile`, `map`, `rules` (approve what the engine settled, generate the rules), `dry_run` and `load`. `POST /jobs/{id}/run` queues everything up to the review of the rules (`discover, profile, map, rules`) or the stages named in the request; `POST /jobs/{id}/stages/{stage}` queues one. Both answer `202` with the task id. The stages of one request are a Celery chain, so a later stage starts only when the earlier one returned.

Workers are Celery workers with Redis as the broker (`docker compose --profile app up`, or `celery -A app.jobs.celery_app:celery_app worker -Q legacylift`). Results are not stored: the state of a job lives in `jobs` and `job_events`. Settings: `REDIS_URL`, `JOBS_EXECUTION` (`celery`, or `eager` to run stages inside the API process for local work and tests; refused outside local use), retries, time limits.

`stages.run_stage` knows nothing about Celery, which keeps the rules testable without a broker. It guarantees:

- **Idempotent**. A stage whose work is already behind the job (profile on a mapped job) is skipped and says why, so a redelivered or doubled task does nothing. A load of a completed job is skipped, and the warehouse also refuses a second load by a unique index.
- **One stage per job at a time**. A PostgreSQL advisory lock, held on its own connection for the length of the stage, keeps two workers from touching the same job. The second task retries a few times and then gives up without failing the job.
- **Blocked is not failed**. A stage that needs a person (no target schema, a rule that needs a code map, a dry run that finds blocking problems) ends as `blocked`, the job stays where it is, and the reason is in an event.
- **Transient failures are retried**. A database or network error rolls the stage back and the task retries with exponential backoff (5 s, 10 s, 20 s, up to 3 retries). Only when the retries run out is the job marked FAILED.
- **Other failures fail the job once**. The stage is rolled back, the job goes to FAILED, and the event names the stage and the exception class, never the message (which may hold paths or data).
- **Acknowledged late**. A worker that dies mid-stage hands its task to another one; because of the points above that is safe.

### Events and progress

Every state change writes a `job_events` row (the state machine does it). A stage also writes progress notes (`Stage profile started`, `Stage profile finished`, `Stage rules is waiting: ...`) with the same from and to state, in their own transactions so they are visible while the stage runs. `GET /jobs/{id}/events?after=` lists them with a cursor.

`GET /jobs/{id}/events/stream` is a Server-Sent Events stream: it replays the history (or what came after `Last-Event-ID` or `?after=`), then polls for new events every half second, sends a keep-alive comment every 15 s, sends an `end` event when the job reaches a final state, and closes by itself after five minutes with a `timeout` event so that a browser `EventSource` reconnects and resumes. The state is read before the events, so when it is final every event up to it has been sent. Each poll is a short tenant scoped transaction, so a stream never holds a connection while it waits.

### Cancel

`POST /jobs/{id}/cancel` marks a job that is not finished as FAILED with the message "Cancelled by a user"; stages that have not started skip it, and it can still be rolled back. A stage that is running holds the job row, is not interrupted, and the call answers `409` ("cancel again when it has finished") instead of waiting.

### The REST surface

All routes are under `/api/v1`, documented at `/docs` and `/openapi.json`, and classified by role in `tests/test_api_surface.py`, which fails when a route is added without a classification.

| Area | Routes |
| --- | --- |
| auth | `login`, `refresh`, `logout`, `me` |
| sources | list, get, upload, delete, `samples` (list the bundled systems) and `samples/{name}` (register one; registering twice returns the same sources) |
| jobs | create, list (filter by status and source), get (with `next_stage`), `run`, `stages/{stage}`, `cancel`, `events`, `events/stream`; `rollback` (synchronous) |
| mappings | list, `PATCH` (change target or hint; `null` rejects), approve, reject, `approve-decided`, `bulk-approve` |
| transforms | rules list, update, code map, `transforms/preview` (try steps on a sample, nothing saved) |
| reports | `reconciliation`, `quarantine` (paginated, filtered), `quarantine.csv` |
| schemas | target schemas and their fields |
| admin | users (list, create, change role, remove), audit log, leads, LLM settings and usage |
| public | `metrics` (aggregates of the daily snapshot, no revenue) and `leads` (the contact form) |

Conventions. List endpoints that are new return `{items, total, limit, offset}` with `limit` at most 200; the older list endpoints keep returning bare arrays. Errors always look like `{"detail": ..., "error": {"code", "message", "status", "fields"?}}`: `detail` is what FastAPI sends, `error.code` is stable (`not_found`, `forbidden`, `conflict`, `validation_error`, ...), and validation errors list the offending fields without echoing what was submitted. State changes need the `X-Requested-With: LegacyLift` header, including the public form.

### Details worth knowing

- **Samples**. The bundled systems (commerce, ClinicDesk) are registered as sources of the organization, encrypted like an upload and marked `is_sample`. They exist only where the repository's `datasets/fixtures` are installed; the list says which are available.
- **Deleting a source** is refused while a job uses it, removes the encrypted file, and is audited.
- **Users**. Creating a user sets a first password that must pass the password policy; an address that already has an account anywhere gets the same refusal, so the endpoint cannot be used to find out who has one. Removing a user deletes the membership and revokes that user's refresh tokens for this organization; the account stays because it may belong to others. The last admin and yourself cannot be removed or demoted.
- **Leads**. The form accepts a contact or demo request, drops submissions that fill the hidden `website` field without telling the sender, and is limited to five per minute per address.
- **Quarantine export** streams from its own short session and writes cells that start with `=`, `+`, `-`, `@`, tab or carriage return with a leading quote, so an exported file cannot run a formula when opened in a spreadsheet.
- **Mapping edits** are accepted while a job is MAPPED or AWAITING_REVIEW; in the second state the rules must be generated again (the response says so).

### Limits

- Access tokens stay valid for up to 15 minutes after a user is removed (they are stateless); refresh stops at once.
- A running stage cannot be interrupted; cancel works between stages.
- Task payloads carry the organization and user ids and are trusted: whoever can write to the broker can run stages for any organization, so Redis must not be reachable from outside (the compose file binds it to localhost and sets no external port).
- The SSE stream polls the database. That is simple and robust through proxies, and costs one small query per open stream every half second; a pub/sub channel would replace it at much larger scale.
- The public metrics come from the platform wide daily snapshot, which Phase 11 fills with simulated organizations; until then they are zeros.

## Billing, metrics and analytics (Phase 10)

There are no real customers, so the revenue figures are simulated. The logic behind them is not: every number is computed from tables, and every migration that completes in the application writes real usage that flows into the next invoice and the next metrics snapshot. Rows created for the demonstration carry `is_simulated = true`, every analytics answer says whether it includes any, and the footnote "Revenue figures are simulated for demonstration" travels with them.

### Plans (illustrative)

| Plan | Base fee | Included records / month | Overage |
| --- | --- | --- | --- |
| Starter | free | 100,000 | $0.50 per 1,000 records |
| Professional | $499 | 2,000,000 | $0.20 per 1,000 records |
| Enterprise | $2,500 | 25,000,000 | $0.08 per 1,000 records |

The Starter overage rate is not in the plan text; it is an assumption so that a free account cannot load without limit. The plans are rows in `plans` (inserted by the migration), served at `GET /public/plans`, and priced by one function (`app/billing/plans.py`): overage = (records above the allowance / 1,000) x rate, rounded half up to whole cents once per invoice line. `GET /public/pricing/estimate` is the same function for the pricing page calculator.

### Metering

A completed load writes one `usage_events` row (records loaded, tokens used by the job, processing time of the load) in the same transaction as the job's completion. A unique index on `job_id` makes it once per job however often it is attempted. An organization that has no subscription gets the free Starter plan from its first usage. A rolled back load removes its usage event, unless the month it belongs to has already been invoiced (then the invoice stands).

### Invoices

`python -m app.billing.cli invoices --month 2026-09` (and the `monthly_invoices` Celery beat task on the first of every month) invoices the month that has ended. A subscription is billed for a month when it is **active on the month's last day**: a base line with the fee of that plan, and an overage line for the records loaded during the month. Billing on the month end state is what makes the books balance: the base lines of a month add up to the MRR of its last day. One invoice per subscription and period is enforced by a unique index, so a second run changes nothing; a bill of zero (a free plan with no overage) is not issued; one organization failing does not stop the others. Statuses are `open` at issue, `overdue` when the due date (30 days) has passed (marked by the same run), `paid` or `void` when an admin says so (`PATCH /billing/invoices/{id}`). There is no payment processor.

### The snapshot

Every night the `nightly_snapshot` beat task (or `python -m app.billing.cli snapshot`) writes one `daily_metrics` row for yesterday and refreshes today's. The row is computed inside the database by `platform_day_metrics(date)`, a function that aggregates over all organizations (see Cross organization reads below). Running a day again replaces its row, which is how the invoice run keeps revenue current: it re-takes the snapshot of the period's last day after issuing the invoices.

### Metric definitions

- **MRR** is the sum of the base fees of the subscriptions that are active at the end of the day. Usage revenue is tracked separately and is not in MRR.
- **New MRR**: the fee of an organization that had no subscription the day before. **Expansion**: the increase of an organization that keeps a subscription (Starter to a paid plan counts). **Contraction**: a decrease. **Churned MRR**: the fee of a subscription that ended without a successor.
- **Net new MRR = new + expansion - contraction - churned.** The MRR at the end of a period is the MRR at its start plus net new MRR; every revenue report returns `checks.mrr_movements_add_up`.
- **Gross churn rate** = churned MRR of the period / MRR at the start of the period (null when the start MRR is zero).
- **Net revenue retention (trailing 12 months)** = the MRR today of the organizations that paid something twelve months ago / what those organizations paid then. It includes their expansion and contraction and excludes organizations that joined since. Null when nobody paid a year earlier.
- **ARPA** = MRR / paying accounts (accounts with a fee above zero) at the end of the period. **ARR** = 12 x MRR.
- **Subscription revenue** and **usage revenue** are the base and overage lines of invoices that are not void, recognised on the last day of the period they bill. For any range their sum equals the invoice totals of that range; `checks.revenue_matches_invoices` compares them on every request, `checks.complete` says whether every day of the range has a snapshot.
- **Success rate** = completed jobs / finished jobs of the day; **average duration** and **quality score** are daily averages over the usage events and reconciliation reports. Over a range they are averaged over the days, weighted by the jobs of each day.
- **Tokens by provider** count non-cached successful calls (input plus output). **Failovers** count provider calls that were rate limited, timed out or failed. **Cache hits** count answers served from the cache.

In a bucket (day, week, month) stocks (MRR, accounts, retention) take the value on the bucket's last day, flows are summed, ratios are weighted by jobs. Weeks start on Monday.

### The simulated history

`python -m app.billing.cli history --reset` (or `uv run --project backend python scripts/seed_billing_history.py`) writes 24 months of history (seed 42, deterministic, about 15 seconds): 140 fictional organizations across five industries and four regions, an S curve of signups, a plan mix that depends on the industry, monthly churn (about 4% on Starter, 2% on Professional, 0.7% on Enterprise, higher for accounts that barely use the product), expansion when usage stays above the allowance and some contraction when a large plan is barely used, Q4 seasonality (usage up about 25% in October to December, down in winter), about 6,400 jobs with plausible sizes, durations, token use (60% of runs use none), provider failovers, failures and quality, then invoices produced by the real invoice code and payments (most on time, some late, a few never, a few voided). Every number is a consequence of those rows, so the totals reconcile by construction and the tests check that they do.

### Analytics endpoints

All under `/api/v1/analytics`, with `from`, `to` (default: the last 90 days of data), `granularity` (`day`, `week`, `month`) and, on the series endpoints, `compare=true` for the previous period of the same length and the change. A range is limited to 800 days. Answers are cached for 60 seconds (Redis; `CACHE_BACKEND=memory` for local work, `off` to disable). If Redis is down the answer is computed without it.

| Endpoint | Who | Content |
| --- | --- | --- |
| `GET /revenue` | admin | series, KPIs, previous period, change, reconciliation checks |
| `GET /revenue/breakdown?dimension=plan\|industry\|region` | admin | MRR (stock) and usage revenue (flow) by key and bucket, and the current split |
| `GET /customers/top` | admin | the customers with the most revenue in 12 months, with a 12 month trend |
| `GET /invoices` | admin | invoices of all customers, filtered (status, customer, period) and paginated |
| `GET /operations` | analyst, admin | jobs, success rate, duration, records, tokens by provider, failovers, cache hits, quality |
| `GET /revenue.csv`, `/operations.csv`, `/invoices.csv` | as above | the same data as CSV, text cells that start with a formula character are quoted |

An organization's own billing is under `/api/v1/billing`: the plan with this month's usage meter and projection and the usage events (any signed in user), the invoice list and printable detail, marking an invoice paid or void, and changing the plan (admin).

### Cross organization reads

Revenue spans organizations and Row Level Security rightly keeps one organization from reading another's rows. The platform wide numbers therefore come from `SECURITY DEFINER` functions owned by the migration role (`platform_day_metrics`, `platform_top_customers`, `platform_invoice_page`, `platform_revenue_totals`, `platform_billable_orgs`, `platform_mark_overdue`, and an internal `platform_org_state`). They return aggregates and per customer rows in which only simulated organizations carry their name (a live organization appears as "Live organization 3f9a1c"). EXECUTE is revoked from PUBLIC and granted to the application role, except for the internal function, which the application cannot call. The migration role must bypass Row Level Security (a superuser does, which is the default of the Postgres image); the tests check that the functions see every organization while ordinary queries do not.

### Limits

- The current month's revenue is zero until its invoices are issued; MRR is live.
- A plan change takes effect at once and is not prorated; the month is billed on the plan active on its last day.
- A day's operational numbers are final only after the day has ended; today's snapshot is a running figure.
- Dollar amounts are USD cents; there is no tax, currency conversion or credit note.
- Revenue and customer rows are readable by any organization's admin, since there is no platform operator role yet; this is why live customers are masked.
- The nightly and monthly jobs need `celery beat` running (exactly one instance).

## Frontend foundation and public site (Phase 11)

The site is a React single page application in `frontend/`. It is the public face of the project (home, platform, solutions, pricing, security, resources, company, contact, legal) and the sign in page; the console behind the sign in is built in the next phase.

### Stack and layout

React 18, TypeScript (strict), Vite, Tailwind CSS 3, Radix UI primitives with shadcn style components written into `src/components/ui`, TanStack Query, React Router, React Hook Form with Zod, Lucide icons, Inter self hosted through `@fontsource-variable/inter`. Vitest and Testing Library for components, Playwright (with axe) for the browser. The plan also lists ECharts, TanStack Table and Framer Motion: none is needed by the public site, so none is installed yet (they arrive with the console; the count up of the metric band is twelve lines of code and respects reduced motion).

```
frontend/
  src/styles/        tokens.css (the one token file), globals.css
  src/components/    ui/ (button, input, field, badge, card, accordion, tabs, sheet, navigation menu, skeleton), layout, header, footer, forms, pricing, sections, diagrams, product preview
  src/site/          pages/ and content/ (the text of the site, as typed data)
  src/app/           the sign in guard and the console entry page
  src/lib/           api client, auth, formatting, page metadata, count up, page loading
  e2e/               Playwright tests, helpers and a mock of the API
  scripts/           build-docs, build-images, lighthouse, serve-dist, screens, overflow
  public/            icons, social image, manifest, robots, sitemap, images/ (original SVG) and ATTRIBUTION.md
```

### Design system (`src/styles/tokens.css`)

One file defines every color (space separated RGB channels, so Tailwind can apply opacity), radius, shadow, the type scale (fluid between 360 and 1280 px), the content width (1280 px) and the header height; `tailwind.config.ts` reads those variables and components contain no raw value. Palette: navy `#0B1F3A`, primary blue `#0F62FE`, teal `#00A3A3`, neutral grays, success, warning and error. The teal is decoration only: the text teal is darker, and `primary-ink` is the blue used for text on a pale blue surface, because the plain blue reaches only 4.4:1 there (the automated audit found it). Spacing follows the 8 point grid of Tailwind's scale, layout uses a 12 column grid. A `.dark` theme is defined for the console; the public site is always light.

### Layout

A top utility bar (Contact, Documentation, Status); a sticky header with mega menus (Platform, Solutions, Resources, Company, and Pricing as a link), a `Sign in` link and an `Open console` button that both lead to `/login`; a mobile drawer (Radix Dialog: focus is trapped, Escape closes it and focus returns to the button). The header condenses on scroll (a smaller mark, a shadow, a translucent background) without changing its height, so nothing below it ever moves. The footer has five link columns, a one field form that stores a lead of the kind `newsletter`, the legal links, a language and region label and the demonstration notice.

### Pages and where their text comes from

Home (hero with an illustration of the console, six capability cards, the metric band, how it works, five industry tiles, a product tour, a security summary, three sample scenarios, the FAQ, a closing call to action), Platform (the six stages in depth, supported sources, architecture figure), Solutions (an index and one page per industry), Pricing (plan cards from the API, comparison table, cost calculator), Security, Resources (documentation viewer, API reference link, three articles), Company, Contact, Request a demo, Privacy and Terms (labelled as samples), Status, Login, and a 404.

The text lives in `src/site/content/*.ts` as typed data, and a test (`content.test.ts`) keeps it honest: every navigation, footer and content link must match a real route, no long dash anywhere, no certification claimed, unique ids, and a measured outcome allowed only on the two industries that have a sample system (healthcare and retail). The other three industry pages say "no sample system yet: no figures claimed" and describe what would be verified. Numbers that appear on the site (94.7 percent of columns mapped without a model, 6,338 rows into 5,515 loaded and 823 quarantined, quality 91.3 to 99.5, 25,900 records into 20,662 and 5,238) are the ones measured on the samples and documented above; the mapping confidences and values in the illustrations are the real output of the mapping step on the commerce sample, and the field layout is the one recovered from the clinic application.

### Data from the API

The metric band reads `GET /api/v1/public/metrics`, the plan cards `GET /public/plans`, the calculator `GET /public/pricing/estimate` (so the prices come from the same function that prices invoices), and the forms `POST /public/leads`. Every one has a loading state of fixed size, an error state, and where it makes sense a retry. The forms validate with Zod before they send, map a rate limit and a server failure to plain sentences, keep what was typed when a send fails, and carry a hidden field for bots that is never announced to screen readers. All figures from the demo environment are labelled as such.

### Signing in

`/login` posts to `/auth/login`, explains a wrong password (and the lockout), a rate limit and a network failure, and sends the person to the page they came from (`?next=`, accepted only when it is a path on this site) or to the entry page of their role. The console routes (`/app/*`) are guarded: a signed out person is sent to the sign in page with `next` set, and the server remains the authority. The session cookie cannot be read by scripts, so the site keeps a hint in local storage (set at sign in, cleared at sign out) and only asks the server who is signed in when the hint is set or on a console route; a visitor therefore makes no failing request on a public page.

### Documentation viewer

`scripts/build-docs.mjs` renders `docs/architecture.md` and `docs/security.md` to HTML at build time (it refuses a long dash or any active content), numbers the headings for a table of contents, and makes scrollable tables and code blocks reachable with the keyboard. The viewer loads that file only when opened, so the documents cost nothing on other pages.

### Imagery

Every image is original: hand drawn SVG illustrations for the industries and the diagrams, an SVG mark and social image rendered to PNG by `scripts/build-images.mjs`. No photograph is used. The plan allows Unsplash or Pexels photography, but each photograph needs its photographer, source and license verified by a person who has seen it, and that record cannot be made honestly without doing so; `public/images/ATTRIBUTION.md` says so and says where photographs would go. The product images on the home page are illustrations built with the real design system and labelled as illustrations. `scripts/capture_screenshots.ts` (Playwright) produces authentic screenshots of the running application as WebP; the console pages are added to it as the console is built.

### Accessibility

Targets WCAG 2.1 AA and is checked by tests, not by eye: axe runs on every public page at 1280 and 375 px, on each open mega menu, on the mobile drawer, on the FAQ, the tour and the calculator once used, and on the forms with their errors. It found real problems that were fixed: a label that pointed at a wrapper instead of its input (password field), duplicate accessible names, text contrast below 4.5:1 on two surfaces, an ARIA label on a paragraph, and scrollable regions the keyboard could not reach. There is a skip link, one visible focus style, keyboard operable menus, tabs and accordion, a live announcement of the new page title on every route change, alternative text or hidden decoration for every figure, and `prefers-reduced-motion` support. One rule is switched off for the open mega menus only: Radix renders a focus proxy that is `aria-hidden` and focusable by design, so that Tab moves into the menu (documented in `e2e/a11y.spec.ts`).

### Performance and layout stability

Home is in the main bundle, every other page is its own chunk, the libraries are split into long lived chunks, fonts are subsetted by unicode range with a fallback face that has Inter's metrics, and images carry explicit dimensions. Layout shift is designed out and measured: the footer stays hidden while a page placeholder is on screen (otherwise it jumps when the page arrives), a global rule gives grids a shrinkable default column (a long word in the footer had made the phone layout wider than the screen), and every data block reserves its size while loading.

Measured with Lighthouse (desktop preset, `npm run lighthouse`, a production like server with gzip and long lived caching, ten pages): performance 99 to 100, accessibility 100, best practices 100, SEO 100 (the sign in page is `noindex` on purpose, so its SEO score is lower), cumulative layout shift 0 on every page. In the browser tests every page, at 360, 768, 1280 and 1920 px, has no horizontal overflow, one `h1`, its own title and description, and a layout shift score below 0.02 while loading and scrolling.

### Tests

- `npm test`: 83 Vitest tests (formatting, the API client and its errors, the calculator and price cards, the forms and their failures, sign in and the redirect target rules, the metric band and the count up, the header, menus and drawer, and the content integrity checks).
- `npm run e2e`: 219 Playwright tests against the built site (page by page health, widths, layout shift; every link on every page including the mega menus and every anchor; axe; keyboard use, sticky header, drawer, calculator, forms stored by the server, bot trap, sign in, sign out, guarded console, documentation viewer, reduced motion). They run against the real API when it is up. When it is not (no Docker, CI), `node e2e/mock-api.mjs` stands in for the public and sign in endpoints with the same contract, and `E2E_MOCK_API_URL` tells the tests where it is; the sign in tests need `E2E_PASSWORD` (the password of the demo users) and, against the real API, the five a minute sign in limit means running them once a minute.
- `npm run lint` and `npm run typecheck`; the lint also rejects the long dash character anywhere in the frontend.

### Deployment

`frontend/Dockerfile` builds the site and serves it with nginx (non root, read only, no capabilities, `docker compose --profile app up`, then http://localhost:8080). `nginx.conf` serves the static files with a year of caching for hashed assets and revalidation for the page shell, falls back to `index.html` for client side routes, and forwards `/api`, `/health`, `/docs` and `/openapi.json` to the API. The pages carry a strict Content-Security-Policy (scripts, styles, fonts, images and connections only from the site itself, no framing, no plugins), `nosniff`, `X-Frame-Options`, a referrer policy and a permissions policy. The browser tests run against a server that reads those exact headers from `nginx.conf`, so a violation would be a console error and fail a test. The image was built and run in Phase 13: it starts under the unprivileged user, serves the site and its headers, falls back to the page shell for client side routes, and passes the image scan (see Evaluation, testing and hardening).

### Limits

- A client side application cannot give every page its own title, description and social preview to crawlers that do not run scripts: the title, description and canonical address are set by the page (and read by search engines that render), while the shell carries defaults. Pre rendering the public pages is the way to close this if it matters.
- An address that does not exist shows the not found page with HTTP status 200, as every single page application does.
- The social image and canonical addresses use the placeholder domain `legacylift.example` until the site has a real one.
- There are no photographs and no screenshots of the console yet (see Imagery); the console itself is described in the next section.
- Sign in needs the API.
- The suites were first run against the mock API; the whole suite, including the accessibility and interaction tests, the leads stored by the real database and the real sign in, now also runs against the live API (Phase 13).

## The console (Phase 12)

The signed in part of the application: `frontend/src/console/`, served under `/app`. Everything it shows comes from the API; there is no data in the frontend but what the API returns.

### What the backend added for it

- `GET /overview` (the organization's month: jobs, records, success rate, average duration, tokens, the previous month for comparison, jobs in progress, recent jobs), `GET /notifications` (what happened lately to the organization's jobs: finished, failed, rolled back, waiting for a person), `GET` and `DELETE /mapping-memory` (search, paging, forget one).
- The job list carries the name and kind of the source and the name of the owner, and filters by status, kind, text, dates and owner, and sorts by any of them (`GET /jobs`).
- `GET /auth/organizations` and `POST /auth/switch`: a person who belongs to several organizations lists them and switches; the list is read through a `SECURITY DEFINER` function because the application role can see only the organization of the request. Switching issues new tokens and is audited.
- The audit log searches by action, person, entity or details and reports the number of matches in `X-Total-Count`. `GET` and `PUT /admin/retention` set how long the encrypted copy of an uploaded file is kept; a nightly task removes the copies past it.
- The dry run answers with a cost summary (rows to load, tokens used against the budget, an estimated load time with its basis).
- Analytics: `GET /analytics/operations/detail` (mapping methods, stage latency percentiles, quarantine reasons, failed stages, discovery confidence, model use, token savings), `GET /analytics/revenue/cohorts`, and a `days` parameter on every range endpoint ("the last N days up to the latest data", so a preset such as 30 days does not depend on the date of the client).

### Layout, navigation and roles

A collapsible sidebar (remembered), a top bar with the organization switcher, the command palette (Ctrl K or Command K: pages for the role, migrations found by name, switch theme, sign out), notifications (polled every 30 seconds, a count of what is newer than the last time the list was opened) and the user menu, breadcrumbs derived from the address, skeleton loaders that keep the size of what they stand for, empty states, an error boundary per page that clears when the route changes, and toasts (a polite status for success, an alert for errors). On a phone the sidebar is a sheet. A light and a dark theme: the choice is applied to the document root so that dialogs and menus, which render outside the page, follow it, and the console removes it when it closes because the public site is always light.

Navigation and routes are defined once (`shell/nav.ts`) with the roles that may use each page: viewers read (overview, migrations, schemas, memory, billing); analysts also run migrations and read the operations analytics; administrators also see revenue, team, audit log and settings. A page a role may not open shows a plain explanation, and the server refuses the same requests (browser tests assert both).

### The migration wizard

Seven steps (`wizard/`). The address holds the job and the step (`/app/migrations/:id?step=mapping`), so a person can leave and come back to the same place. A step can be opened when the job has reached it or after it, never before (`furthestStep`; the dry run is started in its own step, so a job that is waiting for review can open it).

1. **Source**: a sample system or an uploaded file (with the data file of a .NET application); the target schema follows the sample until the person chooses another. Creating the migration registers or uploads the source and creates the job.
2. **Discovery**: runs `discover` and `profile`, shows the stages and the live events while it works, then every table and field with how it was found, its confidence and its details, personal data flagged, the quality score, and an evidence drawer with the raw output of REA for any field.
3. **Mapping review**: runs `map` and `rules`, then a table of source column, suggested target, confidence bar (a meter, with the number beside it), method badge and state; accept, change through a searchable target picker (suggestions first), reject; approve everything at or above 85 percent confidence in one action, or the selected rows. Changing a mapping marks the rules stale, and they are generated again when the person continues.
4. **Transform preview**: every rule with what it does to a sample, before and after side by side, the number and rate of failures; a rule that needs the meaning of codes shows an editor and blocks the way on until it is complete.
5. **Dry run**: how many rows would load, which would be quarantined and why, the rules with failures, and the cost summary.
6. **Execute and monitor**: a confirmation, the `load` stage in the background, the stage timeline with the time each stage took, the live events.
7. **Reconciliation report**: counts, the quality gauge (before and after), the checks per table, the quarantined rows with a reason filter and CSV export, and rollback after a confirmation.

Progress is read from the events of the job. The browser follows them with Server-Sent Events (`/jobs/{id}/events/stream`: the history first, then new events, an `end` event when the job finishes; a browser reconnects and resumes from the last event) and falls back to polling the list when the stream cannot be used. The state of a stage is derived from the messages the workers write (`Stage x started`, `Stage x finished`, and the messages of the dry run and load endpoints) by a pure function that is unit tested (`stageTimeline`).

### The dashboards

Two dashboards, drawn with ECharts (loaded only when a dashboard is first opened, as a chunk of its own, with the SVG renderer and only the chart types used): **Revenue** (administrators) and **Analytics** (analysts and administrators). Both have one row of controls above everything they scope: a range (30 days, 90 days, 12 months, 24 months, custom), the bucket (day, week, month), a comparison with the previous period of the same length, CSV export and print. The state is in the address.

Chart rules, applied by shared builders (`charts/builders.ts`) and tested:

- Eight categorical hues in a fixed order (`charts/palette.ts`). The order was checked with the validator of the data visualization method, on the surfaces the charts sit on: in the light theme the worst adjacent pair is 9.1 in colorblind simulation (target 8) and 19.6 for normal vision (floor 15); in the dark theme 8.4 and 19.3. A series takes its hue by identity, not by rank: a plan, a provider, a mapping method and a kind of MRR movement have the same color in every chart, and hiding a series does not repaint the others. Three light hues (aqua, yellow, magenta) are under 3:1 against white, which the method allows only with visible labels or a table: every chart has a legend, a tooltip and a table of the same numbers.
- One axis per chart. The plan's "customer growth and ARPA trend on two axes" is two charts, because two scales on one plot invent a correlation; unit economics (revenue per 1,000 records, model tokens per 1,000 records) are two charts for the same reason.
- Bars at most 24 px thick, rounded 4 px at the data end only, a 2 px gap of the surface color between stacked segments; lines 2 px; the last point of a line marked with a ring and labelled; areas a wash of 10 percent; hairline, solid gridlines; ink in text colors, never the series color; one tooltip that lists every series at the pointed period, built from escaped text.
- Every chart has a legend whose entries are buttons that toggle a series (operable from the keyboard), a table of the same numbers behind a switch, and a text summary in words (the first and last value, the highest and lowest). The drawing itself is hidden from assistive technology, because the summary and the table carry the same information. While data is fetched again the previous drawing stays, dimmed.
- Sequential data (cohort retention) uses one hue: light to dark on the light theme and dark to light on the dark theme, so that the low end recedes into the surface in both.
- The MRR projection (a straight line through the last 12 months, continued six months, dashed, labelled "projection") is computed in the browser from the monthly series (`forecastLinear`), and the page says that it assumes nothing changes. The MRR movement chart has a waterfall form of the same numbers.
- The latency chart shows the median and the 95th percentile side by side, because percentiles do not add up and so are not stacked. The token savings from memory are labelled as an estimate, with the formula that makes it.

### Tests

- Vitest: the wizard steps against a mocked API (every step, what blocks the way on, bulk approval, the override picker, the code map editor, the cost summary, the report and the confirmations), the stage timeline, the discovery tables, the chart transforms (forecast, waterfall, bins, cohorts, unit economics and others), the chart builders (the mark specifications), the palette, the date range state, navigation by role, the command palette filter, the error boundary, toasts and the organization switcher.
- Playwright (`e2e/console.spec.ts`, against the real API, the real pipeline and the seeded history): each role signs in through the form; pages by role; the shell; the wizard from the first step to the report on both sample systems (with a rollback); both dashboards, their controls and their tables; billing, team, audit log, settings, schemas and memory; every console page in both themes under axe at WCAG 2.1 AA; no page overflows sideways at 360 and 768 px; no console error and no Content-Security-Policy violation.

### Limits

- Email invitations are not implemented: the Team page adds a person directly with a first password, and says so.
- The organization switcher is tested against the API and in unit tests, not in a browser test, because the demo has one organization.
- The projection is a straight line, not a forecast model. It says what the recent trend implies, nothing about churn or pricing.
- The history behind both dashboards is simulated for demonstration, and the pages say so; the operations figures cover the whole platform, not one organization.
- The console is not tested in browsers other than Chromium.

## Evaluation, testing and hardening (Phase 13)

### The evaluation

`scripts/eval_run.py` (`make eval`) measures the platform on the two sample systems and writes [evaluation.md](evaluation.md) (and `evaluation.json`). Every figure in that document is produced by the run that wrote it. It has three parts:

- **Scoring against the ground truth.** The fixtures are built with a known answer (`datasets/fixtures/*/ground_truth.json`); the platform never reads it, the harness does. Field discovery recall (the schema of the database, the layout of the binary file, and the layout of the same file without its program), recall of the injected defects by type, and the accuracy of the mapping by rules and embedding before any language model is asked.
- **Two complete runs of each system through the API** in a new organization, the second after the first has taught the mapping memory: wall time of every stage, how each column was mapped, how many decisions a person had to take (a reviewer that knows the answer accepts, corrects or rejects, and the repeat run needs fewer), tokens and model calls by provider from `llm_calls`, and the reconciliation.
- **The loaded data against the clean data.** What became of the row of every injected defect (quarantined with the expected reason, removed as a duplicate), and every loaded row compared field by field with the clean data the fixture was built from.

What it does not do: the mapping accuracy after the language model is measured only when a provider key is configured; without one the document says it was not measured and gives the ceiling (what a model that always picks the right candidate could reach) labelled as an upper bound. Tokens, model calls and the effect of the cache are zero in a run without a key, and the document says that too.

What it found, besides the figures: a harness that applied no meaning to the codes of the clinic file loaded the class of every encounter as a number, which the field comparison showed at once; the loaded rows differ from the clean data only in capitalization (the case rule treats unusual capitalization as a defect, so an acronym is changed too), and the six injected defects that are not quarantined are all in a source field that the clinic target schema has no column for.

### Coverage and the tests

`scripts/coverage_gate.py` fails when the line coverage of `pipeline` (discovery, profiling, mapping, transform, job orchestration and the scoring), `llm` or `core` is under 80 percent. The backend suite (930 tests, 5 skipped) gives 94 percent overall, 92.2 percent for the pipeline, 97.6 percent for `llm` and 97.7 percent for `core`. The router is tested against recorded provider responses (`backend/tests/recorded/llm`: the answers and the failures of both providers, replayed through the real clients), and no test reaches the network. The frontend has 207 Vitest tests (the wizard steps, the chart transforms and builders, the shell) and 275 Playwright tests (three of them only sign the demo roles in) browser tests.

New tests of this phase: `test_log_hygiene.py` (what must never reach a log), `test_security_probes.py` (hostile input, privilege escalation, forged tokens, error bodies), `test_read_your_writes.py` (below), and the console browser tests.

### Load sanity

`scripts/load_check.py` (`make load-check`, [load-check.md](load-check.md)) migrates a synthetic delimited file of 50,000 and then of 200,000 rows through the API, with defects injected, and samples the memory of the process every 100 ms. It passes when the growth for the large file is under a set limit (700 MB) and is at most 2.5 times the growth for the small one, because the data is read, transformed and written in chunks: in the run documented there, 200,000 rows grew the process by 172 MB against 108 MB for 50,000 rows, and the load ran at about 6,100 rows a second.

### Security pass

[security-review.md](security-review.md) checks the platform against the chapters of OWASP ASVS 4.0.3, with the test that proves each control, and records the scans: dependencies of the three package sets, secrets in the tree and in every commit, Trivy on the files, the Dockerfiles and the three images, a review of the logs, and hostile input. No finding of high severity is open. The pass changed the code in four places:

- **The commit came after the response.** The session of a request was committed when the dependency that yielded it was closed, which in this version of FastAPI is after the response has been sent. A client that read right after a write could read the old data, and a write could be acknowledged and then fail. The console found it (a mapping was overridden and the table that reloaded at once still showed the old target). Every route now declares the session with `scope="function"`, which closes it, and so commits, before the response is sent; a failed commit is an error response. A test checks the order of the commit and the first byte of the response, and that no route forgets the scope.
- **A null character in a search box was a server error.** PostgreSQL refuses it; the API answered 500 and logged a stack trace. A value the database cannot store is now a 422 that does not echo the value.
- **Dependencies and images.** `zod` and `react-router` were upgraded to fixed versions, `certifi` too; the REA runner needed an `overrides` entry because the pinned `rea-agents` asks for one exact `zod`; the base of the frontend image was stale; the runner image no longer carries npm; one Dockerfile did not declare its user.
- **The Content-Security-Policy.** Radix dialogs and menus insert one inline style element (the scroll lock). It is allowed by its hash and nothing else inline is.

### What the browser tests found

The console tests ran for the first time against the real API and found, besides the commit order above, three defects that the unit tests could not: a step of the wizard that the person could never open (the dry run is started inside its own step, but the step was reachable only after a dry run had passed); a table that scrolls sideways made the whole page scroll sideways on a phone (visually hidden text inside it is positioned against the page unless the scroll container is positioned); and an accessible name that did not contain the visible text of the command palette button.

### Continuous integration

`.github/workflows/ci.yml`: the backend tests with the coverage gate, the browser tests against the whole stack, the frontend lint, types, tests and build, the scans (secrets with history, `pip-audit`, `npm audit`, Trivy on files), the three images built and scanned, and the load check on the main branch and weekly. The same steps were run by hand in the environment where this phase was done; the workflow itself has not run on GitHub.

### Limits

- The evaluation is two systems, built by the same builder that wrote the answer; it shows that the method works on them, not how it does on any legacy system. Its timings come from one machine and one process.
- The language model, its accuracy and the effect of its cache are not measured here, because no provider key was configured. The harness measures them when there is one.
- The load check measures a delimited file with one table; the memory of PostgreSQL is not in its figures.
- The weekly and per-push scans find known advisories. They do not replace a review of new code, and a formal threat model is not part of the repository.
