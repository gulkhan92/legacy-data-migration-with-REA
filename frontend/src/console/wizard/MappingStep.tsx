import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, Wand2, X } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { pipelineApi, type Job, type JobEvent, type Mapping, type TargetField } from "../api";
import { Modal, SearchInput, Select } from "../ui/controls";
import { describeError, EmptyState, ErrorState, LoadingRows } from "../ui/states";
import { ConfidenceBar, MethodBadge } from "../ui/status";
import { useToast } from "../ui/toast";
import { useStageRunner } from "./hooks";
import { EventLog, StageTimeline, StepFrame, StepNote } from "./shared";

/** Mappings the rules and the embedding settled with at least this confidence can be approved together. */
export const HIGH_CONFIDENCE = 0.85;

export type MappingFilter = "all" | "review" | "approved" | "unmapped";

/** A mapping's state in words: approved, waiting for a person, rejected, or without any target. */
export function mappingState(m: Pick<Mapping, "status" | "target">): "approved" | "review" | "rejected" | "unmapped" {
  if (m.status === "rejected") return "rejected";
  if (m.target === null) return "unmapped";
  return m.status === "approved" ? "approved" : "review";
}

export function filterMappings(rows: Mapping[], filter: MappingFilter, text: string): Mapping[] {
  const needle = text.trim().toLowerCase();
  return rows.filter((m) => {
    const state = mappingState(m);
    if (filter === "review" && state !== "review") return false;
    if (filter === "approved" && state !== "approved") return false;
    if (filter === "unmapped" && state !== "unmapped" && state !== "rejected") return false;
    return !needle || `${m.source_table}.${m.source_column} ${m.target ?? ""}`.toLowerCase().includes(needle);
  });
}

/** The ids that "approve all high confidence" would approve: waiting for review, with a target, and sure enough. */
export function highConfidenceIds(rows: Mapping[], threshold = HIGH_CONFIDENCE): string[] {
  return rows.filter((m) => mappingState(m) === "review" && (m.confidence ?? 0) >= threshold).map((m) => m.id);
}

const stateBadge = {
  approved: <Badge variant="success">Approved</Badge>,
  review: <Badge variant="warning">Needs review</Badge>,
  rejected: <Badge variant="neutral">Rejected</Badge>,
  unmapped: <Badge variant="neutral">No target</Badge>,
};

/** The list inside the picker. It exists only while the picker is open, so the search starts empty every time. */
function PickerBody({ mapping, fields, onPick, busy }: { mapping: Mapping | null; fields: TargetField[]; onPick: (field: TargetField) => void; busy: boolean }) {
  const [text, setText] = useState("");
  const listId = useId();
  const suggested = useMemo(() => new Map((mapping?.candidates ?? []).map((c) => [c.field, c.similarity])), [mapping]);
  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return fields
      .filter((f) => !needle || `${f.table}.${f.name} ${f.description ?? ""} ${f.synonyms.join(" ")}`.toLowerCase().includes(needle))
      .sort((a, b) => (suggested.get(`${b.table}.${b.name}`) ?? 0) - (suggested.get(`${a.table}.${a.name}`) ?? 0) || `${a.table}.${a.name}`.localeCompare(`${b.table}.${b.name}`));
  }, [fields, text, suggested]);
  return (
    <>
      <SearchInput value={text} onChange={setText} label="Search the target fields" placeholder="Search by name, table or description" />
      <ul id={listId} aria-label="Target fields" className="mt-3 max-h-80 divide-y divide-border overflow-y-auto rounded-lg border border-border">
        {shown.length === 0 ? (
          <li className="p-4 text-sm text-foreground-muted">No target field matches that.</li>
        ) : (
          shown.map((f) => {
            const key = `${f.table}.${f.name}`;
            const similarity = suggested.get(key);
            return (
              <li key={f.id}>
                <button type="button" disabled={busy} onClick={() => onPick(f)} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-surface-muted">
                  <span className="min-w-0 flex-1">
                    <span className="block font-mono text-sm font-semibold">{key}</span>
                    <span className="block text-sm text-foreground-muted">{f.description ?? ""}</span>
                  </span>
                  <span className="flex shrink-0 flex-col items-end gap-1">
                    <Badge variant="neutral">{f.type}</Badge>
                    {f.required ? <Badge variant="primary">required</Badge> : null}
                    {similarity !== undefined ? <span className="text-xs text-foreground-muted">suggested, {Math.round(similarity * 100)}% similar</span> : null}
                  </span>
                </button>
              </li>
            );
          })
        )}
      </ul>
    </>
  );
}

