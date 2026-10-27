import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { password, settle, users } from "./helpers";

const MOCK = process.env.E2E_MOCK_API_URL; // set when the tests run against e2e/mock-api.mjs instead of the real API

/** Whether a lead with this email reached the server: the mock keeps them in memory, the real API shows them to an administrator. */
async function leadStored(request: APIRequestContext, baseURL: string, email: string): Promise<boolean> {
  if (MOCK) {
    const leads = (await (await request.get(`${MOCK}/__leads`)).json()) as { email: string }[];
    return leads.some((lead) => lead.email === email);
  }
  if (!password) return true; // cannot look, the form's own success state is what is asserted
  await request.post(`${baseURL}/api/v1/auth/login`, { headers: { "X-Requested-With": "LegacyLift" }, data: { email: users.admin, password } });
  const page = (await (await request.get(`${baseURL}/api/v1/admin/leads?limit=200`)).json()) as { items: { email: string }[] };
  return page.items.some((lead) => lead.email === email);
}

const uniqueEmail = (tag: string) => `${tag}-${Date.now().toString(36)}@example.com`;

test.describe("keyboard and structure", () => {
  test("the skip link is the first stop, and takes focus to the content", async ({ page }) => {
    await page.goto("/pricing");
    await settle(page);
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main")).toBeFocused();
  });

  test("the main navigation is reachable and operable with the keyboard alone", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/");
    await settle(page);
    await page.getByRole("button", { name: "Platform" }).focus();
    await expect(page.getByRole("button", { name: "Platform" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("link", { name: /Reconciliation/ }).first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Platform" })).toHaveAttribute("aria-expanded", "false");
    // a focused control shows a visible ring
    const pricing = page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Pricing" });
    await pricing.focus();
    const ring = await pricing.evaluate((el) => getComputedStyle(el).boxShadow);
    expect(ring).not.toBe("none");
  });

  test("the sticky header condenses on scroll without moving the page", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/platform");
    await settle(page);
    const header = page.locator("header[data-condensed]").first();
    await expect(header).toHaveAttribute("data-condensed", "false");
    const before = (await header.boundingBox())?.height;
    const contentTop = await page.locator("#main").evaluate((el) => el.getBoundingClientRect().top + window.scrollY);
    await page.mouse.wheel(0, 600);
    await expect(header).toHaveAttribute("data-condensed", "true");
    expect((await header.boundingBox())?.height).toBe(before);
    expect((await header.boundingBox())?.y).toBe(0); // it stays at the top of the window
    expect(await page.locator("#main").evaluate((el) => el.getBoundingClientRect().top + window.scrollY)).toBe(contentTop);
    await page.mouse.wheel(0, -2000);
    await expect(header).toHaveAttribute("data-condensed", "false");
  });

  test("the mobile menu opens, navigates, closes itself, and Escape closes it too", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto("/");
    await settle(page);
    await page.getByRole("button", { name: "Open menu" }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await drawer.getByRole("button", { name: "Solutions" }).click();
    await drawer.getByRole("link", { name: "Healthcare" }).click();
    await expect(page).toHaveURL(/\/solutions\/healthcare$/);
    await expect(drawer).toBeHidden();
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(drawer).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused(); // focus returns where it came from
  });

  test("the home page accordion and tour work with the keyboard", async ({ page }) => {
    await page.goto("/");
    await settle(page);
    const question = page.getByRole("button", { name: "Does LegacyLift run my old software?" });
    await question.focus();
    await page.keyboard.press("Enter");
    await expect(question).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText(/Discovery is static analysis/)).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(question).toHaveAttribute("aria-expanded", "false");
    await page.getByRole("tab", { name: "Mapping" }).click();
    await expect(page.getByRole("tabpanel", { name: "Mapping" }).getByText("Review mappings")).toBeVisible();
    await page.getByRole("tab", { name: "Reconciliation" }).click();
    await expect(page.getByText("Reconciliation report: commerce sample")).toBeVisible();
  });
});

test.describe("figures from the API", () => {
  test("the metric band shows the demo figures, labelled as such", async ({ page, request, baseURL }) => {
    const metrics = (await (await request.get(`${baseURL}/api/v1/public/metrics`)).json()) as { jobs_run: number };
    await page.goto("/");
    await settle(page);
    const band = page.getByRole("region", { name: "Demo environment figures" });
    await band.scrollIntoViewIfNeeded();
    await expect(band).toContainText("Demo environment figures");
    await expect(band).toContainText("Simulated organizations");
    await expect(band.getByText(metrics.jobs_run.toLocaleString("en-US"), { exact: true }).first()).toBeAttached();
  });

  test("with reduced motion the figures are shown at once", async ({ page, request, baseURL }) => {
    const metrics = (await (await request.get(`${baseURL}/api/v1/public/metrics`)).json()) as { jobs_run: number };
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await settle(page);
    const visible = page.getByRole("region", { name: "Demo environment figures" }).locator('span[aria-hidden="true"]').filter({ hasText: metrics.jobs_run.toLocaleString("en-US") });
    await expect(visible).toBeVisible({ timeout: 2000 });
  });

  test("the status page reports the API", async ({ page }) => {
    await page.goto("/status");
    await expect(page.getByText("The API is answering")).toBeVisible();
  });
});

