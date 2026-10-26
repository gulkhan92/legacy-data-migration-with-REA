// Phone width screenshots of the home page (hero, metric band, open menu) into test-results/, for a visual review. Usage: node scripts/mobile-crops.mjs
import { chromium } from "@playwright/test";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 375, height: 800 } });
await page.goto("http://127.0.0.1:4173/", { waitUntil: "networkidle" });
await page.screenshot({ path: "test-results/m-hero.png" });
await page.getByRole("region", { name: "Demo environment figures" }).scrollIntoViewIfNeeded();
await page.waitForTimeout(1500);
await page.screenshot({ path: "test-results/m-metrics.png" });
await page.getByRole("button", { name: "Open menu" }).click();
await page.getByRole("dialog").getByRole("button", { name: "Solutions" }).click();
await page.waitForTimeout(400);
await page.screenshot({ path: "test-results/m-drawer.png" });
await browser.close();
