import { formatCompact, formatInt, formatMs, formatPercent } from "@/lib/format";
import type { Granularity, OpsDetail, OpsReport } from "../api";
import { reasonLabel } from "../wizard/DryRunStep";
import { barOption, lineOption } from "./builders";
import { ChartCard } from "./ChartCard";
import { colorOf, SLOTS } from "./palette";
import { describeSeries, histogramBins, periodLabel, shares } from "./transforms";
import { useTheme } from "../shell/theme";

type Props = { report: OpsReport; busy: boolean };

const STAGES = [
  ["discover", "Discover"],
  ["profile", "Profile"],
  ["map", "Map"],
  ["rules", "Rules"],
  ["dry_run", "Dry run"],
  ["load", "Load"],
] as const;

const day = (p: string) => p.slice(0, 10);

function useAccent() {
  const { resolved } = useTheme();
  return { mode: resolved, color: SLOTS[resolved][0] ?? "#2a78d6" };
}

export function JobsChart({ report, busy }: Props) {
  const { color } = useAccent();
  const periods = report.series.map((s) => s.period);
  const values = report.series.map((s) => s.jobs);
  const g: Granularity = report.granularity;
  const build = (theme: Parameters<typeof barOption>[0]) =>
    barOption(theme, { categories: periods, series: [{ key: "jobs", label: "Jobs", color, values }], unit: "Jobs", format: formatInt, granularity: g, titleOf: day });
  return (
    <ChartCard
      title="Jobs"
      description={`Migrations finished, per ${g}`}
      summary={describeSeries("Jobs per " + g, periods, values, formatInt)}
      build={build}
      empty={periods.length === 0}
      busy={busy}
      table={{ columns: ["Period", "Jobs"], rows: report.series.map((s) => [day(s.period), formatInt(s.jobs)]) }}
    />
  );
}

export function SuccessChart({ report, busy }: Props) {
  const { color } = useAccent();
  const periods = report.series.map((s) => s.period);
  const values = report.series.map((s) => (s.success_rate === null ? null : s.success_rate * 100));
  const build = (theme: Parameters<typeof lineOption>[0]) =>
    lineOption(theme, {
      periods,
      series: [{ key: "success", label: "Success rate", color, values }],
      granularity: report.granularity,
      unit: "Percent of jobs",
      format: (v) => `${v.toFixed(1)}%`,
      max: 100,
      scale: true,
      titleOf: day,
    });
  return (
    <ChartCard
      title="Success rate"
      description="Share of finished jobs that completed"
      summary={describeSeries("Success rate", periods, values, (v) => `${v.toFixed(1)}%`)}
      build={build}
      empty={periods.length === 0}
      busy={busy}
      table={{ columns: ["Period", "Success rate"], rows: report.series.map((s) => [day(s.period), s.success_rate === null ? "none" : formatPercent(s.success_rate)]) }}
    />
  );
}

export function RecordsChart({ report, busy }: Props) {
  const { color } = useAccent();
  const periods = report.series.map((s) => s.period);
  const values = report.series.map((s) => s.records);
  const build = (theme: Parameters<typeof lineOption>[0]) =>
    lineOption(theme, {
      periods,
      series: [{ key: "records", label: "Records", color, values }],
      granularity: report.granularity,
      unit: "Records",
      format: formatCompact,
      area: true,
      titleOf: day,
    });
  return (
    <ChartCard
      title="Records migrated"
      description={`Records loaded, per ${report.granularity}`}
      summary={describeSeries("Records migrated", periods, values, formatCompact)}
      build={build}
      empty={periods.length === 0}
      busy={busy}
      table={{ columns: ["Period", "Records"], rows: report.series.map((s) => [day(s.period), formatInt(s.records)]) }}
    />
  );
}