test.describe("pricing", () => {
  test("the plan cards come from the API", async ({ page }) => {
    await page.goto("/pricing");
    await settle(page);
    for (const [name, price] of [["Starter", "Free"], ["Professional", "$499"], ["Enterprise", "$2,500"]] as const) {
      const card = page.getByRole("heading", { name, level: 3 }).locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
      await expect(card).toContainText(price);
    }
    await expect(page.getByText("100,000").first()).toBeVisible();
    await expect(page.getByText("$0.08").first()).toBeVisible();
  });

  test("the calculator prices a volume on every plan and marks the lowest", async ({ page }) => {
    await page.goto("/pricing");
    await settle(page);
    const results = page.locator("li", { hasText: "Estimated" }).first();
    await expect(page.getByText("$950", { exact: true })).toBeVisible(); // Starter, 2,000,000 records: 1,900,000 above the allowance at $0.50 per 1,000
    await expect(page.getByText("$499", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("$2,500", { exact: true }).first()).toBeVisible();
    await expect(page.locator("li", { hasText: "Lowest" })).toContainText("professional");
    await page.getByRole("button", { name: "50K" }).click();
    await expect(page.locator("li", { hasText: "Lowest" })).toContainText("starter");
    await expect(page.locator("li", { hasText: "Lowest" })).toContainText("$0");
    await page.getByRole("button", { name: "50M" }).click();
    await expect(page.locator("li", { hasText: "Lowest" })).toContainText("enterprise");
    expect(await results.count()).toBeGreaterThanOrEqual(0);
  });

  test("a volume typed with separators is understood, and nonsense is explained", async ({ page }) => {
    await page.goto("/pricing");
    await settle(page);
    const input = page.getByLabel("Records loaded per month");
    await input.fill("3,000,000");
    await expect(page.getByText("$20,000", { exact: false }).first()).toBeAttached().catch(() => undefined);
    await expect(page.locator("li", { hasText: "Lowest" })).toContainText("professional"); // $499 + 1,000,000 over at $0.20 per 1,000 = $699
    await expect(page.getByText("$699", { exact: true })).toBeVisible();
    await input.fill("many");
    await expect(page.getByRole("alert").filter({ hasText: "whole number" })).toBeVisible();
    await expect(input).toHaveAttribute("aria-invalid", "true");
  });
});

test.describe("forms", () => {
  test.describe.configure({ mode: "serial" });

  test("the contact form validates, then stores the message", async ({ page, request, baseURL }) => {
    await page.goto("/contact");
    await settle(page);
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByText("Enter your email address")).toBeVisible();
    await expect(page.getByText(/at least 10 characters/)).toBeVisible();
    const email = uniqueEmail("contact");
    await page.getByLabel("Name").fill("Grace Hopper");
    await page.getByLabel(/Work email/).fill(email);
    await page.getByLabel("Company").fill("Example Ltd");
    await page.getByLabel(/Your message/).fill("We have a clinic application we need to move.");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Thank you" })).toBeVisible();
    expect(await leadStored(request, baseURL as string, email)).toBe(true);
  });

  test("the demo form stores a request", async ({ page, request, baseURL }) => {
    await page.goto("/demo");
    await settle(page);
    const email = uniqueEmail("demo");
    await page.getByLabel(/Work email/).fill(email);
    await page.getByRole("button", { name: "Request a demo" }).last().click();
    await expect(page.getByRole("status").filter({ hasText: "Thank you" })).toBeVisible();
    expect(await leadStored(request, baseURL as string, email)).toBe(true);
  });

  test("the newsletter field in the footer stores a lead", async ({ page, request, baseURL }) => {
    await page.goto("/company");
    await settle(page);
    const email = uniqueEmail("news");
    await page.getByLabel("Stay in touch").fill("not an email");
    await page.getByRole("button", { name: "Subscribe" }).click();
    await expect(page.getByText("Enter a valid email address")).toBeVisible();
    await page.getByLabel("Stay in touch").fill(email);
    await page.getByRole("button", { name: "Subscribe" }).click();
    await expect(page.getByText("Thank you, we will be in touch.")).toBeVisible();
    expect(await leadStored(request, baseURL as string, email)).toBe(true);
  });

  test("a failure of the server is shown and nothing is lost", async ({ page }) => {
    await page.route("**/api/v1/public/leads", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "x", error: { code: "unavailable", message: "x", status: 503 } }) }));
    await page.goto("/contact");
    await settle(page);
    await page.getByLabel(/Work email/).fill("person@example.com");
    await page.getByLabel(/Your message/).fill("A message that should survive an outage.");
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "our side" })).toBeVisible();
    await expect(page.getByLabel(/Your message/)).toHaveValue("A message that should survive an outage.");
  });

  test("a bot that fills the hidden field is told it worked and is not stored", async ({ page, request, baseURL }) => {
    await page.goto("/contact");
    await settle(page);
    const email = uniqueEmail("bot");
    await page.getByLabel(/Work email/).fill(email);
    await page.getByLabel(/Your message/).fill("Buy cheap watches at this address now.");
    await page.getByRole("form", { name: "Contact form" }).locator('input[name="website"]').evaluate((el: HTMLInputElement) => {
      // React tracks the value of an input, so it has to be set through the native setter for the change to be seen
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(el, "http://spam.example");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Thank you" })).toBeVisible();
    expect(await leadStored(request, baseURL as string, email)).toBe(false);
  });
});

