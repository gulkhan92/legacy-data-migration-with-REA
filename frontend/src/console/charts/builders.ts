/**
 * Chart options built to the chart standards: 2 px lines, bars at most 24 px thick with a 4 px rounded data end and a square base, a 2 px
 * gap of the surface color between touching marks, hairline solid gridlines, ink in text tokens (never the series color), one tooltip
 * that lists every series at the pointed position, the end of a line marked and labelled. Colors come from palette.ts by identity.
 */
import type { EChartsOption } from "./echarts";
import { RAMP, type Chrome } from "./palette";
import { compactTick, escapeHtml, periodLabel } from "./transforms";

export type Granularity = "day" | "week" | "month";
export type Kind = "line" | "area" | "bar";

export type SeriesSpec = {
  /** The identity: the legend toggles it and the color follows it. */
  key: string;
  label: string;
  color: string;
  values: (number | null)[];
  /** A projection is drawn dashed, which is what dashes mean on a chart. */
  dashed?: boolean;
  /** Where the series starts, when it begins after the first period (a forecast). */
  offset?: number;
};

type Format = (value: number) => string;

const FONT = "Inter Variable, system-ui, -apple-system, Segoe UI, sans-serif";

export const reduceMotion = (): boolean =>
  typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

type Row = { name: string; color: string; value: string; kind?: "line" | "rect" };

/** The tooltip: the value first and strong, the series name after it and quieter, a short key in the series color. Every label is escaped. */
export function tooltipHtml(theme: Chrome, title: string, rows: Row[]): string {
  const lines = rows
    .map((r) => {
      const key =
        r.kind === "rect"
          ? `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${r.color};margin-right:8px"></span>`
          : `<span style="display:inline-block;width:14px;height:2px;border-radius:1px;background:${r.color};margin-right:8px;vertical-align:middle"></span>`;
      return `<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:4px"><span style="display:flex;align-items:center;color:${theme.inkSecondary}">${key}${escapeHtml(r.name)}</span><strong style="color:${theme.ink};font-variant-numeric:tabular-nums">${escapeHtml(r.value)}</strong></div>`;
    })
    .join("");
  return `<div style="min-width:160px"><div style="color:${theme.inkSecondary};font-size:12px">${escapeHtml(title)}</div>${lines}</div>`;
}

function frame(theme: Chrome, extra: Partial<EChartsOption> = {}): EChartsOption {
  return {
    animation: !reduceMotion(),
    animationDuration: 300,
    backgroundColor: "transparent",
    textStyle: { fontFamily: FONT, color: theme.inkSecondary, fontSize: 12 },
    aria: { enabled: false },
    ...extra,
  };
}

const axisLine = (theme: Chrome) => ({ lineStyle: { color: theme.axis, width: 1, type: "solid" as const } });
const hairlines = (theme: Chrome) => ({ show: true, lineStyle: { color: theme.grid, width: 1, type: "solid" as const } });

function valueAxis(theme: Chrome, format: Format, unit: string, max?: number, scale = false) {
  return {
    type: "value" as const,
    name: unit,
    nameLocation: "end" as const,
    nameGap: 12,
    nameTextStyle: { color: theme.inkMuted, align: "left" as const, fontSize: 12 },
    axisLabel: { color: theme.inkMuted, formatter: (v: number) => format(v) },
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: hairlines(theme),
    ...(max !== undefined ? { max } : {}),
    ...(scale ? { scale: true } : {}),
  };
}

/** The ticks of a time axis: not every label of a long series, and the labels short. */
function timeAxis(theme: Chrome, periods: string[], granularity: Granularity) {
  return {
    type: "category" as const,
    data: periods,
    boundaryGap: false,
    axisLabel: { color: theme.inkMuted, hideOverlap: true, formatter: (p: string) => periodLabel(p, granularity) },
    axisLine: axisLine(theme),
    axisTick: { show: false },
  };
}

const TOP = 36;

export type LineSpec = {
  periods: string[];
  series: SeriesSpec[];
  granularity: Granularity;
  unit: string;
  format: Format;
  /** Shade under the line (a wash of the series hue). */
  area?: boolean;
  /** Axis maximum, for a score or a rate that cannot go past it. */
  max?: number;
  /** Let the axis start near the data instead of at zero (a score that moves a little). */
  scale?: boolean;
  /** The text of the first line of the tooltip for a period. */
  titleOf?: (period: string) => string;
};

