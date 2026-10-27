import type { JobEvent, JobStatus, Stage } from "../api";

export type StepId = "source" | "discovery" | "mapping" | "transform" | "dryrun" | "execute" | "report";

export const STEPS: { id: StepId; label: string; short: string }[] = [
  { id: "source", label: "Source", short: "Source" },
  { id: "discovery", label: "Discovery", short: "Discovery" },
  { id: "mapping", label: "Mapping review", short: "Mapping" },
  { id: "transform", label: "Transform preview", short: "Transform" },
  { id: "dryrun", label: "Dry run", short: "Dry run" },
  { id: "execute", label: "Execute and monitor", short: "Execute" },
  { id: "report", label: "Reconciliation report", short: "Report" },
];

/** The furthest step that a job in this state has reached, which is where the wizard opens when someone returns to it. */
export function stepForStatus(status: JobStatus): StepId {
  switch (status) {
    case "CREATED":
    case "UPLOADED":
    case "DISCOVERING":
      return "discovery";
    case "PROFILED":
      return "mapping";
    case "MAPPED":
      return "mapping";
    case "AWAITING_REVIEW":
      return "transform";
    case "DRY_RUN":
      return "dryrun";
    case "READY":
      return "dryrun";
    case "LOADING":
    case "RECONCILING":
      return "execute";
    case "COMPLETED":
    case "ROLLED_BACK":
      return "report";
    case "FAILED":
      return "execute";
  }
}

export function stepIndex(id: StepId): number {
  return STEPS.findIndex((s) => s.id === id);
}

/** How far a job is through the seven steps, as a number from 0 to 1, for the progress bars of the overview. */
export function progressOf(status: JobStatus): number {
  const done: Record<JobStatus, number> = {
    CREATED: 1,
    UPLOADED: 1,
    DISCOVERING: 1.5,
    PROFILED: 2,
    MAPPED: 2.5,
    AWAITING_REVIEW: 3,
    DRY_RUN: 4,
    READY: 5,
    LOADING: 5.5,
    RECONCILING: 6,
    COMPLETED: 7,
    ROLLED_BACK: 7,
    FAILED: 0,
  }; // fmt: skip
  return done[status] / STEPS.length;
}

/**
 * The furthest step a person may open in a state. It is one past the step the job opens at when the next step is where the next
 * action is taken: the dry run is started in the dry run step, and the load in the execute step.
 */
export function furthestStep(status: JobStatus): StepId {
  switch (status) {
    case "AWAITING_REVIEW":
    case "DRY_RUN":
      return "dryrun";
    case "READY":
    case "LOADING":
    case "RECONCILING":
    case "FAILED":
      return "execute";
    default:
      return stepForStatus(status);
  }
}

/** Going back to a finished step is always allowed; going ahead only as far as the state of the job allows. */
export function reachable(status: JobStatus | null, id: StepId): boolean {
  if (status === null) return id === "source";
  if (id === "source") return true;
  return stepIndex(id) <= stepIndex(furthestStep(status));
}

// ------------------------------------------------------------------ the stage timeline of a job, from its events

export type StageState = "pending" | "running" | "done" | "failed";
export type StageRow = { stage: Stage; label: string; state: StageState; startedAt: string | null; finishedAt: string | null; seconds: number | null; message: string | null };

export const STAGE_LABELS: Record<Stage, string> = {
  discover: "Discover the structure",
  profile: "Profile the data",
  map: "Map to the target schema",
  rules: "Generate transform rules",
  dry_run: "Dry run on a sample",
  load: "Load, check and reconcile",
};

const STARTED = /^Stage (\w+) started/;
const FINISHED = /^Stage (\w+) finished/;
const FAILED = /^Stage (\w+) failed/;

/**
 * The dry run and the load can also be started by their own endpoints, which write state changes instead of "Stage x" notes.
 * Those messages mark the same beginnings and ends.
 */
const ALT_STARTED: [RegExp, Stage][] = [
  [/^Dry run on a sample/, "dry_run"],
  [/^Loading into staging tables/, "load"],
];
const ALT_FINISHED: [RegExp, Stage][] = [
  [/^The dry run found/, "dry_run"],
  [/^Load and reconciliation finished/, "load"],
];

function classify(message: string): { stage: Stage; kind: "started" | "finished" | "failed" } | null {
  const started = STARTED.exec(message)?.[1];
  if (started) return { stage: started as Stage, kind: "started" };
  const finished = FINISHED.exec(message)?.[1];
  if (finished) return { stage: finished as Stage, kind: "finished" };
  const failed = FAILED.exec(message)?.[1];
  if (failed) return { stage: failed as Stage, kind: "failed" };
  const altStart = ALT_STARTED.find(([re]) => re.test(message));
  if (altStart) return { stage: altStart[1], kind: "started" };
  const altEnd = ALT_FINISHED.find(([re]) => re.test(message));
  if (altEnd) return { stage: altEnd[1], kind: "finished" };
  return null;
}

/**
 * The state of each pipeline stage, read from the job's events (the same ones the live stream delivers): a stage starts with
 * "Stage x started" and ends with "Stage x finished" or "Stage x failed". Stages with no event yet are pending.
 */
export function stageTimeline(events: JobEvent[], stages: Stage[] = ["discover", "profile", "map", "rules", "dry_run", "load"]): StageRow[] {
  const rows = new Map<Stage, StageRow>(
    stages.map((stage) => [stage, { stage, label: STAGE_LABELS[stage], state: "pending", startedAt: null, finishedAt: null, seconds: null, message: null }]),
  );
  for (const event of [...events].sort((a, b) => a.id - b.id)) {
    const hit = classify(event.message ?? "");
    const row = hit ? rows.get(hit.stage) : undefined;
    if (!hit || !row) continue;
    if (hit.kind === "started") {
      row.state = "running";
      row.startedAt = event.created_at;
      row.finishedAt = null;
      row.seconds = null;
      row.message = null;
    } else {
      row.state = hit.kind === "failed" ? "failed" : "done";
      row.finishedAt = event.created_at;
      row.message = hit.kind === "failed" ? event.message : null;
      if (row.startedAt) row.seconds = Math.max(0, (Date.parse(event.created_at) - Date.parse(row.startedAt)) / 1000);
    }
  }
  return [...rows.values()];
}

/** Events in order, without repeats (a reconnected stream resends the last one). */
export function mergeEvents(current: JobEvent[], incoming: JobEvent[]): JobEvent[] {
  if (incoming.length === 0) return current;
  const seen = new Set(current.map((e) => e.id));
  const added = incoming.filter((e) => !seen.has(e.id));
  return added.length === 0 ? current : [...current, ...added].sort((a, b) => a.id - b.id);
}
