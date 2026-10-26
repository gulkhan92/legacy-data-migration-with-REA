import { useState } from "react";
import { cn } from "@/lib/cn";
import { formatCents, formatCentsCompact, formatDay, formatInt, formatPercent } from "@/lib/format";
import type { Breakdown, Cohort, Granularity, OpsReport, RevenueReport } from "../api";
import { useTheme } from "../shell/theme";
import { barOption, heatmapOption, timeLineOption, waterfallOption } from "./builders";
import { ChartCard } from "./ChartCard";
import { colorOf, SLOTS, type Chrome } from "./palette";
import { cohortCells, describeSeries, forecastLinear, mrrWaterfall, unitEconomics, topWithOther } from "./transforms";

const dollars = (cents: number) => formatCents(Math.round(cents));
const day = (p: string) => formatDay(p);

function useMode() {
  const { resolved } = useTheme();
  return { mode: resolved, accent: SLOTS[resolved][0] ?? "#2a78d6", second: SLOTS[resolved][1] ?? "#eb6834" };
}

const labelsFor = (granularity: Granularity, span: number): "day" | "month" => (granularity === "month" || span > 200 ? "month" : "day");

// ------------------------------------------------------------------ MRR over time, with a projection

export function MrrChart({ report, monthly, busy }: { report: RevenueReport; monthly: RevenueReport | undefined; busy: boolean }) {
  const { accent, second } = useMode();
  const periods = report.series.map((s) => s.period);
  const values = report.series.map((s) => s.mrr_cents);
  const projection = monthly
    ? forecastLinear(
        monthly.series.map((s) => s.period),
        monthly.series.map((s) => s.mrr_cents),
        6,
        12,
      )
    : null;
  const lastActual = periods[periods.length - 1];
  const build = (theme: Chrome, hidden: Set<string>) =>
    timeLineOption(theme, {
      unit: "MRR (USD)",
      format: formatCentsCompact,
      area: true,
      labels: labelsFor(report.granularity, periods.length * (report.granularity === "day" ? 1 : report.granularity === "week" ? 7 : 30)),
      titleOf: day,
      series: [
        { key: "mrr", label: "MRR", color: accent, points: hidden.has("mrr") ? [] : periods.map((p, i) => [p, values[i] ?? null] as [string, number | null]) },
        ...(projection && lastActual && !hidden.has("projection")
          ? [
              {
                key: "projection",
                label: "Projection",
                color: second,
                dashed: true,
                quietUntil: lastActual.slice(0, 10),
                points: [
                  [lastActual, values[values.length - 1] ?? null] as [string, number | null],
                  ...projection.periods.map((p, i) => [p, projection.values[i] ?? null] as [string, number | null]),
                ],
              },
            ]
          : []),
      ],
    });
  const legend = [
    { key: "mrr", label: "MRR", color: accent, shape: "line" as const },
    ...(projection ? [{ key: "projection", label: "Projection (linear trend)", color: second, shape: "line" as const }] : []),
  ];
  const last = projection ? projection.values[projection.values.length - 1] : null;
  return (
    <ChartCard
      title="Monthly recurring revenue"
      description="MRR over the range. The dashed line is a projection of the next six months, not a forecast of what will happen."
      summary={`${describeSeries("MRR", periods, values, (v) => formatCentsCompact(v))}${projection && last !== null && last !== undefined ? ` The dashed projection continues the linear trend of the last ${projection.basis} months and reaches ${formatCentsCompact(last)} in six months. It assumes nothing changes: no churn shock, no price change.` : ""}`}
      legend={legend}
      build={build}
      height={320}
      empty={periods.length === 0}
      busy={busy}
      table={{
        columns: ["Period", "MRR"],
        rows: [
          ...report.series.map((s) => [day(s.period), dollars(s.mrr_cents)]),
          ...(projection ? projection.periods.map((p, i) => [`${day(p)} (projection)`, dollars(projection.values[i] ?? 0)]) : []),
        ],
      }}
    />
  );
}

// ------------------------------------------------------------------ MRR movement, with a waterfall

