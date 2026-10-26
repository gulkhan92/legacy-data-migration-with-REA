import type { Discovery, Profile } from "../api";

export type FieldRow = {
  name: string;
  /** What to ask the evidence endpoint for: TABLE.field. */
  subject: string;
  type: string;
  /** How it was found: read from the database, or recovered from the program (CIL) or the data (structure). */
  method: string;
  /** 0 to 1. A column read from a database declaration is certain; a recovered layout field has the confidence of the analysis. */
  confidence: number;
  detail: string;
  pii: boolean;
  nullRate: number | null;
};
export type TableRows = { name: string; rows: number | null; note: string; confidence: number | null; fields: FieldRow[] };

const text = (value: unknown): string => (typeof value === "string" ? value : "");
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

/**
 * One list of tables and fields for both kinds of source, so that the discovery view does not care where they came from.
 * `profile` adds the row counts, the inferred type and the personal data flag from profiling.
 */
export function discoveryTables(discovery: Discovery, profile: Profile | null): TableRows[] {
  const profileTables = new Map((profile?.tables ?? []).map((t) => [t.name, t]));
  return discovery.tables.map((raw) => {
    const name = text(raw.name);
    const profiled = profileTables.get(name);
    const columns = new Map((profiled?.columns ?? []).map((c) => [c.name, c]));
    const list = (Array.isArray(raw.fields) ? raw.fields : Array.isArray(raw.columns) ? raw.columns : []) as Record<string, unknown>[];
    const layout = Array.isArray(raw.fields);
    const fields = list.map<FieldRow>((f) => {
      const column = columns.get(text(f.name));
      const declared = text(f.declared_type) || text(f.type);
      return {
        name: text(f.name),
        subject: `${name}.${text(f.name)}`,
        type: layout ? `${declared}${num(f.length) !== null ? `(${num(f.length)})` : ""}` : declared || "unknown",
        method: layout ? text(f.method) || "structure" : "declaration",
        confidence: layout ? (num(f.confidence) ?? 0) : 1,
        detail: layout
          ? [text(f.semantic), text(f.encoding), num(f.offset) !== null ? `offset ${num(f.offset)}` : ""].filter(Boolean).join(", ")
          : [column?.inferred_type, f.nullable === false ? "required" : ""].filter(Boolean).join(", "),
        pii: column?.pii ?? false,
        nullRate: column?.null_rate ?? null,
      };
    });
    return {
      name,
      rows: profiled?.rows ?? num(raw.record_count),
      note: layout ? [text(raw.writer), num(raw.record_size) !== null ? `${num(raw.record_size)} bytes per record` : ""].filter(Boolean).join(", ") : text(raw.kind),
      confidence: layout ? num(raw.confidence) : null,
      fields,
    };
  });
}

/** The few numbers at the top of the results. */
export function discoverySummary(discovery: Discovery, tables: TableRows[]): { label: string; value: string }[] {
  const fieldCount = tables.reduce((n, t) => n + t.fields.length, 0);
  const rows = tables.reduce((n, t) => n + (t.rows ?? 0), 0);
  const low = tables.flatMap((t) => t.fields).filter((f) => f.confidence < 0.7).length;
  const items = [
    { label: "Kind of source", value: discovery.format ? `Binary file (${discovery.format})` : (discovery.engine ?? discovery.kind ?? "Database") },
    { label: "Tables", value: String(tables.length) },
    { label: "Fields", value: String(fieldCount) },
    { label: "Rows", value: rows.toLocaleString("en-US") },
  ];
  if (discovery.format) items.push({ label: "Fields below 70% confidence", value: String(low) });
  return items;
}
