/** The API client. Same origin (the dev server and the production proxy forward /api), cookies for the session, and the CSRF header on every request. */

export type FieldError = { field: string; message: string; type?: string };

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: FieldError[];

  constructor(status: number, code: string, message: string, fields: FieldError[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

type Options = { method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"; body?: unknown; signal?: AbortSignal };

const CSRF_HEADER = "X-Requested-With";
const CSRF_VALUE = "LegacyLift";

async function readError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string; fields?: FieldError[] }; detail?: unknown };
    const message = body.error?.message ?? (typeof body.detail === "string" ? body.detail : "The request failed");
    return new ApiError(response.status, body.error?.code ?? `http_${response.status}`, message, body.error?.fields ?? []);
  } catch {
    return new ApiError(response.status, `http_${response.status}`, "The request failed");
  }
}

export async function api<T>(path: string, { method = "GET", body, signal }: Options = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "include",
      signal,
      headers: { [CSRF_HEADER]: CSRF_VALUE, Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "The server could not be reached. Check your connection and try again.");
  }
  if (!response.ok) throw await readError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ------------------------------------------------------------------ the public endpoints

export type PublicMetrics = {
  demo: boolean;
  as_of: string | null;
  days: number;
  organizations: number;
  jobs_run: number;
  records_migrated: number;
  success_rate: number | null;
  average_quality_score: number | null;
  average_duration_seconds: number | null;
};

export type PublicPlan = {
  code: string;
  name: string;
  monthly_fee_cents: number;
  included_records: number;
  overage_cents_per_1000: number;
};

export type Estimate = {
  plan: string;
  currency: string;
  records: number;
  included_records: number;
  overage_records: number;
  base_cents: number;
  overage_cents: number;
  total_cents: number;
};

export type LeadKind = "contact" | "demo" | "newsletter";

export type LeadInput = { name?: string; email: string; company?: string; message?: string; kind: LeadKind; website?: string };

export const publicApi = {
  metrics: (signal?: AbortSignal) => api<PublicMetrics>("/api/v1/public/metrics", { signal }),
  plans: (signal?: AbortSignal) => api<PublicPlan[]>("/api/v1/public/plans", { signal }),
  estimate: (plan: string, records: number, signal?: AbortSignal) =>
    api<Estimate>(`/api/v1/public/pricing/estimate?plan=${encodeURIComponent(plan)}&records=${Math.max(0, Math.floor(records))}`, { signal }),
  lead: (input: LeadInput) => api<{ received: boolean }>("/api/v1/public/leads", { method: "POST", body: input }),
  health: (signal?: AbortSignal) => api<{ status: string }>("/health", { signal }),
};

// ------------------------------------------------------------------ the session

export type Role = "admin" | "analyst" | "viewer";

export type Me = { user_id: string; email: string; full_name: string; org_id: string; org_name: string; role: Role };

export const sessionApi = {
  me: (signal?: AbortSignal) => api<Me>("/api/v1/auth/me", { signal }),
  login: (email: string, password: string) => api<Me>("/api/v1/auth/login", { method: "POST", body: { email, password } }),
  logout: () => api<void>("/api/v1/auth/logout", { method: "POST" }),
};
