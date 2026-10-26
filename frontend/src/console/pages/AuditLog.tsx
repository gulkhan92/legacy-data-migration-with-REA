import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { formatDateTime } from "@/lib/format";
import { adminApi, type AuditEntry } from "../api";
import { Pagination, SearchInput, useDebounced } from "../ui/controls";
import { PageHeader } from "../ui/page";
import { EmptyState, ErrorState, LoadingRows } from "../ui/states";

const PAGE = 25;

/** "job.load" becomes "Job load". */
export const actionLabel = (action: string): string => {
  const text = action.replace(/[._]/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

/** The details of an entry as short text: the few keys and values, no braces. */
export function summarize(detail: Record<string, unknown>): string {
  return Object.entries(detail)
    .slice(0, 4)
    .map(([k, v]) => `${k}: ${typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}`)
    .join(", ");
}

export default function AuditLog() {
  const [text, setText] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useDebounced(text, 300);
  const params = { q, from, to, limit: PAGE, offset };
  const log = useQuery({ queryKey: ["audit", params], queryFn: ({ signal }) => adminApi.audit(params, signal), placeholderData: (previous) => previous });
  return (
    <>
      <PageHeader
        title="Audit log"
        description="Who did what in this organization, newest first. Sign ins, uploads, mapping approvals, loads, role changes and settings are all recorded, and nothing here can be edited."
      />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <SearchInput
          value={text}
          onChange={(v) => {
            setText(v);
            setOffset(0);
          }}
          label="Search the audit log"
          placeholder="Search action, person, entity or details"
          className="w-80"
        />
        <label className="text-sm">
          <span className="mb-1 block font-medium">From</span>
          <Input
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setOffset(0);
            }}
            className="h-10 w-40 text-sm"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">To</span>
          <Input
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setOffset(0);
            }}
            className="h-10 w-40 text-sm"
          />
        </label>
      </div>
      {log.isPending ? (
        <LoadingRows rows={8} />
      ) : log.isError ? (
        <ErrorState error={log.error} onRetry={() => void log.refetch()} />
      ) : log.data.items.length === 0 ? (
        <EmptyState title="No events match">{q || from || to ? "Try fewer words or a wider range of dates." : "Events appear here as people use the console."}</EmptyState>
      ) : (
        <>
          <div className={log.isFetching ? "overflow-x-auto rounded-lg border border-border opacity-60" : "overflow-x-auto rounded-lg border border-border"}>
            <table className="w-full min-w-[52rem] text-left text-sm">
              <caption className="sr-only">Audit events</caption>
              <thead className="bg-surface-muted text-foreground-muted">
                <tr>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    When
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Who
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Action
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    On
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Details
                  </th>
                </tr>
              </thead>
              <tbody>
                {log.data.items.map((e: AuditEntry) => (
                  <tr key={e.id} className="border-t border-border align-top">
                    <td className="whitespace-nowrap px-4 py-3 text-foreground-muted">{formatDateTime(e.created_at)}</td>
                    <td className="px-4 py-3">{e.user_name ?? <span className="text-foreground-muted">the system</span>}</td>
                    <td className="px-4 py-3 font-medium">{actionLabel(e.action)}</td>
                    <td className="px-4 py-3 text-foreground-muted">
                      {e.entity_type ?? ""}
                      {e.entity_id ? (
                        <span className="block max-w-[12rem] truncate font-mono text-xs" title={e.entity_id}>
                          {e.entity_id}
                        </span>
                      ) : null}
                    </td>
                    <td className="max-w-sm break-words px-4 py-3 text-foreground-muted">{summarize(e.detail)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={log.data.total} limit={PAGE} offset={offset} onChange={setOffset} noun="events" />
        </>
      )}
    </>
  );
}