/** Lines (and areas): a crosshair that snaps to a period, one tooltip for every series, the last point marked with a ring and labelled. */
export function lineOption(theme: Chrome, spec: LineSpec): EChartsOption {
  const { periods, series, granularity, unit, format } = spec;
  const lastIndex = (s: SeriesSpec) => {
    for (let i = s.values.length - 1; i >= 0; i--) if (s.values[i] !== null && s.values[i] !== undefined) return i + (s.offset ?? 0);
    return -1;
  };
  return frame(theme, {
    grid: { left: 8, right: 56, top: TOP, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      confine: true,
      axisPointer: { type: "line", lineStyle: { color: theme.axis, width: 1, type: "solid" } },
      backgroundColor: theme.surface,
      borderColor: theme.grid,
      padding: 10,
      textStyle: { color: theme.ink, fontFamily: FONT },
      formatter: (raw: unknown) => {
        const items = (Array.isArray(raw) ? raw : [raw]) as { axisValue: string; seriesName: string; color: string; value: number | null }[];
        const title = spec.titleOf ? spec.titleOf(items[0]?.axisValue ?? "") : (items[0]?.axisValue ?? "").slice(0, 10);
        return tooltipHtml(
          theme,
          title,
          items.filter((i) => i.value !== null && i.value !== undefined).map((i) => ({ name: i.seriesName, color: i.color, value: format(Number(i.value)) })),
        );
      },
    },
    xAxis: timeAxis(theme, periods, granularity),
    yAxis: valueAxis(theme, format, unit, spec.max, spec.scale),
    series: series.map((s) => {
      const end = lastIndex(s);
      const data = periods.map((_, i) => {
        const v = s.values[i - (s.offset ?? 0)];
        return i < (s.offset ?? 0) || v === null || v === undefined ? null : v;
      });
      return {
        name: s.label,
        type: "line" as const,
        data,
        smooth: false,
        connectNulls: false,
        showSymbol: true,
        symbol: "circle",
        symbolSize: (_: unknown, params: { dataIndex: number }) => (params.dataIndex === end ? 8 : 0),
        itemStyle: { color: s.color, borderColor: theme.surface, borderWidth: 2 },
        lineStyle: { color: s.color, width: 2, cap: "round" as const, join: "round" as const, type: s.dashed ? ("dashed" as const) : ("solid" as const) },
        ...(spec.area ? { areaStyle: { color: s.color, opacity: 0.1 } } : {}),
        emphasis: { focus: "none" as const, scale: 1.4 },
        endLabel: { show: true, formatter: (p: { value: unknown }) => format(Number(p.value)), color: theme.ink, fontSize: 12, distance: 6 },
        labelLayout: { hideOverlap: true },
      };
    }),
  });
}

export type TimeSeriesSpec = {
  key: string;
  label: string;
  color: string;
  points: [string, number | null][];
  dashed?: boolean;
  /** Hide this series in the tooltip at and before this day (the start of a projection repeats the last real point). */ quietUntil?: string;
};

export type TimeLineSpec = {
  series: TimeSeriesSpec[];
  unit: string;
  format: Format;
  area?: boolean;
  /** Stack the series on top of each other (parts of a whole over time). */ stack?: boolean;
  /** The label of a tick: "month" for spans of a year or more. */ labels: "day" | "month";
  titleOf?: (day: string) => string;
};

/** A line chart on a time axis, so that a series can run on past the others (a projection) whatever the size of the buckets. */
export function timeLineOption(theme: Chrome, spec: TimeLineSpec): EChartsOption {
  const { series, format } = spec;
  const ends = series.map((s) => {
    for (let i = s.points.length - 1; i >= 0; i--) if (s.points[i]![1] !== null) return i;
    return -1;
  });
  const dayOf = (ts: number) => new Date(ts).toISOString().slice(0, 10);
  return frame(theme, {
    grid: { left: 8, right: 64, top: TOP, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      confine: true,
      axisPointer: { type: "line", lineStyle: { color: theme.axis, width: 1, type: "solid" } },
      backgroundColor: theme.surface,
      borderColor: theme.grid,
      padding: 10,
      textStyle: { color: theme.ink, fontFamily: FONT },
      formatter: (raw: unknown) => {
        const items = (Array.isArray(raw) ? raw : [raw]) as { axisValue: number; seriesName: string; seriesIndex: number; color: string; value: [number, number | null] }[];
        const day = dayOf(items[0]?.axisValue ?? 0);
        const rows = items
          .filter((i) => i.value && i.value[1] !== null && i.value[1] !== undefined)
          .filter((i) => {
            const quiet = series[i.seriesIndex]?.quietUntil;
            return !quiet || day > quiet;
          })
          .map((i) => ({ name: i.seriesName, color: i.color, value: format(Number(i.value[1])) }));
        return tooltipHtml(theme, spec.titleOf ? spec.titleOf(day) : day, rows);
      },
    },
    xAxis: {
      type: "time",
      axisLabel: { color: theme.inkMuted, hideOverlap: true, formatter: (ts: number) => periodLabel(dayOf(ts), spec.labels === "month" ? "month" : "day") },
      axisLine: axisLine(theme),
      axisTick: { show: false },
      splitLine: { show: false },
    },
    yAxis: valueAxis(theme, format, spec.unit),
    series: series.map((s, index) => ({
      name: s.label,
      type: "line" as const,
      data: s.points.map(([d, v]) => [Date.parse(`${d.slice(0, 10)}T00:00:00Z`), v]),
      smooth: false,
      showSymbol: true,
      symbol: "circle",
      symbolSize: (_: unknown, params: { dataIndex: number }) => (params.dataIndex === ends[index] ? 8 : 0),
      itemStyle: { color: s.color, borderColor: theme.surface, borderWidth: 2 },
      lineStyle: { color: s.color, width: 2, cap: "round" as const, join: "round" as const, type: s.dashed ? ("dashed" as const) : ("solid" as const) },
      ...(spec.stack ? { stack: "total" } : {}),
      ...(spec.area && !s.dashed ? { areaStyle: { color: s.color, opacity: 0.1 } } : {}),
      emphasis: { focus: "none" as const, scale: 1.4 },
      endLabel: { show: !spec.stack, formatter: (p: { value: unknown }) => format(Number((p.value as [number, number])[1])), color: theme.ink, fontSize: 12, distance: 6 },
      labelLayout: { hideOverlap: true },
    })),
  });
}