export function MovementChart({ report, busy }: { report: RevenueReport; busy: boolean }) {
  const { mode } = useMode();
  const [view, setView] = useState<"bars" | "waterfall">("bars");
  const periods = report.series.map((s) => s.period);
  const legend = [
    ["new", "New"],
    ["expansion", "Expansion"],
    ["contraction", "Contraction"],
    ["churned", "Churn"],
  ].map(([key, label]) => ({ key: key as string, label: label as string, color: colorOf(key as string, mode), shape: "rect" as const }));
  const k = report.kpis;
  const steps = mrrWaterfall(k);
  const build = (theme: Chrome, hidden: Set<string>) => {
    if (view === "waterfall") {
      return waterfallOption(
        theme,
        steps.map((s) => ({ label: s.label, base: s.base, value: s.value, signed: s.signed, color: s.kind === "total" ? theme.inkMuted : colorOf(s.key, theme.mode) })),
        formatCentsCompact,
        "MRR (USD)",
      );
    }
    const pick = (key: string, sign: 1 | -1, f: (s: RevenueReport["series"][number]) => number) => ({
      key,
      label: legend.find((l) => l.key === key)?.label ?? key,
      color: colorOf(key, theme.mode),
      values: report.series.map((s) => (hidden.has(key) ? null : sign * f(s))),
    });
    return barOption(theme, {
      categories: periods,
      series: [
        pick("new", 1, (s) => s.new_mrr_cents),
        pick("expansion", 1, (s) => s.expansion_mrr_cents),
        pick("contraction", -1, (s) => s.contraction_mrr_cents),
        pick("churned", -1, (s) => s.churned_mrr_cents),
      ],
      unit: "Change in MRR (USD)",
      format: (v) => (v < 0 ? `-${formatCentsCompact(-v)}` : formatCentsCompact(v)),
      stacked: true,
      granularity: report.granularity,
      titleOf: day,
    });
  };
  const net = k.net_new_mrr_cents;
  return (
    <ChartCard
      title="MRR movement"
      description="What changed the MRR: new customers and expansion add, contraction and churn take away"
      summary={`Over the range MRR went from ${formatCentsCompact(k.mrr_start_cents)} to ${formatCentsCompact(k.mrr_cents)}, a net change of ${net < 0 ? "-" : "+"}${formatCentsCompact(Math.abs(net))}: new ${formatCentsCompact(k.new_mrr_cents)}, expansion ${formatCentsCompact(k.expansion_mrr_cents)}, contraction -${formatCentsCompact(k.contraction_mrr_cents)}, churn -${formatCentsCompact(k.churned_mrr_cents)}.`}
      legend={view === "bars" ? legend : undefined}
      build={build}
      height={320}
      empty={periods.length === 0}
      busy={busy}
      actions={
        <div role="group" aria-label="MRR movement: form" className="inline-flex rounded-md border border-border p-0.5">
          {(["bars", "waterfall"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setView(v)}
              className={cn("rounded px-2.5 py-1 text-sm font-medium", view === v ? "bg-primary-soft text-primary-ink" : "text-foreground-muted hover:text-foreground")}
            >
              {v === "bars" ? "By period" : "Waterfall"}
            </button>
          ))}
        </div>
      }
      table={
        view === "waterfall"
          ? { columns: ["Step", "Amount"], rows: steps.map((s) => [s.label, `${s.kind === "down" ? "-" : s.kind === "up" ? "+" : ""}${dollars(Math.abs(s.signed))}`]) }
          : {
              columns: ["Period", "New", "Expansion", "Contraction", "Churn"],
              rows: report.series.map((s) => [
                day(s.period),
                dollars(s.new_mrr_cents),
                dollars(s.expansion_mrr_cents),
                `-${dollars(s.contraction_mrr_cents)}`,
                `-${dollars(s.churned_mrr_cents)}`,
              ]),
            }
      }
    />
  );
}

// ------------------------------------------------------------------ by plan, industry, region

