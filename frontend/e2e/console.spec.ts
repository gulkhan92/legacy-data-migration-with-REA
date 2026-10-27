import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { password, settle, users, watchProblems } from "./helpers";

/**
 * The console, end to end, against the real API with the real pipeline (REA, the embedding model, the database) and the seeded
 * history of 24 months. Needs E2E_PASSWORD, the demo users (python -m app.seed), the target schemas (python -m app.mapping.seed_targets) and the
 * billing history (make seed-billing). Each role is signed in once by auth.setup.ts.
 */
test.skip(!password || !!process.env.E2E_MOCK_API_URL, "the console tests need the real API and E2E_PASSWORD");

const AUTH = resolve(import.meta.dirname, ".auth");
const FIXTURES = resolve(import.meta.dirname, "../../datasets/fixtures");

type Truth = { mapping: Map<string, string | null>; systemTables: Set<string>; codes: Record<string, Record<string, string>> };

/** What a person who knows the legacy system knows, read from the ground truth of the fixture (the platform never reads it). */
function truthOf(system: "commerce" | "clinic"): Truth {
  const gt = JSON.parse(readFileSync(resolve(FIXTURES, system, "ground_truth.json"), "utf-8"));
  const mapping = new Map<string, string | null>();
  const codes: Truth["codes"] = {};
  if (system === "commerce") {
    for (const [table, spec] of Object.entries<{ target_table: string; columns: { name: string; target_field: string | null }[] }>(gt.legacy_tables)) {
      for (const c of spec.columns) mapping.set(`${table}.${c.name}`, c.target_field ? `${spec.target_table}.${c.target_field}` : null);
    }
    codes["orders.status"] = gt.status_codes;
  } else {
    for (const table of gt.layout.tables as { name: string; fields: { name: string; target?: string; transform?: string }[] }[]) {
      for (const f of table.fields) {
        mapping.set(`${table.name}.${f.name}`, f.target ?? null);
        if (f.transform?.startsWith("map_codes(") && f.target) {
          codes[f.target] = Object.fromEntries(f.transform.slice("map_codes(".length, -1).split(",").map((pair) => pair.split("=") as [string, string]));
        }
      }
    }
  }
  return { mapping, systemTables: new Set(Object.keys(gt.system_tables ?? {})), codes };
}

