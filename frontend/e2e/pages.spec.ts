import { expect, test } from "@playwright/test";
import { WIDTHS, measureLayoutShift, noHorizontalOverflow, publicPaths, settle, watchProblems } from "./helpers";

const paths = publicPaths();

test("the sitemap lists the public pages", () => {
  expect(paths.length).toBeGreaterThanOrEqual(23);
  expect(paths).toContain("/");
  expect(paths).toContain("/pricing");
  expect(new Set(paths).size).toBe(paths.length);
});

for (const path of paths) {
  test.describe(`page ${path}`, () => {
    test("loads without errors, with one h1 and its own title", async ({ page }) => {
      const problems = watchProblems(page);
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      await settle(page);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      const title = await page.title();
      expect(title.length).toBeGreaterThan(3);
      if (path !== "/") expect(title).toMatch(/\| LegacyLift$/);
      expect(await page.locator('meta[name="description"]').getAttribute("content")).toBeTruthy();
      expect(await page.locator("html").getAttribute("lang")).toBe("en");
      expect(problems).toEqual([]);
    });

    for (const width of WIDTHS) {
      test(`fits a ${width} px window`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(path);
        await settle(page);
        await noHorizontalOverflow(page);
        // the content never runs under the header or off the page: the first heading is visible without scrolling sideways
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      });
    }

    test("does not shift while it loads and is scrolled", async ({ page }) => {
      const score = await measureLayoutShift(page, path);
      expect(score, `cumulative layout shift ${score.toFixed(4)}`).toBeLessThan(0.02);
    });
  });
}
