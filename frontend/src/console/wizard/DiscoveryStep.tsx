import { useQuery } from "@tanstack/react-query";
import { Search, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatInt } from "@/lib/format";
import { pipelineApi, type Job, type JobEvent } from "../api";
import { Drawer } from "../ui/controls";
import { ErrorState, LoadingRows } from "../ui/states";
import { ConfidenceBar } from "../ui/status";
import { discoverySummary, discoveryTables, type FieldRow } from "./discovery";
import { useStageRunner } from "./hooks";
import { EventLog, StageTimeline, StepFrame } from "./shared";

const PRE_DISCOVERY = new Set(["CREATED", "UPLOADED"]);

function EvidenceDrawer({ jobId, subject, onClose }: { jobId: string; subject: string | null; onClose: () => void }) {
  const evidence = useQuery({
    queryKey: ["evidence", jobId, subject],
    queryFn: ({ signal }) => pipelineApi.evidence(jobId, subject as string, signal),
    enabled: !!subject,
    staleTime: 5 * 60_000,
  });
  return (
    <Drawer
      open={!!subject}
      onOpenChange={(open) => !open && onClose()}
      title={`Evidence for ${subject ?? ""}`}
      description="What the reverse engineering tool (REA) found in the source for this field. It is the raw output, kept as the proof of the result."
    >
      {evidence.isPending ? (
        <LoadingRows rows={3} />
      ) : evidence.isError ? (
        <ErrorState error={evidence.error} onRetry={() => void evidence.refetch()} />
      ) : (
        <ul className="space-y-5">
          {evidence.data.map((item) => (
            <li key={item.id} className="rounded-lg border border-border">
              <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-muted px-4 py-2 text-sm">
                <span className="font-semibold">{item.command}</span>
                {item.rea_version ? <Badge variant="neutral">REA {item.rea_version}</Badge> : null}
                {item.duration_ms !== null ? <span className="text-foreground-muted">{item.duration_ms} ms</span> : null}
              </div>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs" tabIndex={0} aria-label={`Raw output of ${item.command}`}>
                {JSON.stringify(item.raw_json, null, 2)}
              </pre>
            </li>
          ))}
        </ul>
      )}
    </Drawer>
  );
}

