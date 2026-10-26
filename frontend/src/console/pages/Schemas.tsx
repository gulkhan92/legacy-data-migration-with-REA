import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { pipelineApi, type TargetField, type TargetSchema } from "../api";
import { SearchInput } from "../ui/controls";
import { PageHeader } from "../ui/page";
import { EmptyState, ErrorState, LoadingRows, QueryView } from "../ui/states";

function Fields({ schema }: { schema: TargetSchema }) {
  const [text, setText] = useState("");
  const fields = useQuery({ queryKey: ["target-fields", schema.id], queryFn: ({ signal }) => pipelineApi.fields(schema.id, signal), staleTime: 10 * 60_000 });
  const grouped = useMemo(() => {
    const needle = text.trim().toLowerCase();
    const map = new Map<string, TargetField[]>();
    for (const f of fields.data ?? []) {
      if (needle && !`${f.table}.${f.name} ${f.description ?? ""} ${f.synonyms.join(" ")}`.toLowerCase().includes(needle)) continue;
      map.set(f.table, [...(map.get(f.table) ?? []), f]);
    }
    return [...map.entries()];
  }, [fields.data, text]);
  if (fields.isPending) return <LoadingRows rows={6} />;
  if (fields.isError) return <ErrorState error={fields.error} onRetry={() => void fields.refetch()} />;
  return (
    <div className="space-y-5">
      <SearchInput value={text} onChange={setText} label={`Search the fields of ${schema.name}`} placeholder="Search name, description or synonym" className="max-w-sm" />
      {grouped.length === 0 ? (
        <EmptyState title="No field matches that" />
      ) : (
        grouped.map(([table, list]) => (
          <section key={table} aria-label={`Table ${table}`}>
            <h3 className="mb-2 text-lg font-semibold">
              {table} <span className="text-sm font-normal text-foreground-muted">{list.length} fields</span>
            </h3>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full min-w-[48rem] text-left text-sm">
                <caption className="sr-only">Fields of {table}</caption>
                <thead className="bg-surface-muted text-foreground-muted">
                  <tr>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Field
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Type
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Description
                    </th>
                    <th scope="col" className="px-4 py-2 font-semibold">
                      Known as
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((f) => (
                    <tr key={f.id} className="border-t border-border align-top">
                      <td className="px-4 py-2 font-mono text-[0.8125rem] font-semibold">
                        {f.name}
                        {f.required ? (
                          <Badge variant="primary" className="ml-2">
                            required
                          </Badge>
                        ) : null}
                        {f.pii ? (
                          <Badge variant="warning" className="ml-2">
                            personal data
                          </Badge>
                        ) : null}
                      </td>
                      <td className="px-4 py-2 text-foreground-muted">{f.type}</td>
                      <td className="px-4 py-2">{f.description}</td>
                      <td className="px-4 py-2 text-foreground-muted">{f.synonyms.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}
    </div>
  );
}

export default function Schemas() {
  const schemas = useQuery({ queryKey: ["target-schemas"], queryFn: ({ signal }) => pipelineApi.schemas(signal), staleTime: 10 * 60_000 });
  return (
    <>
      <PageHeader
        title="Target schemas"
        description="The shapes that legacy data is mapped into. Each field has a description and the other names it is known by, which is what the matching reads."
      />
      <QueryView query={schemas}>
        {(list) =>
          list.length === 0 ? (
            <EmptyState title="No target schemas">None is installed on this server.</EmptyState>
          ) : (
            <Tabs defaultValue={list[0]?.id}>
              <TabsList aria-label="Target schemas">
                {list.map((s) => (
                  <TabsTrigger key={s.id} value={s.id}>
                    {s.name}
                    <span className="text-xs font-normal">v{s.version}</span>
                  </TabsTrigger>
                ))}
              </TabsList>
              {list.map((s) => (
                <TabsContent key={s.id} value={s.id}>
                  <p className="mb-4 text-foreground-muted">{s.description}</p>
                  <Fields schema={s} />
                </TabsContent>
              ))}
            </Tabs>
          )
        }
      </QueryView>
    </>
  );
}
