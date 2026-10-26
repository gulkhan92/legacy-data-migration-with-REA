import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Eye, PlayCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/cn";
import { pipelineApi, type Job, type Preview, type Rule } from "../api";
import { describeError, EmptyState, ErrorState, LoadingRows } from "../ui/states";
import { useToast } from "../ui/toast";
import { StepFrame, StepNote } from "./shared";

export const ruleKey = (r: Pick<Rule, "target_table" | "target_field">): string => `${r.target_table}.${r.target_field}`;

/** "trim, case, parse_date" for a rule's steps. */
export function describeSteps(rule: Pick<Rule, "steps">): string {
  return rule.steps.map((s) => s.op).join(", ");
}

/** The codes a rule is waiting for a meaning for, and the meanings it already has. */
export function codeMapOf(rule: Rule): { codes: string[]; mapping: Record<string, string>; passthrough: boolean } {
  const step = rule.steps.find((s) => s.op === "map_codes");
  const missing = (rule.failures as { missing?: { codes?: unknown } }).missing?.codes;
  const mapping = (step?.mapping ?? {}) as Record<string, string>;
  const codes = Array.isArray(missing) ? missing.map(String) : [];
  return { codes: [...new Set([...codes, ...Object.keys(mapping)])], mapping, passthrough: step?.passthrough === true };
}

function CodeMapEditor({ job, rule, onSaved }: { job: Job; rule: Rule; onSaved: () => void }) {
  const toast = useToast();
  const initial = codeMapOf(rule);
  const [mapping, setMapping] = useState<Record<string, string>>(initial.mapping);
  const [passthrough, setPassthrough] = useState(initial.passthrough);
  const save = useMutation({
    mutationFn: () => pipelineApi.putCodeMap(job.id, rule.id, Object.fromEntries(Object.entries(mapping).filter(([, v]) => v.trim() !== "")), passthrough),
    onSuccess: () => {
      toast.success(`The meaning of the codes for ${ruleKey(rule)} was saved`);
      onSaved();
    },
    onError: (error) => toast.error("The codes could not be saved", describeError(error)),
  });
  const complete = initial.codes.every((c) => (mapping[c] ?? "").trim() !== "") || passthrough;
  return (
    <form
      className="rounded-lg border border-warning/40 bg-warning-soft p-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <h4 className="font-semibold">This rule needs you</h4>
      <p className="mt-1 text-sm">The source stores {ruleKey(rule)} as codes, and the data does not say what they mean. Write the meaning of each code.</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {initial.codes.map((code) => (
          <label key={code} className="flex items-center gap-3 text-sm">
            <span className="w-16 shrink-0 font-mono font-semibold">Code {code}</span>
            <Input
              aria-label={`Meaning of code ${code}`}
              value={mapping[code] ?? ""}
              onChange={(e) => setMapping({ ...mapping, [code]: e.target.value })}
              className="h-10 text-sm"
              placeholder="for example pending"
            />
          </label>
        ))}
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input type="checkbox" className="h-4 w-4 accent-primary" checked={passthrough} onChange={(e) => setPassthrough(e.target.checked)} />
        Keep codes that have no meaning as they are
      </label>
      <Button type="submit" size="sm" className="mt-4" disabled={!complete || save.isPending}>
        Save the meanings
      </Button>
    </form>
  );
}

