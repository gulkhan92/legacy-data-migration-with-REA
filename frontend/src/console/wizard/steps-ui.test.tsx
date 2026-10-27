import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "@/lib/auth";
import type { DryRunResult, Job, Mapping, Reconciliation, Rule } from "../api";
import { ToastProvider } from "../ui/toast";
import { DiscoveryStep } from "./DiscoveryStep";
import { DryRunStep } from "./DryRunStep";
import { ExecuteStep } from "./ExecuteStep";
import { HIGH_CONFIDENCE, MappingStep, filterMappings, highConfidenceIds, mappingState } from "./MappingStep";
import { QualityGauge, ReportStep } from "./ReportStep";
import { SourceStep } from "./SourceStep";
import { TransformStep, codeMapOf, describeSteps } from "./TransformStep";

const api = vi.hoisted(() => ({
  samples: vi.fn(),
  registerSample: vi.fn(),
  create: vi.fn(),
  schemas: vi.fn(),
  discovery: vi.fn(),
  profile: vi.fn(),
  quality: vi.fn(),
  evidence: vi.fn(),
  mappings: vi.fn(),
  bulkApprove: vi.fn(),
  approveMapping: vi.fn(),
  patchMapping: vi.fn(),
  fields: vi.fn(),
  rules: vi.fn(),
  putCodeMap: vi.fn(),
  preview: vi.fn(),
  dryRun: vi.fn(),
  runStages: vi.fn(),
  runStage: vi.fn(),
  reconciliation: vi.fn(),
  quarantine: vi.fn(),
  rollback: vi.fn(),
  generateRules: vi.fn(),
  getSource: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return {
    ...original,
    sourcesApi: { ...original.sourcesApi, samples: api.samples, registerSample: api.registerSample, get: api.getSource },
    jobsApi: { ...original.jobsApi, create: api.create, runStages: api.runStages, runStage: api.runStage },
    pipelineApi: {
      ...original.pipelineApi,
      schemas: api.schemas,
      discovery: api.discovery,
      profile: api.profile,
      quality: api.quality,
      evidence: api.evidence,
      mappings: api.mappings,
      bulkApprove: api.bulkApprove,
      approveMapping: api.approveMapping,
      patchMapping: api.patchMapping,
      fields: api.fields,
      rules: api.rules,
      putCodeMap: api.putCodeMap,
      preview: api.preview,
      dryRun: api.dryRun,
      reconciliation: api.reconciliation,
      quarantine: api.quarantine,
      rollback: api.rollback,
      generateRules: api.generateRules,
    },
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, sessionApi: { ...original.sessionApi, me: vi.fn().mockRejectedValue(new original.ApiError(401, "not_authenticated", "x")) } };
});

const job = (status: Job["status"], extra: Partial<Job> = {}): Job => ({
  id: "job-1",
  status,
  source_id: "src-1",
  data_source_id: null,
  target_schema_id: "schema-1",
  error_message: null,
  token_budget: 6000,
  tokens_used: 0,
  created_at: "2026-10-26T10:00:00Z",
  updated_at: "2026-10-26T10:00:00Z",
  started_at: null,
  completed_at: null,
  next_stage: null,
  source_name: "commerce_legacy.sqlite",
  source_kind: "sqlite",
  owner_name: "Demo Analyst",
  ...extra,
});

const mapping = (id: string, over: Partial<Mapping> = {}): Mapping => ({
  id,
  source_table: "CUST_MST",
  source_column: `col_${id}`,
  target: `customers.f_${id}`,
  method: "embedding",
  status: "suggested",
  decision: "review",
  confidence: 0.9,
  similarity: 0.8,
  margin: 0.1,
  candidates: [{ field: `customers.f_${id}`, similarity: 0.8 }],
  rationale: null,
  pii: false,
  ...over,
});

function renderStep(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter>
          <ToastProvider>{ui}</ToastProvider>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.schemas.mockResolvedValue([{ id: "schema-1", name: "commerce", version: "1", description: null, built_in: true }]);
  api.getSource.mockResolvedValue({ id: "src-1", name: "x", kind: "sqlite", original_filename: "x", size_bytes: 385024, sha256: null, is_sample: true, created_at: "2026-10-26T10:00:00Z" });
});