export function QualityChart({ report, busy }: Props) {
  const { color } = useAccent();
  const periods = report.series.map((s) => s.period);
  const values = report.series.map((s) => s.quality_score);
  const build = (theme: Parameters<typeof lineOption>[0]) =>
    lineOption(theme, {
      periods,
      series: [{ key: "quality", label: "Quality score", color, values }],
      granularity: report.granularity,
      unit: "Score out of 100",
      format: (v) => v.toFixed(1),
      max: 100,
      scale: true,
      titleOf: day,
    });
  return (
    <ChartCard
      title="Data quality score"
      description="Average score of the sources at profiling, out of 100"
      summary={describeSeries("Data quality score", periods, values, (v) => v.toFixed(1)) + " The axis does not start at zero."}
      build={build}
      empty={periods.length === 0}
      busy={busy}
      table={{ columns: ["Period", "Score"], rows: report.series.map((s) => [day(s.period), s.quality_score === null ? "none" : s.quality_score.toFixed(1)]) }}
    />
  );
}

export function TokensChart({ report, busy }: Props) {
  const { mode } = useAccent();
  const periods = report.series.map((s) => s.period);
  const perJob = (tokens: number, jobs: number) => (jobs > 0 ? Math.round(tokens / jobs) : 0);
  const groq = report.series.map((s) => perJob(s.tokens_groq, s.jobs));
  const gemini = report.series.map((s) => perJob(s.tokens_gemini, s.jobs));
  const legend = [
    { key: "groq", label: "Groq", color: colorOf("groq", mode), shape: "rect" as const },
    { key: "gemini", label: "Gemini", color: colorOf("gemini", mode), shape: "rect" as const },
  ];
  const build = (theme: Parameters<typeof barOption>[0], hidden: Set<string>) =>
    barOption(theme, {
      categories: periods,
      series: [
        { key: "groq", label: "Groq", color: colorOf("groq", theme.mode), values: hidden.has("groq") ? periods.map(() => null) : groq },
        { key: "gemini", label: "Gemini", color: colorOf("gemini", theme.mode), values: hidden.has("gemini") ? periods.map(() => null) : gemini },
      ],
      unit: "Tokens per job",
      format: formatInt,
      stacked: true,
      granularity: report.granularity,
      titleOf: day,
    });
  const totalGroq = report.series.reduce((n, s) => n + s.tokens_groq, 0);
  const totalGemini = report.series.reduce((n, s) => n + s.tokens_gemini, 0);
  return (
    <ChartCard
      title="Model tokens per job"
      description="Tokens the language model used for each job, by provider"
      summary={`In this range Groq used ${formatCompact(totalGroq)} tokens and Gemini ${formatCompact(totalGemini)}, over ${formatInt(report.kpis.jobs)} jobs. Cache hits are not counted.`}
      legend={legend}
      build={build}
      empty={periods.length === 0}
      busy={busy}
      table={{ columns: ["Period", "Groq per job", "Gemini per job"], rows: report.series.map((s, i) => [day(s.period), formatInt(groq[i] ?? 0), formatInt(gemini[i] ?? 0)]) }}
    />
  );
}

