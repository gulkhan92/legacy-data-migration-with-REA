import { useQueries, useQuery } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { SiteLink } from "@/components/SiteLink";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { publicApi, type PublicPlan } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatCents, formatInt, formatRate } from "@/lib/format";

export const planKeys = ["starter", "professional", "enterprise"] as const;

const blurbs: Record<string, string> = {
  starter: "Try the whole pipeline on a small system.",
  professional: "For teams moving several systems a year.",
  enterprise: "For large histories and regular migrations.",
};

const everyPlan = [
  "Discovery, mapping, transformation, validation and reconciliation",
  "Quarantine with reasons, and CSV export",
  "Rollback by job",
  "Audit log and role based access",
  "REST API and API documentation",
];

export function usePlans() {
  return useQuery({ queryKey: ["public-plans"], queryFn: ({ signal }) => publicApi.plans(signal), staleTime: 10 * 60_000, retry: 1 });
}

function PlanSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-3" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-[420px] w-full rounded-lg" />
      ))}
    </div>
  );
}

function PlanCard({ plan, featured }: { plan: PublicPlan; featured: boolean }) {
  return (
    <Card className={cn("flex flex-col", featured && "border-primary shadow-lg ring-1 ring-primary")}>
      <CardHeader>
        <CardTitle>{plan.name}</CardTitle>
        <CardDescription>{blurbs[plan.code] ?? ""}</CardDescription>
      </CardHeader>
      <CardContent className="flex-1">
        <p className="flex items-baseline gap-1">
          <span className="text-4xl font-bold tabular-nums text-foreground">{plan.monthly_fee_cents === 0 ? "Free" : formatCents(plan.monthly_fee_cents)}</span>
          {plan.monthly_fee_cents === 0 ? null : <span className="text-foreground-muted">per month</span>}
        </p>
        <ul className="mt-6 space-y-3 text-foreground-muted">
          <li className="flex gap-2">
            <Check className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
            <span><strong className="text-foreground">{formatInt(plan.included_records)}</strong> records included each month</span>
          </li>
          <li className="flex gap-2">
            <Check className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
            <span>Then <strong className="text-foreground">{formatRate(plan.overage_cents_per_1000)}</strong> per 1,000 records</span>
          </li>
          <li className="flex gap-2">
            <Check className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
            <span>Everything in the platform</span>
          </li>
        </ul>
      </CardContent>
      <CardFooter>
        <Button asChild variant={featured ? "default" : "outline"} className="w-full">
          <SiteLink to="/login">{plan.monthly_fee_cents === 0 ? "Start free" : "Open console"}</SiteLink>
        </Button>
      </CardFooter>
    </Card>
  );
}

