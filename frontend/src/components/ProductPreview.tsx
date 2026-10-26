import { CheckCircle2, ChevronRight, FileSearch, Layers3, Scale, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";

/**
 * An illustration of the console, drawn with the real design system and filled with real examples from the sample systems.
 * It is labelled as an illustration wherever it appears. The authentic screenshots are captured from the running console by
 * scripts/capture_screenshots.ts once the console exists.
 */
/** Real output of the mapping step on the commerce sample (first run, so every method is "embedding"). */
const mappingRows: { source: string; target: string | null; confidence: number; method: string }[] = [
  { source: "CUST_MST.cust_no", target: "customers.source_key", confidence: 0.9, method: "embedding" },
  { source: "CUST_MST.email_adr", target: "customers.email", confidence: 0.9, method: "embedding" },
  { source: "INV_HDR.inv_dt", target: "orders.order_date", confidence: 0.9, method: "embedding" },
  { source: "INV_HDR.stat_cd", target: "orders.status", confidence: 0.6, method: "embedding" },
  { source: "PROD_MST.prod_nm", target: null, confidence: 0, method: "review" },
];

function Bar({ value }: { value: number }) {
  return (
    <span className="flex items-center gap-2" aria-label={`Confidence ${Math.round(value * 100)} percent`}>
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-border" aria-hidden="true">
        <span className="block h-full rounded-full bg-primary" style={{ width: `${value * 100}%` }} />
      </span>
      <span className="text-xs tabular-nums text-foreground-muted">{value.toFixed(2)}</span>
    </span>
  );
}

export function MappingPanel({ className }: { className?: string }) {
  return (
    <div className={cn("overflow-hidden rounded-xl border border-border bg-surface-raised shadow-lg", className)}>
      <div className="flex items-center gap-2 border-b border-border bg-surface-muted px-4 py-2.5" aria-hidden="true">
        <span className="h-2.5 w-2.5 rounded-full bg-border-strong" />
        <span className="h-2.5 w-2.5 rounded-full bg-border-strong" />
        <span className="h-2.5 w-2.5 rounded-full bg-border-strong" />
        <span className="ml-3 text-xs font-medium text-foreground-subtle">New migration / Mapping review</span>
      </div>
      <div className="p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <p className="text-sm font-semibold text-foreground">Review mappings</p>
          <Badge variant="teal">27 of 32 columns matched</Badge>
        </div>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Example mapping review for the commerce sample</caption>
          <thead>
            <tr className="border-b border-border text-xs uppercase tracking-wide text-foreground-subtle">
              <th scope="col" className="pb-2 pr-2 font-semibold">Source column</th>
              <th scope="col" className="pb-2 pr-2 font-semibold">Target field</th>
              <th scope="col" className="hidden pb-2 pr-2 font-semibold sm:table-cell">Confidence</th>
              <th scope="col" className="pb-2 font-semibold">Method</th>
            </tr>
          </thead>
          <tbody>
            {mappingRows.map((row) => (
              <tr key={row.source} className="border-b border-border/70 last:border-0">
                <td className="py-2 pr-2 font-mono text-xs text-foreground">{row.source}</td>
                <td className="py-2 pr-2 font-mono text-xs text-foreground">{row.target ?? "no confident match"}</td>
                <td className="hidden py-2 pr-2 sm:table-cell">{row.target ? <Bar value={row.confidence} /> : <span className="text-xs text-foreground-subtle">not scored</span>}</td>
                <td className="py-2">
                  <Badge variant={row.target ? "primary" : "warning"}>{row.method}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-3 flex items-center justify-between text-xs text-foreground-muted">
          <span className="inline-flex items-center gap-1">
            <CheckCircle2 className="h-3.5 w-3.5 text-success" aria-hidden="true" />
            Nothing is loaded until you approve
          </span>
          <span className="inline-flex items-center gap-0.5 font-semibold text-primary">
            Approve, override or reject <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
        </div>
      </div>
    </div>
  );
}

export function DiscoveryPanel({ className }: { className?: string }) {
  // Read from the recovered layout of the ClinicDesk sample (record size 177 bytes).
  const fields = [
    ["PatNo", "int32", "offset 0, 4 bytes"],
    ["LastNm", "char", "offset 4, 30 bytes"],
    ["BirthDt", "int32", "offset 54, 4 bytes"],
    ["Flags", "uint16", "offset 62, 2 bytes"],
    ["Ssn", "char", "offset 64, 11 bytes"],
  ];
  return (
    <div className={cn("rounded-xl border border-border bg-surface-raised p-4 shadow-lg", className)}>
      <div className="mb-3 flex items-center justify-between">
        <p className="inline-flex items-center gap-2 text-sm font-semibold"><FileSearch className="h-4 w-4 text-primary" aria-hidden="true" />Recovered record layout: PATIENT</p>
        <Badge variant="success">validated against the data file</Badge>
      </div>
      <ul className="divide-y divide-border text-sm">
        {fields.map(([name, type, offset]) => (
          <li key={name} className="flex items-center justify-between py-2">
            <span className="font-mono text-xs">{name}</span>
            <span className="text-xs text-foreground-muted">{type} &middot; {offset}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-foreground-muted">Each field links to the evidence: the instructions that read it.</p>
    </div>
  );
}

export function TransformPanel({ className }: { className?: string }) {
  // Values found in the commerce sample, and what the generated rules do with them.
  const rows = [
    ["01-Jan-09", "2009-01-01"],
    ["20090102", "2009-01-02"],
    ["$0.99", "0.99"],
    ["status code 3", "cancelled"],
    ["unknown", "quarantined: invalid date"],
  ];
  return (
    <div className={cn("rounded-xl border border-border bg-surface-raised p-4 shadow-lg", className)}>
      <p className="mb-3 inline-flex items-center gap-2 text-sm font-semibold"><Wand2 className="h-4 w-4 text-primary" aria-hidden="true" />Before and after, on a sample of the source</p>
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Example values before and after the rules</caption>
        <thead>
          <tr className="text-xs uppercase tracking-wide text-foreground-subtle">
            <th scope="col" className="pb-2 font-semibold">Before</th>
            <th scope="col" className="pb-2 font-semibold">After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([before, after]) => (
            <tr key={before} className="border-t border-border/70">
              <td className="py-2 font-mono text-xs text-foreground-muted">{before}</td>
              <td className="py-2 font-mono text-xs text-foreground">{after}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-xs text-foreground-muted">Failures are counted per rule before anything is loaded.</p>
    </div>
  );
}

export function ReconciliationPanel({ className }: { className?: string }) {
  const cards = [
    ["Source rows", "6,338"],
    ["Loaded", "5,515"],
    ["Quarantined", "823"],
    ["Unexplained", "0"],
  ];
  return (
    <div className={cn("rounded-xl border border-border bg-surface-raised p-4 shadow-lg", className)}>
      <p className="mb-3 inline-flex items-center gap-2 text-sm font-semibold"><Scale className="h-4 w-4 text-primary" aria-hidden="true" />Reconciliation report: commerce sample</p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {cards.map(([label, value]) => (
          <div key={label} className="rounded-md bg-surface-muted p-3">
            <dt className="text-xs text-foreground-muted">{label}</dt>
            <dd className="text-xl font-bold tabular-nums text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
      <ul className="mt-3 space-y-1.5 text-sm">
        {["Row accounting", "Distinct keys", "Null counts", "Numeric sums", "Sampled rows"].map((check) => (
          <li key={check} className="flex items-center gap-2 text-foreground-muted">
            <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" />
            {check}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The hero figure: a mapping review panel with two smaller panels behind it. */
export function HeroPreview() {
  return (
    <figure className="relative" aria-label="Illustration of the LegacyLift console">
      <div className="absolute -inset-6 -z-10 rounded-3xl bg-gradient-to-br from-primary/25 via-teal/15 to-transparent blur-2xl" aria-hidden="true" />
      <MappingPanel />
      <div className="absolute -bottom-6 -left-4 hidden w-52 rounded-lg border border-border bg-surface-raised p-3 shadow-lg md:block" aria-hidden="true">
        <p className="flex items-center gap-1.5 text-xs font-semibold"><Layers3 className="h-3.5 w-3.5 text-teal-text" />Quality score</p>
        <p className="mt-1 text-2xl font-bold tabular-nums">91.3 <span className="text-base font-medium text-success">to 99.5</span></p>
      </div>
      <figcaption className="mt-8 text-center text-xs text-foreground-subtle md:text-right">Illustration with real examples from the commerce sample system</figcaption>
    </figure>
  );
}
