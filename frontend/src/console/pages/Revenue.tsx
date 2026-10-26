import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { formatCents, formatCentsCompact, formatDay, formatInt, formatPercent, formatSignedPercent } from "@/lib/format";
import { analyticsApi, type RevenueReport } from "../api";
import { DashboardControls } from "../charts/DashboardControls";
import { useDashboardRange } from "../charts/range";
import { CohortChart, DimensionChart, GrowthCharts, MovementChart, MrrChart, PlanChart, RevenueKindChart, UnitEconomicsCharts } from "../charts/revenue-charts";
import { Sparkline } from "../charts/Sparkline";
import { changeOf, downsample, pluck } from "../charts/transforms";
import { Pagination, SearchInput, Select, useDebounced } from "../ui/controls";
import { PageHeader, Panel } from "../ui/page";
import { EmptyState, ErrorState, LoadingRows } from "../ui/states";
import { deltaFromRatio, StatTile } from "../ui/stat";
import { invoiceTone } from "./Billing";

function Kpis({ report }: { report: RevenueReport }) {
  const k = report.kpis;
  const c = report.change;
  const series = report.series;
  const spark = (pick: (s: RevenueReport["series"][number]) => number | null | undefined, label: string) => <Sparkline values={downsample(pluck(series, pick))} label={label} />;
  return (
    <section aria-label="Key figures" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatTile
        label="MRR"
        value={formatCentsCompact(k.mrr_cents)}
        delta={deltaFromRatio(changeOf(c, "mrr_cents"), formatSignedPercent, "up")}
        spark={spark((s) => s.mrr_cents, "MRR over the range")}
        hint={`${formatCents(k.mrr_cents)} exactly`}
      />
      <StatTile label="ARR" value={formatCentsCompact(k.arr_cents)} delta={deltaFromRatio(changeOf(c, "arr_cents"), formatSignedPercent, "up")} hint="MRR times twelve" />
      <StatTile
        label="Net new MRR"
        value={`${k.net_new_mrr_cents < 0 ? "-" : ""}${formatCentsCompact(Math.abs(k.net_new_mrr_cents))}`}
        delta={deltaFromRatio(changeOf(c, "net_new_mrr_cents"), formatSignedPercent, "up")}
        spark={spark((s) => s.net_new_mrr_cents, "Net new MRR per period")}
        hint="New plus expansion, minus contraction and churn"
      />
      <StatTile
        label="Net revenue retention"
        value={k.net_revenue_retention_12m === null ? "none" : formatPercent(k.net_revenue_retention_12m, 0)}
        delta={deltaFromRatio(changeOf(c, "net_revenue_retention_12m"), formatSignedPercent, "up")}
        spark={spark((s) => (s.net_revenue_retention_12m === null ? null : s.net_revenue_retention_12m * 100), "Net revenue retention over the range")}
        hint="Over twelve months"
      />
      <StatTile
        label="Gross churn"
        value={formatPercent(k.gross_churn_rate, 1)}
        delta={deltaFromRatio(changeOf(c, "gross_churn_rate"), formatSignedPercent, "down")}
        hint="MRR lost to churn and contraction, over the range"
      />
      <StatTile
        label="ARPA"
        value={formatCentsCompact(k.arpa_cents)}
        delta={deltaFromRatio(changeOf(c, "arpa_cents"), formatSignedPercent, "up")}
        spark={spark((s) => s.arpa_cents, "ARPA over the range")}
        hint="MRR per paying account"
      />
      <StatTile
        label="Paying accounts"
        value={formatInt(k.paying_orgs)}
        delta={deltaFromRatio(changeOf(c, "paying_orgs"), formatSignedPercent, "up")}
        spark={spark((s) => s.paying_orgs, "Paying accounts over the range")}
        hint={`${formatInt(k.active_orgs)} active in all`}
      />
    </section>
  );
}

