import { expect, test } from "@playwright/test";

test("the home page renders its main sections with live figures", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Move decades of legacy data");
  await expect(page.getByRole("heading", { name: "Demo environment figures" })).toBeVisible();
  await expect(page.getByText("Records migrated")).toBeVisible();
  await page.screenshot({ path: "test-results/home.png", fullPage: false });
});
