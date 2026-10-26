import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { formatCents, formatDay, formatInt, formatRate } from "@/lib/format";
import { billingApi, type Invoice, type Plan } from "../api";
import { Modal, Pagination } from "../ui/controls";
import { PageHeader, Panel } from "../ui/page";
import { isAdmin } from "../ui/roles";
import { describeError, EmptyState, ErrorState, LoadingRows, QueryView } from "../ui/states";
import { Meter } from "../ui/status";
import { useToast } from "../ui/toast";

export const invoiceTone = (status: string): "success" | "warning" | "neutral" => (status === "paid" ? "success" : status === "open" ? "warning" : "neutral");

function PlanChange({ current }: { current: Plan }) {
  const toast = useToast();
  const client = useQueryClient();
  const [choice, setChoice] = useState<Plan | null>(null);
  const plans = useQuery({ queryKey: ["plans"], queryFn: ({ signal }) => billingApi.plans(signal), staleTime: 10 * 60_000 });
  const change = useMutation({
    mutationFn: (plan: Plan) => billingApi.changePlan(plan.code),
    onSuccess: () => {
      toast.success(`The plan is now ${choice?.name ?? "changed"}`);
      setChoice(null);
      void client.invalidateQueries({ queryKey: ["subscription"] });
      void client.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (error) => toast.error("The plan could not be changed", describeError(error)),
  });
  return (
    <Panel title="Change the plan" description="A change applies at once. Usage this month is priced on the new plan.">
      {plans.isPending ? (
        <LoadingRows rows={2} />
      ) : plans.isError ? (
        <ErrorState error={plans.error} onRetry={() => void plans.refetch()} />
      ) : (
        <ul className="grid gap-4 md:grid-cols-3">
          {plans.data.map((p) => (
            <li key={p.code} className="flex flex-col rounded-lg border border-border p-4">
              <p className="font-semibold">
                {p.name} {p.code === current.code ? <Badge variant="primary">Current</Badge> : null}
              </p>
              <p className="mt-1 text-2xl font-bold">{p.monthly_fee_cents === 0 ? "Free" : `${formatCents(p.monthly_fee_cents)} a month`}</p>
              <p className="mt-1 flex-1 text-sm text-foreground-muted">
                {formatInt(p.included_records)} records included, then {formatRate(p.overage_cents_per_1000)} per 1,000.
              </p>
              <Button className="mt-3" size="sm" variant="outline" disabled={p.code === current.code} onClick={() => setChoice(p)}>
                {p.code === current.code ? "Your plan" : `Switch to ${p.name}`}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Modal
        open={!!choice}
        onOpenChange={(open) => !open && setChoice(null)}
        title={`Switch to ${choice?.name ?? ""}?`}
        description={
          choice
            ? `${choice.monthly_fee_cents === 0 ? "It is free" : `It costs ${formatCents(choice.monthly_fee_cents)} a month`} and includes ${formatInt(choice.included_records)} records.`
            : undefined
        }
        footer={
          <>
            <Button variant="outline" onClick={() => setChoice(null)}>
              Keep {current.name}
            </Button>
            <Button disabled={change.isPending} onClick={() => choice && change.mutate(choice)}>
              Change the plan
            </Button>
          </>
        }
      />
    </Panel>
  );
}

const INVOICES = 10;

function Invoices() {
  const [offset, setOffset] = useState(0);
  const invoices = useQuery({
    queryKey: ["invoices", offset],
    queryFn: ({ signal }) => billingApi.invoices({ limit: INVOICES, offset }, signal),
    placeholderData: (previous) => previous,
  });
  return (
    <Panel title="Invoices" bodyClassName="p-0">
      {invoices.isPending ? (
        <div className="p-5">
          <LoadingRows rows={3} />
        </div>
      ) : invoices.isError ? (
        <div className="p-5">
          <ErrorState error={invoices.error} onRetry={() => void invoices.refetch()} />
        </div>
      ) : invoices.data.items.length === 0 ? (
        <div className="p-5">
          <EmptyState title="No invoices yet">The first invoice is issued at the end of the month in which a migration loads data.</EmptyState>
        </div>
      ) : (
        <div className="px-5 pb-5">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-sm">
              <caption className="sr-only">Invoices</caption>
              <thead className="text-foreground-muted">
                <tr>
                  <th scope="col" className="py-3 pr-4 font-semibold">
                    Period
                  </th>
                  <th scope="col" className="py-3 pr-4 font-semibold">
                    Issued
                  </th>
                  <th scope="col" className="py-3 pr-4 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="py-3 pr-4 text-right font-semibold">
                    Total
                  </th>
                  <th scope="col" className="py-3">
                    <span className="sr-only">Open</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {invoices.data.items.map((i: Invoice) => (
                  <tr key={i.id} className="border-t border-border">
                    <td className="py-3 pr-4">
                      {formatDay(i.period_start)} to {formatDay(i.period_end)}
                    </td>
                    <td className="py-3 pr-4 text-foreground-muted">{formatDay(i.issued_at)}</td>
                    <td className="py-3 pr-4">
                      <Badge variant={invoiceTone(i.status)} className="capitalize">
                        {i.status}
                      </Badge>
                    </td>
                    <td className="py-3 pr-4 text-right tabular-nums">{formatCents(i.total_cents)}</td>
                    <td className="py-3 text-right">
                      <Link
                        to={`/app/billing/invoices/${i.id}`}
                        className="font-semibold text-primary hover:underline"
                        aria-label={`Open the invoice for ${formatDay(i.period_start)}`}
                      >
                        Open
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={invoices.data.total} limit={INVOICES} offset={offset} onChange={setOffset} noun="invoices" />
        </div>
      )}
    </Panel>
  );
}

export default function Billing() {
  const { me } = useAuth();
  const subscription = useQuery({ queryKey: ["subscription"], queryFn: ({ signal }) => billingApi.subscription(signal) });
  const usage = useQuery({ queryKey: ["usage"], queryFn: ({ signal }) => billingApi.usage(signal) });
  const admin = me ? isAdmin(me.role) : false;
  return (
    <>
      <PageHeader title="Billing" description="Your plan, what this month has used of it, and the invoices." />
      <div className="space-y-6">
        <QueryView query={subscription}>
          {(s) => {
            const u = s.usage;
            return (
              <>
                <Panel title={`${s.plan.name} plan`} description={`${formatDay(s.period.start)} to ${formatDay(s.period.end)}`}>
                  <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
                    <div>
                      <p className="text-sm font-medium text-foreground-muted">Records loaded this month</p>
                      <p className="mt-1 text-3xl font-bold">
                        {formatInt(u.records)} <span className="text-base font-normal text-foreground-muted">of {formatInt(u.included_records)} included</span>
                      </p>
                      <div className="mt-3">
                        <Meter value={u.records} max={u.included_records} label="Records loaded of the monthly allowance">
                          <p className="mt-2 text-sm text-foreground-muted">
                            {u.percent_of_allowance.toFixed(1)}% of the allowance used.{" "}
                            {u.overage_records > 0
                              ? `${formatInt(u.overage_records)} records over, charged at ${formatRate(s.plan.overage_cents_per_1000)} per 1,000.`
                              : `Records beyond it cost ${formatRate(s.plan.overage_cents_per_1000)} per 1,000.`}
                          </p>
                        </Meter>
                      </div>
                    </div>
                    <dl className="space-y-2 rounded-lg bg-surface-muted p-4 text-sm">
                      <div className="flex justify-between">
                        <dt className="text-foreground-muted">Plan fee</dt>
                        <dd className="tabular-nums">{formatCents(u.base_cents)}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-foreground-muted">Overage so far</dt>
                        <dd className="tabular-nums">{formatCents(u.overage_cents)}</dd>
                      </div>
                      <div className="flex justify-between border-t border-border pt-2 font-semibold">
                        <dt>This month so far</dt>
                        <dd className="tabular-nums">{formatCents(u.total_cents)}</dd>
                      </div>
                      <div className="flex justify-between text-foreground-muted">
                        <dt>Projected for the month</dt>
                        <dd className="tabular-nums">{formatCents(u.projected_total_cents)}</dd>
                      </div>
                    </dl>
                  </div>
                  {s.is_simulated ? <p className="mt-3 text-xs text-foreground-muted">This subscription belongs to a simulated organization.</p> : null}
                </Panel>
                {admin ? <PlanChange current={s.plan} /> : null}
              </>
            );
          }}
        </QueryView>

        <Panel title="Usage by month" description="Records loaded and model tokens used in the last twelve months." bodyClassName="p-0">
          {usage.isPending ? (
            <div className="p-5">
              <LoadingRows rows={3} />
            </div>
          ) : usage.isError ? (
            <div className="p-5">
              <ErrorState error={usage.error} onRetry={() => void usage.refetch()} />
            </div>
          ) : usage.data.months.length === 0 ? (
            <div className="p-5">
              <EmptyState title="No usage yet">Usage appears after the first migration loads data.</EmptyState>
            </div>
          ) : (
            <div className="overflow-x-auto px-5 pb-5">
              <table className="w-full min-w-[28rem] text-left text-sm">
                <caption className="sr-only">Usage by month</caption>
                <thead className="text-foreground-muted">
                  <tr>
                    <th scope="col" className="py-3 pr-4 font-semibold">
                      Month
                    </th>
                    <th scope="col" className="py-3 pr-4 text-right font-semibold">
                      Jobs
                    </th>
                    <th scope="col" className="py-3 pr-4 text-right font-semibold">
                      Records
                    </th>
                    <th scope="col" className="py-3 text-right font-semibold">
                      Tokens
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {usage.data.months.map((m) => (
                    <tr key={m.month} className="border-t border-border">
                      <td className="py-3 pr-4">
                        {new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${m.month}T00:00:00Z`))}
                      </td>
                      <td className="py-3 pr-4 text-right tabular-nums">{formatInt(m.jobs)}</td>
                      <td className="py-3 pr-4 text-right tabular-nums">{formatInt(m.records)}</td>
                      <td className="py-3 text-right tabular-nums">{formatInt(m.tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        {admin ? <Invoices /> : <p className="text-sm text-foreground-muted">Invoices are visible to administrators.</p>}
      </div>
    </>
  );
}
