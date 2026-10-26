import { BarChart3, Table2 } from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useTheme } from "../shell/theme";
import { EmptyState } from "../ui/states";
import type { EChartsOption } from "./echarts";
import { Chart } from "./Chart";
import { chrome, type Chrome } from "./palette";

export type LegendItem = { key: string; label: string; color: string; shape?: "line" | "rect" };
export type TableData = { columns: string[]; rows: (string | number)[][] };

/** A legend whose entries are buttons: they toggle a series on and off, work from the keyboard, and keep every other series in its own color. */
export function Legend({ items, hidden, onToggle }: { items: LegendItem[]; hidden: Set<string>; onToggle: (key: string) => void }) {
  return (
    <ul aria-label="Legend. Press an entry to show or hide that series." className="flex flex-wrap gap-x-4 gap-y-1">
      {items.map((item) => {
        const off = hidden.has(item.key);
        return (
          <li key={item.key}>
            <button
              type="button"
              aria-pressed={!off}
              onClick={() => onToggle(item.key)}
              className={cn("inline-flex items-center gap-2 rounded px-1.5 py-1 text-sm text-foreground-muted hover:text-foreground", off && "opacity-50")}
            >
              <span
                aria-hidden="true"
                className={cn("inline-block", item.shape === "line" ? "h-0.5 w-4 rounded-full" : "h-2.5 w-2.5 rounded-sm")}
                style={{ background: item.color }}
              />
              <span className={off ? "line-through" : ""}>{item.label}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function TableView({ data, caption }: { data: TableData; caption: string }) {
  return (
    <div className="max-h-80 overflow-auto rounded-lg border border-border">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-surface-muted text-foreground-muted">
          <tr>
            {data.columns.map((c, i) => (
              <th key={c} scope="col" className={cn("px-3 py-2 font-semibold", i > 0 && "text-right")}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, r) => (
            <tr key={r} className="border-t border-border">
              {row.map((cell, i) => (
                <td key={i} className={cn("px-3 py-1.5", i > 0 ? "text-right tabular-nums" : "font-medium")}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Props = {
  title: string;
  description?: string;
  /** A sentence or two, in words, of what the chart shows. It is the text version for people who cannot see the drawing. */
  summary: string;
  legend?: LegendItem[];
  /** Builds the option from the ink of the current theme and the series that are switched off. */
  build: (theme: Chrome, hidden: Set<string>) => EChartsOption;
  table: TableData;
  height?: number;
  /** While the data is being fetched again the previous drawing stays, dimmed, so that nothing jumps. */
  busy?: boolean;
  empty?: boolean;
  actions?: ReactNode;
  className?: string;
};

/**
 * Every chart is this card: a title and description, a legend with toggles (two or more series), the drawing, the text summary, and a
 * table of the same numbers behind a switch, which is the accessible equivalent of the drawing.
 */
export function ChartCard({ title, description, summary, legend, build, table, height = 280, busy, empty, actions, className }: Props) {
  const { resolved } = useTheme();
  const [view, setView] = useState<"chart" | "table">("chart");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const id = useId();
  const theme = useMemo(() => chrome(resolved), [resolved]);
  const option = build(theme, hidden);
  const toggle = (key: string) =>
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else if (legend && next.size < legend.length - 1) next.add(key); // the last visible series stays
      return next;
    });

  return (
    <figure aria-labelledby={`${id}-title`} aria-describedby={`${id}-summary`} className={cn("min-w-0 rounded-lg border border-border bg-surface p-5 shadow-sm", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={`${id}-title`} className="text-base font-semibold">
            {title}
          </h3>
          {description ? <p className="mt-0.5 text-sm text-foreground-muted">{description}</p> : null}
        </div>
        <div className="flex items-center gap-2">
          {actions}
          <div role="group" aria-label={`${title}: view`} className="inline-flex rounded-md border border-border p-0.5">
            <button
              type="button"
              aria-pressed={view === "chart"}
              onClick={() => setView("chart")}
              className={cn(
                "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-sm font-medium",
                view === "chart" ? "bg-primary-soft text-primary-ink" : "text-foreground-muted hover:text-foreground",
              )}
            >
              <BarChart3 className="h-4 w-4" aria-hidden="true" />
              Chart
            </button>
            <button
              type="button"
              aria-pressed={view === "table"}
              onClick={() => setView("table")}
              className={cn(
                "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-sm font-medium",
                view === "table" ? "bg-primary-soft text-primary-ink" : "text-foreground-muted hover:text-foreground",
              )}
            >
              <Table2 className="h-4 w-4" aria-hidden="true" />
              Table
            </button>
          </div>
        </div>
      </div>
      {legend && legend.length >= 2 && view === "chart" ? (
        <div className="mt-3">
          <Legend items={legend} hidden={hidden} onToggle={toggle} />
        </div>
      ) : null}
      <div className={cn("mt-3 transition-opacity", busy && "opacity-50")} aria-busy={busy}>
        {empty ? (
          <EmptyState title="No data in this range" className="py-10">
            Pick a wider range of dates.
          </EmptyState>
        ) : view === "chart" ? (
          <Chart option={option} height={height} />
        ) : (
          <TableView data={table} caption={title} />
        )}
      </div>
      <figcaption id={`${id}-summary`} className="mt-3 text-sm text-foreground-muted">
        {summary}
      </figcaption>
    </figure>
  );
}
