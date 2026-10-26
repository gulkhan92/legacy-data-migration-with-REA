// Fails when a source file contains the long dash characters (a team standard: use a comma, a colon, parentheses or a hyphen).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";
// The two long dash characters, written as character codes so that this file does not contain them.
const LONG_DASH = new RegExp(`[${String.fromCharCode(0x2013, 0x2014)}]`);

const root = resolve(import.meta.dirname, "..");
const skip = new Set(["node_modules", "dist", "generated", "playwright-report", "test-results", "lighthouse-reports", ".vite"]);
const extensions = new Set([".ts", ".tsx", ".css", ".html", ".md", ".json", ".mjs", ".js", ".svg", ".xml", ".txt", ".webmanifest"]);
const bad = [];

function walk(dir) {
  for (const name of readdirSync(dir)) {
    if (skip.has(name) || name === "package-lock.json") continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full);
    else if (extensions.has(extname(name))) {
      const text = readFileSync(full, "utf8");
      if (LONG_DASH.test(text)) bad.push(full.slice(root.length + 1));
    }
  }
}
walk(root);
if (bad.length) {
  console.error("Long dash found in:\n  " + bad.join("\n  "));
  process.exit(1);
}
console.log("No long dash characters.");