/** Three plan cards read from GET /api/v1/public/plans. */
export function PlanCards() {
  const { data, isPending, isError, refetch } = usePlans();
  if (isPending) return <PlanSkeleton />;
  if (isError || !data) {
    return (
      <div role="alert" className="rounded-lg border border-danger/30 bg-danger-soft p-6">
        <p className="font-semibold text-danger">The plans could not be loaded.</p>
        <p className="mt-1 text-foreground-muted">The API may be unavailable. The prices on this page come from it.</p>
        <Button variant="outline" className="mt-4" onClick={() => void refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      {data.map((plan) => (
        <PlanCard key={plan.code} plan={plan} featured={plan.code === "professional"} />
      ))}
    </div>
  );
}

export function PlanComparison() {
  const { data } = usePlans();
  if (!data) return <Skeleton className="h-64 w-full" />;
  const rows: [string, (p: PublicPlan) => string][] = [
    ["Monthly fee", (p) => (p.monthly_fee_cents === 0 ? "Free" : formatCents(p.monthly_fee_cents))],
    ["Records included each month", (p) => formatInt(p.included_records)],
    ["Price per 1,000 records above that", (p) => formatRate(p.overage_cents_per_1000)],
  ];
  return (
    <div className="overflow-x-auto rounded-lg border border-border" role="region" aria-label="Comparison of the plans" tabIndex={0}>
      <table className="w-full min-w-[560px] text-left">
        <caption className="sr-only">Comparison of the three plans</caption>
        <thead className="bg-surface-muted">
          <tr>
            <th scope="col" className="px-4 py-3 text-sm font-semibold text-foreground-muted">
              <span className="sr-only">Feature</span>
            </th>
            {data.map((p) => (
              <th key={p.code} scope="col" className="px-4 py-3 text-base font-semibold text-foreground">
                {p.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label} className="border-t border-border">
              <th scope="row" className="px-4 py-3 text-sm font-medium text-foreground-muted">{label}</th>
              {data.map((p) => (
                <td key={p.code} className="px-4 py-3 tabular-nums text-foreground">{value(p)}</td>
              ))}
            </tr>
          ))}
          <tr className="border-t border-border">
            <th scope="row" className="px-4 py-3 align-top text-sm font-medium text-foreground-muted">Included in every plan</th>
            <td colSpan={data.length} className="px-4 py-3">
              <ul className="grid gap-1.5 text-foreground-muted sm:grid-cols-2">
                {everyPlan.map((item) => (
                  <li key={item} className="flex gap-2">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden="true" />
                    {item}
                  </li>
                ))}
              </ul>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

const presets = [50_000, 500_000, 2_000_000, 10_000_000, 50_000_000];
export const MAX_RECORDS = 10_000_000_000;

/** Parses what a person typed ("2,500,000", "2 500 000") into a whole number of records, or null when it is not one. */
export function parseRecords(text: string): number | null {
  const cleaned = text.replace(/[\s,_]/g, "");
  if (!/^\d+$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return Number.isSafeInteger(value) && value <= MAX_RECORDS ? value : null;
}

/** Records per month in, estimated monthly cost per plan out. The prices come from the API, the same function that prices real invoices. */
export function CostCalculator() {
  const [text, setText] = useState("2,000,000");
  const records = parseRecords(text);
  const settled = useDebounced(records, 300);
  const results = useQueries({
    queries: planKeys.map((plan) => ({
      queryKey: ["estimate", plan, settled],
      queryFn: ({ signal }: { signal: AbortSignal }) => publicApi.estimate(plan, settled ?? 0, signal),
      enabled: settled !== null,
      staleTime: 10 * 60_000,
      retry: 1,
      placeholderData: (previous: unknown) => previous as never,
    })),
  });
  const totals = results.map((r) => r.data?.total_cents);
  const cheapest = totals.every((t) => t !== undefined) ? Math.min(...(totals as number[])) : undefined;
  const failed = results.some((r) => r.isError);

  return (
    <div className="rounded-xl border border-border bg-surface-raised p-6 shadow-md md:p-8">
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        <div>
          <Label htmlFor="records">Records loaded per month</Label>
          <Input
            id="records"
            inputMode="numeric"
            autoComplete="off"
            value={text}
            aria-invalid={records === null ? true : undefined}
            aria-describedby="records-help"
            onChange={(event) => setText(event.target.value)}
          />
          <p id="records-help" className={cn("mt-1.5 text-sm", records === null ? "font-medium text-danger" : "text-foreground-muted")} role={records === null ? "alert" : undefined}>
            {records === null ? "Enter a whole number of records, for example 2,000,000." : "A record is one row loaded into the new system."}
          </p>
          <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Example volumes">
            {presets.map((preset) => (
              <Button key={preset} size="sm" variant={records === preset ? "default" : "outline"} aria-pressed={records === preset} onClick={() => setText(formatInt(preset))}>
                {preset >= 1_000_000 ? `${preset / 1_000_000}M` : `${preset / 1_000}K`}
              </Button>
            ))}
          </div>
        </div>
        <div aria-live="polite">
          <p className="mb-3 text-sm font-semibold text-foreground">Estimated monthly cost</p>
          <ul className="grid gap-3 sm:grid-cols-3">
            {planKeys.map((plan, index) => {
              const result = results[index];
              const data = result?.data;
              return (
                <li key={plan} className={cn("min-h-[132px] rounded-lg border p-4", data && data.total_cents === cheapest ? "border-success bg-success-soft" : "border-border bg-background")}>
                  <p className="text-sm font-semibold capitalize text-foreground">{plan}</p>
                  {data ? (
                    <>
                      <p className="mt-1 text-2xl font-bold tabular-nums text-foreground">{formatCents(data.total_cents)}</p>
                      <p className="mt-1 text-xs text-foreground-muted">
                        {formatCents(data.base_cents)} base
                        {data.overage_cents > 0 ? ` + ${formatCents(data.overage_cents)} for ${formatInt(data.overage_records)} extra records` : ", no overage"}
                      </p>
                      {data.total_cents === cheapest ? <Badge variant="success" className="mt-2">Lowest</Badge> : null}
                    </>
                  ) : result?.isError ? (
                    <p className="mt-2 text-sm text-danger">Unavailable</p>
                  ) : records === null ? (
                    <p className="mt-2 text-sm text-foreground-muted">Enter a number</p>
                  ) : (
                    <Skeleton className="mt-2 h-8 w-24" />
                  )}
                </li>
              );
            })}
          </ul>
          {failed ? <p role="alert" className="mt-3 text-sm text-danger">The prices could not be loaded from the API. Try again in a moment.</p> : null}
          <p className="mt-3 text-xs text-foreground-muted">Illustrative prices for a demonstration project. No payment is taken anywhere on this site.</p>
        </div>
      </div>
    </div>
  );
}
