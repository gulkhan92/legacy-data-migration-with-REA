import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Database, Play } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { formatCompact, formatDay, formatInt, formatMs, formatPercent, formatSignedPercent, timeAgo } from "@/lib/format";
import { consoleApi, sourcesApi, type Job } from "../api";
import { progressOf, stepForStatus, STEPS } from "../wizard/steps";
import { PageHeader, Panel } from "../ui/page";
import { EmptyState, QueryView } from "../ui/states";
import { deltaOf, StatTile } from "../ui/stat";
import { JobStatusBadge } from "../ui/status";
import { canWork } from "../ui/roles";

function JobCard({ job }: { job: Job }) {
  const step = STEPS.find((s) => s.id === stepForStatus(job.status));
  return (
    <li>
      <Link to={`/app/migrations/${job.id}`} className="block rounded-lg border border-border bg-surface p-4 hover:border-primary">
        <div className="flex items-start justify-between gap-2">
          <p className="min-w-0 truncate font-semibold">{job.source_name ?? "Migration"}</p>
          <JobStatusBadge status={job.status} />
        </div>
        <p className="mt-1 text-sm text-foreground-muted">
          Step {STEPS.findIndex((s) => s.id === step?.id) + 1} of {STEPS.length}: {step?.label}
        </p>
        <div
          className="mt-3 h-2 overflow-hidden rounded-full bg-primary-soft"
          role="progressbar"
          aria-label={`Progress of ${job.source_name ?? "the migration"}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progressOf(job.status) * 100)}
        >
          <div className="h-full rounded-full bg-primary" style={{ width: `${Math.round(progressOf(job.status) * 100)}%` }} />
        </div>
        <p className="mt-2 text-xs text-foreground-muted">Updated {timeAgo(job.updated_at)}</p>
      </Link>
    </li>
  );
}

export default function Overview() {
  const { me } = useAuth();
  const overview = useQuery({ queryKey: ["overview"], queryFn: ({ signal }) => consoleApi.overview(signal), refetchInterval: 15_000 });
  const samples = useQuery({ queryKey: ["samples"], queryFn: ({ signal }) => sourcesApi.samples(signal), staleTime: 5 * 60_000 });
  const working = me ? canWork(me.role) : false;

  return (
    <>
      <PageHeader
        title="Overview"
        description={
          overview.data
            ? `${formatDay(overview.data.month.from)} to ${formatDay(overview.data.month.to)}. Everything here is this organization's own data.`
            : "Your migrations this month."
        }
        actions={
          working ? (
            <Button asChild>
              <Link to="/app/migrations/new">
                <Play className="h-4 w-4" aria-hidden="true" />
                New migration
              </Link>
            </Button>
          ) : null
        }
      />
      <QueryView query={overview} skeleton={<OverviewSkeleton />}>
        {(data) => {
          const k = data.kpis;
          const p = data.previous;
          const vs = "vs last month";
          return (
            <div className="space-y-8">
              <section aria-label="This month in numbers" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
                <StatTile
                  label="Jobs this month"
                  value={formatInt(k.jobs)}
                  delta={deltaOf(k.jobs, p.jobs, formatSignedPercent, "up", vs)}
                  hint="Finished jobs, completed or failed"
                />
                <StatTile
                  label="Records migrated"
                  value={formatCompact(k.records)}
                  delta={deltaOf(k.records, p.records, formatSignedPercent, "up", vs)}
                  hint={`${formatInt(k.records)} records loaded`}
                />
                <StatTile
                  label="Success rate"
                  value={k.success_rate === null ? "none yet" : formatPercent(k.success_rate)}
                  delta={deltaOf(k.success_rate, p.success_rate, formatSignedPercent, "up", vs)}
                  hint="Of the jobs that finished"
                />
                <StatTile
                  label="Average duration"
                  value={k.avg_duration_ms === null ? "none yet" : formatMs(k.avg_duration_ms)}
                  delta={deltaOf(k.avg_duration_ms, p.avg_duration_ms, formatSignedPercent, "down", vs)}
                  hint="Of the load stage"
                />
                <StatTile
                  label="Tokens used"
                  value={formatCompact(k.tokens)}
                  delta={deltaOf(k.tokens, p.tokens, formatSignedPercent, "none", vs)}
                  hint="Language model tokens, cache hits not counted"
                />
              </section>

              <Panel title="In progress" description="Migrations that are not finished. Open one to continue where it stopped.">
                {data.in_progress.length === 0 ? (
                  <EmptyState title="Nothing in progress" icon={<Database className="h-10 w-10" />}>
                    {working ? "Start a migration from one of the sample systems below, or upload your own." : "When a colleague starts a migration it shows up here."}
                  </EmptyState>
                ) : (
                  <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {data.in_progress.map((job) => (
                      <JobCard key={job.id} job={job} />
                    ))}
                  </ul>
                )}
              </Panel>

              <Panel
                title="Recent migrations"
                actions={
                  <Button asChild variant="ghost" size="sm">
                    <Link to="/app/migrations">
                      All migrations
                      <ArrowRight className="h-4 w-4" aria-hidden="true" />
                    </Link>
                  </Button>
                }
                bodyClassName="p-0"
              >
                {data.recent.length === 0 ? (
                  <div className="p-5">
                    <EmptyState title="No migrations yet">Your first one appears here as soon as you create it.</EmptyState>
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[36rem] text-left text-sm">
                      <caption className="sr-only">Recent migrations</caption>
                      <thead className="text-foreground-muted">
                        <tr>
                          <th scope="col" className="px-5 py-3 font-semibold">
                            System
                          </th>
                          <th scope="col" className="px-5 py-3 font-semibold">
                            Status
                          </th>
                          <th scope="col" className="px-5 py-3 font-semibold">
                            Owner
                          </th>
                          <th scope="col" className="px-5 py-3 font-semibold">
                            Created
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.recent.map((job) => (
                          <tr key={job.id} className="border-t border-border">
                            <td className="px-5 py-3">
                              <Link to={`/app/migrations/${job.id}`} className="font-medium text-primary hover:underline">
                                {job.source_name ?? job.id}
                              </Link>
                            </td>
                            <td className="px-5 py-3">
                              <JobStatusBadge status={job.status} />
                            </td>
                            <td className="px-5 py-3 text-foreground-muted">{job.owner_name ?? "unknown"}</td>
                            <td className="px-5 py-3 text-foreground-muted">{timeAgo(job.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>

              {working ? (
                <Panel title="Quick start" description="Two legacy systems come with the platform. Each is a real migration from start to finish.">
                  <ul className="grid gap-4 md:grid-cols-2">
                    {(samples.data ?? []).map((sample) => (
                      <li key={sample.name} className="flex flex-col rounded-lg border border-border p-4">
                        <p className="font-semibold">{sample.title}</p>
                        <p className="mt-1 flex-1 text-sm text-foreground-muted">{sample.description}</p>
                        <div className="mt-3">
                          {sample.available ? (
                            <Button asChild size="sm" variant="outline">
                              <Link to={`/app/migrations/new?sample=${sample.name}`}>Start with this system</Link>
                            </Button>
                          ) : (
                            <p className="text-sm text-foreground-muted">The sample files are not installed on this server.</p>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </Panel>
              ) : null}
            </div>
          );
        }}
      </QueryView>
    </>
  );
}

function OverviewSkeleton() {
  return (
    <div role="status" aria-label="Loading the overview" className="space-y-8">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="h-32 animate-pulse rounded-lg bg-surface-muted" />
        ))}
      </div>
      <div className="h-48 animate-pulse rounded-lg bg-surface-muted" />
      <div className="h-64 animate-pulse rounded-lg bg-surface-muted" />
    </div>
  );
}