const PLAN_LABEL: Record<string, string> = { starter: "Starter", professional: "Professional", enterprise: "Enterprise" };

export function PlanChart({ breakdown, granularity, busy }: { breakdown: Breakdown; granularity: Granularity; busy: boolean }) {
  const { mode } = useMode();
  const keys = ["starter", "professional", "enterprise"].filter((k) => breakdown.keys.includes(k));
  const legend = keys.map((k) => ({ key: k, label: PLAN_LABEL[k] ?? k, color: colorOf(k, mode), shape: "rect" as const }));
  const build = (theme: Chrome, hidden: Set<string>) =>
    timeLineOption(theme, {
      unit: "MRR (USD)",
      format: formatCentsCompact,
      area: true,
      stack: true,
      labels: labelsFor(granularity, breakdown.mrr.length * (granularity === "day" ? 1 : granularity === "week" ? 7 : 30)),
      titleOf: day,
      series: keys.map((k) => ({
        key: k,
        label: PLAN_LABEL[k] ?? k,
        color: colorOf(k, theme.mode),
        points: breakdown.mrr.map((r) => [r.period, hidden.has(k) ? 0 : (r.values[k] ?? 0)] as [string, number | null]),
      })),
    });
  const share = breakdown.current.share;
  return (
    <ChartCard
      title="MRR by plan"
      description="The MRR of each plan, stacked"
      summary={`Today ${formatCentsCompact(breakdown.current.total_mrr_cents)} of MRR: ${keys.map((k) => `${PLAN_LABEL[k] ?? k} ${formatPercent(share[k] ?? 0, 0)}`).join(", ")}. The Starter plan is free, so it adds no MRR.`}
      legend={legend}
      build={build}
      empty={breakdown.mrr.length === 0}
      busy={busy}
      table={{ columns: ["Period", ...keys.map((k) => PLAN_LABEL[k] ?? k)], rows: breakdown.mrr.map((r) => [day(r.period), ...keys.map((k) => dollars(r.values[k] ?? 0))]) }}
    />
  );
}

export function DimensionChart({ title, noun, breakdown, busy }: { title: string; noun: string; breakdown: Breakdown; busy: boolean }) {
  const { accent } = useMode();
  const entries = topWithOther(Object.entries(breakdown.current.mrr_cents), 7);
  const total = breakdown.current.total_mrr_cents;
  const build = (theme: Chrome) =>
    barOption(theme, {
      categories: entries.map(([k]) => k),
      series: [{ key: "mrr", label: "MRR", color: accent, values: entries.map(([, v]) => v) }],
      unit: "MRR (USD)",
      format: formatCentsCompact,
      horizontal: true,
      endLabels: true,
    });
  return (
    <ChartCard
      title={title}
      description={`Today's MRR by ${noun}, largest first`}
      summary={
        entries.length === 0
          ? "No MRR."
          : `${entries[0]![0]} is the largest ${noun} with ${formatCentsCompact(entries[0]![1])} (${formatPercent(total > 0 ? entries[0]![1] / total : 0, 0)} of ${formatCentsCompact(total)}).`
      }
      build={build}
      height={Math.max(180, entries.length * 44 + 56)}
      empty={entries.length === 0}
      busy={busy}
      table={{
        columns: [noun.charAt(0).toUpperCase() + noun.slice(1), "MRR", "Share"],
        rows: entries.map(([k, v]) => [k, dollars(v), formatPercent(total > 0 ? v / total : 0, 1)]),
      }}
    />
  );
}

// ------------------------------------------------------------------ subscription versus usage