function TopCustomers() {
  const top = useQuery({ queryKey: ["top-customers"], queryFn: ({ signal }) => analyticsApi.top(signal), staleTime: 5 * 60_000 });
  return (
    <Panel title="Top 10 customers" description="By revenue over the last twelve months. The line is their monthly revenue." bodyClassName="p-0">
      {top.isPending ? (
        <div className="p-5">
          <LoadingRows rows={5} />
        </div>
      ) : top.isError ? (
        <div className="p-5">
          <ErrorState error={top.error} onRetry={() => void top.refetch()} />
        </div>
      ) : (
        <div className="overflow-x-auto px-5 pb-5">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <caption className="sr-only">Top customers by revenue over twelve months</caption>
            <thead className="text-foreground-muted">
              <tr>
                <th scope="col" className="py-3 pr-4 font-semibold">
                  Customer
                </th>
                <th scope="col" className="py-3 pr-4 font-semibold">
                  Plan
                </th>
                <th scope="col" className="py-3 pr-4 font-semibold">
                  Region
                </th>
                <th scope="col" className="py-3 pr-4 text-right font-semibold">
                  Revenue, 12 months
                </th>
                <th scope="col" className="py-3 pr-4 text-right font-semibold">
                  MRR
                </th>
                <th scope="col" className="py-3 font-semibold">
                  Monthly revenue
                </th>
              </tr>
            </thead>
            <tbody>
              {top.data.items.map((c) => (
                <tr key={c.name} className="border-t border-border">
                  <th scope="row" className="py-3 pr-4 font-semibold">
                    {c.name}
                    <span className="block text-xs font-normal text-foreground-muted">{c.industry}</span>
                  </th>
                  <td className="py-3 pr-4 capitalize">{c.plan}</td>
                  <td className="py-3 pr-4 text-foreground-muted">{c.region}</td>
                  <td className="py-3 pr-4 text-right tabular-nums">{formatCentsCompact(c.revenue_12m_cents)}</td>
                  <td className="py-3 pr-4 text-right tabular-nums">{formatCents(c.mrr_cents)}</td>
                  <td className="py-3">
                    <Sparkline values={c.trend.slice(0, -1)} label={`Monthly revenue of ${c.name}`} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {top.data.items.some((c) => c.simulated) ? <p className="mt-3 text-xs text-foreground-muted">{top.data.footnote}</p> : null}
        </div>
      )}
    </Panel>
  );
}

const PAGE = 10;

function InvoiceTable({ from, to }: { from: string; to: string }) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useDebounced(text, 300);
  const params = { q, status, from, to, limit: PAGE, offset };
  const invoices = useQuery({ queryKey: ["all-invoices", params], queryFn: ({ signal }) => analyticsApi.invoices(params, signal), placeholderData: (previous) => previous });
  return (
    <Panel title="Invoices" description="Every customer's invoices whose period ends in the range." bodyClassName="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <SearchInput
          value={text}
          onChange={(v) => {
            setText(v);
            setOffset(0);
          }}
          label="Search invoices by customer"
          placeholder="Search by customer"
          className="w-64"
        />
        <Select
          value={status}
          aria-label="Invoice status"
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All statuses</option>
          <option value="paid">Paid</option>
          <option value="open">Open</option>
          <option value="void">Void</option>
        </Select>
        {invoices.data ? (
          <p className="pb-2 text-sm text-foreground-muted">
            {formatInt(invoices.data.total)} invoices, {formatCentsCompact(invoices.data.total_cents)} in all
          </p>
        ) : null}
      </div>
      {invoices.isPending ? (
        <LoadingRows rows={4} />
      ) : invoices.isError ? (
        <ErrorState error={invoices.error} onRetry={() => void invoices.refetch()} />
      ) : invoices.data.items.length === 0 ? (
        <EmptyState title="No invoice matches">Try another customer or status.</EmptyState>
      ) : (
        <>
          <div className={invoices.isFetching ? "overflow-x-auto opacity-60" : "overflow-x-auto"}>
            <table className="w-full min-w-[44rem] text-left text-sm">
              <caption className="sr-only">Invoices of all customers</caption>
              <thead className="text-foreground-muted">
                <tr>
                  <th scope="col" className="py-3 pr-4 font-semibold">
                    Customer
                  </th>
                  <th scope="col" className="py-3 pr-4 font-semibold">
                    Plan
                  </th>
                  <th scope="col" className="py-3 pr-4 font-semibold">
                    Period
                  </th>
                  <th scope="col" className="py-3 pr-4 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="py-3 text-right font-semibold">
                    Total
                  </th>
                </tr>
              </thead>
              <tbody>
                {invoices.data.items.map((i) => (
                  <tr key={i.id} className="border-t border-border">
                    <th scope="row" className="py-3 pr-4 font-semibold">
                      {i.customer}
                    </th>
                    <td className="py-3 pr-4 capitalize">{i.plan_code}</td>
                    <td className="py-3 pr-4 text-foreground-muted">
                      {formatDay(i.period_start)} to {formatDay(i.period_end)}
                    </td>
                    <td className="py-3 pr-4">
                      <Badge variant={invoiceTone(i.status)} className="capitalize">
                        {i.status}
                      </Badge>
                    </td>
                    <td className="py-3 text-right tabular-nums">{formatCents(i.total_cents)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={invoices.data.total} limit={PAGE} offset={offset} onChange={setOffset} noun="invoices" />
        </>
      )}
    </Panel>
  );
}

/** The revenue dashboard, for administrators. Every figure is computed from the tables; the history behind it is simulated, and the page says so. */
export default function Revenue() {
  const range = useDashboardRange();
  const monthlyParams = { ...range.params, granularity: "month" as const, compare: false };
  const report = useQuery({
    queryKey: ["revenue", range.params],
    queryFn: ({ signal }) => analyticsApi.revenue(range.params, signal),
    enabled: range.valid,
    placeholderData: (previous) => previous,
  });
  const monthly = useQuery({
    queryKey: ["revenue-monthly", monthlyParams],
    queryFn: ({ signal }) => analyticsApi.revenue(monthlyParams, signal),
    enabled: range.valid,
    placeholderData: (previous) => previous,
  });
  const opsMonthly = useQuery({
    queryKey: ["ops-monthly", monthlyParams],
    queryFn: ({ signal }) => analyticsApi.operations(monthlyParams, signal),
    enabled: range.valid,
    placeholderData: (previous) => previous,
  });
  const plan = useQuery({
    queryKey: ["breakdown", "plan", range.params],
    queryFn: ({ signal }) => analyticsApi.breakdown("plan", range.params, signal),
    enabled: range.valid,
    placeholderData: (previous) => previous,
  });
  const industry = useQuery({
    queryKey: ["breakdown", "industry", range.params],
    queryFn: ({ signal }) => analyticsApi.breakdown("industry", range.params, signal),
    enabled: range.valid,
    placeholderData: (previous) => previous,
  });
  const region = useQuery({
    queryKey: ["breakdown", "region", range.params],
    queryFn: ({ signal }) => analyticsApi.breakdown("region", range.params, signal),
    enabled: range.valid,
    placeholderData: (previous) => previous,
  });
  const cohorts = useQuery({ queryKey: ["cohorts"], queryFn: ({ signal }) => analyticsApi.cohorts(signal), staleTime: 5 * 60_000 });

  const busy = [report, monthly, opsMonthly, plan, industry, region].some((q) => q.isFetching && !q.isPending);
  const first = [report, monthly, plan, industry, region, opsMonthly].find((q) => q.isError);
  const data = report.data;

  return (
    <>
      <PageHeader title="Revenue" description="Recurring revenue, how it moves, who it comes from and how long customers stay. For administrators." />
      <DashboardControls range={range} csv="revenue" />
      {!range.valid ? null : report.isPending || monthly.isPending || plan.isPending ? (
        <LoadingRows rows={8} />
      ) : first ? (
        <ErrorState error={first.error} onRetry={() => void first.refetch()} />
      ) : data && monthly.data && plan.data && industry.data && region.data ? (
        <div className="space-y-6">
          <p className="text-sm text-foreground-muted">
            {formatDay(data.range.from)} to {formatDay(data.range.to)}, grouped by {data.granularity}.{" "}
            {range.compare ? "Changes are against the previous period of the same length." : "Turn on the comparison to see how each figure moved."}
          </p>
          <Kpis report={data} />
          <div className="grid gap-6 xl:grid-cols-2">
            <div className="xl:col-span-2">
              <MrrChart report={data} monthly={monthly.data} busy={busy} />
            </div>
            <MovementChart report={data} busy={busy} />
            <PlanChart breakdown={plan.data} granularity={data.granularity} busy={busy} />
            <DimensionChart title="MRR by industry" noun="industry" breakdown={industry.data} busy={busy} />
            <DimensionChart title="MRR by region" noun="region" breakdown={region.data} busy={busy} />
            <RevenueKindChart monthly={monthly.data} busy={busy} />
            {cohorts.data ? (
              <CohortChart cohorts={cohorts.data.cohorts} busy={cohorts.isFetching} />
            ) : cohorts.isError ? (
              <ErrorState error={cohorts.error} onRetry={() => void cohorts.refetch()} />
            ) : (
              <LoadingRows rows={4} />
            )}
            <GrowthCharts report={data} busy={busy} />
            {opsMonthly.data ? (
              <UnitEconomicsCharts revenue={monthly.data} ops={opsMonthly.data} busy={busy} />
            ) : opsMonthly.isError ? (
              <ErrorState error={opsMonthly.error} onRetry={() => void opsMonthly.refetch()} />
            ) : null}
          </div>
          <TopCustomers />
          <InvoiceTable from={data.range.from} to={data.range.to} />
          <p className="text-sm text-foreground-muted" data-testid="simulated-footnote">
            {data.footnote}
          </p>
        </div>
      ) : null}
    </>
  );
}
