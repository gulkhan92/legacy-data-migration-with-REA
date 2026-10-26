import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDuration, formatInt, formatPercent } from "@/lib/format";
import { pipelineApi, type DryRunResult, type Job, type TableDryRun } from "../api";
import { describeError } from "../ui/states";
import { Meter } from "../ui/status";
import { useToast } from "../ui/toast";
import { StepFrame, StepNote } from "./shared";

/** Quarantine reasons in words. */
export const REASONS: Record<string, string> = {
  required_null: "A required value is missing",
  invalid_date: "A date could not be read",
  duplicate: "A duplicate of another row",
  orphan_fk: "Refers to a record that does not exist",
  out_of_range: "A value is out of range",
  invalid_number: "A number could not be read",
  whitespace: "Stray spaces",
  case_anomaly: "Unusual capitalization",
};
export const reasonLabel = (code: string): string => REASONS[code] ?? code.replace(/_/g, " ");

function TableCard({ table }: { table: TableDryRun }) {
  const quarantined = Object.values(table.would_quarantine).reduce((n, v) => n + v, 0);
  const share = table.sample_rows > 0 ? table.would_load / table.sample_rows : 0;
  const risky = table.rules.filter((r) => r.failures > 0 || r.out_of_range > 0);
  return (
    <section aria-label={`Dry run of ${table.target_table}`} className="rounded-lg border border-border bg-surface p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lg font-semibold">
          {table.target_table} <span className="text-sm font-normal text-foreground-muted">from {table.source_table}</span>
        </h3>
        <p className="text-sm text-foreground-muted">
          Sample of {formatInt(table.sample_rows)} rows{table.source_rows !== null ? ` out of ${formatInt(table.source_rows)}` : ""}
        </p>
      </div>
      <dl className="mt-4 grid gap-3 sm:grid-cols-3">
        <div>
          <dt className="text-sm text-foreground-muted">Would load</dt>
          <dd className="text-xl font-bold">
            {formatInt(table.would_load)} <span className="text-sm font-normal text-foreground-muted">({formatPercent(share, 0)})</span>
          </dd>
        </div>
        <div>
          <dt className="text-sm text-foreground-muted">Would be quarantined</dt>
          <dd className="text-xl font-bold">{formatInt(quarantined)}</dd>
        </div>
        <div>
          <dt className="text-sm text-foreground-muted">Why</dt>
          <dd className="text-sm">
            {quarantined === 0 ? (
              "Nothing"
            ) : (
              <ul>
                {Object.entries(table.would_quarantine).map(([reason, count]) => (
                  <li key={reason}>
                    {reasonLabel(reason)}: <strong>{count}</strong>
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>
      </dl>
      {risky.length > 0 ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[28rem] text-left text-sm">
            <caption className="mb-1 text-left text-sm font-semibold">Rules with failures in the sample</caption>
            <thead className="text-foreground-muted">
              <tr>
                <th scope="col" className="py-1 pr-4 font-semibold">
                  Field
                </th>
                <th scope="col" className="py-1 pr-4 font-semibold">
                  Steps
                </th>
                <th scope="col" className="py-1 pr-4 font-semibold">
                  Failures
                </th>
                <th scope="col" className="py-1 font-semibold">
                  Out of range
                </th>
              </tr>
            </thead>
            <tbody>
              {risky.map((r) => (
                <tr key={r.target_field} className="border-t border-border">
                  <td className="py-1.5 pr-4 font-mono text-[0.8125rem]">{r.target_field}</td>
                  <td className="py-1.5 pr-4 text-foreground-muted">{r.ops.join(", ")}</td>
                  <td className="py-1.5 pr-4">
                    {r.failures} <span className="text-foreground-muted">({formatPercent(r.failure_rate)})</span>
                  </td>
                  <td className="py-1.5">{r.out_of_range}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-4 text-sm text-foreground-muted">No rule failed on this sample.</p>
      )}
      {table.preview.length > 0 ? (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-semibold">Example rows, source and target</summary>
          <div className="mt-2 overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[32rem] text-left text-sm">
              <caption className="sr-only">Example rows of {table.target_table}</caption>
              <thead className="bg-surface-muted text-foreground-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    Row
                  </th>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    Source
                  </th>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    Target
                  </th>
                  <th scope="col" className="px-3 py-2 font-semibold">
                    Result
                  </th>
                </tr>
              </thead>
              <tbody>
                {table.preview.slice(0, 6).map((p) => (
                  <tr key={p.row} className="border-t border-border align-top">
                    <td className="px-3 py-2 tabular-nums text-foreground-muted">{p.row}</td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {Object.entries(p.before)
                        .map(([k, v]) => `${k}: ${v === null ? "null" : String(v)}`)
                        .join(", ")}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {Object.entries(p.after)
                        .map(([k, v]) => `${k}: ${v === null ? "null" : String(v)}`)
                        .join(", ")}
                    </td>
                    <td className="px-3 py-2">{p.reason ? <Badge variant="danger">{reasonLabel(p.reason)}</Badge> : <Badge variant="success">Loads</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </section>
  );
}

function CostSummary({ cost }: { cost: DryRunResult["cost"] }) {
  return (
    <section aria-label="Cost summary" className="rounded-lg border border-border bg-surface p-5">
      <h3 className="text-lg font-semibold">Cost summary</h3>
      <dl className="mt-3 grid gap-4 sm:grid-cols-3">
        <div>
          <dt className="text-sm text-foreground-muted">Rows to load</dt>
          <dd className="text-xl font-bold">{formatInt(cost.source_rows)}</dd>
        </div>
        <div>
          <dt className="text-sm text-foreground-muted">Estimated load time</dt>
          <dd className="text-xl font-bold">{cost.estimated_seconds === null ? "no estimate" : formatDuration(cost.estimated_seconds)}</dd>
          <dd className="mt-1 text-xs text-foreground-muted">Based on {cost.basis}.</dd>
        </div>
        <div>
          <dt className="text-sm text-foreground-muted">Model tokens used by this migration</dt>
          <dd className="text-xl font-bold">
            {formatInt(cost.tokens_used)} <span className="text-sm font-normal text-foreground-muted">of {formatInt(cost.token_budget)}</span>
          </dd>
          <dd className="mt-2">
            <Meter value={cost.tokens_used} max={cost.token_budget} label="Tokens used of the budget of this migration" />
          </dd>
        </div>
      </dl>
    </section>
  );
}

export function DryRunStep({ job, working, onNext, onBack }: { job: Job; working: boolean; onNext: () => void; onBack: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [result, setResult] = useState<DryRunResult | null>(null);
  const run = useMutation({
    mutationFn: () => pipelineApi.dryRun(job.id),
    onSuccess: (r) => {
      setResult(r);
      void client.invalidateQueries({ queryKey: ["job", job.id] });
    },
    onError: (error) => toast.error("The dry run could not run", describeError(error)),
  });
  const ready = result ? result.ready : job.status === "READY";

  return (
    <StepFrame
      title="Dry run"
      intro="The whole set of rules runs over a sample of the source, and nothing is written to the target. You see how many rows would load, which would be quarantined and why, and what the real load would cost."
      footer={
        <>
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button disabled={!ready} onClick={onNext}>
            Continue to execute
          </Button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        {working ? (
          <Button onClick={() => run.mutate()} disabled={run.isPending}>
            <FlaskConical className="h-4 w-4" aria-hidden="true" />
            {run.isPending ? "Running the dry run" : result || job.status === "READY" ? "Run the dry run again" : "Run the dry run"}
          </Button>
        ) : (
          <p className="text-sm text-foreground-muted">Your role can not start it.</p>
        )}
        {run.isPending ? (
          <span role="status" className="text-sm text-foreground-muted">
            Transforming the sample
          </span>
        ) : null}
      </div>

      {!result && job.status === "READY" ? <StepNote tone="success">The last dry run found nothing that blocks a load. Run it again to see its details.</StepNote> : null}

      {result ? (
        <div className="space-y-5">
          {result.blocking.length > 0 ? (
            <StepNote tone="danger">
              <p className="font-semibold">The load is blocked until this is fixed:</p>
              <ul className="mt-1 list-disc pl-5">
                {result.blocking.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
              <p className="mt-2">Go back to the transform preview and fix the rule.</p>
            </StepNote>
          ) : (
            <StepNote tone="success">The dry run found nothing that blocks the load.</StepNote>
          )}
          {result.warnings.length > 0 ? (
            <StepNote tone="warning">
              <ul className="list-disc pl-5">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </StepNote>
          ) : null}
          <CostSummary cost={result.cost} />
          <div className="space-y-4">
            {result.tables.map((t) => (
              <TableCard key={t.target_table} table={t} />
            ))}
          </div>
        </div>
      ) : null}
    </StepFrame>
  );
}