export function RevenueKindChart({ monthly, busy }: { monthly: RevenueReport; busy: boolean }) {
  const { mode } = useMode();
  const periods = monthly.series.map((s) => s.period);
  const legend = [
    { key: "subscription", label: "Subscription", color: colorOf("subscription", mode), shape: "rect" as const },
    { key: "usage", label: "Usage (overage)", color: colorOf("usage", mode), shape: "rect" as const },
  ];
  const build = (theme: Chrome, hidden: Set<string>) =>
    barOption(theme, {
      categories: periods,
      series: [
        {
          key: "subscription",
          label: "Subscription",
          color: colorOf("subscription", theme.mode),
          values: monthly.series.map((s) => (hidden.has("subscription") ? null : s.subscription_revenue_cents)),
        },
        { key: "usage", label: "Usage (overage)", color: colorOf("usage", theme.mode), values: monthly.series.map((s) => (hidden.has("usage") ? null : s.usage_revenue_cents)) },
      ],
      unit: "Invoiced (USD)",
      format: formatCentsCompact,
      stacked: true,
      granularity: "month",
      titleOf: day,
    });
  const k = monthly.kpis;
  return (
    <ChartCard
      title="Subscription and usage revenue"
      description="Invoiced revenue per month: the plan fee and the charge for records beyond the allowance"
      summary={`Over the range ${formatCentsCompact(k.total_revenue_cents)} was invoiced: ${formatCentsCompact(k.subscription_revenue_cents)} subscription and ${formatCentsCompact(k.usage_revenue_cents)} usage (${formatPercent(Number(k.usage_share ?? 0), 0)} of the total).`}
      legend={legend}
      build={build}
      empty={periods.length === 0}
      busy={busy}
      table={{
        columns: ["Month", "Subscription", "Usage", "Total"],
        rows: monthly.series.map((s) => [day(s.period), dollars(s.subscription_revenue_cents), dollars(s.usage_revenue_cents), dollars(s.total_revenue_cents)]),
      }}
    />
  );
}

// ------------------------------------------------------------------ cohorts

export function CohortChart({ cohorts, busy }: { cohorts: Cohort[]; busy: boolean }) {
  const { cells, rows, columns } = cohortCells(cohorts);
  const build = (theme: Chrome) => heatmapOption(theme, cells, rows, columns);
  const first = cohorts.filter((c) => c.logos.length > 12 && c.logos[12] !== null && c.logos[12] !== undefined);
  const twelve = first.length > 0 ? first.reduce((n, c) => n + (c.logos[12] ?? 0) * c.size, 0) / first.reduce((n, c) => n + c.size, 0) : null;
  return (
    <ChartCard
      title="Cohort retention"
      description="Of the customers who signed up in a month (rows), the share still active after each month since (columns)"
      summary={`${cohorts.length} signup months. ${twelve === null ? "" : `After twelve months ${twelve.toFixed(0)}% of customers were still active, weighted by cohort size. `}Darker cells mean more customers retained; empty cells are months that have not happened yet.`}
      build={build}
      height={Math.max(300, cohorts.length * 16 + 120)}
      empty={cohorts.length === 0}
      busy={busy}
      table={{
        columns: ["Signup month", "Customers", ...Array.from({ length: columns }, (_, i) => `M${i}`)],
        rows: cohorts.map((c) => [
          c.month,
          formatInt(c.size),
          ...Array.from({ length: columns }, (_, i) => (c.logos[i] === null || c.logos[i] === undefined ? "" : `${Math.round(c.logos[i] as number)}%`)),
        ]),
      }}
    />
  );
}

// ------------------------------------------------------------------ growth and ARPA: two charts, one axis each

