import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type Delta = {
  text: string;
  direction: "up" | "down" | "flat";
  /** Whether a rise is good news (success rate) or bad news (failures, duration). */ goodWhen: "up" | "down" | "none";
  period: string;
};

/** The change between two numbers, ready to show. A missing earlier value gives no delta. */
export function deltaOf(current: number | null, previous: number | null, format: (change: number) => string, goodWhen: Delta["goodWhen"], period: string): Delta | undefined {
  if (current === null || previous === null || previous === 0) return undefined;
  const change = (current - previous) / Math.abs(previous);
  if (Math.abs(change) < 0.0005) return { text: "no change", direction: "flat", goodWhen, period };
  return { text: format(change), direction: change > 0 ? "up" : "down", goodWhen, period };
}

/** The same, from a change the API already computed (the `change` of a report asked with compare). */
export function deltaFromRatio(
  change: number | null | undefined,
  format: (change: number) => string,
  goodWhen: Delta["goodWhen"],
  period = "vs the previous period",
): Delta | undefined {
  if (change === null || change === undefined || !Number.isFinite(change)) return undefined;
  if (Math.abs(change) < 0.0005) return { text: "no change", direction: "flat", goodWhen, period };
  return { text: format(change), direction: change > 0 ? "up" : "down", goodWhen, period };
}

function tone(delta: Delta): string {
  if (delta.direction === "flat" || delta.goodWhen === "none") return "text-foreground-muted";
  return (delta.direction === "up") === (delta.goodWhen === "up") ? "text-success" : "text-danger";
}

/** A figure with its label, an optional change against a named period (an arrow and a sign, never only a color) and an optional sparkline. */
export function StatTile({
  label,
  value,
  delta,
  hint,
  spark,
  className,
}: {
  label: string;
  value: ReactNode;
  delta?: Delta | undefined;
  hint?: ReactNode;
  spark?: ReactNode;
  className?: string;
}) {
  const Icon = delta?.direction === "up" ? ArrowUpRight : delta?.direction === "down" ? ArrowDownRight : Minus;
  return (
    <div className={cn("flex min-w-0 flex-col rounded-lg border border-border bg-surface p-5 shadow-sm", className)}>
      <p className="text-sm font-medium text-foreground-muted">{label}</p>
      <p className="mt-1 text-3xl font-bold leading-tight text-foreground">{value}</p>
      {delta ? (
        <p className={cn("mt-1 flex items-center gap-1 text-sm font-medium", tone(delta))}>
          <Icon className="h-4 w-4" aria-hidden="true" />
          <span>{delta.text}</span>
          <span className="font-normal text-foreground-muted">{delta.period}</span>
        </p>
      ) : hint ? (
        <p className="mt-1 text-sm text-foreground-muted">{hint}</p>
      ) : null}
      {spark ? <div className="mt-3">{spark}</div> : null}
    </div>
  );
}
