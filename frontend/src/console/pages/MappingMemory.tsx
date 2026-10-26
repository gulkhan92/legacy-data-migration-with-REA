import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { consoleApi, pipelineApi, type Memory } from "../api";
import { Modal, Pagination, SearchInput, Select, useDebounced } from "../ui/controls";
import { PageHeader } from "../ui/page";
import { canWork } from "../ui/roles";
import { describeError, EmptyState, ErrorState, LoadingRows } from "../ui/states";
import { useToast } from "../ui/toast";

const PAGE = 20;

export default function MappingMemory() {
  const { me } = useAuth();
  const toast = useToast();
  const client = useQueryClient();
  const [text, setText] = useState("");
  const [schema, setSchema] = useState("");
  const [offset, setOffset] = useState(0);
  const [forgetting, setForgetting] = useState<Memory | null>(null);
  const q = useDebounced(text, 300);
  const working = me ? canWork(me.role) : false;
  const schemas = useQuery({ queryKey: ["target-schemas"], queryFn: ({ signal }) => pipelineApi.schemas(signal), staleTime: 10 * 60_000 });
  const params = { q, schema, limit: PAGE, offset };
  const memory = useQuery({ queryKey: ["memory", params], queryFn: ({ signal }) => consoleApi.memory(params, signal), placeholderData: (previous) => previous });
  const forget = useMutation({
    mutationFn: (item: Memory) => consoleApi.forgetMemory(item.id),
    onSuccess: () => {
      toast.success("The mapping was forgotten");
      setForgetting(null);
      void client.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (error) => toast.error("It could not be forgotten", describeError(error)),
  });

  return (
    <>
      <PageHeader
        title="Mapping memory"
        description="Every mapping a person approved is remembered and reused the next time a similar column shows up, so a repeat migration needs less review and fewer model tokens. Forget one here if it was wrong."
      />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <SearchInput
          value={text}
          onChange={(v) => {
            setText(v);
            setOffset(0);
          }}
          label="Search the remembered mappings"
          placeholder="Search source description or target"
          className="w-72"
        />
        <label className="text-sm">
          <span className="sr-only">Target schema</span>
          <Select
            value={schema}
            onChange={(e) => {
              setSchema(e.target.value);
              setOffset(0);
            }}
            aria-label="Target schema"
          >
            <option value="">All target schemas</option>
            {(schemas.data ?? []).map((s) => (
              <option key={s.id} value={s.name}>
                {s.name}
              </option>
            ))}
          </Select>
        </label>
      </div>
      {memory.isPending ? (
        <LoadingRows rows={6} />
      ) : memory.isError ? (
        <ErrorState error={memory.error} onRetry={() => void memory.refetch()} />
      ) : memory.data.items.length === 0 ? (
        <EmptyState title={q || schema ? "No remembered mapping matches" : "Nothing remembered yet"}>
          {q || schema ? "Try fewer words or another schema." : "Approve mappings in a migration and they appear here."}
        </EmptyState>
      ) : (
        <>
          <div className={memory.isFetching ? "overflow-x-auto rounded-lg border border-border opacity-60" : "overflow-x-auto rounded-lg border border-border"}>
            <table className="w-full min-w-[52rem] text-left text-sm">
              <caption className="sr-only">Remembered mappings</caption>
              <thead className="bg-surface-muted text-foreground-muted">
                <tr>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Source column, as described
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Target
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Approved
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Last approved
                  </th>
                  {working ? (
                    <th scope="col" className="px-4 py-3">
                      <span className="sr-only">Actions</span>
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {memory.data.items.map((m) => (
                  <tr key={m.id} className="border-t border-border align-top">
                    <td className="max-w-md px-4 py-3 text-foreground-muted">{m.source_text}</td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-[0.8125rem] font-semibold">{m.target}</span>
                      <span className="block text-xs text-foreground-muted">{m.target_schema}</span>
                    </td>
                    <td className="px-4 py-3 tabular-nums">
                      {m.times_approved} time{m.times_approved === 1 ? "" : "s"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-foreground-muted">{formatDateTime(m.last_approved_at)}</td>
                    {working ? (
                      <td className="px-4 py-3 text-right">
                        <Button variant="ghost" size="sm" onClick={() => setForgetting(m)} aria-label={`Forget the mapping to ${m.target}`}>
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                          Forget
                        </Button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={memory.data.total} limit={PAGE} offset={offset} onChange={setOffset} noun="mappings" />
        </>
      )}
      <Modal
        open={!!forgetting}
        onOpenChange={(open) => !open && setForgetting(null)}
        title="Forget this mapping?"
        description={forgetting ? `Columns like this one will no longer be mapped to ${forgetting.target} automatically. Earlier migrations are not changed.` : undefined}
        footer={
          <>
            <Button variant="outline" onClick={() => setForgetting(null)}>
              Keep it
            </Button>
            <Button variant="secondary" disabled={forget.isPending} onClick={() => forgetting && forget.mutate(forgetting)}>
              Forget it
            </Button>
          </>
        }
      />
    </>
  );
}
