import { CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import type { JobEvent, Job } from "../api";
import { stageTimeline, type StageRow } from "./steps";
import type { Stage } from "../api";

const stateWord = { pending: "Waiting", running: "Running", done: "Done", failed: "Failed" } as const;

function StageIcon({ state }: { state: StageRow["state"] }) {
  if (state === "done") return <CheckCircle2 className="h-5 w-5 text-success" aria-hidden="true" />;
  if (state === "failed") return <XCircle className="h-5 w-5 text-danger" aria-hidden="true" />;
  if (state === "running") return <Loader2 className="h-5 w-5 animate-spin text-primary motion-reduce:animate-none" aria-hidden="true" />;
  return <CircleDashed className="h-5 w-5 text-foreground-subtle" aria-hidden="true" />;
}

/** The pipeline stages, each with its state and how long it took, read from the job's events. */
export function StageTimeline({ events, stages, label = "Stages" }: { events: JobEvent[]; stages?: Stage[]; label?: string }) {
  const rows = stageTimeline(events, stages);
  return (
    <ol aria-label={label} className="space-y-3">
      {rows.map((row) => (
        <li key={row.stage} className="flex items-start gap-3">
          <StageIcon state={row.state} />
          <div className="min-w-0 flex-1">
            <p className="font-medium">
              {row.label} <span className="sr-only">: {stateWord[row.state]}</span>
            </p>
            <p className="text-sm text-foreground-muted">
              {row.state === "pending"
                ? "Waiting"
                : row.state === "running"
                  ? "Running now"
                  : row.state === "failed"
                    ? (row.message ?? "Failed")
                    : `Done in ${row.seconds === null ? "unknown time" : `${row.seconds < 10 ? row.seconds.toFixed(1) : Math.round(row.seconds)} s`}`}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** The raw events as they arrive: a log that screen readers hear politely. */
export function EventLog({ events, limit = 40 }: { events: JobEvent[]; limit?: number }) {
  const shown = events.slice(-limit);
  return (
    <div className="rounded-lg border border-border bg-surface-muted">
      <p className="border-b border-border px-4 py-2 text-sm font-semibold">Live events</p>
      {shown.length === 0 ? (
        <p className="px-4 py-3 text-sm text-foreground-muted">Nothing has happened yet.</p>
      ) : (
        <ol role="log" aria-live="polite" aria-label="Events of this migration" className="max-h-56 overflow-y-auto px-4 py-2 font-mono text-xs">
          {shown.map((e) => (
            <li key={e.id} className="flex gap-3 py-0.5">
              <time dateTime={e.created_at} className="shrink-0 text-foreground-muted">
                {formatDateTime(e.created_at)}
              </time>
              <span className="shrink-0 font-semibold">{e.to_status}</span>
              <span className="min-w-0 break-words">{e.message ?? ""}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** A step of the wizard: a heading, what the step does, the content, and the buttons that move on. */
export function StepFrame({ title, intro, children, footer }: { title: string; intro: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <section aria-labelledby="step-title" className="space-y-6">
      <div>
        <h2 id="step-title" className="text-xl font-semibold">
          {title}
        </h2>
        <p className="mt-1 max-w-3xl text-foreground-muted">{intro}</p>
      </div>
      {children}
      {footer ? <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">{footer}</div> : null}
    </section>
  );
}

/** True while a stage runs on the server: the state says so, or a stage was just started and the state has not moved yet. */
export function isBusy(job: Job | undefined, starting: boolean): boolean {
  return starting || (job ? ["DISCOVERING", "DRY_RUN", "LOADING", "RECONCILING"].includes(job.status) : false);
}

export function StepNote({ tone = "info", children }: { tone?: "info" | "warning" | "danger" | "success"; children: ReactNode }) {
  const styles = {
    info: "border-primary/30 bg-primary-soft",
    warning: "border-warning/40 bg-warning-soft",
    danger: "border-danger/40 bg-danger-soft",
    success: "border-success/40 bg-success-soft",
  } as const;
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cn("rounded-lg border p-4 text-sm", styles[tone])}>
      {children}
    </div>
  );
}
