import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { MoreHorizontal, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { adminApi, jobsApi, type Job } from "../api";
import { DataTable, type Sort } from "../ui/data-table";
import { Modal, Pagination, SearchInput, Select, useDebounced } from "../ui/controls";
import { DropdownContent, DropdownItem, DropdownMenu, DropdownTrigger } from "../ui/menu";
import { PageHeader } from "../ui/page";
import { roleLabel } from "../ui/roles";
import { canWork } from "../ui/roles";
import { describeError, EmptyState, ErrorState, LoadingRows } from "../ui/states";
import { JobStatusBadge, STATUS_OPTIONS } from "../ui/status";
import { useToast } from "../ui/toast";

const PAGE = 15;
const KINDS = [
  { value: "sqlite", label: "SQLite database" },
  { value: "dotnet_binary", label: ".NET binary" },
  { value: "csv", label: "Delimited text" },
];
const kindLabel = (kind: string | null) => KINDS.find((k) => k.value === kind)?.label ?? kind ?? "";
const OPEN = new Set(["FAILED", "COMPLETED", "ROLLED_BACK"]);

export default function Migrations() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const toast = useToast();
  const client = useQueryClient();
  const [cancelling, setCancelling] = useState<Job | null>(null);
  const [text, setText] = useState(params.get("q") ?? "");
  const q = useDebounced(text, 300);
  const status = params.get("status") ?? "";
  const kind = params.get("kind") ?? "";
  const owner = params.get("owner") ?? "";
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const offset = Number(params.get("offset") ?? 0) || 0;
  const sort: Sort = { key: params.get("sort") ?? "created", order: params.get("order") === "asc" ? "asc" : "desc" };
  const working = me ? canWork(me.role) : false;

  function update(changes: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("offset" in changes)) next.delete("offset");
    setParams(next, { replace: true });
  }

  const members = useQuery({
    queryKey: ["members-for-filter"],
    queryFn: ({ signal }) => adminApi.users({ limit: 200 }, signal),
    enabled: me?.role === "admin",
    staleTime: 5 * 60_000,
  });
  const filters = { status, source_kind: kind, q, created_from: from, created_to: to, owner_id: owner, sort: sort.key, order: sort.order, limit: PAGE, offset };
  const jobs = useQuery({ queryKey: ["jobs", filters], queryFn: ({ signal }) => jobsApi.list(filters, signal), placeholderData: (previous) => previous, refetchInterval: 10_000 });

  const cancel = useMutation({
    mutationFn: (job: Job) => jobsApi.cancel(job.id),
    onSuccess: () => {
      toast.success("The migration was cancelled");
      setCancelling(null);
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (error) => toast.error("It could not be cancelled", describeError(error)),
  });

  const columns = useMemo<ColumnDef<Job, unknown>[]>(
    () => [
      {
        id: "source",
        header: "System",
        cell: ({ row }) => (
          <Link to={`/app/migrations/${row.original.id}`} className="font-medium text-primary hover:underline">
            {row.original.source_name ?? row.original.id}
          </Link>
        ),
      },
      { id: "kind", header: "Kind", cell: ({ row }) => <span className="text-foreground-muted">{kindLabel(row.original.source_kind)}</span> },
      { id: "status", header: "Status", cell: ({ row }) => <JobStatusBadge status={row.original.status} /> },
      { id: "owner", header: "Owner", cell: ({ row }) => <span className="text-foreground-muted">{row.original.owner_name ?? "unknown"}</span> },
      { id: "created", header: "Created", cell: ({ row }) => <span className="whitespace-nowrap text-foreground-muted">{formatDateTime(row.original.created_at)}</span> },
      {
        id: "actions",
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => {
          const job = row.original;
          return (
            <DropdownMenu>
              <DropdownTrigger asChild>
                <Button variant="ghost" size="icon" aria-label={`Actions for ${job.source_name ?? "the migration"}`}>
                  <MoreHorizontal className="h-5 w-5" aria-hidden="true" />
                </Button>
              </DropdownTrigger>
              <DropdownContent align="end">
                <DropdownItem asChild>
                  <Link to={`/app/migrations/${job.id}`}>{job.status === "COMPLETED" || job.status === "ROLLED_BACK" ? "View the report" : "Open"}</Link>
                </DropdownItem>
                {working && !OPEN.has(job.status) ? (
                  <DropdownItem onSelect={() => setCancelling(job)} className="text-danger">
                    Cancel the migration
                  </DropdownItem>
                ) : null}
              </DropdownContent>
            </DropdownMenu>
          );
        },
      },
    ],
    [working],
  );

  const filtered = !!(status || kind || q || owner || from || to);

  return (
    <>
      <PageHeader
        title="Migrations"
        description="Every migration of this organization. Filter by state, kind of system, date or owner, and sort by any column that has an arrow."
        actions={
          working ? (
            <Button asChild>
              <Link to="/app/migrations/new">
                <Plus className="h-4 w-4" aria-hidden="true" />
                New migration
              </Link>
            </Button>
          ) : null
        }
      />
      <form role="search" aria-label="Filter migrations" className="mb-4 flex flex-wrap items-end gap-3" onSubmit={(e) => e.preventDefault()}>
        <SearchInput
          value={text}
          onChange={(v) => {
            setText(v);
            update({ q: v || null });
          }}
          label="Search by system name"
          className="w-64"
        />
        <label className="text-sm">
          <span className="mb-1 block font-medium">Status</span>
          <Select value={status} onChange={(e) => update({ status: e.target.value || null })} aria-label="Status">
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Kind of system</span>
          <Select value={kind} onChange={(e) => update({ kind: e.target.value || null })} aria-label="Kind of system">
            <option value="">All kinds</option>
            {KINDS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Owner</span>
          <Select value={owner} onChange={(e) => update({ owner: e.target.value || null })} aria-label="Owner">
            <option value="">Everyone</option>
            {me ? <option value={me.user_id}>Me</option> : null}
            {(members.data?.items ?? [])
              .filter((m) => m.user_id !== me?.user_id)
              .map((m) => (
                <option key={m.user_id} value={m.user_id}>
                  {m.full_name} ({roleLabel(m.role)})
                </option>
              ))}
          </Select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Created from</span>
          <Input type="date" value={from} onChange={(e) => update({ from: e.target.value || null })} className="h-10 w-40 text-sm" />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Created to</span>
          <Input type="date" value={to} onChange={(e) => update({ to: e.target.value || null })} className="h-10 w-40 text-sm" />
        </label>
        {filtered ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setText("");
              setParams(new URLSearchParams(), { replace: true });
            }}
          >
            Clear filters
          </Button>
        ) : null}
      </form>

      {jobs.isPending ? (
        <LoadingRows rows={6} />
      ) : jobs.isError ? (
        <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />
      ) : jobs.data.items.length === 0 ? (
        <EmptyState
          title={filtered ? "No migration matches these filters" : "No migrations yet"}
          action={
            filtered ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setText("");
                  setParams(new URLSearchParams(), { replace: true });
                }}
              >
                Clear filters
              </Button>
            ) : working ? (
              <Button asChild size="sm">
                <Link to="/app/migrations/new">Start the first migration</Link>
              </Button>
            ) : undefined
          }
        >
          {filtered
            ? "Try a wider date range or fewer filters."
            : "A migration takes a legacy system, finds its structure, maps it to a target schema and loads it with a reconciliation report."}
        </EmptyState>
      ) : (
        <>
          <DataTable
            caption="Migrations"
            data={jobs.data.items}
            columns={columns}
            rowKey={(j) => j.id}
            sortable={["source", "kind", "status", "created"]}
            sort={sort}
            onSort={(s) => update({ sort: s.key, order: s.order })}
            busy={jobs.isFetching && !jobs.isPending}
          />
          <Pagination total={jobs.data.total} limit={PAGE} offset={offset} onChange={(o) => update({ offset: o ? String(o) : null })} noun="migrations" />
        </>
      )}

      <Modal
        open={!!cancelling}
        onOpenChange={(open) => !open && setCancelling(null)}
        title="Cancel this migration?"
        description={`${cancelling?.source_name ?? "The migration"} will be marked as failed with the reason "cancelled". A stage that is running now finishes first.`}
        footer={
          <>
            <Button variant="outline" onClick={() => setCancelling(null)}>
              Keep it
            </Button>
            <Button variant="secondary" disabled={cancel.isPending} onClick={() => cancelling && cancel.mutate(cancelling)}>
              Cancel the migration
            </Button>
          </>
        }
      />
    </>
  );
}
