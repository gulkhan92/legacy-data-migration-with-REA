import { Download, Printer } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { analyticsApi, type Granularity } from "../api";
import { Select } from "../ui/controls";
import { PRESETS, type Preset, type RangeState } from "./range";

type Props = {
  range: RangeState & { update: (changes: Record<string, string | null>) => void };
  csv: "revenue" | "operations";
};

/** One row above everything it scopes: the date range (presets first, a custom range behind the last one), the bucket size, the comparison, and the exports. */
export function DashboardControls({ range, csv }: Props) {
  const choose = (preset: Preset) => range.update({ range: preset === "90d" ? null : preset, granularity: null, ...(preset === "custom" ? {} : { from: null, to: null }) });
  return (
    <form aria-label="Dashboard controls" className="mb-6 flex flex-wrap items-end gap-x-5 gap-y-3 print:hidden" onSubmit={(e) => e.preventDefault()}>
      <fieldset>
        <legend className="mb-1 text-sm font-medium">Date range</legend>
        <div role="radiogroup" aria-label="Date range" className="inline-flex flex-wrap rounded-lg bg-surface-muted p-1">
          {PRESETS.map((p) => (
            <button
              key={p.value}
              type="button"
              role="radio"
              aria-checked={range.preset === p.value}
              onClick={() => choose(p.value)}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm font-semibold",
                range.preset === p.value ? "bg-background text-primary shadow-sm" : "text-foreground-muted hover:text-foreground",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </fieldset>
      {range.preset === "custom" ? (
        <>
          <label className="text-sm">
            <span className="mb-1 block font-medium">From</span>
            <Input type="date" value={range.from} onChange={(e) => range.update({ from: e.target.value || null })} className="h-10 w-40 text-sm" aria-invalid={!range.valid} />
          </label>
          <label className="text-sm">
            <span className="mb-1 block font-medium">To</span>
            <Input type="date" value={range.to} onChange={(e) => range.update({ to: e.target.value || null })} className="h-10 w-40 text-sm" aria-invalid={!range.valid} />
          </label>
          {!range.valid ? (
            <p role="alert" className="pb-2 text-sm font-medium text-danger">
              Choose two days, the first not after the second.
            </p>
          ) : null}
        </>
      ) : null}
      <label className="text-sm">
        <span className="mb-1 block font-medium">Group by</span>
        <Select value={range.granularity} onChange={(e) => range.update({ granularity: e.target.value as Granularity })}>
          <option value="day">Day</option>
          <option value="week">Week</option>
          <option value="month">Month</option>
        </Select>
      </label>
      <label className="flex h-10 items-center gap-2 text-sm font-medium">
        <input type="checkbox" className="h-4 w-4 accent-primary" checked={range.compare} onChange={(e) => range.update({ compare: e.target.checked ? "1" : null })} />
        Compare with the previous period
      </label>
      <div className="ml-auto flex gap-2">
        <Button asChild variant="outline" size="sm">
          <a href={analyticsApi.csvUrl(csv, range.params)} download>
            <Download className="h-4 w-4" aria-hidden="true" />
            Export CSV
          </a>
        </Button>
        <Button variant="outline" size="sm" onClick={() => window.print()}>
          <Printer className="h-4 w-4" aria-hidden="true" />
          Print
        </Button>
      </div>
    </form>
  );
}
