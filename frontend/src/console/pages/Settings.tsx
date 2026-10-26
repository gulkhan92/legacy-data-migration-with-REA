import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, PlugZap } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { formatInt } from "@/lib/format";
import { adminApi, type LlmProvider, type LlmSettings } from "../api";
import { PageHeader, Panel } from "../ui/page";
import { describeError, ErrorState, LoadingRows } from "../ui/states";
import { Meter } from "../ui/status";
import { useToast } from "../ui/toast";

const number = (label: string, min: number, max: number) =>
  z.coerce
    .number({ invalid_type_error: `${label} must be a number` })
    .int(`${label} must be a whole number`)
    .min(min, `${label} must be at least ${formatInt(min)}`)
    .max(max, `${label} must be at most ${formatInt(max)}`);

const budgets = z.object({
  job_token_budget: number("The budget per migration", 500, 100_000),
  org_daily_token_budget: number("The daily budget", 1_000, 10_000_000),
  max_calls_per_job: number("The number of calls", 1, 10),
});
type Budgets = z.infer<typeof budgets>;

const retention = z.object({ retention_days: number("The retention", 7, 3650) });
type Retention = z.infer<typeof retention>;

function ProviderRow({ provider, position, last, onMove }: { provider: LlmProvider; position: number; last: boolean; onMove: (delta: -1 | 1) => void }) {
  const toast = useToast();
  const [result, setResult] = useState<string | null>(null);
  const test = useMutation({
    mutationFn: () => adminApi.testConnection(provider.name),
    onSuccess: (r) => setResult(`Connected${typeof r.latency_ms === "number" ? ` in ${r.latency_ms} ms` : ""}.`),
    onError: (error) => {
      setResult(null);
      toast.error(`The connection to ${provider.name} failed`, describeError(error));
    },
  });
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface p-4">
      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-soft text-sm font-bold text-primary-ink" aria-hidden="true">
        {position + 1}
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-semibold capitalize">
          {provider.name} <span className="font-mono text-sm font-normal normal-case text-foreground-muted">{provider.model}</span>
        </p>
        <p className="text-sm text-foreground-muted">
          {provider.configured ? "A key is configured on the server." : "No key is configured on the server, so this provider is skipped."} Circuit: {provider.circuit}. About{" "}
          {formatInt(provider.tokens_available)} tokens and {provider.requests_available} requests available.
        </p>
        {result ? (
          <p role="status" className="mt-1 text-sm font-medium text-success">
            {result}
          </p>
        ) : null}
      </div>
      <Badge variant={provider.configured ? "success" : "neutral"}>{provider.configured ? "Configured" : "No key"}</Badge>
      <div className="flex gap-1">
        <Button variant="outline" size="icon" disabled={position === 0} onClick={() => onMove(-1)} aria-label={`Use ${provider.name} earlier`}>
          <ArrowUp className="h-4 w-4" aria-hidden="true" />
        </Button>
        <Button variant="outline" size="icon" disabled={last} onClick={() => onMove(1)} aria-label={`Use ${provider.name} later`}>
          <ArrowDown className="h-4 w-4" aria-hidden="true" />
        </Button>
        <Button variant="outline" size="sm" className="h-10" disabled={!provider.configured || test.isPending} onClick={() => test.mutate()}>
          <PlugZap className="h-4 w-4" aria-hidden="true" />
          Test connection
        </Button>
      </div>
    </li>
  );
}

