import { describe, expect, it } from "vitest";
import { canVisit, crumbsFor, navFor } from "./nav";
import { filterCommands, type Command } from "./palette";
import { resolve } from "./theme";

const labels = (role: Parameters<typeof navFor>[0]) => navFor(role).flatMap((g) => g.items.map((i) => i.label));

describe("navigation by role", () => {
  it("gives an administrator everything", () => {
    expect(labels("admin")).toEqual(["Overview", "Migrations", "New migration", "Target schemas", "Mapping memory", "Analytics", "Revenue", "Billing", "Team", "Audit log", "Settings"]);
  });

  it("gives an analyst the work and the operations analytics, not revenue, team, audit or settings", () => {
    const list = labels("analyst");
    expect(list).toContain("New migration");
    expect(list).toContain("Analytics");
    for (const hidden of ["Revenue", "Team", "Audit log", "Settings"]) expect(list).not.toContain(hidden);
  });

  it("gives a viewer only the pages that read", () => {
    expect(labels("viewer")).toEqual(["Overview", "Migrations", "Target schemas", "Mapping memory", "Billing"]);
  });

  it("drops a group that has nothing left for the role", () => {
    expect(navFor("viewer").map((g) => g.group)).toEqual(["Work", "Organization"]);
  });
});

describe("canVisit", () => {
  it("allows what the role may use and refuses the rest", () => {
    expect(canVisit("admin", "/app/revenue")).toBe(true);
    expect(canVisit("analyst", "/app/revenue")).toBe(false);
    expect(canVisit("viewer", "/app/analytics")).toBe(false);
    expect(canVisit("viewer", "/app/migrations/new")).toBe(false);
    expect(canVisit("viewer", "/app/migrations/5b0f2a9e")).toBe(true);
    expect(canVisit("analyst", "/app/team")).toBe(false);
    expect(canVisit("viewer", "/app/billing/invoices/abc")).toBe(true);
  });

  it("does not guess about a path it does not know", () => {
    expect(canVisit("viewer", "/app/unknown")).toBe(true);
  });
});

describe("crumbsFor", () => {
  it("names each segment and shows an id as the thing it is", () => {
    expect(crumbsFor("/app/migrations/5b0f2a9e")).toEqual([
      { label: "Migrations", to: "/app/migrations" },
      { label: "Migration", to: "/app/migrations/5b0f2a9e" },
    ]);
    expect(crumbsFor("/app/billing/invoices/abc").map((c) => c.label)).toEqual(["Billing", "Invoice", "Invoice"]);
    expect(crumbsFor("/app")).toEqual([]);
  });
});

describe("filterCommands", () => {
  const icon = (() => null) as unknown as Command["icon"];
  const commands: Command[] = [
    { id: "a", label: "Migrations", keywords: "jobs list", icon, run: () => {} },
    { id: "b", label: "New migration", keywords: "wizard upload start", icon, run: () => {} },
    { id: "c", label: "Audit log", keywords: "events security", icon, run: () => {} },
  ];

  it("returns everything for an empty search", () => {
    expect(filterCommands(commands, "  ")).toHaveLength(3);
  });

  it("matches words of the label and of the keywords", () => {
    expect(filterCommands(commands, "upload").map((c) => c.id)).toEqual(["b"]);
    expect(filterCommands(commands, "sec").map((c) => c.id)).toEqual(["c"]);
  });

  it("puts a label that starts with the text first", () => {
    expect(filterCommands(commands, "migr").map((c) => c.id)).toEqual(["a", "b"]);
  });

  it("needs every word to match", () => {
    expect(filterCommands(commands, "new audit")).toEqual([]);
  });
});

describe("resolve", () => {
  it("follows the system only when asked to", () => {
    expect(resolve("dark", false)).toBe("dark");
    expect(resolve("light", true)).toBe("light");
    expect(resolve("system", true)).toBe("dark");
    expect(resolve("system", false)).toBe("light");
  });
});
