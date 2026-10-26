/** Typed calls to the console endpoints. The shapes mirror what the API returns; the backend tests pin them down. */
import { api, apiWithTotal, type Role } from "@/lib/api";

const v1 = "/api/v1";

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

/** A query string from the entries that have a value. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

// ------------------------------------------------------------------ jobs and sources

export type JobStatus =
  "CREATED" | "UPLOADED" | "DISCOVERING" | "PROFILED" | "MAPPED" | "AWAITING_REVIEW" | "DRY_RUN" | "READY" | "LOADING" | "RECONCILING" | "COMPLETED" | "FAILED" | "ROLLED_BACK";

export type Stage = "discover" | "profile" | "map" | "rules" | "dry_run" | "load";
export type SourceKind = "sqlite" | "csv" | "dotnet_binary" | "sql_dump" | "access" | string;

export type Job = {
  id: string;
  status: JobStatus;
  source_id: string;
  data_source_id: string | null;
  target_schema_id: string | null;
  error_message: string | null;
  token_budget: number;
  tokens_used: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  next_stage: Stage | null;
  source_name: string | null;
  source_kind: SourceKind | null;
  owner_name: string | null;
};

export type Source = {
  id: string;
  name: string;
  kind: SourceKind;
  original_filename: string | null;
  size_bytes: number | null;
  sha256: string | null;
  is_sample: boolean;
  created_at: string;
};
export type Sample = { name: string; title: string; description: string; target_schema: string; files: string[]; available: boolean };
export type SampleRegistered = { sample: string; target_schema: string; sources: Source[]; source_id: string; data_source_id: string | null };
export type Queued = { job_id: string; stages: Stage[]; task_id: string; execution: string };

export type JobEvent = { id: number; from_status: JobStatus | null; to_status: JobStatus; message: string | null; detail: Record<string, unknown>; created_at: string };

export type JobFilters = {
  status?: string;
  source_kind?: string;
  q?: string;
  created_from?: string;
  created_to?: string;
  owner_id?: string;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
};

export const jobsApi = {
  list: (filters: JobFilters, signal?: AbortSignal) => api<Page<Job>>(`${v1}/jobs${qs(filters)}`, { signal }),
  get: (id: string, signal?: AbortSignal) => api<Job>(`${v1}/jobs/${id}`, { signal }),
  create: (body: { source_id: string; data_source_id?: string | null; target_schema: string }) => api<Job>(`${v1}/jobs`, { method: "POST", body }),
  runStages: (id: string, stages: Stage[], targetSchema?: string) =>
    api<Queued>(`${v1}/jobs/${id}/run`, { method: "POST", body: { stages, ...(targetSchema ? { target_schema: targetSchema } : {}) } }),
  runStage: (id: string, stage: Stage) => api<Queued>(`${v1}/jobs/${id}/stages/${stage}`, { method: "POST", body: {} }),
  cancel: (id: string) => api<Job>(`${v1}/jobs/${id}/cancel`, { method: "POST" }),
  events: (id: string, after = 0, signal?: AbortSignal) => api<{ items: JobEvent[]; last_id: number | null }>(`${v1}/jobs/${id}/events${qs({ after, limit: 500 })}`, { signal }),
  streamUrl: (id: string, after = 0) => `${v1}/jobs/${id}/events/stream${qs({ after })}`,
};

export const sourcesApi = {
  get: (id: string, signal?: AbortSignal) => api<Source>(`${v1}/sources/${id}`, { signal }),
  samples: (signal?: AbortSignal) => api<Sample[]>(`${v1}/sources/samples`, { signal }),
  registerSample: (name: string) => api<SampleRegistered>(`${v1}/sources/samples/${name}`, { method: "POST" }),
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return api<Source>(`${v1}/sources/upload`, { method: "POST", body });
  },
};

// ------------------------------------------------------------------ discovery, profile, mapping, rules

/** What discovery found. A database gives its tables and columns as declared; a binary file gives a layout of tables and fields with a confidence each. */
export type Discovery = {
  /** Present for a database ("sqlite"). */
  kind?: string;
  /** Present for a binary file ("CDB1"). */
  format?: string;
  method?: string;
  engine?: string;
  validated?: boolean;
  confidence?: number;
  file_size?: number;
  limitations?: string[];
  ambiguities?: unknown[];
  tables: Record<string, unknown>[];
  views?: { name: string; sql?: string }[];
  [key: string]: unknown;
};