function BeforeAfter({ preview }: { preview: Preview }) {
  if (preview.rows.length === 0) return <p className="text-sm text-foreground-muted">The sample has no rows to show.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[32rem] text-left text-sm">
        <caption className="sr-only">Source values before the rule and the values after it</caption>
        <thead className="bg-surface-muted text-foreground-muted">
          <tr>
            <th scope="col" className="px-4 py-2 font-semibold">
              Row
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Before (source)
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              After (target)
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Result
            </th>
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((r) => (
            <tr key={r.row} className={cn("border-t border-border", r.failed && "bg-danger-soft/50")}>
              <td className="px-4 py-2 tabular-nums text-foreground-muted">{r.row}</td>
              <td className="px-4 py-2 font-mono text-[0.8125rem]">
                {Object.values(r.before)
                  .map((v) => (v === null ? "null" : String(v)))
                  .join(" | ")}
              </td>
              <td className="px-4 py-2 font-mono text-[0.8125rem]">{r.after === null ? <span className="text-foreground-muted">empty</span> : r.after}</td>
              <td className="px-4 py-2">{r.failed ? <Badge variant="danger">Failed{r.kind ? `: ${r.kind}` : ""}</Badge> : <Badge variant="success">Converted</Badge>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TransformStep({ job, working, onNext, onBack }: { job: Job; working: boolean; onNext: () => void; onBack: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const [checking, setChecking] = useState(false);

  const rules = useQuery({ queryKey: ["rules", job.id], queryFn: ({ signal }) => pipelineApi.rules(job.id, signal) });
  const list = useMemo(() => rules.data ?? [], [rules.data]);
  const needsInput = list.filter((r) => r.status === "needs_input");
  const active = list.find((r) => ruleKey(r) === selected) ?? needsInput[0] ?? list[0];

  const preview = useMutation({
    mutationFn: (rule: Rule) => pipelineApi.preview(job.id, { target_table: rule.target_table, target_field: rule.target_field, rows: 6 }),
    onSuccess: (result, rule) => setPreviews((current) => ({ ...current, [ruleKey(rule)]: result })),
    onError: (error) => toast.error("The preview could not run", describeError(error)),
  });

  // Showing the selected rule: preview it once, without a click.
  useEffect(() => {
    if (!active || active.status === "needs_input" || previews[ruleKey(active)] || !working) return;
    preview.mutate(active);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id, active?.status, working]);

  async function checkAll() {
    setChecking(true);
    for (const rule of list.filter((r) => r.status !== "needs_input" && !r.inputs.some((i) => i.startsWith("@")))) {
      try {
        const result = await pipelineApi.preview(job.id, { target_table: rule.target_table, target_field: rule.target_field, rows: 6 });
        setPreviews((current) => ({ ...current, [ruleKey(rule)]: result }));
      } catch {
        /* a rule that cannot be previewed (it reads other rules) is simply left out */
      }
    }
    setChecking(false);
  }

  const saved = () => {
    setPreviews({});
    void client.invalidateQueries({ queryKey: ["rules", job.id] });
    void client.invalidateQueries({ queryKey: ["job", job.id] });
  };

  const failedTotal = Object.values(previews).reduce((n, p) => n + p.failed, 0);
  const canContinue = needsInput.length === 0 && ["AWAITING_REVIEW", "READY", "DRY_RUN"].includes(job.status);

  return (
    <StepFrame
      title="Transform preview"
      intro="A rule is the recipe that turns a source value into a target value: trim it, fix its case, parse the date, translate the code. Here you see what each rule does to a sample of the real data, and how many values it could not convert."
      footer={
        <>
          <Button variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button disabled={!canContinue} onClick={onNext}>
            Continue to the dry run
          </Button>
        </>
      }
    >
      {rules.isPending ? (
        <LoadingRows rows={6} />
      ) : rules.isError ? (
        <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />
      ) : list.length === 0 ? (
        <EmptyState title="There are no rules yet">Rules are generated from the approved mappings. Go back and approve some mappings.</EmptyState>
      ) : (
        <>
          {needsInput.length > 0 ? (
            <StepNote tone="warning">
              {needsInput.length} rule{needsInput.length === 1 ? " needs" : "s need"} your input before the dry run can pass: {needsInput.map(ruleKey).join(", ")}.
            </StepNote>
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-foreground-muted" aria-live="polite">
              {list.length} rules.{" "}
              {Object.keys(previews).length > 0 ? `${failedTotal} values failed in the ${Object.keys(previews).length} previewed.` : "Choose a rule to see what it does."}
            </p>
            {working ? (
              <Button variant="outline" size="sm" disabled={checking} onClick={() => void checkAll()}>
                <PlayCircle className="h-4 w-4" aria-hidden="true" />
                {checking ? "Checking every rule" : "Preview every rule"}
              </Button>
            ) : null}
          </div>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
            <ul aria-label="Rules" className="max-h-[32rem] divide-y divide-border overflow-y-auto rounded-lg border border-border">
              {list.map((rule) => {
                const key = ruleKey(rule);
                const p = previews[key];
                return (
                  <li key={rule.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(key)}
                      aria-current={active && ruleKey(active) === key ? "true" : undefined}
                      className={cn("flex w-full items-start gap-2 px-4 py-3 text-left hover:bg-surface-muted", active && ruleKey(active) === key && "bg-primary-soft")}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-[0.8125rem] font-semibold">{key}</span>
                        <span className="block truncate text-xs text-foreground-muted">
                          {rule.inputs.join(", ")} <ArrowRight className="inline h-3 w-3" aria-hidden="true" /> {describeSteps(rule)}
                        </span>
                      </span>
                      {rule.status === "needs_input" ? (
                        <Badge variant="warning">Needs input</Badge>
                      ) : p ? (
                        <Badge variant={p.failed > 0 ? "danger" : "success"}>{p.failed > 0 ? `${p.failed} failed` : "No failures"}</Badge>
                      ) : (
                        <Eye className="mt-1 h-4 w-4 text-foreground-subtle" aria-label="Not previewed yet" />
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
            <div className="min-w-0 space-y-4" aria-live="polite">
              {active ? (
                <>
                  <div>
                    <h3 className="font-mono text-base font-semibold">{ruleKey(active)}</h3>
                    <p className="text-sm text-foreground-muted">
                      From {active.source_table ?? "other rules"}: {active.inputs.join(", ")}. Steps: {describeSteps(active)}. Origin: {active.origin}.
                    </p>
                    {active.notes.length > 0 ? (
                      <ul className="mt-1 list-disc pl-5 text-sm text-foreground-muted">
                        {active.notes.map((n) => (
                          <li key={n}>{n}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                  {active.status === "needs_input" && working ? (
                    <CodeMapEditor key={`${active.id}:${JSON.stringify(active.steps)}`} job={job} rule={active} onSaved={saved} />
                  ) : null}
                  {active.status === "needs_input" && !working ? <StepNote tone="warning">This rule needs an analyst to supply the meaning of its codes.</StepNote> : null}
                  {preview.isPending && active.status !== "needs_input" && !previews[ruleKey(active)] ? <LoadingRows rows={3} /> : null}
                  {previews[ruleKey(active)] ? (
                    <>
                      <dl className="grid grid-cols-3 gap-3 text-sm">
                        <div className="rounded-lg border border-border bg-surface p-3">
                          <dt className="text-foreground-muted">Rows sampled</dt>
                          <dd className="text-lg font-bold">{previews[ruleKey(active)]?.sampled}</dd>
                        </div>
                        <div className="rounded-lg border border-border bg-surface p-3">
                          <dt className="text-foreground-muted">Values failed</dt>
                          <dd className="text-lg font-bold">{previews[ruleKey(active)]?.failed}</dd>
                        </div>
                        <div className="rounded-lg border border-border bg-surface p-3">
                          <dt className="text-foreground-muted">Failure rate</dt>
                          <dd className="text-lg font-bold">{formatPercent(previews[ruleKey(active)]?.failure_rate ?? 0)}</dd>
                        </div>
                      </dl>
                      <BeforeAfter preview={previews[ruleKey(active)] as Preview} />
                    </>
                  ) : null}
                </>
              ) : null}
            </div>
          </div>
        </>
      )}
    </StepFrame>
  );
}
