import { describe, expect, it } from "vitest";
import {
  addMonths,
  changeOf,
  cohortCells,
  compactTick,
  describeSeries,
  downsample,
  escapeHtml,
  forecastLinear,
  histogramBins,
  mrrWaterfall,
  per1000,
  periodLabel,
  pluck,
  shares,
  topWithOther,
  unitEconomics,
} from "./transforms";

describe("addMonths", () => {
  it("moves by months across a year end", () => {
    expect(addMonths("2026-10-01", 1)).toBe("2026-11-01");
    expect(addMonths("2026-11-15", 3)).toBe("2027-02-01");
    expect(addMonths("2026-01-01", -1)).toBe("2025-12-01");
  });
});

describe("forecastLinear", () => {
  const periods = ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01"];

  it("continues a straight line", () => {
    const projection = forecastLinear(periods, [100, 200, 300, 400, 500], 3);
    expect(projection?.values).toEqual([600, 700, 800]);
    expect(projection?.periods).toEqual(["2026-06-01", "2026-07-01", "2026-08-01"]);
    expect(projection?.slope).toBeCloseTo(100);
  });

  it("is flat for a flat series", () => {
    expect(forecastLinear(periods, [50, 50, 50, 50, 50], 2)?.values).toEqual([50, 50]);
  });

  it("never goes below zero", () => {
    expect(forecastLinear(periods, [400, 300, 200, 100, 0], 3)?.values).toEqual([0, 0, 0]);
  });

  it("uses only the last window of points", () => {
    const projection = forecastLinear(periods, [1000, 0, 100, 200, 300], 1, 4);
    expect(projection?.basis).toBe(4);
    expect(projection?.values).toEqual([400]);
  });

  it("needs at least three points and a horizon", () => {
    expect(forecastLinear(periods.slice(0, 2), [1, 2], 3)).toBeNull();
    expect(forecastLinear(periods, [1, 2, 3, 4, 5], 0)).toBeNull();
    expect(forecastLinear([], [], 3)).toBeNull();
  });
});

describe("mrrWaterfall", () => {
  const steps = mrrWaterfall({ mrr_start_cents: 1000, new_mrr_cents: 200, expansion_mrr_cents: 100, contraction_mrr_cents: 50, churned_mrr_cents: 150, mrr_cents: 1100 });

  it("starts and ends with totals", () => {
    expect(steps.map((s) => s.key)).toEqual(["start", "new", "expansion", "contraction", "churned", "end"]);
    expect(steps[0]).toMatchObject({ base: 0, value: 1000, kind: "total" });
    expect(steps[5]).toMatchObject({ base: 0, value: 1100, kind: "total" });
  });

  it("floats each movement where the last one ended", () => {
    expect(steps[1]).toMatchObject({ base: 1000, value: 200, kind: "up" });
    expect(steps[2]).toMatchObject({ base: 1200, value: 100, kind: "up" });
    expect(steps[3]).toMatchObject({ base: 1250, value: 50, kind: "down", signed: -50 });
    expect(steps[4]).toMatchObject({ base: 1100, value: 150, kind: "down", signed: -150 });
  });

  it("ends where the last movement ended when the figures add up", () => {
    expect(steps[4]!.base).toBe(steps[5]!.value);
  });
});