export type BarSpec = {
  categories: string[];
  series: SeriesSpec[];
  unit: string;
  format: Format;
  stacked?: boolean;
  horizontal?: boolean;
  /** Category labels as drawn (default: as they are). */
  categoryLabel?: (category: string) => string;
  /** A value at the end of each bar (for a few bars), not for a long series. */
  endLabels?: boolean;
  /** A time axis (columns by period). */
  granularity?: Granularity;
  titleOf?: (category: string) => string;
};

/** Bars and columns: at most 24 px thick, rounded 4 px at the data end only, a 2 px surface gap between segments. */
export function barOption(theme: Chrome, spec: BarSpec): EChartsOption {
  const { categories, series, stacked, horizontal, format } = spec;
  const roundEnd = horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0];
  const label = spec.categoryLabel ?? ((c: string) => (spec.granularity ? periodLabel(c, spec.granularity) : c));
  const cat = {
    type: "category" as const,
    data: categories,
    inverse: !!horizontal,
    axisLabel: { color: theme.inkMuted, hideOverlap: true, formatter: label },
    axisLine: axisLine(theme),
    axisTick: { show: false },
    boundaryGap: true,
  };
  const val = valueAxis(theme, format, spec.unit);
  return frame(theme, {
    grid: { left: 8, right: spec.endLabels ? 64 : 16, top: TOP, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      confine: true,
      axisPointer: { type: "shadow", shadowStyle: { color: theme.grid, opacity: 0.35 } },
      backgroundColor: theme.surface,
      borderColor: theme.grid,
      padding: 10,
      textStyle: { color: theme.ink, fontFamily: FONT },
      formatter: (raw: unknown) => {
        const items = (Array.isArray(raw) ? raw : [raw]) as { name: string; seriesName: string; color: string; value: number | null }[];
        const title = spec.titleOf ? spec.titleOf(items[0]?.name ?? "") : label(items[0]?.name ?? "");
        return tooltipHtml(
          theme,
          title,
          items
            .filter((i) => i.value !== null && i.value !== undefined)
            .map((i) => ({ name: i.seriesName, color: i.color, value: format(Number(i.value)), kind: "rect" as const })),
        );
      },
    },
    xAxis: horizontal ? val : cat,
    yAxis: horizontal ? cat : val,
    series: series.map((s, index) => ({
      name: s.label,
      type: "bar" as const,
      data: s.values,
      ...(stacked ? { stack: "total" } : {}),
      barMaxWidth: 24,
      barCategoryGap: "30%",
      itemStyle: {
        color: s.color,
        // only the outermost segment of a stack has a data end to round
        borderRadius: stacked && index !== series.length - 1 ? 0 : roundEnd,
        ...(stacked ? { borderColor: theme.surface, borderWidth: 1 } : {}),
      },
      emphasis: { focus: "none" as const, itemStyle: { opacity: 0.85 } },
      ...(spec.endLabels && !stacked
        ? {
            label: {
              show: true,
              position: horizontal ? ("right" as const) : ("top" as const),
              color: theme.ink,
              fontSize: 12,
              formatter: (p: { value: unknown }) => format(Number(p.value)),
            },
          }
        : {}),
    })),
  });
}