export type Evidence = { id: string; subject: string; command: string; rea_version: string | null; duration_ms: number | null; raw_json: Record<string, unknown> };

export type ProfileColumn = {
  name: string;
  table: string;
  pii: boolean;
  null_rate: number;
  distinct_count: number;
  inferred_type: string;
  semantic_type?: string | null;
  expanded_name?: string | null;
  declared_type?: string | null;
  pii_kinds?: string[];
  min?: string | null;
  max?: string | null;
  notes?: string[];
};
export type ProfileTable = { name: string; rows: number; columns: ProfileColumn[] };
export type Profile = { tables: ProfileTable[] };

export type QualityTable = { table: string; rows: number; score: number; warning_rows: number; blocking_rows: number; issues_by_type: Record<string, number> };
export type Quality = { score: number; formula: string; tables: QualityTable[]; issue_counts: Record<string, number>; issues_total: number; issues_truncated: boolean };

export type MappingMethod = "memory" | "embedding" | "llm" | "human" | string;
export type MappingStatus = "suggested" | "approved" | "rejected" | string;
export type Mapping = {
  id: string;
  source_table: string;
  source_column: string;
  target: string | null;
  method: MappingMethod;
  status: MappingStatus;
  decision: string | null;
  confidence: number | null;
  similarity: number | null;
  margin: number | null;
  candidates: { field: string; similarity: number }[];
  rationale: string | null;
  pii: boolean;
};

export type TargetSchema = { id: string; name: string; version: string; description: string | null; built_in: boolean };
export type TargetField = {
  id: string;
  table: string;
  name: string;
  type: string;
  required: boolean;
  description: string | null;
  synonyms: string[];
  examples: string[];
  pii: boolean;
};

export type RuleStep = { op: string; [key: string]: unknown };
export type Rule = {
  id: string;
  target_table: string;
  target_field: string;
  source_table: string | null;
  inputs: string[];
  steps: RuleStep[];
  status: "ready" | "needs_input" | string;
  origin: string;
  notes: string[];
  failures: Record<string, unknown>;
  sample_failure_count: number | null;
};

export type PreviewRow = { row: number; before: Record<string, unknown>; after: string | null; failed: boolean; kind: string | null };
export type Preview = { sampled: number; failed: number; failure_rate: number; steps: string[]; rows: PreviewRow[] };

export type RuleReport = {
  target_field: string;
  inputs: string[];
  ops: string[];
  rows: number;
  nulls: number;
  failures: number;
  out_of_range: number;
  step_failures: number[];
  failure_rate: number;
};
export type TableDryRun = {
  target_table: string;
  source_table: string;
  sample_rows: number;
  would_load: number;
  would_quarantine: Record<string, number>;
  rules: RuleReport[];
  preview: { row: number; before: Record<string, unknown>; after: Record<string, unknown>; reason: string | null; detail: string | null }[];
  source_rows: number | null;
  seconds: number;
};
export type DryRunResult = {
  status: JobStatus;
  ready: boolean;
  blocking: string[];
  warnings: string[];
  tables: TableDryRun[];
  cost: { source_rows: number; tokens_used: number; token_budget: number; estimated_seconds: number | null; basis: string };
};