export function MappingMethodsChart({ detail, busy }: { detail: OpsDetail; busy: boolean }) {
  const { mode } = useAccent();
  const order = ["memory", "embedding", "llm", "human"];
  const names: Record<string, string> = { memory: "Memory", embedding: "Embedding", llm: "Language model", human: "Reviewed by a person" };
  const parts = shares(detail.mapping_methods, order);
  const legend = order.map((k) => ({ key: k, label: names[k] ?? k, color: colorOf(k, mode), shape: "rect" as const }));
  const build = (theme: Parameters<typeof barOption>[0], hidden: Set<string>) => {
    const option = barOption(theme, {
      categories: ["Columns mapped"],
      series: parts.map((p) => ({ key: p.key, label: names[p.key] ?? p.key, color: colorOf(p.key, theme.mode), values: [hidden.has(p.key) ? null : p.share * 100] })),
      unit: "Share of mapped columns",
      format: (v) => `${v.toFixed(0)}%`,
      stacked: true,
      horizontal: true,
    });
    // A segment is labelled with its share only where the text fits (at least 10 percent of the bar).
    const series = (option.series as { name: string; label?: unknown }[]).map((s, i) => ({
      ...s,
      label: {
        show: (parts[i]?.share ?? 0) >= 0.1 && !hidden.has(parts[i]?.key ?? ""),
        position: "inside",
        color: parts[i]?.key === "human" ? "#0b0b0b" : "#ffffff",
        fontSize: 12,
        formatter: () => `${((parts[i]?.share ?? 0) * 100).toFixed(0)}%`,
      },
    }));
    return { ...option, series } as typeof option;
  };
  const lead = [...parts].sort((a, b) => b.share - a.share)[0];
  return (
    <ChartCard
      title="Mapping automation"
      description="How each column was mapped"
      summary={
        detail.mapping_total === 0
          ? "No columns were mapped in this range."
          : `${formatInt(detail.mapping_total)} columns were mapped. ${names[lead?.key ?? ""] ?? ""} settled ${formatPercent(lead?.share ?? 0, 0)} of them; a person had to decide ${formatPercent(parts.find((p) => p.key === "human")?.share ?? 0, 0)}.`
      }
      legend={legend}
      build={build}
      height={170}
      empty={detail.mapping_total === 0}
      busy={busy}
      table={{ columns: ["Method", "Columns", "Share"], rows: parts.map((p) => [names[p.key] ?? p.key, formatInt(p.count), formatPercent(p.share, 1)]) }}
    />
  );
}

export function LatencyChart({ detail, busy }: { detail: OpsDetail; busy: boolean }) {
  const { mode } = useAccent();
  const legend = [
    { key: "p50", label: "Median (p50)", color: colorOf("p50", mode), shape: "rect" as const },
    { key: "p95", label: "Slowest 5 percent (p95)", color: colorOf("p95", mode), shape: "rect" as const },
  ];
  const rows = STAGES.map(([key, label]) => ({
    key,
    label,
    p50: detail.stage_latency[key]?.p50 ?? 0,
    p95: detail.stage_latency[key]?.p95 ?? 0,
    count: detail.stage_latency[key]?.count ?? 0,
  }));
  const build = (theme: Parameters<typeof barOption>[0], hidden: Set<string>) =>
    barOption(theme, {
      categories: rows.map((r) => r.label),
      series: [
        { key: "p50", label: "Median (p50)", color: colorOf("p50", theme.mode), values: hidden.has("p50") ? rows.map(() => null) : rows.map((r) => r.p50) },
        { key: "p95", label: "Slowest 5 percent (p95)", color: colorOf("p95", theme.mode), values: hidden.has("p95") ? rows.map(() => null) : rows.map((r) => r.p95) },
      ],
      unit: "Seconds",
      format: (v) => (v >= 90 ? `${(v / 60).toFixed(1)} min` : `${v.toFixed(v < 10 ? 1 : 0)} s`),
      horizontal: true,
    });
  const slowest = [...rows].sort((a, b) => b.p95 - a.p95)[0];
  return (
    <ChartCard
      title="Latency by stage"
      description="Median and slowest 5 percent of each pipeline stage"
      summary={`The load is the slowest stage: half of the loads take ${formatMs((rows.find((r) => r.key === "load")?.p50 ?? 0) * 1000)} or less, and the slowest 5 percent ${formatMs((rows.find((r) => r.key === "load")?.p95 ?? 0) * 1000)} or more. Slowest tail overall: ${slowest?.label ?? "none"}.`}
      legend={legend}
      build={build}
      height={320}
      empty={rows.every((r) => r.count === 0)}
      busy={busy}
      table={{ columns: ["Stage", "Median (s)", "Slowest 5% (s)", "Runs"], rows: rows.map((r) => [r.label, r.p50.toFixed(1), r.p95.toFixed(1), formatInt(r.count)]) }}
    />
  );
}

