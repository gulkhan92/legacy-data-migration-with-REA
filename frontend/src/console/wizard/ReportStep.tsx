import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, Undo2, XCircle } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatInt } from "@/lib/format";
import { pipelineApi, type Job, type Reconciliation, type TableReport } from "../api";
import { Modal, Pagination, Select } from "../ui/controls";
import { describeError, EmptyState, ErrorState, LoadingRows } from "../ui/states";
import { useToast } from "../ui/toast";
import { reasonLabel } from "./DryRunStep";
import { StepFrame, StepNote } from "./shared";

/** A half circle from 0 to 100. The arc is the score after the migration; a tick marks the score before it. */
export function QualityGauge({ before, after }: { before: number | null; after: number | null }) {
  const radius = 80;
  const cx = 100;
  const cy = 100;
  const point = (value: number, r: number) => {
    const angle = Math.PI * (1 - Math.min(100, Math.max(0, value)) / 100);
    return { x: cx + r * Math.cos(angle), y: cy - r * Math.sin(angle) };
  };
  const arc = (from: number, to: number) => {
    const a = point(from, radius);
    const b = point(to, radius);
    return `M ${a.x.toFixed(2)} ${a.y.toFixed(2)} A ${radius} ${radius} 0 0 1 ${b.x.toFixed(2)} ${b.y.toFixed(2)}`;
  };
  const tickIn = before === null ? null : point(before, radius - 14);
  const tickOut = before === null ? null : point(before, radius + 14);
  const summary =
    after === null ? "No quality score" : `Data quality score after the migration: ${after.toFixed(1)} of 100${before === null ? "" : `, before it ${before.toFixed(1)}`}`;
  return (
    <figure className="mx-auto w-full max-w-xs" role="img" aria-label={summary}>
      <svg viewBox="0 0 200 118" className="w-full">
        <path d={arc(0, 100)} fill="none" stroke="rgb(var(--border))" strokeWidth="14" strokeLinecap="round" />
        {after !== null ? <path d={arc(0, Math.max(0.5, after))} fill="none" stroke="rgb(var(--primary))" strokeWidth="14" strokeLinecap="round" /> : null}
        {tickIn && tickOut ? <line x1={tickIn.x} y1={tickIn.y} x2={tickOut.x} y2={tickOut.y} stroke="rgb(var(--foreground))" strokeWidth="3" strokeLinecap="round" /> : null}
        <text x={cx} y={cy - 8} textAnchor="middle" fontSize="30" fontWeight="700" fill="rgb(var(--foreground))">
          {after === null ? "none" : after.toFixed(1)}
        </text>
        <text x={cx} y={cy + 12} textAnchor="middle" fontSize="11" fill="rgb(var(--foreground-muted))">
          of 100 after
        </text>
      </svg>
      <figcaption className="text-center text-sm text-foreground-muted">
        {before === null ? "Data quality score" : `Before the migration: ${before.toFixed(1)} (the tick)`}
      </figcaption>
    </figure>
  );
}

function Count({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <dt className="text-sm text-foreground-muted">{label}</dt>
      <dd className="mt-1 text-2xl font-bold">{value}</dd>
      {hint ? <dd className="mt-1 text-xs text-foreground-muted">{hint}</dd> : null}
    </div>
  );
}

function Ok({ ok, children }: { ok: boolean; children?: React.ReactNode }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 text-success">
      <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
      {children ?? "Passed"}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 font-semibold text-danger">
      <XCircle className="h-4 w-4" aria-hidden="true" />
      {children ?? "Failed"}
    </span>
  );
}