describe("series helpers", () => {
  it("downsamples to at most the count, keeping the last value", () => {
    const values = Array.from({ length: 100 }, (_, i) => i);
    const out = downsample(values, 12);
    expect(out).toHaveLength(12);
    expect(out[out.length - 1]).toBe(99);
    expect(downsample([1, 2, 3], 12)).toEqual([1, 2, 3]);
  });

  it("plucks numbers and leaves gaps out", () => {
    expect(pluck([{ v: 1 }, { v: null }, { v: 3 }, { v: undefined }], (r) => r.v)).toEqual([1, 3]);
  });

  it("divides per thousand and has no ratio without records", () => {
    expect(per1000(500, 2000)).toBe(250);
    expect(per1000(500, 0)).toBeNull();
  });

  it("joins revenue and operations by period", () => {
    const result = unitEconomics(
      [
        { period: "2026-08-01", total_revenue_cents: 100_000 },
        { period: "2026-09-01", total_revenue_cents: 200_000 },
        { period: "2026-10-01", total_revenue_cents: 300_000 },
      ],
      [
        { period: "2026-08-01", records: 1000, tokens_total: 50 },
        { period: "2026-09-01", records: 0, tokens_total: 10 },
      ],
    );
    expect(result.revenuePer1000).toEqual([
      { period: "2026-08-01", value: 1000 },
      { period: "2026-09-01", value: null },
    ]);
    expect(result.tokensPer1000[0]).toEqual({ period: "2026-08-01", value: 50 });
  });

  it("folds the tail into Other and never invents an eighth hue", () => {
    const entries: [string, number][] = Array.from({ length: 10 }, (_, i) => [`k${i}`, 10 - i]);
    const out = topWithOther(entries, 7);
    expect(out).toHaveLength(8);
    expect(out[7]).toEqual(["Other", 3 + 2 + 1]);
    expect(out[0]).toEqual(["k0", 10]);
    expect(topWithOther([["a", 1]], 7)).toEqual([["a", 1]]);
  });

  it("computes shares that sum to one", () => {
    const parts = shares({ memory: 3, llm: 1 }, ["memory", "embedding", "llm"]);
    expect(parts.map((p) => p.share)).toEqual([0.75, 0, 0.25]);
    expect(shares({}, ["a"])[0]?.share).toBe(0);
  });
});

describe("histogram and cohorts", () => {
  it("labels equal bins", () => {
    const bins = histogramBins([0, 0, 1, 2, 3, 4, 5, 6, 7, 8]);
    expect(bins[0]).toEqual({ label: "0-10%", count: 0 });
    expect(bins[9]).toEqual({ label: "90-100%", count: 8 });
  });

  it("makes a cell for every known month and none for months that have not happened", () => {
    const { cells, rows, columns } = cohortCells([
      { month: "2025-01", size: 4, logos: [100, 75, null], mrr: [] },
      { month: "2025-02", size: 2, logos: [100], mrr: [] },
    ]);
    expect(rows).toEqual(["2025-01", "2025-02"]);
    expect(columns).toBe(2);
    expect(cells).toEqual([
      { x: 0, y: 0, value: 100 },
      { x: 1, y: 0, value: 75 },
      { x: 0, y: 1, value: 100 },
    ]);
  });
});

describe("labels and text", () => {
  it("formats period ticks", () => {
    expect(periodLabel("2026-10-01", "month")).toBe("Oct 26");
    expect(periodLabel("2026-10-22", "day")).toBe("Oct 22");
  });

  it("compacts axis ticks", () => {
    expect(compactTick(0)).toBe("0");
    expect(compactTick(1500)).toBe("1.5K");
    expect(compactTick(2_000_000)).toBe("2M");
  });

  it("escapes what goes into a tooltip", () => {
    expect(escapeHtml(`<img src=x onerror="a(1)">&`)).toBe("&lt;img src=x onerror=&quot;a(1)&quot;&gt;&amp;");
    expect(escapeHtml("it's")).toBe("it&#39;s");
  });

  it("reads a change out of a comparison", () => {
    expect(changeOf({ jobs: { change: 0.1 } }, "jobs")).toBe(0.1);
    expect(changeOf({ jobs: { change: null } }, "jobs")).toBeNull();
    expect(changeOf(undefined, "jobs")).toBeNull();
  });

  it("describes a series in words", () => {
    const text = describeSeries("MRR", ["2026-01-01", "2026-02-01", "2026-03-01"], [10, 30, 20], (v) => `${v}`);
    expect(text).toContain("3 points from 2026-01-01 to 2026-03-01");
    expect(text).toContain("starts at 10 and ends at 20");
    expect(text).toContain("highest is 30 on 2026-02-01");
    expect(describeSeries("MRR", [], [], String)).toBe("MRR: no data in this range.");
  });
});