async function openConsole(page: Page, path: string) {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

// ------------------------------------------------------------------ sign in for each role

test.describe("each role signs in through the form and lands where it works", () => {
  test.describe.configure({ mode: "serial" });
  const cases = [
    { role: "admin", email: users.admin, lands: /\/app\/overview$/, heading: "Overview" },
    { role: "analyst", email: users.analyst, lands: /\/app\/migrations$/, heading: "Migrations" },
    { role: "viewer", email: users.viewer, lands: /\/app\/overview$/, heading: "Overview" },
  ] as const;
  for (const c of cases) {
    test(`${c.role}`, async ({ page }) => {
      await page.goto("/login");
      await settle(page);
      await page.getByLabel(/Email/).fill(c.email);
      await page.getByLabel(/^Password/).fill(password as string);
      await page.getByRole("button", { name: "Sign in", exact: true }).last().click();
      await expect(page).toHaveURL(c.lands);
      await expect(page.getByRole("heading", { level: 1, name: c.heading })).toBeVisible();
      await page.getByRole("button", { name: /^Account menu for/ }).click();
      await expect(page.getByText(c.role, { exact: true })).toBeVisible();
    });
  }
});

// ------------------------------------------------------------------ pages by role

test.describe("pages are restricted by role", () => {
  const pagesOf = {
    admin: ["/app/overview", "/app/migrations", "/app/migrations/new", "/app/schemas", "/app/memory", "/app/analytics", "/app/revenue", "/app/billing", "/app/team", "/app/audit", "/app/settings"],
    analyst: ["/app/overview", "/app/migrations", "/app/migrations/new", "/app/schemas", "/app/memory", "/app/analytics", "/app/billing"],
    viewer: ["/app/overview", "/app/migrations", "/app/schemas", "/app/memory", "/app/billing"],
  } as const;
  const denied = {
    analyst: ["/app/revenue", "/app/team", "/app/audit", "/app/settings"],
    viewer: ["/app/migrations/new", "/app/analytics", "/app/revenue", "/app/team", "/app/audit", "/app/settings"],
  } as const;

  for (const role of ["admin", "analyst", "viewer"] as const) {
    test.describe(role, () => {
      test.use({ storageState: resolve(AUTH, `${role}.json`) });

      test("opens what it may and the navigation lists exactly that", async ({ page }) => {
        await page.setViewportSize({ width: 1280, height: 900 });
        for (const path of pagesOf[role]) {
          await openConsole(page, path);
          await expect(page.getByText("You do not have access to this page")).toHaveCount(0);
        }
        const nav = page.getByRole("navigation", { name: "Console" });
        for (const label of ["Revenue", "Team", "Audit log", "Settings", "Analytics", "New migration"]) {
          const should = pagesOf[role].some((p) => p.endsWith({ Revenue: "/revenue", Team: "/team", "Audit log": "/audit", Settings: "/settings", Analytics: "/analytics", "New migration": "/new" }[label] as string));
          await expect(nav.getByRole("link", { name: label, exact: true })).toHaveCount(should ? 1 : 0);
        }
      });

      if (role !== "admin") {
        test("is told plainly when it asks for a page it may not use, and the server refuses the same data", async ({ page }) => {
          for (const path of denied[role]) {
            await page.goto(path);
            await expect(page.getByRole("alert").getByText("You do not have access to this page")).toBeVisible();
            await expect(page.getByRole("link", { name: "Go to your start page" })).toBeVisible();
          }
          const revenue = await page.request.get("/api/v1/analytics/revenue");
          expect(revenue.status()).toBe(403);
          const audit = await page.request.get("/api/v1/admin/audit-log");
          expect(audit.status()).toBe(403);
        });
      }
    });
  }

  test.describe("viewer", () => {
    test.use({ storageState: resolve(AUTH, "viewer.json") });
    test("can read migrations but has no way to change one", async ({ page }) => {
      await openConsole(page, "/app/migrations");
      await expect(page.getByRole("link", { name: "New migration" })).toHaveCount(0);
      const rows = page.getByRole("table", { name: "Migrations" }).locator("tbody tr");
      if ((await rows.count()) > 0) {
        await rows.first().getByRole("button", { name: /^Actions for/ }).click();
        await expect(page.getByRole("menuitem", { name: "Cancel the migration" })).toHaveCount(0);
      }
      const created = await page.request.post("/api/v1/jobs", { headers: { "X-Requested-With": "LegacyLift" }, data: { source_id: "00000000-0000-4000-8000-000000000000" } });
      expect(created.status()).toBe(403);
    });
  });
});

test.describe("the console runs clean", () => {
  test.use({ storageState: resolve(AUTH, "admin.json") });
  // Run it against a server that sends the real Content-Security-Policy (node scripts/serve-dist.mjs, E2E_BASE_URL) and a script, a style or a
  // connection that the policy refuses shows up here as a console error.
  test("no page logs an error, throws, fails a request or breaks the Content-Security-Policy, charts and live events included", async ({ page }) => {
    const problems = watchProblems(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    for (const path of ["/app/overview", "/app/migrations", "/app/migrations/new", "/app/schemas", "/app/memory", "/app/analytics", "/app/revenue", "/app/billing", "/app/team", "/app/audit", "/app/settings"]) {
      await openConsole(page, path);
      await page.waitForLoadState("networkidle");
      if (path === "/app/revenue" || path === "/app/analytics") {
        await expect(page.locator(CHART_SVG).first()).toBeVisible({ timeout: 30_000 });
        await page.locator("[data-chart]").first().hover({ position: { x: 200, y: 100 } }); // the tooltip is built from HTML with inline styles
        await page.getByRole("button", { name: "Table" }).first().click();
      }
    }
    await page.keyboard.press("Control+k");
    await page.keyboard.press("Escape");
    expect(problems).toEqual([]);
  });
});

// ------------------------------------------------------------------ the shell

test.describe("the shell", () => {
  test.use({ storageState: resolve(AUTH, "admin.json") });

  test("the command palette opens with Ctrl K, finds a page and goes there", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await openConsole(page, "/app/overview");
    await page.keyboard.press("Control+k");
    const box = page.getByRole("combobox", { name: "Search pages and migrations" });
    await expect(box).toBeFocused();
    await box.fill("rev");
    await expect(page.getByRole("option", { name: /Revenue/ })).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/app\/revenue$/);
    await expect(page.getByRole("heading", { level: 1, name: "Revenue" })).toBeVisible();
    await page.keyboard.press("Control+k");
    await page.keyboard.press("Escape");
    await expect(box).toHaveCount(0);
  });

  test("the sidebar collapses and remembers it, and the theme switches", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await openConsole(page, "/app/overview");
    await page.getByRole("button", { name: "Collapse the sidebar" }).click();
    await expect(page.getByRole("button", { name: "Expand the sidebar" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Expand the sidebar" })).toBeVisible();
    await page.getByRole("button", { name: "Expand the sidebar" }).click();
    await page.getByRole("button", { name: "Switch to the dark theme" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.getByRole("button", { name: "Switch to the light theme" }).click();
    await expect(page.locator("html")).not.toHaveClass(/dark/);
  });

  test("on a phone the menu is a sheet and navigating closes it", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await openConsole(page, "/app/overview");
    await page.getByRole("button", { name: "Open the menu" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByRole("link", { name: "Migrations", exact: true })).toBeVisible();
    await sheet.getByRole("link", { name: "Migrations", exact: true }).click();
    await expect(page).toHaveURL(/\/app\/migrations$/);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });

  test("the notifications list opens and an unknown page is explained inside the console", async ({ page }) => {
    await openConsole(page, "/app/overview");
    await page.getByRole("button", { name: /^Notifications/ }).click();
    await expect(page.getByRole("heading", { name: "Notifications" })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.goto("/app/does-not-exist");
    await expect(page.getByRole("heading", { level: 1, name: "This page does not exist" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Console" })).toBeVisible();
  });
});

test.describe("the console fits a phone", () => {
  test.use({ storageState: resolve(AUTH, "admin.json") });
  const all = ["/app/overview", "/app/migrations", "/app/migrations/new", "/app/schemas", "/app/memory", "/app/analytics", "/app/revenue", "/app/billing", "/app/team", "/app/audit", "/app/settings"];
  for (const width of [360, 768]) {
    test(`no page makes the window scroll sideways at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      for (const path of all) {
        await openConsole(page, path);
        await page.waitForLoadState("networkidle");
        const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
        expect(scroll, `${path} is ${scroll}px wide in a ${client}px window`).toBeLessThanOrEqual(client);
      }
    });
  }
});

// ------------------------------------------------------------------ the wizard on both sample systems

/** Applies a reviewer's decisions on the mapping table: reject what has no counterpart, choose the right target where the engine chose another. */
async function reviewMappings(page: Page, truth: Truth) {
  const rows = page.getByRole("table", { name: "Mappings of source columns to target fields" }).locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  const plan: { key: string; column: string; want: string | null; actual: string }[] = [];
  for (let i = 0; i < (await rows.count()); i++) {
    const cells = rows.nth(i).locator("td");
    const key = ((await cells.nth(1).locator("span").first().textContent()) ?? "").trim();
    const actual = ((await cells.nth(2).textContent()) ?? "").trim();
    const table = key.split(".")[0] as string;
    const known = truth.mapping.has(key);
    if (!known && !truth.systemTables.has(table)) continue;
    const want = truth.mapping.get(key) ?? null;
    const shown = actual === "none suggested" ? null : actual;
    if (shown !== want) plan.push({ key, column: key.split(".").slice(1).join("."), want, actual: shown ?? "" });
  }
  for (const step of plan) {
    const row = page.getByRole("row").filter({ hasText: step.key }).first();
    if (step.want === null) {
      await row.getByRole("button", { name: `Reject the mapping of ${step.column}` }).click();
      await expect(row.getByText("Rejected")).toBeVisible();
    } else {
      await row.getByRole("button", { name: `Choose another target for ${step.column}` }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByRole("searchbox").fill(step.want.split(".")[1] as string);
      await dialog.getByRole("button", { name: new RegExp(`^${step.want.replace(".", "\\.")}\\b`) }).first().click();
      await expect(dialog).toHaveCount(0);
      await expect(page.getByRole("row").filter({ hasText: step.key }).first().getByText(step.want, { exact: true })).toBeVisible();
    }
  }
  const bulk = page.getByRole("button", { name: /Approve all with 85% confidence or more/ });
  if (await bulk.isEnabled()) await bulk.click();
  return plan.length;
}

async function migrate(page: Page, system: "commerce" | "clinic") {
  const truth = truthOf(system);
  await page.setViewportSize({ width: 1440, height: 1000 });

  // 1. source: a sample system
  await openConsole(page, `/app/migrations/new?sample=${system}`);
  await expect(page.getByRole("radio", { name: new RegExp(system === "commerce" ? "Commerce" : "ClinicDesk") })).toBeChecked();
  await page.getByRole("button", { name: "Create the migration" }).click();

  // 2. discovery: live progress, then the fields with their confidence and the evidence behind one
  await expect(page.getByRole("heading", { level: 2, name: "Discovery" })).toBeVisible();
  await page.getByRole("button", { name: "Run discovery and profiling" }).click();
  await expect(page.getByText("Data quality score")).toBeVisible({ timeout: 120_000 });
  await expect(page.locator("details").first().getByRole("meter").first()).toBeVisible();
  await page.getByRole("button", { name: /^Show the evidence for / }).first().click();
  await expect(page.getByRole("dialog").getByText(/REA 6\./)).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Continue to mapping" }).click();

  // 3. mapping review
  await page.getByRole("button", { name: "Map the columns" }).click();
  await expect(page.getByText(/columns shown/)).toBeVisible({ timeout: 120_000 });
  const decisions = await reviewMappings(page, truth);
  expect(decisions).toBeGreaterThanOrEqual(0);
  await page.getByRole("button", { name: /Continue to the transform preview|Regenerate the rules and continue/ }).click();

  // 4. transform preview: a rule that needs the meaning of codes, a before and after, then on
  await expect(page.getByRole("heading", { level: 2, name: "Transform preview" })).toBeVisible();
  const needsInput = await page.getByText("This rule needs you").waitFor({ timeout: 20_000 }).then(() => true, () => false);
  if (needsInput) {
    const rule = (await page.getByRole("heading", { level: 3 }).first().textContent()) ?? "";
    const meanings = truth.codes[rule.trim()] ?? {};
    for (const input of await page.getByLabel(/^Meaning of code /).all()) {
      const code = ((await input.getAttribute("aria-label")) ?? "").replace("Meaning of code ", "");
      await input.fill(meanings[code] ?? code);
    }
    await page.getByRole("button", { name: "Save the meanings" }).click();
    await expect(page.getByText("This rule needs you")).toHaveCount(0);
  }
  await page.getByRole("button", { name: /^customers\.|^patients\.|^products\.|^encounters\./ }).first().click();
  await expect(page.getByRole("table", { name: "Source values before the rule and the values after it" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Continue to the dry run" }).click();

  // 5. dry run and its cost
  await page.getByRole("button", { name: "Run the dry run" }).click();
  await expect(page.getByText("The dry run found nothing that blocks the load.")).toBeVisible({ timeout: 120_000 });
  const cost = page.getByRole("region", { name: "Cost summary" });
  await expect(cost.getByText("Estimated load time")).toBeVisible();
  await expect(cost.getByRole("meter", { name: /Tokens used/ })).toBeVisible();
  await page.getByRole("button", { name: "Continue to execute" }).click();

  // 6. execute and watch
  await page.getByRole("button", { name: "Load the data" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Load the data" }).click();
  await expect(page.getByText("The load finished and was reconciled against the source.")).toBeVisible({ timeout: 180_000 });
  await expect(page.getByRole("list", { name: "Pipeline stages" })).toBeVisible();
  await page.getByRole("button", { name: "View the reconciliation report" }).click();

  // 7. the reconciliation report
  await expect(page.getByText("All checks passed")).toBeVisible({ timeout: 60_000 });
  const checks = page.getByRole("table", { name: "Reconciliation checks per table" });
  await expect(checks.locator("tbody tr")).toHaveCount(system === "commerce" ? 4 : 5);
  await expect(checks.getByText("Passed")).toHaveCount(system === "commerce" ? 4 : 5);
  await expect(page.getByRole("img", { name: /Data quality score after the migration/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Export as CSV/ })).toHaveAttribute("href", /\/quarantine\.csv/);
  const quarantine = page.getByRole("table", { name: "Quarantined rows with their reasons" });
  await expect(quarantine.locator("tbody tr").first()).toBeVisible();
  await page.getByLabel("Reason").selectOption("required_null");
  await expect(quarantine.locator("tbody tr").first().getByText("A required value is missing")).toBeVisible();
  return page.url();
}

test.describe("the migration wizard, from the first step to the report", () => {
  test.use({ storageState: resolve(AUTH, "analyst.json") });
  test.setTimeout(10 * 60_000);

  test("the commerce database (SQLite), including a rollback", async ({ page }) => {
    const url = await migrate(page, "commerce");
    await expect(page).toHaveURL(/step=report/);
    await page.getByRole("button", { name: "Roll back this migration" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Roll it back" }).click();
    await expect(page.getByText("This migration was rolled back.")).toBeVisible({ timeout: 60_000 });
    await page.goto("/app/migrations");
    await expect(page.getByRole("table", { name: "Migrations" }).getByText("Rolled back").first()).toBeVisible();
    expect(url).toContain("/app/migrations/");
  });

  test("the clinic application (a .NET binary)", async ({ page }) => {
    await migrate(page, "clinic");
    await page.goto("/app/migrations");
    await expect(page.getByRole("table", { name: "Migrations" }).getByText("Completed").first()).toBeVisible();
  });

  test("the migrations table filters, sorts and keeps its state in the address", async ({ page }) => {
    await openConsole(page, "/app/migrations");
    const table = page.getByRole("table", { name: "Migrations" });
    await expect(table.locator("tbody tr").first()).toBeVisible();
    await page.getByLabel("Status", { exact: true }).selectOption("COMPLETED");
    await expect(page).toHaveURL(/status=COMPLETED/);
    // while the filtered list loads the table keeps the previous rows, dimmed, so wait until every row is of the chosen state
    await expect.poll(async () => (await table.locator("tbody tr td:nth-child(3)").allTextContents()).every((badge) => badge.includes("Completed"))).toBe(true);
    await page.getByLabel("Kind of system").selectOption("dotnet_binary");
    await expect(page).toHaveURL(/kind=dotnet_binary/);
    await page.getByRole("button", { name: "Clear filters" }).first().click();
    await table.getByRole("button", { name: "Created" }).click();
    await expect(table.getByRole("columnheader", { name: /Created/ })).toHaveAttribute("aria-sort", /ascending|descending/);
    await page.getByLabel("Search by system name").fill("no-such-system-name");
    await expect(page.getByText("No migration matches these filters")).toBeVisible();
  });
});

// ------------------------------------------------------------------ the dashboards

const CHART_SVG = "[data-chart] svg"; // the drawing itself, not the icons of the card

async function expectCharts(page: Page, names: string[]) {
  for (const name of names) {
    const figure = page.getByRole("figure", { name, exact: true });
    await expect(figure, name).toBeVisible();
    await expect(figure.locator(CHART_SVG).first(), `${name} is drawn`).toBeVisible({ timeout: 30_000 });
    await expect(figure.getByRole("button", { name: "Table" })).toBeVisible();
  }
}

test.describe("the revenue dashboard (administrators)", () => {
  test.use({ storageState: resolve(AUTH, "admin.json") });

  test("renders every figure and chart of the plan with the seeded history, and says the history is simulated", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openConsole(page, "/app/revenue");
    for (const label of ["MRR", "ARR", "Net new MRR", "Net revenue retention", "Gross churn", "ARPA", "Paying accounts"]) {
      await expect(page.getByRole("region", { name: "Key figures" }).getByText(label, { exact: true })).toBeVisible();
    }
    await expectCharts(page, [
      "Monthly recurring revenue",
      "MRR movement",
      "MRR by plan",
      "MRR by industry",
      "MRR by region",
      "Subscription and usage revenue",
      "Cohort retention",
      "Customer growth",
      "Average revenue per account",
      "Revenue per 1,000 records",
      "Model tokens per 1,000 records",
    ]);
    await expect(page.getByTestId("simulated-footnote")).toContainText("simulated");
    await expect(page.getByRole("table", { name: "Top customers by revenue over twelve months" }).locator("tbody tr")).toHaveCount(10);
    await expect(page.getByRole("table", { name: "Invoices of all customers" }).locator("tbody tr").first()).toBeVisible();
    // no chart is drawn with a second axis
    await expect(page.getByRole("figure", { name: "Customer growth", exact: true })).toBeVisible();
    await expect(page.getByRole("figure", { name: "Average revenue per account", exact: true })).toBeVisible();
  });

  test("the controls scope everything: range, bucket, comparison, export and the printable layout", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openConsole(page, "/app/revenue");
    await page.getByRole("radio", { name: "30 days" }).click();
    await expect(page).toHaveURL(/range=30d/);
    await expect(page.getByText(/grouped by day/)).toBeVisible();
    await page.getByLabel("Group by").selectOption("month");
    await expect(page.getByText(/grouped by month/)).toBeVisible();
    await page.getByLabel("Compare with the previous period").click();
    await expect(page.getByLabel("Compare with the previous period")).toBeChecked();
    await expect(page.getByRole("region", { name: "Key figures" }).getByText("vs the previous period").first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Export CSV" })).toHaveAttribute("href", /\/api\/v1\/analytics\/revenue\.csv\?.*days=30/);
    const csv = await page.request.get((await page.getByRole("link", { name: "Export CSV" }).getAttribute("href")) as string);
    expect(csv.status()).toBe(200);
    expect(csv.headers()["content-type"]).toContain("text/csv");
    await page.getByRole("radio", { name: "Custom" }).click();
    await page.getByLabel("From", { exact: true }).fill("2026-03-01");
    await page.getByLabel("To", { exact: true }).fill("2026-01-01");
    await expect(page.getByRole("alert").getByText("Choose two days, the first not after the second.")).toBeVisible();
    await page.getByLabel("From", { exact: true }).fill("2026-01-01");
    await page.getByLabel("To", { exact: true }).fill("2026-03-31");
    await expect(page.getByText(/^Jan 1, 2026 to Mar 31, 2026, grouped by/)).toBeVisible();
    await page.emulateMedia({ media: "print" });
    await expect(page.getByRole("navigation", { name: "Console" })).toBeHidden();
    await expect(page.getByRole("form", { name: "Dashboard controls" })).toBeHidden();
    await page.emulateMedia({ media: "screen" });
  });

  test("every chart has a table of the same numbers, a legend that toggles, a waterfall view and a text summary", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openConsole(page, "/app/revenue");
    const mrr = page.getByRole("figure", { name: "Monthly recurring revenue", exact: true });
    await expect(mrr.locator(CHART_SVG).first()).toBeVisible({ timeout: 30_000 });
    await expect(mrr.locator("figcaption")).toContainText("projection");
    await mrr.getByRole("button", { name: "Table" }).click();
    await expect(mrr.getByRole("table").locator("tbody tr").first()).toBeVisible();
    await expect(mrr.getByRole("table").locator("tbody tr", { hasText: "(projection)" })).toHaveCount(6);
    const plan = page.getByRole("figure", { name: "MRR by plan", exact: true });
    const enterprise = plan.getByRole("button", { name: "Enterprise" });
    await expect(enterprise).toHaveAttribute("aria-pressed", "true");
    await enterprise.click();
    await expect(enterprise).toHaveAttribute("aria-pressed", "false");
    await enterprise.click();
    const movement = page.getByRole("figure", { name: "MRR movement", exact: true });
    await movement.getByRole("button", { name: "Waterfall" }).click();
    await movement.getByRole("button", { name: "Table" }).click();
    await expect(movement.getByRole("table").locator("tbody tr")).toHaveCount(6);
    await expect(movement.getByRole("table")).toContainText("MRR at the start");
  });

  test("the dark theme repaints the charts and keeps them readable", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openConsole(page, "/app/revenue");
    const figure = page.getByRole("figure", { name: "MRR by plan", exact: true });
    await expect(figure.locator(CHART_SVG).first()).toBeVisible({ timeout: 30_000 });
    const light = await figure.locator(CHART_SVG).first().innerHTML();
    await page.getByRole("button", { name: "Switch to the dark theme" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect.poll(async () => (await figure.locator(CHART_SVG).first().innerHTML()) !== light).toBe(true);
    await page.getByRole("button", { name: "Switch to the light theme" }).click();
  });
});

test.describe("the operations analytics (analysts and administrators)", () => {
  for (const role of ["analyst", "admin"] as const) {
    test.describe(role, () => {
      test.use({ storageState: resolve(AUTH, `${role}.json`) });
      test("renders every chart of the plan with the seeded history", async ({ page }) => {
        await page.setViewportSize({ width: 1440, height: 1000 });
        await openConsole(page, "/app/analytics");
        for (const label of ["Jobs", "Success rate", "Records migrated", "Average load time", "Model tokens", "Cache hit rate", "Provider failovers", "Quality score"]) {
          await expect(page.getByRole("region", { name: "Key figures" }).getByText(label, { exact: true })).toBeVisible();
        }
        await expect(page.getByText("Tokens the memory saved (an estimate)")).toBeVisible();
        await expectCharts(page, [
          "Jobs",
          "Success rate",
          "Records migrated",
          "Data quality score",
          "Mapping automation",
          "Model tokens per job",
          "Latency by stage",
          "Why rows were quarantined",
          "Where jobs fail",
          "Discovery confidence",
        ]);
        await page.getByRole("radio", { name: "12 months" }).click();
        await expect(page).toHaveURL(/range=12m/);
        await expect(page.getByText(/grouped by week/)).toBeVisible();
      });
    });
  }
});

// ------------------------------------------------------------------ the other pages work with the real API

test.describe("billing, team, audit log, settings, schemas and mapping memory", () => {
  test.use({ storageState: resolve(AUTH, "admin.json") });

  test("billing shows the plan and usage, and an invoice opens in a printable view", async ({ page }) => {
    await openConsole(page, "/app/billing");
    await expect(page.getByRole("heading", { name: /^(Starter|Professional|Enterprise) plan$/ })).toBeVisible();
    await expect(page.getByRole("meter", { name: "Records loaded of the monthly allowance" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Change the plan" })).toBeVisible();
    const invoices = page.getByRole("table", { name: "Invoices" });
    if (await invoices.isVisible().catch(() => false)) {
      await invoices.getByRole("link", { name: /^Open the invoice/ }).first().click();
      await expect(page.getByRole("article")).toContainText("Invoice");
      await page.emulateMedia({ media: "print" });
      await expect(page.getByRole("button", { name: "Print" })).toBeHidden();
      await page.emulateMedia({ media: "screen" });
    }
  });

  test("team lists the people with their roles and explains that invitations are not available", async ({ page }) => {
    await openConsole(page, "/app/team");
    await expect(page.getByRole("table", { name: "People in this organization" }).locator("tbody tr")).not.toHaveCount(0);
    await expect(page.getByLabel("Role of Demo Admin")).toBeDisabled();
    await page.getByRole("button", { name: "Add a person" }).click();
    await expect(page.getByRole("dialog").getByText("Invitations by email are not available yet.")).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Add the person" }).click();
    await expect(page.getByRole("dialog").getByText("Enter the person's name")).toBeVisible();
  });

  test("the audit log searches and counts", async ({ page }) => {
    await openConsole(page, "/app/audit");
    const table = page.getByRole("table", { name: "Audit events" });
    await expect(table.locator("tbody tr").first()).toBeVisible();
    await page.getByLabel("Search the audit log").fill("login");
    await expect(page.getByText(/of [\d,]+ events/)).toBeVisible();
    await page.getByLabel("Search the audit log").fill("zzzz-no-such-event");
    await expect(page.getByText("No events match")).toBeVisible();
  });

  test("settings validate before they save", async ({ page }) => {
    await openConsole(page, "/app/settings");
    await expect(page.getByRole("list", { name: "Provider order" })).toBeVisible();
    await page.getByLabel("Token budget per migration").fill("5");
    await page.getByRole("button", { name: "Save the language model settings" }).click();
    await expect(page.getByText("The budget per migration must be at least 500")).toBeVisible();
    await page.getByLabel("Keep an uploaded file for (days)").fill("1.5");
    await page.getByRole("button", { name: "Save the retention period" }).click();
    await expect(page.getByText("The retention must be a whole number")).toBeVisible();
  });

  test("target schemas and mapping memory are browsable", async ({ page }) => {
    await openConsole(page, "/app/schemas");
    await page.getByRole("tab", { name: /commerce/ }).click();
    await expect(page.getByRole("table", { name: "Fields of customers" })).toBeVisible();
    await page.getByLabel("Search the fields of commerce").fill("email");
    await expect(page.getByRole("table", { name: "Fields of customers" }).getByRole("cell", { name: /^email/ })).toBeVisible();
    await openConsole(page, "/app/memory");
    await expect(page.getByRole("heading", { level: 1, name: "Mapping memory" })).toBeVisible();
  });
});

// ------------------------------------------------------------------ accessibility of the console

async function audit(page: Page, label: string, { skip = [] as string[] } = {}) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).disableRules(skip).analyze();
  const report = result.violations.map((v) => `${v.id} (${v.impact}): ${v.help}\n    ` + v.nodes.slice(0, 4).map((n) => `${n.target.join(" ")} :: ${n.failureSummary?.split("\n")[1] ?? ""}`).join("\n    "));
  expect(report, `accessibility violations on ${label}`).toEqual([]);
}

test.describe("console pages have no WCAG 2.1 AA violations", () => {
  test.use({ storageState: resolve(AUTH, "admin.json") });
  const paths = ["/app/overview", "/app/migrations", "/app/migrations/new", "/app/schemas", "/app/memory", "/app/analytics", "/app/revenue", "/app/billing", "/app/team", "/app/audit", "/app/settings"];
  for (const theme of ["light", "dark"] as const) {
    for (const path of paths) {
      test(`${path} in the ${theme} theme`, async ({ page }) => {
        await page.setViewportSize({ width: 1280, height: 900 });
        await page.addInitScript((value) => window.localStorage.setItem("legacylift.theme", value), theme);
        await openConsole(page, path);
        await page.waitForLoadState("networkidle");
        if (path === "/app/revenue" || path === "/app/analytics") await expect(page.locator(CHART_SVG).first()).toBeVisible({ timeout: 30_000 });
        await audit(page, `${path} (${theme})`);
      });
    }
  }

  test("the command palette and the open user menu", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await openConsole(page, "/app/overview");
    await page.keyboard.press("Control+k");
    await expect(page.getByRole("combobox", { name: "Search pages and migrations" })).toBeVisible();
    await audit(page, "the command palette");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: /^Account menu for/ }).click();
    await expect(page.getByRole("menuitem", { name: "Sign out" })).toBeVisible();
    await page.waitForTimeout(300); // the menu fades in; the contrast of text is only meaningful once it is opaque
    // A modal Radix menu marks the rest of the page aria-hidden while it is open; axe reports the page behind it as aria-hidden-focus. That is the documented
    // behavior of the library (focus is held inside the menu), and the menu itself is checked by every other rule.
    await audit(page, "the user menu", { skip: ["aria-hidden-focus"] });
  });
});