/** One row per target table: the checks that prove nothing was lost, changed or invented. */
export function ChecksumTable({ tables }: { tables: TableReport[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[56rem] text-left text-sm">
        <caption className="sr-only">Reconciliation checks per table</caption>
        <thead className="bg-surface-muted text-foreground-muted">
          <tr>
            <th scope="col" className="px-4 py-3 font-semibold">
              Table
            </th>
            <th scope="col" className="px-4 py-3 font-semibold">
              Source rows
            </th>
            <th scope="col" className="px-4 py-3 font-semibold">
              Loaded
            </th>
            <th scope="col" className="px-4 py-3 font-semibold">
              Quarantined
            </th>
            <th scope="col" className="px-4 py-3 font-semibold">
              Row accounting
            </th>
            <th scope="col" className="px-4 py-3 font-semibold">
              Totals
            </th>
            <th scope="col" className="px-4 py-3 font-semibold">
              Sampled rows
            </th>
            <th scope="col" className="px-4 py-3 font-semibold">
              Result
            </th>
          </tr>
        </thead>
        <tbody>
          {tables.map((t) => {
            const sums = Object.entries(t.checks.numeric_sums);
            const sumsOk = sums.every(([, s]) => s.ok);
            return (
              <tr key={t.target_table} className="border-t border-border align-top">
                <th scope="row" className="px-4 py-3 font-semibold">
                  {t.target_table}
                  <span className="block text-xs font-normal text-foreground-muted">from {t.source_table}</span>
                </th>
                <td className="px-4 py-3 tabular-nums">{formatInt(t.source_rows)}</td>
                <td className="px-4 py-3 tabular-nums">{formatInt(t.loaded)}</td>
                <td className="px-4 py-3 tabular-nums">{formatInt(t.quarantined)}</td>
                <td className="px-4 py-3">
                  <Ok ok={t.checks.row_accounting.ok && t.checks.every_quarantined_row_has_a_reason.ok}>
                    {formatInt(t.checks.row_accounting.loaded)} + {formatInt(t.checks.row_accounting.quarantined)} = {formatInt(t.checks.row_accounting.source)}
                  </Ok>
                </td>
                <td className="px-4 py-3">
                  {sums.length === 0 ? (
                    <span className="text-foreground-muted">no money columns</span>
                  ) : (
                    <Ok ok={sumsOk}>{sums.map(([field, s]) => `${field}: ${s.loaded}`).join(", ")}</Ok>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Ok ok={t.checks.sampled_rows.ok}>
                    {t.checks.sampled_rows.matched} of {t.checks.sampled_rows.checked} match
                  </Ok>
                </td>
                <td className="px-4 py-3">
                  <Ok ok={t.passed} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const PAGE = 20;

function QuarantineTable({ jobId, report, status }: { jobId: string; report: Reconciliation; status: Job["status"] }) {
  const [reason, setReason] = useState("");
  const [table, setTable] = useState("");
  const [offset, setOffset] = useState(0);
  const reasons = [...new Set(report.checks.tables.flatMap((t) => Object.keys(t.by_reason)))].sort();
  const params = { ...(reason ? { reason } : {}), ...(table ? { table } : {}) };
  const page = useQuery({
    queryKey: ["quarantine", jobId, reason, table, offset],
    queryFn: ({ signal }) => pipelineApi.quarantine(jobId, { ...params, limit: PAGE, offset }, signal),
    placeholderData: (previous) => previous,
    enabled: status !== "ROLLED_BACK",
  });
  return (
    <section aria-label="Quarantined rows" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold">Quarantined rows</h3>
          <p className="text-sm text-foreground-muted">Rows that could not be loaded, each with the reason. Nothing is dropped without a record.</p>
        </div>
        <Button asChild variant="outline" size="sm">
          <a href={pipelineApi.quarantineCsvUrl(jobId, params)} download>
            <Download className="h-4 w-4" aria-hidden="true" />
            Export as CSV
          </a>
        </Button>
      </div>
      <div className="flex flex-wrap gap-3">
        <label className="text-sm">
          <span className="mb-1 block font-medium">Reason</span>
          <Select
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All reasons</option>
            {reasons.map((r) => (
              <option key={r} value={r}>
                {reasonLabel(r)}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Table</span>
          <Select
            value={table}
            onChange={(e) => {
              setTable(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All tables</option>
            {report.checks.tables.map((t) => (
              <option key={t.target_table} value={t.target_table}>
                {t.target_table}
              </option>
            ))}
          </Select>
        </label>
      </div>
      {page.isPending ? (
        <LoadingRows rows={4} />
      ) : page.isError ? (
        <ErrorState error={page.error} onRetry={() => void page.refetch()} />
      ) : page.data.rows.length === 0 ? (
        <EmptyState title="No quarantined rows">{reason || table ? "No row matches these filters." : "Every row loaded."}</EmptyState>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[44rem] text-left text-sm">
              <caption className="sr-only">Quarantined rows with their reasons</caption>
              <thead className="bg-surface-muted text-foreground-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Table
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Source row
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Reason
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Detail
                  </th>
                  <th scope="col" className="px-4 py-2 font-semibold">
                    Raw values
                  </th>
                </tr>
              </thead>
              <tbody>
                {page.data.rows.map((r) => (
                  <tr key={`${r.table}-${r.row}-${r.reason}`} className="border-t border-border align-top">
                    <td className="px-4 py-2">{r.table}</td>
                    <td className="px-4 py-2 tabular-nums">{r.row}</td>
                    <td className="px-4 py-2">
                      <Badge variant="warning">{reasonLabel(r.reason)}</Badge>
                    </td>
                    <td className="px-4 py-2 text-foreground-muted">{r.detail}</td>
                    <td className="max-w-xs px-4 py-2 font-mono text-xs">
                      <span className="line-clamp-2 break-words" title={JSON.stringify(r.raw)}>
                        {JSON.stringify(r.raw)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={page.data.total} limit={PAGE} offset={offset} onChange={setOffset} noun="rows" />
        </>
      )}
    </section>
  );
}

export function ReportStep({ job, working, onBack }: { job: Job; working: boolean; onBack: () => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const report = useQuery({ queryKey: ["reconciliation", job.id], queryFn: ({ signal }) => pipelineApi.reconciliation(job.id, signal), retry: false });
  const rollback = useMutation({
    mutationFn: () => pipelineApi.rollback(job.id),
    onSuccess: () => {
      setConfirming(false);
      toast.success("The migration was rolled back", "The rows it loaded were removed from the target.");
      void client.invalidateQueries({ queryKey: ["job", job.id] });
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (error) => {
      setConfirming(false);
      toast.error("It could not be rolled back", describeError(error));
    },
  });

  return (
    <StepFrame
      title="Reconciliation report"
      intro="The proof that the migration is complete: every source row is either loaded or quarantined with a reason, totals match, and a sample of rows was compared field by field."
      footer={
        <>
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          {working && job.status === "COMPLETED" ? (
            <Button variant="secondary" onClick={() => setConfirming(true)}>
              <Undo2 className="h-4 w-4" aria-hidden="true" />
              Roll back this migration
            </Button>
          ) : null}
        </>
      }
    >
      {job.status === "ROLLED_BACK" ? (
        <StepNote tone="info">This migration was rolled back. The report below is what it showed when it was loaded; the rows are no longer in the target.</StepNote>
      ) : null}
      {report.isPending ? (
        <LoadingRows rows={5} />
      ) : report.isError ? (
        <ErrorState error={report.error} onRetry={() => void report.refetch()} />
      ) : (
        <>
          <div className="grid gap-6 lg:grid-cols-[1fr_16rem]">
            <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Counts">
              <Count label="Source rows" value={formatInt(report.data.source_rows)} />
              <Count label="Loaded" value={formatInt(report.data.loaded_rows)} />
              <Count label="Quarantined" value={formatInt(report.data.quarantined_rows)} hint="Each with a reason" />
              <div className="rounded-lg border border-border bg-surface p-4">
                <dt className="text-sm text-foreground-muted">Reconciliation</dt>
                <dd className="mt-2 text-lg font-bold">
                  <Ok ok={report.data.passed}>{report.data.passed ? "All checks passed" : "Checks failed"}</Ok>
                </dd>
              </div>
            </dl>
            <QualityGauge before={report.data.quality_before} after={report.data.quality_after} />
          </div>
          <ChecksumTable tables={report.data.checks.tables} />
          <QuarantineTable jobId={job.id} report={report.data} status={job.status} />
        </>
      )}
      <Modal
        open={confirming}
        onOpenChange={setConfirming}
        title="Roll back this migration?"
        description="The rows it loaded are removed from the target tables and the migration is marked as rolled back. The report and the quarantine stay for the record."
        footer={
          <>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Keep the data
            </Button>
            <Button variant="secondary" disabled={rollback.isPending} onClick={() => rollback.mutate()}>
              Roll it back
            </Button>
          </>
        }
      />
    </StepFrame>
  );
}
