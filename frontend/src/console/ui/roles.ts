import type { Role } from "@/lib/api";

/** Analysts and administrators run migrations; viewers only look. The server enforces the same rule on every request. */
export const canWork = (role: Role): boolean => role === "admin" || role === "analyst";
export const isAdmin = (role: Role): boolean => role === "admin";

const LABELS: Record<Role, string> = { admin: "Administrator", analyst: "Analyst", viewer: "Viewer" };
export const roleLabel = (role: Role): string => LABELS[role] ?? role;

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  admin: "Everything, including team, billing invoices, audit log, settings and the revenue dashboard.",
  analyst: "Runs migrations, reviews mappings and rules, and reads the operations analytics.",
  viewer: "Reads migrations, reports, schemas and the overview. Cannot change anything.",
};