function TargetPicker({
  open,
  onClose,
  mapping,
  fields,
  onPick,
  busy,
}: {
  open: boolean;
  onClose: () => void;
  mapping: Mapping | null;
  fields: TargetField[];
  onPick: (field: TargetField) => void;
  busy: boolean;
}) {
  return (
    <Modal
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Choose the target field"
      description={mapping ? `Where should ${mapping.source_table}.${mapping.source_column} go?` : undefined}
      wide
    >
      <PickerBody mapping={mapping} fields={fields} onPick={onPick} busy={busy} />
    </Modal>
  );
}

export function MappingStep({
  job,
  events,
  reconnect,
  schemaName,
  working,
  onNext,
  onBack,
}: {
  job: Job;
  events: JobEvent[];
  reconnect: () => void;
  schemaName: string | undefined;
  working: boolean;
  onNext: () => void;
  onBack: () => void;
}) {
  const toast = useToast();
  const client = useQueryClient();
  const runner = useStageRunner(job.id, reconnect);
  const [filter, setFilter] = useState<MappingFilter>("all");
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<Mapping | null>(null);
  const [dirty, setDirty] = useState(false);
  const [queued, setQueued] = useState(false);

  const mapped = !["CREATED", "UPLOADED", "DISCOVERING", "PROFILED"].includes(job.status);
  const editable = working && (job.status === "MAPPED" || job.status === "AWAITING_REVIEW");
  // Mapping was started and the job has not yet reached the review: it counts as working even before the state says MAPPED.
  const waiting = (queued && job.status !== "AWAITING_REVIEW" && job.status !== "FAILED") || runner.isPending || job.status === "MAPPED";

  const mappings = useQuery({ queryKey: ["mappings", job.id], queryFn: ({ signal }) => pipelineApi.mappings(job.id, signal), enabled: mapped && job.status !== "FAILED" });
  const fields = useQuery({
    queryKey: ["target-fields", job.target_schema_id],
    queryFn: ({ signal }) => pipelineApi.fields(job.target_schema_id as string, signal),
    enabled: !!job.target_schema_id && editable,
    staleTime: 10 * 60_000,
  });

  const rows = useMemo(() => mappings.data ?? [], [mappings.data]);
  const shown = useMemo(() => filterMappings(rows, filter, text), [rows, filter, text]);
  const counts = useMemo(() => {
    const c = { all: rows.length, review: 0, approved: 0, unmapped: 0 };
    for (const m of rows) {
      const s = mappingState(m);
      if (s === "review") c.review++;
      else if (s === "approved") c.approved++;
      else c.unmapped++;
    }
    return c;
  }, [rows]);
  const high = useMemo(() => highConfidenceIds(rows), [rows]);

  const changed = () => {
    setDirty(true);
    void client.invalidateQueries({ queryKey: ["mappings", job.id] });
    void client.invalidateQueries({ queryKey: ["job", job.id] });
  };
  const fail = (title: string) => (error: unknown) => toast.error(title, describeError(error));

  const approve = useMutation({ mutationFn: (id: string) => pipelineApi.approveMapping(job.id, id), onSuccess: changed, onError: fail("It could not be approved") });
  const bulk = useMutation({
    mutationFn: (ids: string[]) => pipelineApi.bulkApprove(job.id, ids),
    onSuccess: (result) => {
      toast.success(`${result.approved} mappings approved${result.skipped.length ? `, ${result.skipped.length} skipped` : ""}`);
      setSelected(new Set());
      changed();
    },
    onError: fail("They could not be approved"),
  });
  const override = useMutation({
    mutationFn: ({ id, fieldId }: { id: string; fieldId: string | null }) => pipelineApi.patchMapping(job.id, id, { target_field_id: fieldId }),
    onSuccess: () => {
      setEditing(null);
      changed();
    },
    onError: fail("The change could not be saved"),
  });
  const regenerate = useMutation({
    mutationFn: () => pipelineApi.generateRules(job.id),
    onSuccess: () => {
      setDirty(false);
      toast.success("The transform rules were regenerated");
      void client.invalidateQueries({ queryKey: ["job", job.id] });
      void client.invalidateQueries({ queryKey: ["rules", job.id] });
    },
    onError: fail("The rules could not be regenerated"),
  });

  async function carryOn() {
    if (dirty && job.status === "AWAITING_REVIEW") {
      try {
        await regenerate.mutateAsync();
      } catch {
        return;
      }
    }
    onNext();
  }

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const canContinue = job.status === "AWAITING_REVIEW" && !waiting;
  return (
    <StepFrame
      title="Mapping review"
      intro="Each source column is matched to a field of the target schema. Remembered mappings come first, then similarity of meaning, then the language model for what is still unclear. Approve what is right and change what is not."
      footer={
        <>
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button disabled={!canContinue || regenerate.isPending} onClick={() => void carryOn()}>
            {dirty ? "Regenerate the rules and continue" : "Continue to the transform preview"}
          </Button>
        </>
      }
    >
      {job.status === "PROFILED" && !waiting ? (
        <div className="rounded-lg border border-dashed border-border-strong p-8 text-center">
          <Wand2 className="mx-auto h-8 w-8 text-foreground-subtle" aria-hidden="true" />
          <p className="mt-3 font-semibold">The columns have not been mapped yet</p>
          <p className="mx-auto mt-1 max-w-md text-sm text-foreground-muted">
            Mapping to the <strong>{schemaName ?? "chosen"}</strong> schema also generates the first version of the transform rules.
          </p>
          {working ? (
            <Button
              className="mt-5"
              disabled={!schemaName}
              onClick={() => runner.mutate({ stages: ["map", "rules"], ...(schemaName ? { targetSchema: schemaName } : {}) }, { onSuccess: () => setQueued(true) })}
            >
              Map the columns
            </Button>
          ) : (
            <p className="mt-4 text-sm text-foreground-muted">Your role can not start it.</p>
          )}
        </div>
      ) : null}

      {waiting ? (
        <div className="grid gap-6 lg:grid-cols-2" aria-busy="true">
          <div className="rounded-lg border border-border bg-surface p-5">
            <h3 className="mb-4 font-semibold">Mapping</h3>
            <StageTimeline events={events} stages={["map", "rules"]} label="Mapping stages" />
          </div>
          <EventLog events={events} />
        </div>
      ) : null}

      {mapped && !waiting ? (
        mappings.isPending ? (
          <LoadingRows rows={6} />
        ) : mappings.isError ? (
          <ErrorState error={mappings.error} onRetry={() => void mappings.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState title="Nothing was mapped">The source has no columns that could be mapped.</EmptyState>
        ) : (
          <>
            {dirty ? <StepNote tone="warning">The mappings changed, so the transform rules need to be generated again. That happens when you continue.</StepNote> : null}
            <div className="flex flex-wrap items-end gap-3">
              <SearchInput value={text} onChange={setText} label="Search the mappings" placeholder="Search source or target" className="w-64" />
              <div>
                <Label htmlFor="mapping-filter" className="sr-only">
                  Show
                </Label>
                <Select id="mapping-filter" value={filter} onChange={(e) => setFilter(e.target.value as MappingFilter)}>
                  <option value="all">All ({counts.all})</option>
                  <option value="review">Needs review ({counts.review})</option>
                  <option value="approved">Approved ({counts.approved})</option>
                  <option value="unmapped">No target or rejected ({counts.unmapped})</option>
                </Select>
              </div>
              {editable ? (
                <div className="ml-auto flex flex-wrap gap-2">
                  <Button variant="outline" size="sm" disabled={selected.size === 0 || bulk.isPending} onClick={() => bulk.mutate([...selected])}>
                    Approve selected ({selected.size})
                  </Button>
                  <Button size="sm" disabled={high.length === 0 || bulk.isPending} onClick={() => bulk.mutate(high)}>
                    Approve all with {Math.round(HIGH_CONFIDENCE * 100)}% confidence or more ({high.length})
                  </Button>
                </div>
              ) : null}
            </div>
            <p className="text-sm text-foreground-muted" aria-live="polite">
              {shown.length} of {rows.length} columns shown. {counts.review} need a decision.
            </p>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full min-w-[56rem] text-left text-sm">
                <caption className="sr-only">Mappings of source columns to target fields</caption>
                <thead className="bg-surface-muted text-foreground-muted">
                  <tr>
                    {editable ? (
                      <th scope="col" className="w-10 px-4 py-3">
                        <span className="sr-only">Select</span>
                      </th>
                    ) : null}
                    <th scope="col" className="px-4 py-3 font-semibold">
                      Source column
                    </th>
                    <th scope="col" className="px-4 py-3 font-semibold">
                      Suggested target
                    </th>
                    <th scope="col" className="px-4 py-3 font-semibold">
                      Confidence
                    </th>
                    <th scope="col" className="px-4 py-3 font-semibold">
                      Method
                    </th>
                    <th scope="col" className="px-4 py-3 font-semibold">
                      State
                    </th>
                    {editable ? (
                      <th scope="col" className="px-4 py-3 font-semibold">
                        <span className="sr-only">Actions</span>
                      </th>
                    ) : null}
                  </tr>
                </thead>
                <tbody>
                  {shown.map((m) => {
                    const state = mappingState(m);
                    return (
                      <tr key={m.id} className={cn("border-t border-border", state === "review" && "bg-warning-soft/40")}>
                        {editable ? (
                          <td className="px-4 py-3">
                            <input
                              type="checkbox"
                              className="h-4 w-4 accent-primary"
                              checked={selected.has(m.id)}
                              onChange={() => toggle(m.id)}
                              aria-label={`Select ${m.source_table}.${m.source_column}`}
                              disabled={m.target === null}
                            />
                          </td>
                        ) : null}
                        <td className="px-4 py-3">
                          <span className="font-mono text-[0.8125rem]">
                            {m.source_table}.{m.source_column}
                          </span>
                          {m.pii ? (
                            <Badge variant="warning" className="ml-2">
                              Personal data
                            </Badge>
                          ) : null}
                        </td>
                        <td className="px-4 py-3 font-mono text-[0.8125rem]">{m.target ?? <span className="font-sans text-foreground-muted">none suggested</span>}</td>
                        <td className="px-4 py-3">
                          <ConfidenceBar value={m.confidence} label={`Confidence of ${m.source_column}`} />
                        </td>
                        <td className="px-4 py-3">
                          <MethodBadge method={m.method} />
                        </td>
                        <td className="px-4 py-3">{stateBadge[state]}</td>
                        {editable ? (
                          <td className="px-4 py-3">
                            <div className="flex justify-end gap-1">
                              {state === "review" ? (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  disabled={approve.isPending}
                                  onClick={() => approve.mutate(m.id)}
                                  aria-label={`Accept the mapping of ${m.source_column}`}
                                >
                                  <Check className="h-4 w-4" aria-hidden="true" />
                                  Accept
                                </Button>
                              ) : null}
                              <Button variant="ghost" size="sm" onClick={() => setEditing(m)} aria-label={`Choose another target for ${m.source_column}`}>
                                <Pencil className="h-4 w-4" aria-hidden="true" />
                                Change
                              </Button>
                              {m.target !== null && state !== "rejected" ? (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => override.mutate({ id: m.id, fieldId: null })}
                                  aria-label={`Reject the mapping of ${m.source_column}`}
                                >
                                  <X className="h-4 w-4" aria-hidden="true" />
                                  Reject
                                </Button>
                              ) : null}
                            </div>
                          </td>
                        ) : null}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!editable && working ? <p className="text-sm text-foreground-muted">Mappings can be changed while the migration is waiting for review.</p> : null}
          </>
        )
      ) : null}
      <TargetPicker
        open={!!editing}
        onClose={() => setEditing(null)}
        mapping={editing}
        fields={fields.data ?? []}
        busy={override.isPending}
        onPick={(field) => editing && override.mutate({ id: editing.id, fieldId: field.id })}
      />
    </StepFrame>
  );
}
