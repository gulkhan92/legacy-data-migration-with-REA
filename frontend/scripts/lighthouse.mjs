// Runs Lighthouse (desktop) on the public pages of the built site and fails when the targets of the plan are missed:
// performance 90 or more, accessibility 95 or more.
// Usage: node scripts/lighthouse.mjs [baseUrl] [chromePath]
//   The site must be served in a production like way (scripts/serve-dist.mjs) so that compression and caching are measured.
import { mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";
import * as chromeLauncher from "chrome-launcher";
import lighthouse from "lighthouse";
import desktopConfig from "lighthouse/core/config/desktop-config.js";

const base = process.argv[2] ?? "http://127.0.0.1:4174";
const chromePath = process.argv[3] ?? process.env.CHROME_PATH ?? chromium.executablePath();
const pages = ["/", "/platform", "/solutions/healthcare", "/pricing", "/security", "/resources", "/resources/articles/reconciliation-method", "/company", "/contact", "/login"];
const TARGET = { performance: 90, accessibility: 95 };

mkdirSync("lighthouse-reports", { recursive: true });
const chrome = await chromeLauncher.launch({ chromePath, chromeFlags: ["--headless=new", "--no-sandbox", "--disable-gpu"] });
const rows = [];
let failed = false;
try {
  for (const path of pages) {
    const run = await lighthouse(base + path, { port: chrome.port, output: "json", logLevel: "error", onlyCategories: ["performance", "accessibility", "best-practices", "seo"] }, desktopConfig);
    const lhr = run?.lhr;
    if (!lhr) throw new Error(`no result for ${path}`);
    const score = (id) => Math.round((lhr.categories[id]?.score ?? 0) * 100);
    const metric = (id) => lhr.audits[id]?.numericValue ?? 0;
    const row = {
      page: path, performance: score("performance"), accessibility: score("accessibility"), bestPractices: score("best-practices"), seo: score("seo"),
      lcp: Math.round(metric("largest-contentful-paint")), cls: Number(metric("cumulative-layout-shift").toFixed(3)), tbt: Math.round(metric("total-blocking-time")), si: Math.round(metric("speed-index")),
    };
    rows.push(row);
    writeFileSync(`lighthouse-reports/${path === "/" ? "home" : path.slice(1).replaceAll("/", "_")}.json`, JSON.stringify({ ...row, failing: Object.values(lhr.audits).filter((a) => a.score !== null && a.score < 0.9 && a.scoreDisplayMode === "binary").map((a) => a.id) }, null, 1));
    if (row.performance < TARGET.performance || row.accessibility < TARGET.accessibility) failed = true;
  }
} finally {
  await chrome.kill();
}
console.table(rows);
writeFileSync("lighthouse-reports/summary.json", JSON.stringify(rows, null, 1));
if (failed) {
  console.error(`Targets missed: performance ${TARGET.performance}, accessibility ${TARGET.accessibility}`);
  process.exit(1);
}
console.log(`Targets met on ${rows.length} pages: performance >= ${TARGET.performance}, accessibility >= ${TARGET.accessibility}`);