export function GrowthCharts({ report, busy }: { report: RevenueReport; busy: boolean }) {
  const { accent, second } = useMode();
  const span = report.series.length * (report.granularity === "day" ? 1 : report.granularity === "week" ? 7 : 30);
  const labels = labelsFor(report.granularity, span);
  const customers = (theme: Chrome) =>
    timeLineOption(theme, {
      unit: "Paying customers",
      format: formatInt,
      labels,
      titleOf: day,
      series: [{ key: "paying", label: "Paying customers", color: accent, points: report.series.map((s) => [s.period, s.paying_orgs] as [string, number]) }],
    });
  const arpa = (theme: Chrome) =>
    timeLineOption(theme, {
      unit: "ARPA (USD per month)",
      format: formatCentsCompact,
      labels,
      titleOf: day,
      series: [{ key: "arpa", label: "ARPA", color: second, points: report.series.map((s) => [s.period, s.arpa_cents] as [string, number]) }],
    });
  const periods = report.series.map((s) => s.period);
  return (
    <>
      <ChartCard
        title="Customer growth"
        description="Paying customers"
        summary={describeSeries(
          "Paying customers",
          periods,
          report.series.map((s) => s.paying_orgs),
          formatInt,
        )}
        build={customers}
        empty={periods.length === 0}
        busy={busy}
        table={{ columns: ["Period", "Paying customers", "New customers"], rows: report.series.map((s) => [day(s.period), formatInt(s.paying_orgs), formatInt(s.new_orgs)]) }}
      />
      <ChartCard
        title="Average revenue per account"
        description="ARPA: MRR divided by paying customers. Two charts rather than two scales on one."
        summary={describeSeries(
          "ARPA",
          periods,
          report.series.map((s) => s.arpa_cents),
          (v) => formatCentsCompact(v),
        )}
        build={arpa}
        empty={periods.length === 0}
        busy={busy}
        table={{ columns: ["Period", "ARPA"], rows: report.series.map((s) => [day(s.period), dollars(s.arpa_cents)]) }}
      />
    </>
  );
}

// ------------------------------------------------------------------ unit economics

export function UnitEconomicsCharts({ revenue, ops, busy }: { revenue: RevenueReport; ops: OpsReport; busy: boolean }) {
  const { accent, second } = useMode();
  const { revenuePer1000, tokensPer1000 } = unitEconomics(revenue.series, ops.series);
  const money = (v: number) => `$${v < 10 ? v.toFixed(2) : v.toFixed(0)}`;
  const rev = (theme: Chrome) =>
    timeLineOption(theme, {
      unit: "USD per 1,000 records",
      format: money,
      labels: "month",
      titleOf: day,
      series: [{ key: "rev", label: "Revenue per 1,000 records", color: accent, points: revenuePer1000.map((r) => [r.period, r.value] as [string, number | null]) }],
    });
  const tok = (theme: Chrome) =>
    timeLineOption(theme, {
      unit: "Tokens per 1,000 records",
      format: (v) => formatInt(Math.round(v)),
      labels: "month",
      titleOf: day,
      series: [{ key: "tok", label: "Tokens per 1,000 records", color: second, points: tokensPer1000.map((r) => [r.period, r.value] as [string, number | null]) }],
    });
  const lastRev = [...revenuePer1000].reverse().find((r) => r.value !== null)?.value ?? null;
  const lastTok = [...tokensPer1000].reverse().find((r) => r.value !== null)?.value ?? null;
  return (
    <>
      <ChartCard
        title="Revenue per 1,000 records"
        description="Invoiced revenue divided by the records loaded, per month"
        summary={
          describeSeries(
            "Revenue per 1,000 records",
            revenuePer1000.map((r) => r.period),
            revenuePer1000.map((r) => r.value),
            money,
          ) + (lastRev === null ? "" : " Revenue is invoiced monthly, so months are the unit.")
        }
        build={rev}
        empty={revenuePer1000.length === 0}
        busy={busy}
        table={{ columns: ["Month", "Revenue per 1,000 records"], rows: revenuePer1000.map((r) => [day(r.period), r.value === null ? "none" : money(r.value)]) }}
      />
      <ChartCard
        title="Model tokens per 1,000 records"
        description="What the language model costs in tokens for each thousand records migrated, per month"
        summary={
          describeSeries(
            "Tokens per 1,000 records",
            tokensPer1000.map((r) => r.period),
            tokensPer1000.map((r) => r.value),
            (v) => formatInt(Math.round(v)),
          ) + (lastTok === null ? "" : " Tokens are what the language model used; columns settled by memory or by similarity cost none.")
        }
        build={tok}
        empty={tokensPer1000.length === 0}
        busy={busy}
        table={{ columns: ["Month", "Tokens per 1,000 records"], rows: tokensPer1000.map((r) => [day(r.period), r.value === null ? "none" : formatInt(Math.round(r.value))]) }}
      />
    </>
  );
}