describe("mapping helpers", () => {
  const rows = [
    mapping("a", { status: "approved", decision: "auto", confidence: 0.95 }),
    mapping("b", { confidence: 0.9 }),
    mapping("c", { confidence: 0.7 }),
    mapping("d", { target: null, confidence: null, decision: "unmapped" }),
    mapping("e", { status: "rejected" }),
  ];

  it("says where a mapping stands", () => {
    expect(rows.map(mappingState)).toEqual(["approved", "review", "review", "unmapped", "rejected"]);
  });

  it("filters by state and by text", () => {
    expect(filterMappings(rows, "review", "").map((m) => m.id)).toEqual(["b", "c"]);
    expect(filterMappings(rows, "unmapped", "").map((m) => m.id)).toEqual(["d", "e"]);
    expect(filterMappings(rows, "all", "COL_B").map((m) => m.id)).toEqual(["b"]);
  });

  it("offers bulk approval only for rows waiting for review that are sure enough", () => {
    expect(HIGH_CONFIDENCE).toBe(0.85);
    expect(highConfidenceIds(rows)).toEqual(["b"]);
    expect(highConfidenceIds(rows, 0.6)).toEqual(["b", "c"]);
  });
});

describe("rule helpers", () => {
  const rule: Rule = {
    id: "r",
    target_table: "orders",
    target_field: "status",
    source_table: "INV_HDR",
    inputs: ["stat_cd"],
    steps: [{ op: "map_codes", mapping: { "1": "completed" }, passthrough: false }],
    status: "needs_input",
    origin: "deterministic",
    notes: [],
    failures: { missing: { codes: ["1", "2", "3"] } },
    sample_failure_count: null,
  };

  it("lists the codes that need a meaning and the ones that have one", () => {
    expect(codeMapOf(rule)).toEqual({ codes: ["1", "2", "3"], mapping: { "1": "completed" }, passthrough: false });
  });

  it("names the steps of a rule", () => {
    expect(describeSteps({ steps: [{ op: "trim" }, { op: "case" }] })).toBe("trim, case");
  });
});

