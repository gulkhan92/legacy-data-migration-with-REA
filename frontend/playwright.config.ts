import { defineConfig, devices } from "@playwright/test";

/**
 * The end to end tests run against the built site (`npm run build`, then `vite preview` on port 4173), which proxies /api to the API on
 * port 8000. Start the API first (see docs/architecture.md). Set E2E_BASE_URL to test another address.
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:4173";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL, trace: "off", screenshot: "off" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], channel: undefined } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : { command: "npx vite preview --port 4173 --strictPort", url: baseURL, reuseExistingServer: true, timeout: 60_000 },
});
