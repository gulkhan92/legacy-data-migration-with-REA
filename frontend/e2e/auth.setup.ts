import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test as setup } from "@playwright/test";
import { password, users } from "./helpers";

const dir = resolve(import.meta.dirname, ".auth");

/** One sign in per role for the whole run. Without E2E_PASSWORD the sessions are empty and the tests that need one skip themselves. */
for (const [role, email] of Object.entries(users)) {
  setup(`sign in as ${role}`, async ({ request }) => {
    mkdirSync(dir, { recursive: true });
    const file = resolve(dir, `${role}.json`);
    if (!password || process.env.E2E_MOCK_API_URL) {
      writeFileSync(file, JSON.stringify({ cookies: [], origins: [] }));
      return;
    }
    const response = await request.post("/api/v1/auth/login", { headers: { "X-Requested-With": "LegacyLift" }, data: { email, password } });
    if (!response.ok()) throw new Error(`The ${role} could not sign in: ${response.status()}`);
    await request.storageState({ path: file });
  });
}
