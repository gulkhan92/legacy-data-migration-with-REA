// Takes full page screenshots of the public pages at a few widths into test-results/screens, for a visual review.
// Usage: node scripts/screens.mjs [baseUrl]   (the built site must be running, see playwright.config.ts)
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://127.0.0.1:4173";
const pages = ["/", "/platform", "/solutions/healthcare", "/pricing", "/security", "/resources", "/resources/articles/reconciliation-method", "/resources/docs/security", "/company", "/contact", "/login", "/status", "/nope"];
const widths = [1280, 375];
mkdirSync("test-results/screens", { recursive: true });
const browser = await chromium.launch();
for (const width of widths) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  const page = await context.newPage();
  for (const path of pages) {
    await page.goto(base + path, { waitUntil: "networkidle" });
    await page.waitForTimeout(400);
    const name = (path === "/" ? "home" : path.slice(1).replaceAll("/", "_")) + `-${width}.png`;
    await page.screenshot({ path: `test-results/screens/${name}`, fullPage: true });
  }
  await context.close();
}
await browser.close();
console.log("done");