export type TableReport = {
  target_table: string;
  source_table: string;
  source_rows: number;
  staged_rows: number;
  loaded: number;
  quarantined: number;
  by_reason: Record<string, number>;
  unexplained: number;
  passed: boolean;
  failed: string[];
  checks: {
    row_accounting: { source: number; staged: number; loaded: number; quarantined: number; ok: boolean };
    every_quarantined_row_has_a_reason: { without_reason: number; ok: boolean };
    distinct_keys: { source: number; loaded: number; only_in_quarantine: number; ok: boolean };
    null_counts: Record<string, { source_after_transform: number; expected_loaded: number; loaded: number; ok: boolean }>;
    numeric_sums: Record<string, { expected: string; loaded: string; ok: boolean }>;
    sampled_rows: { checked: number; matched: number; mismatches: unknown[]; ok: boolean };
  };
};
export type Reconciliation = {
  passed: boolean;
  source_rows: number;
  loaded_rows: number;
  quarantined_rows: number;
  quality_before: number | null;
  quality_after: number | null;
  checks: { passed: boolean; tables: TableReport[] };
};
export type QuarantineRow = { table: string; row: number; reason: string; detail: string | null; raw: Record<string, unknown> };
export type QuarantinePage = { total: number; rows: QuarantineRow[]; status: JobStatus | null };

