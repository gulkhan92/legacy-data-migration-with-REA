import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, type Page } from "@playwright/test";

/** The public pages, read from the sitemap so that a page added there is covered at once. */
export function publicPaths(): string[] {
  const xml = readFileSync(resolve(import.meta.dirname, "../public/sitemap.xml"), "utf8");
  return [...xml.matchAll(/<loc>https:\/\/legacylift\.example([^<]*)<\/loc>/g)].map((m) => m[1] ?? "/");
}

export const WIDTHS = [360, 768, 1280, 1920] as const;

/** Collects what the browser would show a developer: console errors, uncaught exceptions and failed requests. */
export function watchProblems(page: Page): string[] {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => problems.push(`exception: ${error.message}`));
  page.on("requestfailed", (request) => problems.push(`request failed: ${request.url()}`));
  page.on("response", (response) => {
    if (response.status() >= 400) problems.push(`HTTP ${response.status()}: ${response.url()}`);
  });
  return problems;
}

export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

export async function noHorizontalOverflow(page: Page): Promise<void> {
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(scroll, `the page is ${scroll}px wide in a ${client}px window`).toBeLessThanOrEqual(client);
}

/** The layout shift score of everything that happens while `action` runs, plus a scroll through the whole page. */
export async function measureLayoutShift(page: Page, url: string): Promise<number> {
  await page.addInitScript(() => {
    (window as unknown as { __cls: number }).__cls = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) {
        if (!entry.hadRecentInput) (window as unknown as { __cls: number }).__cls += entry.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.goto(url);
  await settle(page);
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  for (let y = 0; y < height; y += 600) {
    await page.mouse.wheel(0, 600);
    await page.waitForTimeout(60);
  }
  await page.waitForTimeout(500);
  return page.evaluate(() => (window as unknown as { __cls: number }).__cls);
}

/** The password of the demo users, given when the demo data was seeded. Tests that sign in are skipped without it. */
export const password = process.env.E2E_PASSWORD;
export const users = { admin: "admin@legacylift.example", analyst: "analyst@legacylift.example", viewer: "viewer@legacylift.example" } as const;