describe("SourceStep", () => {
  const samples = [
    { name: "commerce", title: "Commerce (legacy SQLite)", description: "A shop.", target_schema: "commerce", files: ["commerce_legacy.sqlite"], available: true },
    { name: "clinic", title: "ClinicDesk (.NET binary)", description: "A clinic.", target_schema: "clinic", files: ["ClinicDesk.dll", "clinic.cdb"], available: true },
  ];

  it("creates the migration from a chosen sample and goes on", async () => {
    api.samples.mockResolvedValue(samples);
    api.registerSample.mockResolvedValue({ sample: "commerce", target_schema: "commerce", sources: [], source_id: "src-1", data_source_id: null });
    api.create.mockResolvedValue(job("CREATED"));
    const onCreated = vi.fn();
    renderStep(<SourceStep job={null} working onCreated={onCreated} />);
    const create = await screen.findByRole("button", { name: "Create the migration" });
    expect(create).toBeDisabled();
    await userEvent.click(await screen.findByRole("radio", { name: /Commerce \(legacy SQLite\)/ }));
    await waitFor(() => expect(create).toBeEnabled());
    await userEvent.click(create);
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("job-1"));
    expect(api.registerSample).toHaveBeenCalledWith("commerce");
    expect(api.create).toHaveBeenCalledWith({ source_id: "src-1", data_source_id: null, target_schema: "commerce" });
  });

  it("takes the target schema of the sample, and the person may change it", async () => {
    api.samples.mockResolvedValue(samples);
    api.schemas.mockResolvedValue([
      { id: "s1", name: "commerce", version: "1", description: null, built_in: true },
      { id: "s2", name: "clinic", version: "1", description: null, built_in: true },
    ]);
    renderStep(<SourceStep job={null} working onCreated={vi.fn()} />);
    await userEvent.click(await screen.findByRole("radio", { name: /ClinicDesk/ }));
    const select = await screen.findByLabelText("Target schema");
    await waitFor(() => expect(select).toHaveValue("clinic"));
    await userEvent.selectOptions(select, "commerce");
    expect(select).toHaveValue("commerce");
  });

  it("tells a viewer that they cannot start one", () => {
    renderStep(<SourceStep job={null} working={false} onCreated={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent(/can look at migrations but not start one/);
  });

  it("shows what an existing migration was made from", async () => {
    renderStep(<SourceStep job={job("PROFILED")} working onCreated={vi.fn()} onNext={vi.fn()} />);
    expect(await screen.findByText("commerce_legacy.sqlite")).toBeInTheDocument();
    expect(await screen.findByText("commerce")).toBeInTheDocument();
  });
});

describe("DiscoveryStep", () => {
  const discovery = { kind: "sqlite", engine: "SQLite", tables: [{ name: "CUST_MST", kind: "table", columns: [{ name: "cust_no", declared_type: "INTEGER" }] }] };

  it("offers to run discovery when it has not run", async () => {
    api.runStages.mockResolvedValue({ job_id: "job-1", stages: ["discover", "profile"], task_id: "t", execution: "eager" });
    renderStep(<DiscoveryStep job={job("CREATED")} events={[]} live={false} reconnect={vi.fn()} working onNext={vi.fn()} onBack={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Run discovery and profiling" }));
    await waitFor(() => expect(api.runStages).toHaveBeenCalledWith("job-1", ["discover", "profile"], undefined));
    expect(screen.getByRole("button", { name: "Continue to mapping" })).toBeDisabled();
  });

  it("shows the fields found and opens the evidence of one", async () => {
    api.discovery.mockResolvedValue(discovery);
    api.profile.mockResolvedValue({ tables: [{ name: "CUST_MST", rows: 60, columns: [{ name: "cust_no", table: "CUST_MST", pii: false, null_rate: 0, distinct_count: 59, inferred_type: "integer" }] }] });
    api.quality.mockResolvedValue({ score: 91.27, formula: "", tables: [], issue_counts: {}, issues_total: 0, issues_truncated: false });
    api.evidence.mockResolvedValue([{ id: "e1", subject: "CUST_MST.cust_no", command: "inspect-sqlite-database", rea_version: "6.7.0", duration_ms: null, raw_json: { kind: "sqlite_column", table: "CUST_MST" } }]);
    renderStep(<DiscoveryStep job={job("PROFILED")} events={[]} live={false} reconnect={vi.fn()} working onNext={vi.fn()} onBack={vi.fn()} />);
    expect(await screen.findByText("CUST_MST")).toBeInTheDocument();
    expect(await screen.findByText("91.3 of 100")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show the evidence for CUST_MST.cust_no" }));
    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByText("inspect-sqlite-database")).toBeInTheDocument();
    expect(within(drawer).getByText(/sqlite_column/)).toBeInTheDocument();
    expect(api.evidence).toHaveBeenCalledWith("job-1", "CUST_MST.cust_no", expect.anything());
  });

  it("goes on only after discovery", async () => {
    api.discovery.mockResolvedValue(discovery);
    api.profile.mockResolvedValue({ tables: [] });
    api.quality.mockResolvedValue({ score: 90, formula: "", tables: [], issue_counts: {}, issues_total: 0, issues_truncated: false });
    const onNext = vi.fn();
    renderStep(<DiscoveryStep job={job("PROFILED")} events={[]} live={false} reconnect={vi.fn()} working onNext={onNext} onBack={vi.fn()} />);
    const next = await screen.findByRole("button", { name: "Continue to mapping" });
    await waitFor(() => expect(next).toBeEnabled());
    await userEvent.click(next);
    expect(onNext).toHaveBeenCalled();
  });
});

describe("MappingStep", () => {
  const rows = [mapping("a", { status: "approved", decision: "auto", confidence: 0.95 }), mapping("b", { confidence: 0.9 }), mapping("c", { confidence: 0.7 }), mapping("d", { target: null, confidence: null, decision: "unmapped" })];

  function open(status: Job["status"] = "AWAITING_REVIEW", working = true, onNext = vi.fn()) {
    api.mappings.mockResolvedValue(rows);
    api.fields.mockResolvedValue([{ id: "f1", table: "customers", name: "email", type: "text", required: true, description: "Email address", synonyms: ["mail"], examples: [], pii: true }]);
    renderStep(<MappingStep job={job(status)} events={[]} reconnect={vi.fn()} schemaName="commerce" working={working} onNext={onNext} onBack={vi.fn()} />);
    return onNext;
  }

  it("shows each column with its target, confidence, method and state", async () => {
    open();
    expect(await screen.findByText("CUST_MST.col_b")).toBeInTheDocument();
    const row = screen.getByText("CUST_MST.col_b").closest("tr") as HTMLElement;
    expect(within(row).getByText("customers.f_b")).toBeInTheDocument();
    expect(within(row).getByRole("meter", { name: "Confidence of col_b" })).toHaveAttribute("aria-valuenow", "90");
    expect(within(row).getByText("Embedding")).toBeInTheDocument();
    expect(within(row).getByText("Needs review")).toBeInTheDocument();
    expect(screen.getByText("No target")).toBeInTheDocument();
  });

  it("approves every row of high confidence in one action", async () => {
    api.bulkApprove.mockResolvedValue({ approved: 1, skipped: [] });
    open();
    await userEvent.click(await screen.findByRole("button", { name: /Approve all with 85% confidence or more \(1\)/ }));
    await waitFor(() => expect(api.bulkApprove).toHaveBeenCalledWith("job-1", ["b"]));
  });

  it("accepts one mapping", async () => {
    api.approveMapping.mockResolvedValue({ id: "c", status: "approved" });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Accept the mapping of col_c" }));
    await waitFor(() => expect(api.approveMapping).toHaveBeenCalledWith("job-1", "c"));
  });

  it("overrides a target through a searchable picker", async () => {
    api.patchMapping.mockResolvedValue({ id: "d", status: "overridden", regenerate_rules: true });
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Choose another target for col_d" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByRole("searchbox"), "mail");
    await userEvent.click(await within(dialog).findByRole("button", { name: /customers\.email/ }));
    await waitFor(() => expect(api.patchMapping).toHaveBeenCalledWith("job-1", "d", { target_field_id: "f1" }));
    expect(await screen.findByText(/transform rules need to be generated again/)).toBeInTheDocument();
  });

  it("filters the table", async () => {
    open();
    await screen.findByText("CUST_MST.col_b");
    await userEvent.selectOptions(screen.getByLabelText("Show"), "unmapped");
    expect(screen.queryByText("CUST_MST.col_b")).not.toBeInTheDocument();
    expect(screen.getByText("CUST_MST.col_d")).toBeInTheDocument();
  });

  it("regenerates the rules before going on when a mapping changed", async () => {
    api.approveMapping.mockResolvedValue({ id: "c", status: "approved" });
    api.generateRules.mockResolvedValue({ status: "AWAITING_REVIEW" });
    const onNext = open();
    await userEvent.click(await screen.findByRole("button", { name: "Accept the mapping of col_c" }));
    await userEvent.click(await screen.findByRole("button", { name: "Regenerate the rules and continue" }));
    await waitFor(() => expect(api.generateRules).toHaveBeenCalledWith("job-1"));
    await waitFor(() => expect(onNext).toHaveBeenCalled());
  });

  it("offers no editing to a viewer", async () => {
    open("AWAITING_REVIEW", false);
    await screen.findByText("CUST_MST.col_b");
    expect(screen.queryByRole("button", { name: /Accept the mapping/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve all with/ })).not.toBeInTheDocument();
  });

  it("maps the columns when that has not happened", async () => {
    api.runStages.mockResolvedValue({ job_id: "job-1", stages: ["map", "rules"], task_id: "t", execution: "eager" });
    renderStep(<MappingStep job={job("PROFILED")} events={[]} reconnect={vi.fn()} schemaName="commerce" working onNext={vi.fn()} onBack={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Map the columns" }));
    await waitFor(() => expect(api.runStages).toHaveBeenCalledWith("job-1", ["map", "rules"], "commerce"));
  });
});

describe("TransformStep", () => {
  const rules: Rule[] = [
    { id: "r1", target_table: "customers", target_field: "city", source_table: "CUST_MST", inputs: ["city_nm"], steps: [{ op: "trim" }], status: "ready", origin: "deterministic", notes: [], failures: {}, sample_failure_count: null },
    {
      id: "r2",
      target_table: "orders",
      target_field: "status",
      source_table: "INV_HDR",
      inputs: ["stat_cd"],
      steps: [{ op: "map_codes", mapping: {} }],
      status: "needs_input",
      origin: "deterministic",
      notes: ["The meaning of numeric codes is not in the data: supply the code map"],
      failures: { missing: { codes: ["1", "2"] } },
      sample_failure_count: null,
    },
  ];

  it("asks for the meaning of codes and blocks the way on until it is given", async () => {
    api.rules.mockResolvedValue(rules);
    api.putCodeMap.mockResolvedValue({ ...rules[1], status: "ready" });
    api.preview.mockResolvedValue({ sampled: 60, failed: 0, failure_rate: 0, steps: ["trim"], rows: [] });
    renderStep(<TransformStep job={job("AWAITING_REVIEW")} working onNext={vi.fn()} onBack={vi.fn()} />);
    expect(await screen.findByText("This rule needs you")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue to the dry run" })).toBeDisabled();
    const save = screen.getByRole("button", { name: "Save the meanings" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Meaning of code 1"), "completed");
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Meaning of code 2"), "pending");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    await waitFor(() => expect(api.putCodeMap).toHaveBeenCalledWith("job-1", "r2", { "1": "completed", "2": "pending" }, false));
  });

  it("shows before and after side by side with the failure count", async () => {
    api.rules.mockResolvedValue([rules[0] as Rule]);
    api.preview.mockResolvedValue({
      sampled: 60,
      failed: 1,
      failure_rate: 0.0167,
      steps: ["trim"],
      rows: [
        { row: 1, before: { city_nm: "  Lisbon " }, after: "Lisbon", failed: false, kind: null },
        { row: 2, before: { city_nm: "???" }, after: null, failed: true, kind: "invalid" },
      ],
    });
    renderStep(<TransformStep job={job("AWAITING_REVIEW")} working onNext={vi.fn()} onBack={vi.fn()} />);
    const table = await screen.findByRole("table", { name: "Source values before the rule and the values after it" });
    expect(within(table).getAllByText("Lisbon")).toHaveLength(2); // the source value and the converted one
    expect(within(table).getByText("Failed: invalid")).toBeInTheDocument();
    expect(screen.getByText("1.7%")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue to the dry run" })).toBeEnabled();
  });
});

describe("DryRunStep", () => {
  const result = (over: Partial<DryRunResult> = {}): DryRunResult => ({
    status: "READY",
    ready: true,
    blocking: [],
    warnings: [],
    tables: [
      {
        target_table: "customers",
        source_table: "CUST_MST",
        sample_rows: 60,
        would_load: 58,
        would_quarantine: { required_null: 2 },
        rules: [{ target_field: "email", inputs: ["email_adr"], ops: ["trim"], rows: 60, nulls: 2, failures: 2, out_of_range: 0, step_failures: [2], failure_rate: 0.033 }],
        preview: [],
        source_rows: 60,
        seconds: 0.01,
      },
    ],
    cost: { source_rows: 6338, tokens_used: 1200, token_budget: 6000, estimated_seconds: 1.3, basis: "the speed of the sample" },
    ...over,
  });

  it("shows what would load, what would be quarantined and why, and the cost", async () => {
    api.dryRun.mockResolvedValue(result());
    const onNext = vi.fn();
    renderStep(<DryRunStep job={job("AWAITING_REVIEW")} working onNext={onNext} onBack={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Continue to execute" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Run the dry run" }));
    expect(await screen.findByText("A required value is missing:")).toBeInTheDocument();
    const cost = screen.getByRole("region", { name: "Cost summary" });
    expect(within(cost).getByText("6,338")).toBeInTheDocument();
    expect(within(cost).getByText("1 s")).toBeInTheDocument();
    expect(within(cost).getByRole("meter", { name: "Tokens used of the budget of this migration" })).toHaveAttribute("aria-valuenow", "1200");
    await userEvent.click(screen.getByRole("button", { name: "Continue to execute" }));
    expect(onNext).toHaveBeenCalled();
  });

  it("blocks the way on when the dry run is blocked, and says what to fix", async () => {
    api.dryRun.mockResolvedValue(result({ ready: false, blocking: ["The rule for orders.status needs input"] }));
    renderStep(<DryRunStep job={job("AWAITING_REVIEW")} working onNext={vi.fn()} onBack={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Run the dry run" }));
    expect(await screen.findByText("The rule for orders.status needs input")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue to execute" })).toBeDisabled();
  });
});

describe("ExecuteStep", () => {
  it("asks before it loads and then starts the load stage", async () => {
    api.runStage.mockResolvedValue({ job_id: "job-1", stages: ["load"], task_id: "t", execution: "eager" });
    renderStep(<ExecuteStep job={job("READY")} events={[]} live={false} reconnect={vi.fn()} working onNext={vi.fn()} onBack={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Load the data" }));
    const dialog = await screen.findByRole("dialog");
    expect(api.runStage).not.toHaveBeenCalled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Load the data" }));
    await waitFor(() => expect(api.runStage).toHaveBeenCalledWith("job-1", "load"));
  });

  it("shows the stage timeline from the events and lets a finished load go to the report", () => {
    const events = [
      { id: 1, from_status: "READY", to_status: "LOADING", message: "Loading into staging tables", detail: {}, created_at: "2026-10-26T10:00:00Z" },
      { id: 2, from_status: "RECONCILING", to_status: "COMPLETED", message: "Load and reconciliation finished", detail: {}, created_at: "2026-10-26T10:00:03Z" },
    ] as never;
    renderStep(<ExecuteStep job={job("COMPLETED")} events={events} live={false} reconnect={vi.fn()} working onNext={vi.fn()} onBack={vi.fn()} />);
    expect(screen.getByRole("list", { name: "Pipeline stages" })).toBeInTheDocument();
    expect(screen.getByText(/Done in 3\.0 s/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View the reconciliation report" })).toBeEnabled();
  });

  it("offers no load to a viewer", () => {
    renderStep(<ExecuteStep job={job("READY")} events={[]} live={false} reconnect={vi.fn()} working={false} onNext={vi.fn()} onBack={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Load the data" })).not.toBeInTheDocument();
  });
});

describe("ReportStep", () => {
  const report: Reconciliation = {
    passed: true,
    source_rows: 6338,
    loaded_rows: 4216,
    quarantined_rows: 2122,
    quality_before: 91.27,
    quality_after: 99.32,
    checks: {
      passed: true,
      tables: [
        {
          target_table: "customers",
          source_table: "CUST_MST",
          source_rows: 60,
          staged_rows: 60,
          loaded: 57,
          quarantined: 3,
          by_reason: { required_null: 2, duplicate: 1 },
          unexplained: 0,
          passed: true,
          failed: [],
          checks: {
            row_accounting: { source: 60, staged: 60, loaded: 57, quarantined: 3, ok: true },
            every_quarantined_row_has_a_reason: { without_reason: 0, ok: true },
            distinct_keys: { source: 59, loaded: 57, only_in_quarantine: 2, ok: true },
            null_counts: {},
            numeric_sums: { unit_price: { expected: "2448.27", loaded: "2448.27", ok: true } },
            sampled_rows: { checked: 109, matched: 109, mismatches: [], ok: true },
          },
        },
      ],
    },
  };

  it("shows the counts, the checks per table and the quality before and after", async () => {
    api.reconciliation.mockResolvedValue(report);
    api.quarantine.mockResolvedValue({ total: 1, rows: [{ table: "customers", row: 2, reason: "required_null", detail: "email: required_null", raw: { f_nm: "Leonie" } }], status: "COMPLETED" });
    renderStep(<ReportStep job={job("COMPLETED")} working onBack={vi.fn()} />);
    expect(await screen.findByText("All checks passed")).toBeInTheDocument();
    expect(screen.getByText("6,338")).toBeInTheDocument();
    expect(screen.getByText("4,216")).toBeInTheDocument();
    const checks = screen.getByRole("table", { name: "Reconciliation checks per table" });
    expect(within(checks).getByText("57 + 3 = 60")).toBeInTheDocument();
    expect(within(checks).getByText("109 of 109 match")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /after the migration: 99\.3 of 100, before it 91\.3/ })).toBeInTheDocument();
    expect(await screen.findByText("A required value is missing")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Export as CSV/ })).toHaveAttribute("href", "/api/v1/jobs/job-1/quarantine.csv");
  });

  it("filters the quarantine by reason and exports the same filter", async () => {
    api.reconciliation.mockResolvedValue(report);
    api.quarantine.mockResolvedValue({ total: 0, rows: [], status: "COMPLETED" });
    renderStep(<ReportStep job={job("COMPLETED")} working onBack={vi.fn()} />);
    await userEvent.selectOptions(await screen.findByLabelText("Reason"), "duplicate");
    await waitFor(() => expect(api.quarantine).toHaveBeenLastCalledWith("job-1", { reason: "duplicate", limit: 20, offset: 0 }, expect.anything()));
    expect(screen.getByRole("link", { name: /Export as CSV/ })).toHaveAttribute("href", "/api/v1/jobs/job-1/quarantine.csv?reason=duplicate");
  });

  it("rolls back only after a confirmation", async () => {
    api.reconciliation.mockResolvedValue(report);
    api.quarantine.mockResolvedValue({ total: 0, rows: [], status: "COMPLETED" });
    api.rollback.mockResolvedValue({ status: "ROLLED_BACK" });
    renderStep(<ReportStep job={job("COMPLETED")} working onBack={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Roll back this migration" }));
    expect(api.rollback).not.toHaveBeenCalled();
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Roll it back" }));
    await waitFor(() => expect(api.rollback).toHaveBeenCalledWith("job-1"));
  });

  it("offers no rollback to a viewer", async () => {
    api.reconciliation.mockResolvedValue(report);
    api.quarantine.mockResolvedValue({ total: 0, rows: [], status: "COMPLETED" });
    renderStep(<ReportStep job={job("COMPLETED")} working={false} onBack={vi.fn()} />);
    await screen.findByText("All checks passed");
    expect(screen.queryByRole("button", { name: "Roll back this migration" })).not.toBeInTheDocument();
  });
});

describe("QualityGauge", () => {
  it("describes itself in words", () => {
    render(<QualityGauge before={80} after={96.5} />);
    expect(screen.getByRole("img")).toHaveAccessibleName("Data quality score after the migration: 96.5 of 100, before it 80.0");
  });

  it("copes with no score", () => {
    render(<QualityGauge before={null} after={null} />);
    expect(screen.getByRole("img")).toHaveAccessibleName("No quality score");
  });
});