/** A floating-bar waterfall: an invisible base lifts each bar to where the last one ended. Totals are neutral, movements keep their own colors. */
export function waterfallOption(
  theme: Chrome,
  steps: { label: string; base: number; value: number; color: string; signed: number }[],
  format: Format,
  unit: string,
): EChartsOption {
  return frame(theme, {
    grid: { left: 8, right: 16, top: TOP, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      confine: true,
      axisPointer: { type: "shadow", shadowStyle: { color: theme.grid, opacity: 0.35 } },
      backgroundColor: theme.surface,
      borderColor: theme.grid,
      padding: 10,
      textStyle: { color: theme.ink, fontFamily: FONT },
      formatter: (raw: unknown) => {
        const items = (Array.isArray(raw) ? raw : [raw]) as { dataIndex: number }[];
        const step = steps[items[0]?.dataIndex ?? 0];
        if (!step) return "";
        return tooltipHtml(theme, step.label, [
          {
            name: step.signed < 0 ? "Decrease" : "MRR",
            color: step.color,
            value: `${step.signed < 0 ? "-" : step.signed > 0 && step.base > 0 ? "+" : ""}${format(Math.abs(step.signed))}`,
            kind: "rect",
          },
        ]);
      },
    },
    xAxis: { type: "category", data: steps.map((s) => s.label), axisLabel: { color: theme.inkMuted, interval: 0 }, axisLine: axisLine(theme), axisTick: { show: false } },
    yAxis: valueAxis(theme, format, unit),
    series: [
      { type: "bar", stack: "wf", data: steps.map((s) => s.base), itemStyle: { color: "transparent" }, emphasis: { disabled: true }, silent: true, tooltip: { show: false } },
      {
        type: "bar",
        stack: "wf",
        barMaxWidth: 24,
        data: steps.map((s) => ({ value: s.value, itemStyle: { color: s.color, borderRadius: [4, 4, 0, 0] } })),
        label: { show: true, position: "top", color: theme.ink, fontSize: 12, formatter: (p: { dataIndex: number }) => format(Math.abs(steps[p.dataIndex]?.signed ?? 0)) },
        emphasis: { focus: "none", itemStyle: { opacity: 0.85 } },
      },
    ],
  });
}

/** Cohort retention: one hue, light to dark in light mode (and dark to light on the dark surface), with its scale beside it. */
export function heatmapOption(theme: Chrome, cells: { x: number; y: number; value: number }[], rows: string[], columns: number): EChartsOption {
  const ramp = RAMP[theme.mode];
  return frame(theme, {
    grid: { left: 8, right: 24, top: 8, bottom: 56, containLabel: true },
    tooltip: {
      confine: true,
      backgroundColor: theme.surface,
      borderColor: theme.grid,
      padding: 10,
      textStyle: { color: theme.ink, fontFamily: FONT },
      formatter: (p: unknown) => {
        const [x, y, v] = ((p as { value: number[] }).value ?? []) as number[];
        return tooltipHtml(theme, `${rows[y ?? 0] ?? ""} signup month, month ${x}`, [
          {
            name: "Customers still active",
            color: ramp[Math.min(ramp.length - 1, Math.floor(((v ?? 0) / 100) * ramp.length))] ?? ramp[0]!,
            value: `${(v ?? 0).toFixed(0)}%`,
            kind: "rect",
          },
        ]);
      },
    },
    xAxis: {
      type: "category",
      data: Array.from({ length: columns }, (_, i) => String(i)),
      name: "Months since signup",
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { color: theme.inkMuted },
      axisLabel: { color: theme.inkMuted, interval: 1 },
      axisLine: axisLine(theme),
      axisTick: { show: false },
      splitArea: { show: false },
    },
    yAxis: {
      type: "category",
      data: rows,
      inverse: true,
      axisLabel: { color: theme.inkMuted, interval: rows.length > 14 ? 1 : 0 },
      axisLine: axisLine(theme),
      axisTick: { show: false },
    },
    visualMap: {
      type: "continuous",
      min: 0,
      max: 100,
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      itemWidth: 14,
      itemHeight: 160,
      text: ["100%", "0%"],
      textStyle: { color: theme.inkSecondary },
      inRange: { color: [...ramp] },
    },
    series: [
      {
        type: "heatmap",
        data: cells.map((c) => [c.x, c.y, Math.round(c.value * 10) / 10]),
        itemStyle: { borderColor: theme.surface, borderWidth: 2, borderRadius: 2 },
        emphasis: { itemStyle: { borderColor: theme.ink, borderWidth: 1 } },
        progressive: 0,
      },
    ],
  });
}

export { compactTick };