export const pipelineApi = {
  discovery: (id: string, signal?: AbortSignal) => api<Discovery>(`${v1}/jobs/${id}/discovery`, { signal }),
  evidence: (id: string, subject: string, signal?: AbortSignal) => api<Evidence[]>(`${v1}/jobs/${id}/discovery/evidence${qs({ subject })}`, { signal }),
  profile: (id: string, signal?: AbortSignal) => api<Profile>(`${v1}/jobs/${id}/profile`, { signal }),
  quality: (id: string, signal?: AbortSignal) => api<Quality>(`${v1}/jobs/${id}/quality${qs({ limit: 1 })}`, { signal }),
  mappings: (id: string, signal?: AbortSignal) => api<Mapping[]>(`${v1}/jobs/${id}/mappings`, { signal }),
  schemas: (signal?: AbortSignal) => api<TargetSchema[]>(`${v1}/target-schemas`, { signal }),
  fields: (schemaId: string, signal?: AbortSignal) => api<TargetField[]>(`${v1}/target-schemas/${schemaId}/fields`, { signal }),
  patchMapping: (jobId: string, mappingId: string, body: { target_field_id?: string | null; transform_hint?: string | null }) =>
    api<{ id: string; status: string; regenerate_rules: boolean }>(`${v1}/jobs/${jobId}/mappings/${mappingId}`, { method: "PATCH", body }),
  approveMapping: (jobId: string, mappingId: string) => api<{ id: string; status: string }>(`${v1}/jobs/${jobId}/mappings/${mappingId}/approve`, { method: "POST", body: {} }),
  bulkApprove: (jobId: string, ids: string[]) =>
    api<{ approved: number; skipped: { id: string; reason: string }[] }>(`${v1}/jobs/${jobId}/mappings/bulk-approve`, { method: "POST", body: { mapping_ids: ids } }),
  rules: (id: string, signal?: AbortSignal) => api<Rule[]>(`${v1}/jobs/${id}/rules`, { signal }),
  generateRules: (id: string) => api<{ status: JobStatus }>(`${v1}/jobs/${id}/rules/generate`, { method: "POST" }),
  putCodeMap: (jobId: string, ruleId: string, mapping: Record<string, string>, passthrough = false) =>
    api<Rule>(`${v1}/jobs/${jobId}/rules/${ruleId}/code-map`, { method: "PUT", body: { mapping, passthrough } }),
  preview: (jobId: string, body: { target_table: string; target_field: string; steps?: RuleStep[]; rows?: number }) =>
    api<Preview>(`${v1}/jobs/${jobId}/transforms/preview`, { method: "POST", body }),
  dryRun: (id: string) => api<DryRunResult>(`${v1}/jobs/${id}/dry-run`, { method: "POST" }),
  rollback: (id: string) => api<{ status: JobStatus }>(`${v1}/jobs/${id}/rollback`, { method: "POST" }),
  reconciliation: (id: string, signal?: AbortSignal) => api<Reconciliation>(`${v1}/jobs/${id}/reconciliation`, { signal }),
  quarantine: (id: string, params: { reason?: string; table?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    api<QuarantinePage>(`${v1}/jobs/${id}/quarantine${qs(params)}`, { signal }),
  quarantineCsvUrl: (id: string, params: { reason?: string; table?: string }) => `${v1}/jobs/${id}/quarantine.csv${qs(params)}`,
};

// ------------------------------------------------------------------ overview, notifications, memory

export type Kpis = { jobs: number; records: number; success_rate: number | null; avg_duration_ms: number | null; tokens: number };
export type Overview = { month: { from: string; to: string }; kpis: Kpis; previous: Kpis; in_progress: Job[]; recent: Job[]; total_jobs: number };
export type Notification = {
  id: number;
  job_id: string;
  source_name: string | null;
  to_status: JobStatus;
  message: string | null;
  kind: "success" | "error" | "info" | "action" | "waiting";
  created_at: string;
};
export type Memory = { id: string; source_text: string; target: string; target_schema: string; transform_hint: string | null; times_approved: number; last_approved_at: string };
export type OrgChoice = { org_id: string; name: string; role: Role; current: boolean };

export const consoleApi = {
  overview: (signal?: AbortSignal) => api<Overview>(`${v1}/overview`, { signal }),
  notifications: (signal?: AbortSignal) => api<Notification[]>(`${v1}/notifications${qs({ limit: 20 })}`, { signal }),
  memory: (params: { q?: string; schema?: string; limit?: number; offset?: number }, signal?: AbortSignal) => api<Page<Memory>>(`${v1}/mapping-memory${qs(params)}`, { signal }),
  forgetMemory: (id: string) => api<void>(`${v1}/mapping-memory/${id}`, { method: "DELETE" }),
  organizations: (signal?: AbortSignal) => api<OrgChoice[]>(`${v1}/auth/organizations`, { signal }),
  switchOrg: (orgId: string) => api(`${v1}/auth/switch`, { method: "POST", body: { org_id: orgId } }),
};

// ------------------------------------------------------------------ billing

export type Plan = { code: string; name: string; monthly_fee_cents: number; included_records: number; overage_cents_per_1000: number };
export type Subscription = {
  plan: Plan;
  status: string;
  since: string;
  period: { start: string; end: string };
  usage: {
    records: number;
    included_records: number;
    overage_records: number;
    base_cents: number;
    overage_cents: number;
    total_cents: number;
    percent_of_allowance: number;
    projected_records: number;
    projected_total_cents: number;
  };
  is_simulated: boolean;
};
export type UsageMonth = { month: string; records: number; tokens: number; jobs: number };
export type UsageEvent = { job_id: string; records_loaded: number; tokens_used: number; duration_ms: number; occurred_at: string };
export type Invoice = {
  id: string;
  period_start: string;
  period_end: string;
  status: "open" | "paid" | "void" | string;
  total_cents: number;
  issued_at: string;
  due_at: string | null;
  paid_at: string | null;
  is_simulated: boolean;
  currency: string;
};
export type InvoiceLine = { kind: string; description: string; quantity: number; unit_price_cents: number; amount_cents: number };
export type InvoiceDetail = Invoice & { plan: string | null; lines: InvoiceLine[] };

export const billingApi = {
  subscription: (signal?: AbortSignal) => api<Subscription>(`${v1}/billing/subscription`, { signal }),
  usage: (signal?: AbortSignal) => api<Page<UsageEvent> & { months: UsageMonth[] }>(`${v1}/billing/usage${qs({ limit: 10 })}`, { signal }),
  invoices: (params: { limit?: number; offset?: number }, signal?: AbortSignal) => api<Page<Invoice>>(`${v1}/billing/invoices${qs(params)}`, { signal }),
  invoice: (id: string, signal?: AbortSignal) => api<InvoiceDetail>(`${v1}/billing/invoices/${id}`, { signal }),
  changePlan: (planCode: string) => api(`${v1}/billing/subscription/plan`, { method: "POST", body: { plan_code: planCode } }),
  plans: (signal?: AbortSignal) => api<Plan[]>(`${v1}/public/plans`, { signal }),
};

// ------------------------------------------------------------------ team, audit, settings

export type Member = { user_id: string; email: string; full_name: string; role: Role; is_active: boolean; member_since: string };
export type AuditEntry = {
  id: string;
  user_id: string | null;
  user_name: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  detail: Record<string, unknown>;
  created_at: string;
};
export type LlmProvider = { name: string; model: string; configured: boolean; circuit: string; tokens_available: number; requests_available: number; limits_from_headers: boolean };
export type LlmSettings = {
  provider_order: string[];
  job_token_budget: number;
  org_daily_token_budget: number;
  max_calls_per_job: number;
  used_today: number;
  providers: LlmProvider[];
};

export const adminApi = {
  users: (params: { q?: string; role?: string; limit?: number; offset?: number }, signal?: AbortSignal) => api<Page<Member>>(`${v1}/admin/users${qs(params)}`, { signal }),
  createUser: (body: { email: string; full_name: string; role: Role; password: string }) => api<Member>(`${v1}/admin/users`, { method: "POST", body }),
  changeRole: (id: string, role: Role) => api<Member>(`${v1}/admin/users/${id}`, { method: "PATCH", body: { role } }),
  removeUser: (id: string) => api<void>(`${v1}/admin/users/${id}`, { method: "DELETE" }),
  audit: (params: { q?: string; action?: string; from?: string; to?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    apiWithTotal<AuditEntry>(`${v1}/admin/audit-log${qs(params)}`, { signal }),
  llmSettings: (signal?: AbortSignal) => api<LlmSettings>(`${v1}/admin/llm-settings`, { signal }),
  saveLlmSettings: (body: { provider_order: string[]; job_token_budget: number; org_daily_token_budget: number; max_calls_per_job: number }) =>
    api<LlmSettings>(`${v1}/admin/llm-settings`, { method: "PUT", body }),
  testConnection: (provider: string) => api<Record<string, unknown>>(`${v1}/admin/llm-settings/test-connection`, { method: "POST", body: { provider } }),
  retention: (signal?: AbortSignal) => api<{ retention_days: number; applies_to: string }>(`${v1}/admin/retention`, { signal }),
  saveRetention: (retention_days: number) => api<{ retention_days: number; applies_to: string }>(`${v1}/admin/retention`, { method: "PUT", body: { retention_days } }),
};

// ------------------------------------------------------------------ analytics

export type Granularity = "day" | "week" | "month";
export type RangeParams = { days?: number; from?: string; to?: string; granularity?: Granularity; compare?: boolean };

export type RevenuePoint = {
  period: string;
  mrr_cents: number;
  new_mrr_cents: number;
  expansion_mrr_cents: number;
  contraction_mrr_cents: number;
  churned_mrr_cents: number;
  net_new_mrr_cents: number;
  subscription_revenue_cents: number;
  usage_revenue_cents: number;
  total_revenue_cents: number;
  active_orgs: number;
  paying_orgs: number;
  new_orgs: number;
  arpa_cents: number;
  net_revenue_retention_12m: number | null;
};
export type RevenueKpis = Record<string, number | string | boolean | null> & {
  mrr_cents: number;
  arr_cents: number;
  mrr_start_cents: number;
  new_mrr_cents: number;
  expansion_mrr_cents: number;
  contraction_mrr_cents: number;
  churned_mrr_cents: number;
  net_new_mrr_cents: number;
  net_revenue_retention_12m: number | null;
  gross_churn_rate: number;
  arpa_cents: number;
  paying_orgs: number;
  active_orgs: number;
  records: number;
  tokens_total: number;
  subscription_revenue_cents: number;
  usage_revenue_cents: number;
  total_revenue_cents: number;
};
export type RevenueReport = {
  range: { from: string; to: string };
  granularity: Granularity;
  series: RevenuePoint[];
  kpis: RevenueKpis;
  previous?: { kpis: RevenueKpis };
  change?: Record<string, { current: number | null; previous: number | null; delta: number | null; change: number | null }>;
  checks: Record<string, unknown>;
  simulated: boolean;
  footnote: string;
};
export type Breakdown = {
  dimension: string;
  keys: string[];
  mrr: { period: string; values: Record<string, number> }[];
  usage_revenue: { period: string; values: Record<string, number> }[];
  current: { mrr_cents: Record<string, number>; share: Record<string, number>; total_mrr_cents: number };
  footnote: string;
};
export type TopCustomer = { name: string; plan: string; trend: number[]; region: string; industry: string; mrr_cents: number; simulated: boolean; revenue_12m_cents: number };
export type Cohort = { month: string; size: number; logos: (number | null)[]; mrr: (number | null)[] };
export type InvoiceRow = {
  id: string;
  customer: string;
  plan_code: string;
  status: string;
  industry: string;
  region: string;
  issued_at: string;
  due_at: string | null;
  paid_at: string | null;
  period_start: string;
  period_end: string;
  total_cents: number;
};

export type OpsPoint = {
  period: string;
  jobs: number;
  success_rate: number | null;
  avg_duration_ms: number | null;
  records: number;
  tokens_groq: number;
  tokens_gemini: number;
  tokens_total: number;
  failovers: number;
  cache_hits: number;
  quality_score: number | null;
};
export type OpsReport = {
  range: { from: string; to: string };
  granularity: Granularity;
  series: OpsPoint[];
  kpis: Record<string, number | null> & {
    jobs: number;
    records: number;
    success_rate: number | null;
    tokens_total: number;
    cache_hits: number;
    failovers: number;
    quality_score: number | null;
  };
  previous?: unknown;
  change?: Record<string, { current: number | null; previous: number | null; delta: number | null; change: number | null }>;
};
export type OpsDetail = {
  range: { from: string; to: string };
  mapping_methods: Record<string, number>;
  mapping_total: number;
  stage_latency: Record<string, { p50: number; p95: number; count: number }>;
  quarantine_reasons: Record<string, number>;
  failed_stages: Record<string, number>;
  discovery_confidence: number[];
  llm: { tokens: number; cache_hits: number; calls_made: number; failed_attempts: number; cache_hit_rate: number | null };
  tokens_saved_by_cache: number;
  tokens_saved_by_memory_estimate: number;
  footnote: string;
};

export const analyticsApi = {
  revenue: (p: RangeParams, signal?: AbortSignal) => api<RevenueReport>(`${v1}/analytics/revenue${qs({ ...p })}`, { signal }),
  breakdown: (dimension: string, p: RangeParams, signal?: AbortSignal) => api<Breakdown>(`${v1}/analytics/revenue/breakdown${qs({ dimension, ...p })}`, { signal }),
  top: (signal?: AbortSignal) => api<{ as_of: string; items: TopCustomer[]; footnote: string }>(`${v1}/analytics/customers/top${qs({ limit: 10 })}`, { signal }),
  cohorts: (signal?: AbortSignal) => api<{ as_of: string; cohorts: Cohort[]; footnote: string }>(`${v1}/analytics/revenue/cohorts`, { signal }),
  invoices: (p: { status?: string; q?: string; from?: string; to?: string; limit?: number; offset?: number }, signal?: AbortSignal) =>
    api<{ items: InvoiceRow[]; total: number; total_cents: number; limit: number; offset: number }>(`${v1}/analytics/invoices${qs(p)}`, { signal }),
  operations: (p: RangeParams, signal?: AbortSignal) => api<OpsReport>(`${v1}/analytics/operations${qs({ ...p })}`, { signal }),
  detail: (p: RangeParams, signal?: AbortSignal) => api<OpsDetail>(`${v1}/analytics/operations/detail${qs({ days: p.days, from: p.from, to: p.to })}`, { signal }),
  csvUrl: (kind: "revenue" | "operations", p: RangeParams) => `${v1}/analytics/${kind}.csv${qs({ ...p })}`,
};
