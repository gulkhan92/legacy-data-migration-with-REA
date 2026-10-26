import { expect, test } from "@playwright/test";
import { publicPaths, settle } from "./helpers";

/** Every link found on every public page (header, mega menus, footer, content) must lead somewhere that exists. */
test("all internal links work, and every anchor has its target", async ({ page, request, baseURL }) => {
  test.setTimeout(240_000);
  const targets = new Map<string, Set<string>>(); // path -> pages that link to it
  const anchors = new Map<string, Set<string>>(); // path -> ids linked to
  const external: { href: string; page: string; rel: string | null; target: string | null }[] = [];

  for (const path of publicPaths()) {
    await page.goto(path);
    await settle(page);
    // open every mega menu so that its links are in the page
    for (const trigger of await page.locator('nav[aria-label="Main"] button[aria-expanded]').all()) {
      await trigger.click();
      await page.waitForTimeout(80);
    }
    const links = await page.locator("a[href]").evaluateAll((els) => els.map((a) => ({ href: a.getAttribute("href") ?? "", rel: a.getAttribute("rel"), target: a.getAttribute("target") })));
    for (const { href, rel, target } of links) {
      if (/^(https?:)?\/\//.test(href)) {
        external.push({ href, page: path, rel, target });
        continue;
      }
      if (href.startsWith("mailto:") || href.startsWith("tel:")) continue;
      const url = new URL(href, `${baseURL}${path}`);
      const key = url.pathname;
      (targets.get(key) ?? targets.set(key, new Set()).get(key))?.add(path);
      if (url.hash.length > 1) (anchors.get(key) ?? anchors.set(key, new Set()).get(key))?.add(decodeURIComponent(url.hash.slice(1)));
    }
  }
  expect(targets.size).toBeGreaterThan(20);

  const broken: string[] = [];
  for (const [path, from] of targets) {
    const response = await request.get(path);
    if (response.status() !== 200) broken.push(`${path} answers ${response.status()} (linked from ${[...from].slice(0, 2).join(", ")})`);
  }
  expect(broken).toEqual([]);

  // a link to a page that renders the "not found" screen is a broken link too
  const missing: string[] = [];
  for (const [path] of targets) {
    if (path === "/docs" || path.startsWith("/api/")) continue; // served by the API, checked above
    await page.goto(path);
    await settle(page);
    const heading = (await page.getByRole("heading", { level: 1 }).first().textContent()) ?? "";
    if (/does not exist|page not found/i.test(heading)) missing.push(path);
    for (const id of anchors.get(path) ?? []) {
      if ((await page.locator(`[id="${id}"]`).count()) === 0) missing.push(`${path}#${id}`);
    }
  }
  expect(missing).toEqual([]);

  // links to other sites open safely
  for (const link of external) {
    if (link.target === "_blank") expect(link.rel ?? "", `${link.href} on ${link.page}`).toMatch(/noopener/);
  }
});

test("the footer, the header and the utility bar are on every page", async ({ page }) => {
  for (const path of ["/", "/platform", "/pricing", "/resources/articles/how-discovery-works", "/legal/terms", "/nope"]) {
    await page.goto(path);
    await settle(page);
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Utility" })).toBeVisible();
    await expect(page.getByRole("contentinfo")).toBeVisible();
    await expect(page.getByRole("contentinfo")).toContainText("demonstration project");
  }
});

test("an address that does not exist shows the not found page, with a 200 shell and no indexing", async ({ page }) => {
  await page.goto("/this/does/not/exist");
  await settle(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page does not exist");
  expect(await page.locator('meta[name="robots"]').getAttribute("content")).toContain("noindex");
  await page.goto("/solutions/not-an-industry");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page does not exist");
  await page.goto("/resources/articles/not-an-article");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page does not exist");
  await page.goto("/resources/docs/not-a-doc");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page does not exist");
});
