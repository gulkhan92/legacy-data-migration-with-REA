// A stand in for the public and sign in endpoints of the API, for running the browser tests when the database is not available
// (CI without Docker, a laptop without the stack). It mirrors the real contract, which the backend tests pin down: the response
// shapes, the error format, the pricing function, the bot trap of the lead form and the CSRF header.
// Usage: node e2e/mock-api.mjs [port]      The real API is what `npm run e2e` is meant to run against; use this only when it cannot.
import { createServer } from "node:http";

const port = Number(process.argv[2] ?? 8001);
const PASSWORD = process.env.E2E_PASSWORD; // no default: without it every sign in is refused
const users = {
  "admin@legacylift.example": { role: "admin", full_name: "Demo Admin" },
  "analyst@legacylift.example": { role: "analyst", full_name: "Demo Analyst" },
  "viewer@legacylift.example": { role: "viewer", full_name: "Demo Viewer" },
};
const plans = [
  { code: "starter", name: "Starter", monthly_fee_cents: 0, included_records: 100000, overage_cents_per_1000: 50.0 },
  { code: "professional", name: "Professional", monthly_fee_cents: 49900, included_records: 2000000, overage_cents_per_1000: 20.0 },
  { code: "enterprise", name: "Enterprise", monthly_fee_cents: 250000, included_records: 25000000, overage_cents_per_1000: 8.0 },
];
const metrics = { demo: true, as_of: "2026-10-22", days: 721, organizations: 101, jobs_run: 6404, records_migrated: 6340468794, success_rate: 0.9388, average_quality_score: 96.2, average_duration_seconds: 293.2 };
const leads = [];
const sessions = new Map();
const attempts = new Map();

const error = (status, code, message, fields) => ({ detail: message, error: { code, message, status, ...(fields ? { fields } : {}) } });

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers });
  res.end(body === undefined ? undefined : JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve(null);
      }
    });
  });
}

// The overage is charged per 1,000 records above the allowance and rounded half up once (app/billing/plans.py).
function estimate(plan, records) {
  const over = Math.max(0, records - plan.included_records);
  const overage = Math.floor((over / 1000) * plan.overage_cents_per_1000 + 0.5);
  return { plan: plan.code, currency: "USD", records, included_records: plan.included_records, overage_records: over, base_cents: plan.monthly_fee_cents, overage_cents: overage, total_cents: plan.monthly_fee_cents + overage };
}

const cookie = (req, name) => (req.headers.cookie ?? "").split(/;\s*/).map((c) => c.split("=")).find(([k]) => k === name)?.[1];
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host}`);
  const unsafe = !["GET", "HEAD", "OPTIONS"].includes(req.method ?? "GET");
  if (unsafe && req.headers["x-requested-with"] !== "LegacyLift") return send(res, 403, error(403, "forbidden", "Missing CSRF header"));

  if (url.pathname === "/health") return send(res, 200, { status: "ok" });
  if (url.pathname === "/docs") {
    res.writeHead(200, { "Content-Type": "text/html" });
    return res.end("<!doctype html><title>API reference (mock)</title><h1>API reference</h1>");
  }
  if (url.pathname === "/__leads") return send(res, 200, leads);
  if (url.pathname === "/__reset") {
    attempts.clear(); // the real API allows five sign ins a minute; a test run that follows another one starts from zero
    return send(res, 200, { reset: true });
  }
  if (url.pathname === "/api/v1/public/plans") return send(res, 200, plans, { "Cache-Control": "public, max-age=300" });
  if (url.pathname === "/api/v1/public/metrics") return send(res, 200, metrics, { "Cache-Control": "public, max-age=300" });

  if (url.pathname === "/api/v1/public/pricing/estimate") {
    const plan = plans.find((p) => p.code === url.searchParams.get("plan"));
    const raw = url.searchParams.get("records") ?? "";
    if (!plan) return send(res, 404, error(404, "not_found", "Unknown plan"));
    if (!/^\d+$/.test(raw) || Number(raw) > 10_000_000_000) return send(res, 422, error(422, "validation_error", "The request is not valid", [{ field: "query.records", message: "invalid", type: "int_parsing" }]));
    return send(res, 200, estimate(plan, Number(raw)));
  }

  if (url.pathname === "/api/v1/public/leads" && req.method === "POST") {
    const body = await readBody(req);
    if (!body || !EMAIL.test(String(body.email ?? "")) || !["contact", "demo", "newsletter"].includes(body.kind ?? "contact")) {
      return send(res, 422, error(422, "validation_error", "The request is not valid", [{ field: "body.email", message: "not an email address", type: "value_error" }]));
    }
    if (!body.website) leads.push({ email: body.email, kind: body.kind ?? "contact", name: body.name ?? null, message: body.message ?? null });
    return send(res, 201, { received: true });
  }

  if (url.pathname === "/api/v1/auth/login" && req.method === "POST") {
    const body = await readBody(req);
    const key = req.socket.remoteAddress ?? "";
    const recent = (attempts.get(key) ?? []).filter((t) => Date.now() - t < 60_000);
    if (recent.length >= 5) return send(res, 429, error(429, "rate_limited", "Rate limit exceeded"));
    attempts.set(key, [...recent, Date.now()]);
    const user = users[String(body?.email ?? "").toLowerCase()];
    if (!user || !PASSWORD || body?.password !== PASSWORD) return send(res, 401, error(401, "not_authenticated", "Invalid credentials"));
    const token = Math.random().toString(36).slice(2);
    const me = { user_id: "00000000-0000-4000-8000-000000000001", email: body.email.toLowerCase(), full_name: user.full_name, org_id: "00000000-0000-4000-8000-0000000000aa", org_name: "Demo Organization", role: user.role };
    sessions.set(token, me);
    return send(res, 200, me, { "Set-Cookie": `access_token=${token}; Path=/; HttpOnly; SameSite=Lax` });
  }
  if (url.pathname === "/api/v1/auth/me") {
    const me = sessions.get(cookie(req, "access_token") ?? "");
    return me ? send(res, 200, me) : send(res, 401, error(401, "not_authenticated", "Not authenticated"));
  }
  if (url.pathname === "/api/v1/auth/logout" && req.method === "POST") {
    sessions.delete(cookie(req, "access_token") ?? "");
    res.writeHead(204, { "Set-Cookie": "access_token=; Path=/; Max-Age=0" });
    return res.end();
  }
  return send(res, 404, error(404, "not_found", "Not Found"));
}).listen(port, "127.0.0.1", () => console.log(`mock api on http://127.0.0.1:${port}`));
