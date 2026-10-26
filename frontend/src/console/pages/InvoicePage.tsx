import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Printer } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { formatCents, formatDay, formatInt } from "@/lib/format";
import { billingApi } from "../api";
import { PageHeader } from "../ui/page";
import { QueryView } from "../ui/states";
import { invoiceTone } from "./Billing";

/** One invoice, laid out to print: the console around it is hidden by the print rules. */
export default function InvoicePage() {
  const { invoiceId } = useParams();
  const { me } = useAuth();
  const invoice = useQuery({ queryKey: ["invoice", invoiceId], queryFn: ({ signal }) => billingApi.invoice(invoiceId as string, signal), enabled: !!invoiceId });
  return (
    <>
      <div className="print:hidden">
        <PageHeader
          title="Invoice"
          description="The lines of one invoice. Print it, or save it as a PDF from the print dialog."
          actions={
            <>
              <Button asChild variant="outline" size="sm">
                <Link to="/app/billing">
                  <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                  All invoices
                </Link>
              </Button>
              <Button size="sm" onClick={() => window.print()}>
                <Printer className="h-4 w-4" aria-hidden="true" />
                Print
              </Button>
            </>
          }
        />
      </div>
      <QueryView query={invoice}>
        {(i) => (
          <article
            aria-label={`Invoice for ${formatDay(i.period_start)} to ${formatDay(i.period_end)}`}
            className="mx-auto max-w-3xl rounded-lg border border-border bg-surface p-8 shadow-sm print:border-0 print:shadow-none"
          >
            <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-6">
              <div>
                <p className="text-2xl font-bold">Invoice</p>
                <p className="mt-1 text-sm text-foreground-muted">LegacyLift, billed to {me?.org_name}</p>
              </div>
              <div className="text-right text-sm">
                <Badge variant={invoiceTone(i.status)} className="capitalize">
                  {i.status}
                </Badge>
                <p className="mt-2 text-foreground-muted">Issued {formatDay(i.issued_at)}</p>
                {i.due_at ? <p className="text-foreground-muted">Due {formatDay(i.due_at)}</p> : null}
                {i.paid_at ? <p className="text-foreground-muted">Paid {formatDay(i.paid_at)}</p> : null}
              </div>
            </header>
            <dl className="mt-6 grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-foreground-muted">Period</dt>
                <dd className="font-semibold">
                  {formatDay(i.period_start)} to {formatDay(i.period_end)}
                </dd>
              </div>
              <div>
                <dt className="text-foreground-muted">Plan</dt>
                <dd className="font-semibold capitalize">{i.plan ?? "not recorded"}</dd>
              </div>
            </dl>
            <table className="mt-6 w-full text-left text-sm">
              <caption className="sr-only">Lines of the invoice</caption>
              <thead className="border-b border-border text-foreground-muted">
                <tr>
                  <th scope="col" className="py-2 pr-4 font-semibold">
                    Description
                  </th>
                  <th scope="col" className="py-2 pr-4 text-right font-semibold">
                    Quantity
                  </th>
                  <th scope="col" className="py-2 pr-4 text-right font-semibold">
                    Unit price
                  </th>
                  <th scope="col" className="py-2 text-right font-semibold">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {i.lines.map((l) => (
                  <tr key={`${l.kind}-${l.description}`} className="border-b border-border">
                    <td className="py-3 pr-4">{l.description}</td>
                    <td className="py-3 pr-4 text-right tabular-nums">{formatInt(l.quantity)}</td>
                    <td className="py-3 pr-4 text-right tabular-nums">{formatCents(Math.round(l.unit_price_cents))}</td>
                    <td className="py-3 text-right tabular-nums">{formatCents(l.amount_cents)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" colSpan={3} className="pt-4 text-right text-base">
                    Total ({i.currency})
                  </th>
                  <td className="pt-4 text-right text-base font-bold tabular-nums">{formatCents(i.total_cents)}</td>
                </tr>
              </tfoot>
            </table>
            {i.is_simulated ? <p className="mt-6 text-xs text-foreground-muted">This invoice belongs to a simulated organization and is not a real charge.</p> : null}
          </article>
        )}
      </QueryView>
    </>
  );
}