export function DiscoveryStep({
  job,
  events,
  live,
  reconnect,
  working,
  onNext,
  onBack,
}: {
  job: Job;
  events: JobEvent[];
  live: boolean;
  reconnect: () => void;
  working: boolean;
  onNext: () => void;
  onBack: () => void;
}) {
  const runner = useStageRunner(job.id, reconnect);
  const [subject, setSubject] = useState<string | null>(null);
  const done = !PRE_DISCOVERY.has(job.status) && job.status !== "DISCOVERING";
  const running = job.status === "DISCOVERING" || runner.isPending;

  const discovery = useQuery({ queryKey: ["discovery", job.id], queryFn: ({ signal }) => pipelineApi.discovery(job.id, signal), enabled: done && job.status !== "FAILED" });
  const profile = useQuery({ queryKey: ["profile", job.id], queryFn: ({ signal }) => pipelineApi.profile(job.id, signal), enabled: done && job.status !== "FAILED" });
  const quality = useQuery({ queryKey: ["quality", job.id], queryFn: ({ signal }) => pipelineApi.quality(job.id, signal), enabled: done && job.status !== "FAILED" });

  const tables = useMemo(() => (discovery.data ? discoveryTables(discovery.data, profile.data ?? null) : []), [discovery.data, profile.data]);
  const summary = useMemo(() => (discovery.data ? discoverySummary(discovery.data, tables) : []), [discovery.data, tables]);
  const canContinue = done && job.status !== "FAILED";

  return (
    <StepFrame
      title="Discovery"
      intro="The platform reads the system to find out what it holds: its tables and fields, their types, and how sure it is of each. Then it profiles the data for quality problems and personal information."
      footer={
        <>
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button disabled={!canContinue} onClick={onNext}>
            Continue to mapping
          </Button>
        </>
      }
    >
      {!done && !running ? (
        <div className="rounded-lg border border-dashed border-border-strong p-8 text-center">
          <Search className="mx-auto h-8 w-8 text-foreground-subtle" aria-hidden="true" />
          <p className="mt-3 font-semibold">Discovery has not run yet</p>
          <p className="mx-auto mt-1 max-w-md text-sm text-foreground-muted">
            It reads the source without changing it. For a database it takes seconds; for a binary file the structure is recovered from the program that wrote it.
          </p>
          {working ? (
            <Button className="mt-5" onClick={() => runner.mutate({ stages: ["discover", "profile"] })}>
              Run discovery and profiling
            </Button>
          ) : (
            <p className="mt-4 text-sm text-foreground-muted">Your role can not start it.</p>
          )}
        </div>
      ) : null}

      {running ? (
        <div className="grid gap-6 lg:grid-cols-2" aria-busy="true">
          <div className="rounded-lg border border-border bg-surface p-5">
            <h3 className="mb-4 flex items-center gap-2 font-semibold">
              Working
              <Badge variant={live ? "success" : "neutral"}>{live ? "Live" : "Connecting"}</Badge>
            </h3>
            <StageTimeline events={events} stages={["discover", "profile"]} label="Discovery stages" />
          </div>
          <EventLog events={events} />
        </div>
      ) : null}

      {done ? (
        discovery.isPending || profile.isPending ? (
          <LoadingRows rows={6} />
        ) : discovery.isError ? (
          <ErrorState error={discovery.error} onRetry={() => void discovery.refetch()} />
        ) : (
          <>
            <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5" aria-label="What was found">
              {summary.map((item) => (
                <div key={item.label} className="rounded-lg border border-border bg-surface p-4">
                  <dt className="text-sm text-foreground-muted">{item.label}</dt>
                  <dd className="mt-1 text-xl font-bold">{item.value}</dd>
                </div>
              ))}
              {quality.data ? (
                <div className="rounded-lg border border-border bg-surface p-4">
                  <dt className="text-sm text-foreground-muted">Data quality score</dt>
                  <dd className="mt-1 text-xl font-bold">{quality.data.score.toFixed(1)} of 100</dd>
                </div>
              ) : null}
            </dl>

            {discovery.data?.limitations && discovery.data.limitations.length > 0 ? (
              <div role="note" className="flex gap-3 rounded-lg border border-warning/40 bg-warning-soft p-4 text-sm">
                <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-warning" aria-hidden="true" />
                <ul className="space-y-1">
                  {discovery.data.limitations.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="space-y-3">
              {tables.map((table, index) => (
                <details key={table.name} open={index === 0} className="group rounded-lg border border-border bg-surface">
                  <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 font-semibold marker:text-foreground-muted">
                    <span>{table.name}</span>
                    <span className="text-sm font-normal text-foreground-muted">
                      {table.fields.length} fields{table.rows !== null ? `, ${formatInt(table.rows)} rows` : ""}
                      {table.note ? `, ${table.note}` : ""}
                    </span>
                    {table.confidence !== null ? (
                      <span className="ml-auto">
                        <ConfidenceBar value={table.confidence} label={`Confidence of the table ${table.name}`} />
                      </span>
                    ) : null}
                  </summary>
                  <div className="overflow-x-auto border-t border-border">
                    <table className="w-full min-w-[40rem] text-left text-sm">
                      <caption className="sr-only">Fields of {table.name}</caption>
                      <thead className="text-foreground-muted">
                        <tr>
                          <th scope="col" className="px-4 py-2 font-semibold">
                            Field
                          </th>
                          <th scope="col" className="px-4 py-2 font-semibold">
                            Type
                          </th>
                          <th scope="col" className="px-4 py-2 font-semibold">
                            Found by
                          </th>
                          <th scope="col" className="px-4 py-2 font-semibold">
                            Confidence
                          </th>
                          <th scope="col" className="px-4 py-2 font-semibold">
                            Details
                          </th>
                          <th scope="col" className="px-4 py-2 font-semibold">
                            <span className="sr-only">Evidence</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {table.fields.map((f: FieldRow) => (
                          <tr key={f.subject} className="border-t border-border">
                            <td className="px-4 py-2 font-mono text-[0.8125rem]">
                              {f.name}{" "}
                              {f.pii ? (
                                <Badge variant="warning" className="ml-1">
                                  Personal data
                                </Badge>
                              ) : null}
                            </td>
                            <td className="px-4 py-2 text-foreground-muted">{f.type}</td>
                            <td className="px-4 py-2 text-foreground-muted">{f.method}</td>
                            <td className="px-4 py-2">
                              <ConfidenceBar value={f.confidence} label={`Confidence of ${f.subject}`} />
                            </td>
                            <td className="px-4 py-2 text-foreground-muted">{f.detail}</td>
                            <td className="px-4 py-2 text-right">
                              <Button variant="ghost" size="sm" onClick={() => setSubject(f.subject)} aria-label={`Show the evidence for ${f.subject}`}>
                                Evidence
                              </Button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              ))}
            </div>
          </>
        )
      ) : null}
      <EvidenceDrawer jobId={job.id} subject={subject} onClose={() => setSubject(null)} />
    </StepFrame>
  );
}