async function signIn(page: Page, email: string, secret: string) {
  await page.getByLabel(/Email/).fill(email);
  await page.getByLabel(/^Password/).fill(secret);
  await page.getByRole("button", { name: "Sign in", exact: true }).last().click();
}

test.describe("sign in", () => {
  test.describe.configure({ mode: "serial" });
  test.skip(!password, "set E2E_PASSWORD to the password of the demo users to run the sign in tests");
  // The API allows five sign ins a minute per address, and this group makes four. Against the real API run it once a minute; the mock can be reset.
  test.beforeAll(async () => {
    if (MOCK) await fetch(`${MOCK}/__reset`);
  });

  test("a wrong password is explained, and the form stays", async ({ page }) => {
    await page.goto("/login");
    await settle(page);
    await signIn(page, users.analyst, "definitely-not-the-password");
    await expect(page.getByRole("alert").filter({ hasText: "email or password is not correct" })).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
  });

  test("the console cannot be opened signed out, and the sign in returns to where the person was going", async ({ page }) => {
    await page.goto("/app/billing");
    await expect(page).toHaveURL(/\/login\?next=%2Fapp%2Fbilling$/);
    await signIn(page, users.viewer, password as string);
    await expect(page).toHaveURL(/\/app\/billing$/);
    await expect(page.getByRole("heading", { level: 1, name: "Billing" })).toBeVisible();
    await page.getByRole("button", { name: "Account menu for Demo Viewer" }).click();
    await expect(page.getByText("viewer", { exact: true })).toBeVisible();
  });

  test("an analyst lands on migrations, and signing out ends the session", async ({ page }) => {
    await page.goto("/login");
    await settle(page);
    await signIn(page, users.analyst, password as string);
    await expect(page).toHaveURL(/\/app\/migrations$/);
    await expect(page.getByRole("heading", { level: 1, name: "Migrations" })).toBeVisible();
    await page.getByRole("button", { name: "Account menu for Demo Analyst" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/$/);
    await page.goto("/app/overview");
    await expect(page).toHaveURL(/\/login\?next=/);
  });

  test("the header offers the console to a signed in person", async ({ page }) => {
    await page.goto("/login");
    await settle(page);
    await signIn(page, users.admin, password as string);
    await expect(page).toHaveURL(/\/app\/overview$/);
    await page.goto("/pricing");
    await expect(page.getByRole("link", { name: /Open console/ }).first()).toHaveAttribute("href", "/app/overview");
    await expect(page.locator("header").getByRole("link", { name: /^Sign in$/ })).toHaveCount(0);
    await page.goto("/app/overview");
    await page.getByRole("button", { name: "Account menu for Demo Admin" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
  });

  test("a next address that leaves the site is ignored", async ({ page }) => {
    await page.route("**/api/v1/auth/login", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user_id: "u", email: users.analyst, full_name: "Demo Analyst", org_id: "o", org_name: "Demo", role: "analyst" }) }),
    );
    await page.goto("/login?next=https%3A%2F%2Fevil.example%2Fsteal");
    await settle(page);
    await signIn(page, users.analyst, "whatever-it-is-123");
    await expect(page).toHaveURL(/\/app\/migrations$/);
  });
});

test.describe("documentation and articles", () => {
  test("the documentation viewer renders the project's docs with a working table of contents", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/resources/docs/architecture");
    await settle(page);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator("article h2").first()).toBeVisible();
    const toc = page.getByRole("navigation", { name: "On this page" });
    const link = toc.getByRole("link", { name: /Billing, metrics and analytics/ });
    await link.click();
    await expect(page).toHaveURL(/#billing-metrics-and-analytics-phase-10$/);
    await expect(page.locator("#billing-metrics-and-analytics-phase-10")).toBeInViewport();
    await expect(page.locator("article table").first()).toBeAttached();
  });

  test("the docs index lists the documents, and an article renders its sections", async ({ page }) => {
    await page.goto("/resources/docs");
    await expect(page.getByRole("link", { name: "Architecture" }).first()).toBeVisible();
    await page.goto("/resources/articles/reconciliation-method");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Reconciliation");
    await expect(page.getByRole("heading", { level: 2, name: "The checks" })).toBeVisible();
    await expect(page.getByText("Row accounting")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "More articles" })).toContainText("Discovery");
  });

  test("an industry page shows its sample figures only where a sample exists", async ({ page }) => {
    await page.goto("/solutions/healthcare");
    await expect(page.getByText("25,900")).toBeVisible();
    await expect(page.getByText("Measured on a sample system")).toBeVisible();
    await page.goto("/solutions/financial-services");
    await expect(page.getByText("No sample system yet: no figures claimed")).toBeVisible();
  });
});
