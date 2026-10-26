// Captures product screenshots from the running application, so the images on the site are authentic and can be regenerated.
//
//   node scripts/capture_screenshots.ts [--base http://127.0.0.1:5173] [--out frontend/public/images/screenshots]
//
// Needs the site (and its API) running, and Playwright's browser (`npx playwright install chromium` in frontend/).
// Console pages need a signed in demo user: set SCREENSHOT_EMAIL and SCREENSHOT_PASSWORD (the account you created with `python -m app.seed`).
// Written to run directly with Node (type stripping), so it uses only syntax that can be erased.
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "../frontend/node_modules/@playwright/test/index.mjs";
import sharp from "../frontend/node_modules/sharp/dist/index.cjs";

type Shot = { name: string; path: string; clip?: { x: number; y: number; width: number; height: number }; wait?: string };

const here = dirname(fileURLToPath(import.meta.url));
const args = new Map<string, string>();
for (let i = 2; i < process.argv.length; i += 2) args.set(String(process.argv[i]).replace(/^--/, ""), String(process.argv[i + 1]));
const base = args.get("base") ?? "http://127.0.0.1:5173";
const out = resolve(here, "..", args.get("out") ?? "frontend/public/images/screenshots");

// Public pages exist now. The console pages are added here as the console is built (the wizard steps, the dashboards, billing), each
// with the sample system it is shown on; they are captured only when SCREENSHOT_EMAIL and SCREENSHOT_PASSWORD are set.
const publicShots: Shot[] = [
  { name: "home", path: "/" },
  { name: "platform", path: "/platform" },
  { name: "pricing", path: "/pricing", wait: "text=Estimated monthly cost" },
  { name: "security", path: "/security" },
  { name: "solutions-healthcare", path: "/solutions/healthcare" },
];
const consoleShots: Shot[] = [];

mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await context.newPage();

async function capture(shot: Shot): Promise<void> {
  await page.goto(base + shot.path, { waitUntil: "networkidle" });
  if (shot.wait) await page.waitForSelector(shot.wait);
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: "*,*::before,*::after{animation:none!important;transition:none!important}" });
  const png = await page.screenshot(shot.clip ? { clip: shot.clip } : {});
  // WebP is a fraction of the size of PNG, and the images are shown with explicit width and height
  const webp = await sharp(png).webp({ quality: 88 }).toBuffer();
  await sharp(webp).toFile(resolve(out, `${shot.name}.webp`));
  console.log(`${shot.name}.webp ${webp.length} bytes`);
}

for (const shot of publicShots) await capture(shot);

const email = process.env.SCREENSHOT_EMAIL;
const password = process.env.SCREENSHOT_PASSWORD;
if (consoleShots.length > 0) {
  if (!email || !password) {
    console.log("Console screenshots skipped: set SCREENSHOT_EMAIL and SCREENSHOT_PASSWORD");
  } else {
    await page.goto(`${base}/login`, { waitUntil: "networkidle" });
    await page.getByLabel(/Email/).fill(email);
    await page.getByLabel(/^Password/).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).last().click();
    await page.waitForURL(/\/app\//);
    for (const shot of consoleShots) await capture(shot);
  }
}
await browser.close();
