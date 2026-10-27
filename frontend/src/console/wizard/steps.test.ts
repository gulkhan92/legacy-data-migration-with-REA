import { describe, expect, it } from "vitest";
import type { JobEvent, JobStatus } from "../api";
import { mergeEvents, progressOf, reachable, STEPS, stageTimeline, stepForStatus } from "./steps";

const event = (id: number, message: string | null, at = "2026-10-26T10:00:00Z", to: JobStatus = "DISCOVERING"): JobEvent => ({
  id,
  from_status: to,
  to_status: to,
  message,
  detail: {},
  created_at: at,
});

describe("the steps", () => {
  it("are the seven of the plan, in order", () => {
    expect(STEPS.map((s) => s.id)).toEqual(["source", "discovery", "mapping", "transform", "dryrun", "execute", "report"]);
  });

  it("open at the furthest step a job has reached", () => {
    expect(stepForStatus("CREATED")).toBe("discovery");
    expect(stepForStatus("PROFILED")).toBe("mapping");
    expect(stepForStatus("AWAITING_REVIEW")).toBe("transform");
    expect(stepForStatus("READY")).toBe("dryrun");
    expect(stepForStatus("LOADING")).toBe("execute");
    expect(stepForStatus("COMPLETED")).toBe("report");
    expect(stepForStatus("FAILED")).toBe("execute");
  });

  it("let a person go back but not ahead", () => {
    expect(reachable(null, "source")).toBe(true);
    expect(reachable(null, "discovery")).toBe(false);
    expect(reachable("AWAITING_REVIEW", "mapping")).toBe(true);
    expect(reachable("AWAITING_REVIEW", "transform")).toBe(true);
    expect(reachable("AWAITING_REVIEW", "dryrun")).toBe(true); // the dry run is started in its own step
    expect(reachable("AWAITING_REVIEW", "execute")).toBe(false);
    expect(reachable("READY", "execute")).toBe(true);
    expect(reachable("READY", "report")).toBe(false);
    expect(reachable("PROFILED", "transform")).toBe(false);
    expect(reachable("COMPLETED", "report")).toBe(true);
  });

  it("measure progress between zero and one", () => {
    expect(progressOf("CREATED")).toBeGreaterThan(0);
    expect(progressOf("COMPLETED")).toBe(1);
    expect(progressOf("FAILED")).toBe(0);
    const order: JobStatus[] = ["CREATED", "DISCOVERING", "PROFILED", "MAPPED", "AWAITING_REVIEW", "DRY_RUN", "READY", "LOADING", "RECONCILING", "COMPLETED"];
    const values = order.map(progressOf);
    expect([...values].sort((a, b) => a - b)).toEqual(values);
  });
});

describe("stageTimeline", () => {
  it("has every stage waiting before anything happens", () => {
    expect(stageTimeline([]).map((r) => r.state)).toEqual(Array(6).fill("pending"));
  });

  it("reads started, finished and the time a stage took", () => {
    const rows = stageTimeline([event(1, "Stage discover started", "2026-10-26T10:00:00Z"), event(2, "Stage discover finished", "2026-10-26T10:00:08Z"), event(3, "Stage profile started", "2026-10-26T10:00:09Z")]);
    expect(rows[0]).toMatchObject({ stage: "discover", state: "done", seconds: 8 });
    expect(rows[1]).toMatchObject({ stage: "profile", state: "running", seconds: null });
    expect(rows[2]?.state).toBe("pending");
  });

  it("shows a failed stage with its message", () => {
    const rows = stageTimeline([event(1, "Stage map started"), event(2, "Stage map failed after 0 retries (boom)", "2026-10-26T10:00:03Z", "FAILED")]);
    expect(rows.find((r) => r.stage === "map")).toMatchObject({ state: "failed", message: "Stage map failed after 0 retries (boom)" });
  });

  it("understands the dry run and the load that have their own endpoints", () => {
    const rows = stageTimeline([
      event(1, "Dry run on a sample", "2026-10-26T10:00:00Z", "DRY_RUN"),
      event(2, "The dry run found nothing that blocks a load", "2026-10-26T10:00:05Z", "READY"),
      event(3, "Loading into staging tables", "2026-10-26T10:01:00Z", "LOADING"),
      event(4, "Load and reconciliation finished", "2026-10-26T10:01:30Z", "COMPLETED"),
    ]);
    expect(rows.find((r) => r.stage === "dry_run")).toMatchObject({ state: "done", seconds: 5 });
    expect(rows.find((r) => r.stage === "load")).toMatchObject({ state: "done", seconds: 30 });
  });

  it("goes by event order, not by the order they were handed in", () => {
    const rows = stageTimeline([event(2, "Stage discover finished", "2026-10-26T10:00:08Z"), event(1, "Stage discover started", "2026-10-26T10:00:00Z")]);
    expect(rows[0]?.state).toBe("done");
  });

  it("starts a stage over when it is run again", () => {
    const rows = stageTimeline([event(1, "Stage rules started"), event(2, "Stage rules finished"), event(3, "Stage rules started")]);
    expect(rows.find((r) => r.stage === "rules")?.state).toBe("running");
  });
});

describe("mergeEvents", () => {
  it("keeps order and drops repeats", () => {
    const merged = mergeEvents([event(1, "a"), event(3, "c")], [event(2, "b"), event(3, "c")]);
    expect(merged.map((e) => e.id)).toEqual([1, 2, 3]);
  });

  it("returns the same list when nothing is new", () => {
    const a = [event(1, "a")];
    expect(mergeEvents(a, [])).toBe(a);
    expect(mergeEvents(a, [event(1, "a")])).toBe(a);
  });
});