function LlmSection({ settings }: { settings: LlmSettings }) {
  const toast = useToast();
  const client = useQueryClient();
  const [order, setOrder] = useState(settings.provider_order);
  const form = useForm<Budgets>({
    resolver: zodResolver(budgets),
    defaultValues: { job_token_budget: settings.job_token_budget, org_daily_token_budget: settings.org_daily_token_budget, max_calls_per_job: settings.max_calls_per_job },
  });
  const save = useMutation({
    mutationFn: (v: Budgets) => adminApi.saveLlmSettings({ ...v, provider_order: order }),
    onSuccess: () => {
      toast.success("The language model settings were saved");
      void client.invalidateQueries({ queryKey: ["llm-settings"] });
    },
    onError: (error) => form.setError("root", { message: describeError(error) }),
  });
  const move = (name: string, delta: -1 | 1) =>
    setOrder((current) => {
      const index = current.indexOf(name);
      const target = index + delta;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target] as string, next[index] as string];
      return next;
    });
  const byName = new Map(settings.providers.map((p) => [p.name, p]));
  const errors = form.formState.errors;
  const dailyBudget = Number(useWatch({ control: form.control, name: "org_daily_token_budget" })) || settings.org_daily_token_budget;
  return (
    <Panel
      title="Language model"
      description="Which provider is tried first, and how many tokens a migration and the whole organization may use. The model only sees columns the rules and the embedding could not settle."
    >
      <form className="space-y-6" onSubmit={form.handleSubmit((v) => save.mutate(v))} noValidate>
        <div>
          <h3 className="mb-2 font-semibold">Provider order</h3>
          <p className="mb-3 text-sm text-foreground-muted">The first provider is tried first; the next one is used when it fails or runs out of allowance.</p>
          <ol aria-label="Provider order" className="space-y-3">
            {order.map((name, index) => {
              const provider = byName.get(name);
              return provider ? <ProviderRow key={name} provider={provider} position={index} last={index === order.length - 1} onMove={(d) => move(name, d)} /> : null;
            })}
          </ol>
        </div>
        <div>
          <p className="text-sm font-medium">Tokens used today by this organization</p>
          <div className="mt-2 max-w-md">
            <Meter value={settings.used_today} max={dailyBudget} label="Tokens used today of the daily budget">
              <p className="mt-1 text-sm text-foreground-muted">
                {formatInt(settings.used_today)} of {formatInt(dailyBudget)}
              </p>
            </Meter>
          </div>
        </div>
        <div className="grid gap-5 md:grid-cols-3">
          <Field label="Token budget per migration" error={errors.job_token_budget?.message} hint="A migration stops asking the model when it reaches this.">
            <Input type="number" inputMode="numeric" {...form.register("job_token_budget")} />
          </Field>
          <Field label="Token budget per day" error={errors.org_daily_token_budget?.message} hint="Across every migration of the organization.">
            <Input type="number" inputMode="numeric" {...form.register("org_daily_token_budget")} />
          </Field>
          <Field label="Model calls per migration" error={errors.max_calls_per_job?.message} hint="Columns are sent in batches, so a few calls are enough.">
            <Input type="number" inputMode="numeric" {...form.register("max_calls_per_job")} />
          </Field>
        </div>
        {errors.root?.message ? (
          <p role="alert" className="text-sm font-medium text-danger">
            {errors.root.message}
          </p>
        ) : null}
        <Button type="submit" disabled={save.isPending}>
          Save the language model settings
        </Button>
      </form>
    </Panel>
  );
}

function RetentionSection() {
  const toast = useToast();
  const client = useQueryClient();
  const current = useQuery({ queryKey: ["retention"], queryFn: ({ signal }) => adminApi.retention(signal) });
  const form = useForm<Retention>({ resolver: zodResolver(retention), defaultValues: { retention_days: 365 } });
  useEffect(() => {
    if (current.data) form.reset({ retention_days: current.data.retention_days });
  }, [current.data, form]);
  const save = useMutation({
    mutationFn: (v: Retention) => adminApi.saveRetention(v.retention_days),
    onSuccess: () => {
      toast.success("The retention period was saved");
      void client.invalidateQueries({ queryKey: ["retention"] });
    },
    onError: (error) => form.setError("root", { message: describeError(error) }),
  });
  const errors = form.formState.errors;
  return (
    <Panel title="Data retention" description={current.data?.applies_to ?? "How long uploaded files are kept."}>
      {current.isPending ? (
        <LoadingRows rows={2} />
      ) : current.isError ? (
        <ErrorState error={current.error} onRetry={() => void current.refetch()} />
      ) : (
        <form className="flex max-w-md flex-col gap-4" onSubmit={form.handleSubmit((v) => save.mutate(v))} noValidate>
          <Field label="Keep an uploaded file for (days)" error={errors.retention_days?.message} hint="From the day its last migration finished. Between 7 and 3,650 days.">
            <Input type="number" inputMode="numeric" {...form.register("retention_days")} />
          </Field>
          {errors.root?.message ? (
            <p role="alert" className="text-sm font-medium text-danger">
              {errors.root.message}
            </p>
          ) : null}
          <div>
            <Button type="submit" disabled={save.isPending}>
              Save the retention period
            </Button>
          </div>
        </form>
      )}
    </Panel>
  );
}

export default function Settings() {
  const llm = useQuery({ queryKey: ["llm-settings"], queryFn: ({ signal }) => adminApi.llmSettings(signal) });
  return (
    <>
      <PageHeader title="Settings" description="How the language model is used and how long uploaded files are kept." />
      <div className="space-y-6">
        {llm.isPending ? <LoadingRows rows={5} /> : llm.isError ? <ErrorState error={llm.error} onRetry={() => void llm.refetch()} /> : <LlmSection key={[llm.data.provider_order.join(), llm.data.job_token_budget, llm.data.org_daily_token_budget, llm.data.max_calls_per_job].join("|")} settings={llm.data} />}
        <RetentionSection />
      </div>
    </>
  );
}
