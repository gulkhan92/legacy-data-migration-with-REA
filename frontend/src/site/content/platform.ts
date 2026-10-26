export type Stage = {
  id: string;
  name: string;
  tagline: string;
  summary: string;
  points: string[];
  evidence: string;
};

/** The six stages of the pipeline. Every statement here is implemented and covered by tests in the repository. */
export const stages: Stage[] = [
  {
    id: "discovery",
    name: "Discovery",
    tagline: "Learn how the old system stores its data",
    summary:
      "Upload a legacy database or a desktop application with its data file. LegacyLift reads the structure with static analysis: it never runs your software.",
    points: [
      "SQLite databases: tables, columns, declared types, indexes, views and triggers, with a few sample rows.",
      "Windows desktop applications written for .NET: the record layout (field names, offsets, lengths, types, encodings) is recovered from the program's metadata and instructions, then validated against the data file.",
      "Every finding keeps its evidence: the exact command, the tool version and the output are stored, so a reviewer can see why a field was named what it was.",
      "A confidence is reported for each layout. Where a layout cannot be proven, it says so instead of guessing.",
    ],
    evidence: "REA static analysis, run in an isolated container with no network",
  },
  {
    id: "mapping",
    name: "Mapping",
    tagline: "Match every source column to a target field",
    summary:
      "Names like cust_no or l_nm are expanded and compared with the target schema by meaning. Rules and embeddings decide most columns; a language model is asked only about the few they cannot settle.",
    points: [
      "Memory first: a column you approved before is recognised on the next job, at zero cost.",
      "A type gate removes impossible matches (a date never feeds a number) before anything is ranked.",
      "One to one matching keeps two columns from claiming the same field.",
      "On the two sample systems, 94.7 percent of columns were mapped correctly with no language model call at all.",
      "Every mapping shows its confidence, its method (memory, embedding, model or human) and the candidates it was chosen from. You approve, override or reject.",
    ],
    evidence: "Measured on 76 columns of the commerce and clinic samples",
  },
  {
    id: "transformation",
    name: "Transformation",
    tagline: "Safe rules, written from what the data looks like",
    summary:
      "Dates in three formats, money with currency symbols, numeric status codes: rules are generated from the profile of each column, shown before and after on a sample, and only then used.",
    points: [
      "Rules are data, not code: a fixed list of operations (cast, parse date, map codes, split, concatenate and a few more). Nothing is evaluated.",
      "Patterns are checked so that a rule cannot hang the system.",
      "A dry run on a sample shows before and after values, and per rule how many values fail.",
      "Meanings that no file can tell, such as what status code 3 stands for, are asked of a person once and remembered.",
    ],
    evidence: "A whitelist of operations, validated on every save",
  },
  {
    id: "validation",
    name: "Validation",
    tagline: "Find what is wrong before it reaches the new system",
    summary:
      "Every column is profiled and the data is scored for quality. Rows that cannot be loaded are not dropped: they go to a quarantine with the reason and the original row.",
    points: [
      "Profiling finds null rates, formats, ranges, duplicates and references that point at nothing.",
      "Personal data is detected by rules and never kept in stored profiles or shown to a language model.",
      "Reasons are specific: required value missing, invalid date, out of range, duplicate, orphan reference.",
      "The quarantine can be filtered by reason and exported as a spreadsheet for the people who own the data.",
    ],
    evidence: "Injected defects on the samples are found and explained",
  },
  {
    id: "reconciliation",
    name: "Reconciliation",
    tagline: "Prove the result, and keep the way back",
    summary:
      "A load is one transaction. Afterwards the numbers are checked against what was read from the source, independently of the code that moved the data.",
    points: [
      "Row accounting: every source row is either loaded or quarantined, with nothing unexplained.",
      "Distinct keys, null counts per field and sums of numeric fields are compared, and a sample of rows is compared field by field.",
      "A quality score before and after shows what the migration improved.",
      "Loading is idempotent by job, and a rollback removes exactly what that job loaded.",
    ],
    evidence: "Checks run on both sample systems with no unexplained difference",
  },
  {
    id: "analytics",
    name: "Analytics",
    tagline: "See what the platform is doing, and what it costs",
    summary:
      "Every job writes usage that flows into invoices and a nightly snapshot. Dashboards read those tables, so every number can be traced.",
    points: [
      "Operations: jobs, success rate, duration, records, tokens by provider, failovers, cache hits and quality.",
      "Revenue: MRR and its movements, retention, churn and customers. The figures shown in this demonstration are simulated, and are labelled as such.",
      "The books reconcile by construction and the tests check it: invoice lines add up to the revenue shown.",
    ],
    evidence: "All figures come from tables; seeded rows are marked as simulated",
  },
];

export const sourceTypes = [
  {
    name: "SQLite database",
    detail: "Schema, indexes and sample rows are inspected. Typical of small business software and many desktop applications.",
    status: "Supported",
  },
  {
    name: ".NET desktop application with its data file",
    detail: "The record layout is recovered from the assembly and validated against the binary data file. No database is needed.",
    status: "Supported",
  },
  {
    name: "CSV and delimited text",
    detail: "Profiled directly: formats, quality and mapping, without a discovery step.",
    status: "Supported",
  },
  {
    name: "Electron application stores and native binaries",
    detail: "Not supported yet. Planned only after the supported sources are complete.",
    status: "Not yet",
  },
] as const;

export const architecture = [
  { id: "browser", label: "Browser", sub: "React single page app" },
  { id: "api", label: "API", sub: "FastAPI, roles, audit log" },
  { id: "worker", label: "Workers", sub: "Celery stages, retries" },
  { id: "db", label: "PostgreSQL", sub: "Row Level Security, pgvector" },
  { id: "rea", label: "REA runner", sub: "Isolated, no network" },
  { id: "llm", label: "Model router", sub: "Optional, masked data only" },
] as const;
