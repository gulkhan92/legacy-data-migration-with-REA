import { useQuery } from "@tanstack/react-query";
import { formatCompact, formatInt, formatMs, formatPercent, formatSignedPercent } from "@/lib/format";
import { analyticsApi } from "../api";
import { DashboardControls } from "../charts/DashboardControls";
import {
  ConfidenceChart,
  ErrorChart,
  FailedStagesChart,
  JobsChart,
  LatencyChart,
  MappingMethodsChart,
  QualityChart,
  RecordsChart,
  SuccessChart,
  TokensChart,
} from "../charts/ops-charts";
import { useDashboardRange } from "../charts/range";
import { changeOf, downsample, pluck } from "../charts/transforms";
import { Sparkline } from "../charts/Sparkline";
import { PageHeader } from "../ui/page";
import { ErrorState, LoadingRows } from "../ui/states";
import { deltaFromRatio, StatTile } from "../ui/stat";

/** The operations dashboard for analysts and administrators: how migrations run, how much the model is used and how good the data is. */
export default function Analytics() {
  const range = useDashboardRange();
  const ops = useQuery({
    queryKey: ["ops", range.params],
    queryFn: ({ signal }) => analyticsApi.operations(range.params, signal),
    enabled: range.valid,
    placeholderData: (previous) => previous,
  });
  const detail = useQuery({
    queryKey: ["ops-detail", range.params],
    queryFn: ({ signal }) => analyticsApi.detail(range.params, signal),
    enabled: range.valid,
    placeholderData: (previous) => previous,
  });
  const busy = (ops.isFetching && !ops.isPending) || (detail.isFetching && !detail.isPending);

  const report = ops.data;
  const d = detail.data;
  const change = report?.change;
  const spark = (pick: (s: NonNullable<typeof report>["series"][number]) => number | null | undefined, label: string) =>
    report ? <Sparkline values={downsample(pluck(report.series, pick))} label={label} /> : null;

  return (
    <>
      <PageHeader title="Analytics" description="How migrations run: volume, success, speed, the share the machine settles on its own, model use and the quality of the data." />
      <DashboardControls range={range} csv="operations" />
      {!range.valid ? null : ops.isPending || detail.isPending ? (
        <LoadingRows rows={8} />
      ) : ops.isError ? (
        <ErrorState error={ops.error} onRetry={() => void ops.refetch()} />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : report && d ? (
        <div className="space-y-6">
          <p className="text-sm text-foreground-muted">
            Figures cover the whole platform; the history behind them is simulated for demonstration and computed from the tables. {report.range.from} to {report.range.to}, grouped
            by {report.granularity}. {range.compare ? "Changes are against the previous period of the same length." : "Turn on the comparison to see how each figure moved."}
          </p>
          <section aria-label="Key figures" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              label="Jobs"
              value={formatInt(report.kpis.jobs)}
              delta={deltaFromRatio(changeOf(change, "jobs"), formatSignedPercent, "up")}
              spark={spark((s) => s.jobs, "Jobs over the range")}
            />
            <StatTile
              label="Success rate"
              value={report.kpis.success_rate === null ? "none" : formatPercent(report.kpis.success_rate)}
              delta={deltaFromRatio(changeOf(change, "success_rate"), formatSignedPercent, "up")}
              spark={spark((s) => (s.success_rate === null ? null : s.success_rate * 100), "Success rate over the range")}
            />
            <StatTile
              label="Records migrated"
              value={formatCompact(report.kpis.records)}
              delta={deltaFromRatio(changeOf(change, "records"), formatSignedPercent, "up")}
              spark={spark((s) => s.records, "Records over the range")}
            />
            <StatTile
              label="Average load time"
              value={report.kpis.avg_duration_ms === null ? "none" : formatMs(Number(report.kpis.avg_duration_ms))}
              delta={deltaFromRatio(changeOf(change, "avg_duration_ms"), formatSignedPercent, "down")}
              spark={spark((s) => s.avg_duration_ms, "Average duration over the range")}
            />
            <StatTile
              label="Model tokens"
              value={formatCompact(report.kpis.tokens_total)}
              delta={deltaFromRatio(changeOf(change, "tokens_total"), formatSignedPercent, "none")}
              hint="Cache hits are not counted"
            />
            <StatTile
              label="Cache hit rate"
              value={d.llm.cache_hit_rate === null ? "none" : formatPercent(d.llm.cache_hit_rate)}
              hint={`${formatInt(d.llm.cache_hits)} answers reused, ${formatInt(d.llm.calls_made)} model calls made`}
            />
            <StatTile
              label="Provider failovers"
              value={formatInt(report.kpis.failovers)}
              delta={deltaFromRatio(changeOf(change, "failovers"), formatSignedPercent, "down")}
              hint="A provider failed and the next took over"
            />
            <StatTile
              label="Quality score"
              value={report.kpis.quality_score === null ? "none" : Number(report.kpis.quality_score).toFixed(1)}
              delta={deltaFromRatio(changeOf(change, "quality_score"), formatSignedPercent, "up")}
              hint="Average of the sources, out of 100"
            />
          </section>

          <section aria-label="Tokens saved" className="grid gap-4 md:grid-cols-2">
            <StatTile
              label="Tokens the memory saved (an estimate)"
              value={formatCompact(d.tokens_saved_by_memory_estimate)}
              hint="Columns mapped from memory, times the average tokens the model used per column it resolved in the same period"
            />
            <StatTile label="Tokens the cache saved" value={formatCompact(d.tokens_saved_by_cache)} hint="Answers the cache returned instead of asking the model again" />
          </section>

          <div className="grid gap-6 xl:grid-cols-2">
            <JobsChart report={report} busy={busy} />
            <SuccessChart report={report} busy={busy} />
            <RecordsChart report={report} busy={busy} />
            <QualityChart report={report} busy={busy} />
            <MappingMethodsChart detail={d} busy={busy} />
            <TokensChart report={report} busy={busy} />
            <LatencyChart detail={d} busy={busy} />
            <ErrorChart detail={d} busy={busy} />
            <FailedStagesChart detail={d} busy={busy} />
            <ConfidenceChart detail={d} busy={busy} />
          </div>
          <p className="text-xs text-foreground-muted">{d.footnote}</p>
        </div>
      ) : null}
    </>
  );
}
