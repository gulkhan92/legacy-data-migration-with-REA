/** The arithmetic behind the charts, kept apart from drawing so that it can be tested: forecasts, waterfalls, bins, matrices, tick formats. */
import type { Cohort, RevenuePoint } from "../api";

// ------------------------------------------------------------------ forecast

export type Projection = { periods: string[]; values: number[]; slope: number; basis: number };

/** The date `steps` periods after `period` ("2026-10-01"), for a monthly series. */
export function addMonths(period: string, steps: number): string {
  const [y, m] = period.slice(0, 7).split("-").map(Number) as [number, number];
  const index = y * 12 + (m - 1) + steps;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}-01`;
}

/**
 * A straight line through the last `window` points (ordinary least squares), continued for `horizon` months. It is a projection, not
 * a prediction: it assumes the recent trend continues, and says nothing of churn or pricing. Never below zero.
 */
export function forecastLinear(periods: string[], values: number[], horizon = 6, window = 12): Projection | null {
  const n = Math.min(window, values.length);
  if (n < 3 || horizon < 1) return null;
  const ys = values.slice(-n);
  const xs = ys.map((_, i) => i);
  const meanX = (n - 1) / 2;
  const meanY = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i++) {
    num += (xs[i]! - meanX) * (ys[i]! - meanY);
    den += (xs[i]! - meanX) ** 2;
  }
  const slope = den === 0 ? 0 : num / den;
  const intercept = meanY - slope * meanX;
  const last = periods[periods.length - 1];
  if (!last) return null;
  const out: number[] = [];
  const labels: string[] = [];
  for (let k = 1; k <= horizon; k++) {
    out.push(Math.max(0, Math.round(intercept + slope * (n - 1 + k))));
    labels.push(addMonths(last, k));
  }
  return { periods: labels, values: out, slope, basis: n };
}

// ------------------------------------------------------------------ waterfall

export type WaterfallStep = {
  key: string;
  label: string;
  /** Where the bar starts and how tall it is, both as positive numbers. */ base: number;
  value: number;
  kind: "total" | "up" | "down";
  signed: number;
};

/** Opening MRR, the four movements, closing MRR: each bar floats where the previous one ended. */
export function mrrWaterfall(k: {
  mrr_start_cents: number;
  new_mrr_cents: number;
  expansion_mrr_cents: number;
  contraction_mrr_cents: number;
  churned_mrr_cents: number;
  mrr_cents: number;
}): WaterfallStep[] {
  const steps: WaterfallStep[] = [{ key: "start", label: "MRR at the start", base: 0, value: k.mrr_start_cents, kind: "total", signed: k.mrr_start_cents }];
  let level = k.mrr_start_cents;
  const move = (key: string, label: string, amount: number, sign: 1 | -1) => {
    const signed = sign * amount;
    const next = level + signed;
    steps.push({ key, label, base: Math.min(level, next), value: Math.abs(signed), kind: sign === 1 ? "up" : "down", signed });
    level = next;
  };
  move("new", "New", k.new_mrr_cents, 1);
  move("expansion", "Expansion", k.expansion_mrr_cents, 1);
  move("contraction", "Contraction", k.contraction_mrr_cents, -1);
  move("churned", "Churn", k.churned_mrr_cents, -1);
  steps.push({ key: "end", label: "MRR at the end", base: 0, value: k.mrr_cents, kind: "total", signed: k.mrr_cents });
  return steps;
}

// ------------------------------------------------------------------ series helpers

/** At most `count` points to draw as a sparkline, taking the last value of each equal slice so the final point is always the latest. */
export function downsample(values: number[], count = 12): number[] {
  if (values.length <= count) return values;
  const out: number[] = [];
  for (let i = 0; i < count; i++) {
    const end = Math.round(((i + 1) * values.length) / count);
    out.push(values[Math.max(0, end - 1)]!);
  }
  return out;
}

/** Values of one field of a series, with gaps (null) kept out. */
export function pluck<T>(rows: T[], pick: (row: T) => number | null | undefined): number[] {
  return rows.map(pick).filter((v): v is number => typeof v === "number" && Number.isFinite(v));
}

/** Customers per record or tokens per record, scaled to a thousand records. A period without records has no ratio. */
export function per1000(amount: number, records: number): number | null {
  return records > 0 ? (amount / records) * 1000 : null;
}

export type Ratio = { period: string; value: number | null };

/** Two series of the same periods divided, period by period: revenue per 1,000 records, tokens per 1,000 records. */
export function unitEconomics(
  revenue: Pick<RevenuePoint, "period" | "total_revenue_cents">[],
  ops: { period: string; records: number; tokens_total: number }[],
): { revenuePer1000: Ratio[]; tokensPer1000: Ratio[] } {
  const byPeriod = new Map(ops.map((o) => [o.period, o]));
  const revenuePer1000: Ratio[] = [];
  const tokensPer1000: Ratio[] = [];
  for (const r of revenue) {
    const o = byPeriod.get(r.period);
    if (!o) continue;
    const dollars = per1000(r.total_revenue_cents / 100, o.records);
    revenuePer1000.push({ period: r.period, value: dollars });
    tokensPer1000.push({ period: r.period, value: per1000(o.tokens_total, o.records) });
  }
  return { revenuePer1000, tokensPer1000 };
}

/** Largest first, the tail folded into "Other" once more than `keep` remain (a ninth hue is never invented). */
export function topWithOther(entries: [string, number][], keep = 7): [string, number][] {
  const sorted = [...entries].sort((a, b) => b[1] - a[1]);
  if (sorted.length <= keep) return sorted;
  const head = sorted.slice(0, keep);
  const rest = sorted.slice(keep).reduce((n, [, v]) => n + v, 0);
  return [...head, ["Other", rest]];
}

/** Shares that sum to one from counts, for a stacked bar of a whole. */
export function shares(counts: Record<string, number>, order: string[]): { key: string; count: number; share: number }[] {
  const total = order.reduce((n, k) => n + (counts[k] ?? 0), 0);
  return order.map((key) => ({ key, count: counts[key] ?? 0, share: total > 0 ? (counts[key] ?? 0) / total : 0 }));
}

// ------------------------------------------------------------------ histogram, cohorts

/** Ten equal bins of a 0 to 1 score: "0-10%", "10-20%", and so on, with the count in each. */
export function histogramBins(counts: number[]): { label: string; count: number }[] {
  const width = counts.length > 0 ? 100 / counts.length : 10;
  return counts.map((count, i) => ({ label: `${Math.round(i * width)}-${Math.round((i + 1) * width)}%`, count }));
}

export type HeatCell = { x: number; y: number; value: number };

/** Cohorts by months since signup: a cell per cohort and month, the share of customers still active (empty where the cohort is too young). */
export function cohortCells(cohorts: Cohort[]): { cells: HeatCell[]; rows: string[]; columns: number } {
  const cells: HeatCell[] = [];
  let columns = 0;
  cohorts.forEach((c, y) => {
    c.logos.forEach((value, x) => {
      if (value === null || value === undefined) return;
      cells.push({ x, y, value });
      columns = Math.max(columns, x + 1);
    });
  });
  return { cells, rows: cohorts.map((c) => c.month), columns };
}

// ------------------------------------------------------------------ labels and ticks

/** "2026-10-01" as a tick: the month and year for monthly points, day and month for daily ones. */
export function periodLabel(period: string, granularity: "day" | "week" | "month"): string {
  const d = new Date(`${period.slice(0, 10)}T00:00:00Z`);
  const opts: Intl.DateTimeFormatOptions = granularity === "month" ? { month: "short", year: "2-digit", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" };
  return new Intl.DateTimeFormat("en-US", opts).format(d);
}

/** Axis ticks that read cleanly: 0, 1K, 2.5M. */
export function compactTick(value: number): string {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}

/** A change the API reports as { current, previous, change } as a delta for a tile. */
export function changeOf(change: Record<string, { change: number | null } | undefined> | undefined, key: string): number | null {
  const c = change?.[key]?.change;
  return typeof c === "number" ? c : null;
}

/** The text a screen reader gets for a line or column series: what it plots, from where to where, the first and last value, the highest. */
export function describeSeries(name: string, periods: string[], values: (number | null)[], format: (v: number) => string): string {
  const points = values.map((v, i) => ({ v, p: periods[i] ?? "" })).filter((x): x is { v: number; p: string } => x.v !== null);
  if (points.length === 0) return `${name}: no data in this range.`;
  const first = points[0]!;
  const last = points[points.length - 1]!;
  const high = points.reduce((a, b) => (b.v > a.v ? b : a));
  const low = points.reduce((a, b) => (b.v < a.v ? b : a));
  return `${name}, ${points.length} points from ${first.p.slice(0, 10)} to ${last.p.slice(0, 10)}. It starts at ${format(first.v)} and ends at ${format(last.v)}. The highest is ${format(high.v)} on ${high.p.slice(0, 10)}, the lowest ${format(low.v)} on ${low.p.slice(0, 10)}.`;
}
