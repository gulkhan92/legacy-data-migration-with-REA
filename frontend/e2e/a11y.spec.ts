import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { publicPaths, settle } from "./helpers";

/** WCAG 2.1 A and AA rules, including color contrast. A violation lists the rule, the elements and how to fix it. */
async function audit(page: Page, label: string, { skip = [] as string[] } = {}) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).disableRules(skip).analyze();
  const report = result.violations.map((v) => `${v.id} (${v.impact}): ${v.help}\n    ` + v.nodes.slice(0, 4).map((n) => `${n.target.join(" ")} :: ${n.failureSummary?.split("\n")[1] ?? ""}`).join("\n    "));
  expect(report, `accessibility violations on ${label}`).toEqual([]);
}

for (const width of [1280, 375]) {
  for (const path of [...publicPaths(), "/login"]) {
    test(`${path} has no WCAG 2.1 AA violations at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(path);
      await settle(page);
      await audit(page, `${path} at ${width}px`);
    });
  }
}

test("the open mega menus have no violations", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await settle(page);
  for (const name of ["Platform", "Solutions", "Resources", "Company"]) {
    await page.getByRole("button", { name }).click();
    await page.waitForTimeout(250);
    // Radix renders a focus proxy (aria-hidden, focusable) next to an open menu so that Tab moves into it; axe reports it as aria-hidden-focus. It is Radix's
    // documented behavior (radix-ui/primitives, navigation menu), the menu itself is checked by every other rule, and the keyboard behavior is tested in flows.spec.ts.
    await audit(page, `the ${name} menu`, { skip: ["aria-hidden-focus"] });
    await page.keyboard.press("Escape");
  }
});

test("the mobile drawer has no violations", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto("/");
  await settle(page);
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Solutions" }).click();
  await page.waitForTimeout(300);
  await audit(page, "the mobile drawer");
});

test("the FAQ, the product tour and the calculator have no violations when used", async ({ page }) => {
  await page.goto("/");
  await settle(page);
  await page.getByRole("button", { name: "Does LegacyLift run my old software?" }).click();
  await page.getByRole("tab", { name: "Mapping" }).click();
  await page.getByRole("tab", { name: "Reconciliation" }).click();
  await page.waitForTimeout(300);
  await audit(page, "the home page with the FAQ and the tour open");
  await page.goto("/pricing");
  await settle(page);
  await page.getByLabel("Records loaded per month").fill("lots");
  await page.waitForTimeout(500);
  await audit(page, "the calculator with an invalid input");
});

test("forms with errors have no violations", async ({ page }) => {
  await page.goto("/contact");
  await settle(page);
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByText("Enter your email address")).toBeVisible();
  await audit(page, "the contact form with errors");
  await page.goto("/login");
  await settle(page);
  await page.getByRole("button", { name: "Sign in", exact: true }).last().click();
  await expect(page.getByText("Enter your password")).toBeVisible();
  await audit(page, "the login form with errors");
});
