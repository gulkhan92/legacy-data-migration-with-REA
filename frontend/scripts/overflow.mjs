// Lists the elements that stick out of the window, for finding a horizontal overflow. Usage: node scripts/overflow.mjs /pricing 360
import { chromium } from "@playwright/test";

const [path = "/", width = "360"] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: Number(width), height: 900 } });
await page.goto(`http://127.0.0.1:4173${path}`, { waitUntil: "networkidle" });
const found = await page.evaluate((limit) => {
  const out = [];
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > limit + 0.5) {
      let ancestorClips = false;
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const o = getComputedStyle(p).overflowX;
        if (o === "auto" || o === "hidden" || o === "scroll") { ancestorClips = true; break; }
      }
      if (!ancestorClips) out.push(`${el.tagName.toLowerCase()}.${String(el.className).split(" ").slice(0, 4).join(".")} right=${Math.round(r.right)} text=${(el.textContent || "").slice(0, 40).replace(/\s+/g, " ")}`);
    }
  }
  return out.slice(0, 12);
}, Number(width));
console.log(found.join("\n") || "nothing sticks out");
await browser.close();
