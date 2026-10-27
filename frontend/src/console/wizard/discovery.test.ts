import { describe, expect, it } from "vitest";
import type { Discovery, Profile } from "../api";
import { discoverySummary, discoveryTables } from "./discovery";

const sqlite: Discovery = {
  kind: "sqlite",
  engine: "SQLite 3.53.3",
  tables: [
    {
      name: "CUST_MST",
      kind: "table",
      columns: [
        { name: "cust_no", declared_type: "INTEGER", nullable: false },
        { name: "email_adr", declared_type: "TEXT", nullable: true },
      ],
    },
  ],
};

const profile: Profile = {
  tables: [
    {
      name: "CUST_MST",
      rows: 60,
      columns: [
        { name: "cust_no", table: "CUST_MST", pii: false, null_rate: 0, distinct_count: 59, inferred_type: "integer" },
        { name: "email_adr", table: "CUST_MST", pii: true, null_rate: 0.03, distinct_count: 58, inferred_type: "email" },
      ],
    },
  ],
};

const binary: Discovery = {
  format: "CDB1",
  method: "cil",
  validated: true,
  tables: [
    {
      name: "PATIENT",
      writer: "ClinicDesk.CdbWriter.WritePatient",
      confidence: 0.95,
      record_size: 177,
      record_count: 306,
      fields: [
        { name: "PatNo", type: "int32", length: 4, offset: 0, method: "cil", confidence: 0.95, semantic: "key" },
        { name: "Flags", type: "bytes", length: 1, offset: 176, method: "structure", confidence: 0.6, encoding: null },
      ],
    },
  ],
};

describe("discoveryTables", () => {
  it("reads a database as certain, with the profile's rows, types and personal data flags", () => {
    const [table] = discoveryTables(sqlite, profile);
    expect(table).toMatchObject({ name: "CUST_MST", rows: 60, note: "table", confidence: null });
    expect(table?.fields[0]).toMatchObject({ name: "cust_no", subject: "CUST_MST.cust_no", type: "INTEGER", method: "declaration", confidence: 1, pii: false });
    expect(table?.fields[0]?.detail).toBe("integer, required");
    expect(table?.fields[1]).toMatchObject({ pii: true, nullRate: 0.03 });
  });

  it("works before profiling has anything to add", () => {
    const [table] = discoveryTables(sqlite, null);
    expect(table?.rows).toBeNull();
    expect(table?.fields[1]?.pii).toBe(false);
  });

  it("reads a recovered layout with the confidence of each field", () => {
    const [table] = discoveryTables(binary, null);
    expect(table).toMatchObject({ name: "PATIENT", rows: 306, confidence: 0.95 });
    expect(table?.note).toContain("177 bytes per record");
    expect(table?.fields[0]).toMatchObject({ name: "PatNo", type: "int32(4)", method: "cil", confidence: 0.95, subject: "PATIENT.PatNo" });
    expect(table?.fields[0]?.detail).toBe("key, offset 0");
    expect(table?.fields[1]?.confidence).toBe(0.6);
  });
});

describe("discoverySummary", () => {
  it("counts tables, fields and rows", () => {
    const summary = discoverySummary(sqlite, discoveryTables(sqlite, profile));
    expect(summary).toEqual([
      { label: "Kind of source", value: "SQLite 3.53.3" },
      { label: "Tables", value: "1" },
      { label: "Fields", value: "2" },
      { label: "Rows", value: "60" },
    ]);
  });

  it("adds how many fields of a binary layout are below 70 percent", () => {
    const summary = discoverySummary(binary, discoveryTables(binary, null));
    expect(summary[0]).toEqual({ label: "Kind of source", value: "Binary file (CDB1)" });
    expect(summary.at(-1)).toEqual({ label: "Fields below 70% confidence", value: "1" });
  });
});
