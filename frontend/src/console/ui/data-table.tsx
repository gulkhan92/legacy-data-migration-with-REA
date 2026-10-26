import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";

export type Sort = { key: string; order: "asc" | "desc" };

type Props<T> = {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  /** Describes the table to assistive technology. */
  caption: string;
  /** Column ids that the server can sort by. Sorting is done by the server, the table only shows and changes it. */
  sortable?: string[];
  sort?: Sort;
  onSort?: (sort: Sort) => void;
  rowKey: (row: T) => string;
  /** The look of a row that is busy (while a refetch is under way the rows stay, dimmed). */
  busy?: boolean;
  className?: string;
};

/** A table built on TanStack Table: headers that sort (with aria-sort), a caption for screen readers, and a wrapper that scrolls sideways on a narrow screen. */
export function DataTable<T>({ columns, data, caption, sortable = [], sort, onSort, rowKey, busy, className }: Props<T>) {
  // TanStack Table returns functions that React Compiler cannot memoize; the table is rebuilt each render, which is cheap at these sizes.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({ columns, data, getCoreRowModel: getCoreRowModel(), getRowId: rowKey, manualSorting: true });
  return (
    <div className={cn("overflow-x-auto rounded-lg border border-border", busy && "opacity-60 transition-opacity", className)}>
      <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-muted text-foreground-muted">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const id = header.column.id;
                const canSort = sortable.includes(id) && onSort;
                const active = sort?.key === id;
                const label = flexRender(header.column.columnDef.header, header.getContext());
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={active ? (sort?.order === "asc" ? "ascending" : "descending") : canSort ? "none" : undefined}
                    className="whitespace-nowrap px-4 py-3 font-semibold"
                  >
                    {canSort ? (
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 rounded hover:text-foreground"
                        onClick={() => onSort({ key: id, order: active && sort?.order === "desc" ? "asc" : "desc" })}
                      >
                        {label}
                        {active ? (
                          sort?.order === "asc" ? (
                            <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
                          ) : (
                            <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
                          )
                        ) : (
                          <ChevronsUpDown className="h-3.5 w-3.5 opacity-50" aria-hidden="true" />
                        )}
                      </button>
                    ) : (
                      label
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="border-t border-border hover:bg-surface-muted/60">
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-4 py-3 align-middle">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
