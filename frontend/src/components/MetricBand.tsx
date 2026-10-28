import { useQuery } from "@tanstack/react-query";
import { Container } from "@/components/layout";
import { Skeleton } from "@/components/ui/skeleton";
import { publicApi, type PublicMetrics } from "@/lib/api";
import { formatCompact, formatDate, formatDuration, formatInt, formatPercent } from "@/lib/format";
import { useCountUp } from "@/lib/useCountUp";

type Stat = { id: string; label: string; value: number; render: (value: number) => string };

export function statsFrom(metrics: PublicMetrics): Stat[] {
  const stats: Stat[] = [
    { id: "records", label: "Records migrated", value: metrics.records_migrated, render: (v) => formatCompact(Math.round(v)) },
    { id: "jobs", label: "Migration jobs run", value: metrics.jobs_run, render: (v) => formatInt(Math.round(v)) },
  ];
  if (metrics.success_rate !== null) stats.push({ id: "success", label: "Job success rate", value: metrics.success_rate, render: (v) => formatPercent(v, 1) });
  if (metrics.average_quality_score !== null) stats.push({ id: "quality", label: "Average data quality score", value: metrics.average_quality_score, render: (v) => v.toFixed(1) });
  if (metrics.average_duration_seconds !== null) stats.push({ id: "duration", label: "Average load time", value: metrics.average_duration_seconds, render: (v) => formatDuration(v) });
  return stats;
}

function CountStat({ stat }: { stat: Stat }) {
  const { ref, value } = useCountUp<HTMLParagraphElement>(stat.value);
  return (
    <div className="text-center md:text-left">
      <p ref={ref} className="text-4xl font-bold tabular-nums text-white md:text-5xl">
        <span aria-hidden="true">{stat.render(value)}</span>
        <span className="sr-only">{stat.render(stat.value)}</span>
      </p>
      <p className="mt-1 text-sm text-white/80">{stat.label}</p>
    </div>
  );
}

/** Figures from GET /api/v1/public/metrics, labelled as the demo environment's. The band keeps its height in every state. */
export function MetricBand() {
  const { data, isPending, isError } = useQuery({ queryKey: ["public-metrics"], queryFn: ({ signal }) => publicApi.metrics(signal), staleTime: 5 * 60_000, retry: 1 });
  const stats = data ? statsFrom(data) : [];

  return (
    <section aria-labelledby="metrics-heading" className="bg-gradient-to-r from-navy via-navy-800 to-navy-700 py-12 text-white">
      <Container>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="metrics-heading" className="text-sm font-semibold uppercase tracking-wider text-teal">
            Demo environment figures
          </h2>
          {data?.as_of ? <p className="text-sm text-white/70">As of {formatDate(data.as_of)}. Simulated organizations and sample systems.</p> : null}
        </div>
        <div className="mt-6 grid min-h-[96px] grid-cols-2 gap-x-6 gap-y-8 md:grid-cols-3 lg:grid-cols-5">
          {isPending
            ? Array.from({ length: 5 }, (_, i) => (
                <div key={i}>
                  <Skeleton className="h-12 w-28 bg-white/10" />
                  <Skeleton className="mt-2 h-4 w-24 bg-white/10" />
                </div>
              ))
            : isError || !data || data.jobs_run === 0
              ? (
                <p className="col-span-full text-white/80" role="status">
                  {isError ? "The demo figures are unavailable right now." : "The demo environment has no figures yet."}
                </p>
              )
              : stats.map((stat) => <CountStat key={stat.id} stat={stat} />)}
        </div>
      </Container>
    </section>
  );
}