export function ErrorChart({ detail, busy }: { detail: OpsDetail; busy: boolean }) {
  const { color } = useAccent();
  const entries = Object.entries(detail.quarantine_reasons).sort((a, b) => b[1] - a[1]);
  const build = (theme: Parameters<typeof barOption>[0]) =>
    barOption(theme, {
      categories: entries.map(([k]) => reasonLabel(k)),
      series: [{ key: "rows", label: "Quarantined rows", color, values: entries.map(([, v]) => v) }],
      unit: "Rows quarantined",
      format: formatInt,
      horizontal: true,
      endLabels: true,
    });
  const total = entries.reduce((n, [, v]) => n + v, 0);
  return (
    <ChartCard
      title="Why rows were quarantined"
      description="Error taxonomy of the rows that could not be loaded. In the simulated history it is a sample of rows per job."
      summary={
        entries.length === 0
          ? "No rows were quarantined in this range."
          : `${formatInt(total)} sampled rows were quarantined. The most common reason is "${reasonLabel(entries[0]![0])}" with ${formatInt(entries[0]![1])}.`
      }
      build={build}
      height={260}
      empty={entries.length === 0}
      busy={busy}
      table={{ columns: ["Reason", "Rows"], rows: entries.map(([k, v]) => [reasonLabel(k), formatInt(v)]) }}
    />
  );
}

export function FailedStagesChart({ detail, busy }: { detail: OpsDetail; busy: boolean }) {
  const { color } = useAccent();
  const entries = STAGES.map(([key, label]) => [label, detail.failed_stages[key] ?? 0] as const)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const build = (theme: Parameters<typeof barOption>[0]) =>
    barOption(theme, {
      categories: entries.map(([k]) => k),
      series: [{ key: "failed", label: "Failed jobs", color, values: entries.map(([, v]) => v) }],
      unit: "Jobs that failed",
      format: formatInt,
      horizontal: true,
      endLabels: true,
    });
  return (
    <ChartCard
      title="Where jobs fail"
      description="The stage a failed job stopped at"
      summary={
        entries.length === 0
          ? "No job failed in this range."
          : `${formatInt(entries.reduce((n, [, v]) => n + v, 0))} jobs failed. Most stopped at ${entries[0]![0].toLowerCase()} (${formatInt(entries[0]![1])}).`
      }
      build={build}
      height={240}
      empty={entries.length === 0}
      busy={busy}
      table={{ columns: ["Stage", "Failed jobs"], rows: entries.map(([k, v]) => [k, formatInt(v)]) }}
    />
  );
}

export function ConfidenceChart({ detail, busy }: { detail: OpsDetail; busy: boolean }) {
  const { color } = useAccent();
  const bins = histogramBins(detail.discovery_confidence);
  const total = bins.reduce((n, b) => n + b.count, 0);
  const high = bins.slice(-2).reduce((n, b) => n + b.count, 0);
  const build = (theme: Parameters<typeof barOption>[0]) =>
    barOption(theme, {
      categories: bins.map((b) => b.label),
      series: [{ key: "fields", label: "Fields", color, values: bins.map((b) => b.count) }],
      unit: "Discovered fields",
      format: formatCompact,
      categoryLabel: (c) => c,
    });
  return (
    <ChartCard
      title="Discovery confidence"
      description="How sure discovery was of each field it found (a database is always certain; a recovered binary layout is less so)"
      summary={
        total === 0
          ? "No fields were discovered in this range."
          : `${formatInt(total)} fields were discovered; ${formatPercent(high / total, 0)} of them with a confidence of 80 percent or more.`
      }
      build={build}
      empty={total === 0}
      busy={busy}
      table={{ columns: ["Confidence", "Fields"], rows: bins.map((b) => [b.label, formatInt(b.count)]) }}
    />
  );
}

export { periodLabel };
