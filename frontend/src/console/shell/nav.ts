import { BarChart3, BookMarked, Brain, CreditCard, FolderKanban, History, LayoutDashboard, LineChart, Plus, Settings, Users, type LucideIcon } from "lucide-react";
import type { Role } from "@/lib/api";

export type NavItem = { to: string; label: string; icon: LucideIcon; roles: Role[]; keywords?: string };

const ALL: Role[] = ["admin", "analyst", "viewer"];
const WORKERS: Role[] = ["admin", "analyst"];

/** Where each role may go. The server enforces the same rules; the console only hides what a role cannot use. */
export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Work",
    items: [
      { to: "/app/overview", label: "Overview", icon: LayoutDashboard, roles: ALL, keywords: "home dashboard" },
      { to: "/app/migrations", label: "Migrations", icon: FolderKanban, roles: ALL, keywords: "jobs list" },
      { to: "/app/migrations/new", label: "New migration", icon: Plus, roles: WORKERS, keywords: "wizard upload start" },
      { to: "/app/schemas", label: "Target schemas", icon: BookMarked, roles: ALL, keywords: "fields" },
      { to: "/app/memory", label: "Mapping memory", icon: Brain, roles: ALL, keywords: "saved mappings" },
    ],
  },
  {
    group: "Insight",
    items: [
      { to: "/app/analytics", label: "Analytics", icon: BarChart3, roles: WORKERS, keywords: "operations latency quality" },
      { to: "/app/revenue", label: "Revenue", icon: LineChart, roles: ["admin"], keywords: "mrr arr churn cohorts" },
    ],
  },
  {
    group: "Organization",
    items: [
      { to: "/app/billing", label: "Billing", icon: CreditCard, roles: ALL, keywords: "plan usage invoices" },
      { to: "/app/team", label: "Team", icon: Users, roles: ["admin"], keywords: "users roles" },
      { to: "/app/audit", label: "Audit log", icon: History, roles: ["admin"], keywords: "events security" },
      { to: "/app/settings", label: "Settings", icon: Settings, roles: ["admin"], keywords: "llm retention budgets" },
    ],
  },
];

export function navFor(role: Role): { group: string; items: NavItem[] }[] {
  return NAV.map((g) => ({ group: g.group, items: g.items.filter((i) => i.roles.includes(role)) })).filter((g) => g.items.length > 0);
}

export function canVisit(role: Role, path: string): boolean {
  const all = NAV.flatMap((g) => g.items);
  const match = all.filter((i) => path === i.to || path.startsWith(`${i.to}/`)).sort((a, b) => b.to.length - a.to.length)[0];
  return match ? match.roles.includes(role) : true;
}

const LABELS: Record<string, string> = {
  app: "Console",
  overview: "Overview",
  migrations: "Migrations",
  new: "New migration",
  schemas: "Target schemas",
  memory: "Mapping memory",
  analytics: "Analytics",
  revenue: "Revenue",
  billing: "Billing",
  invoices: "Invoice",
  team: "Team",
  audit: "Audit log",
  settings: "Settings",
};

/** Breadcrumbs of a path: each segment with its link, a job or invoice id shown as the thing it is. */
export function crumbsFor(pathname: string): { label: string; to: string }[] {
  const parts = pathname.split("/").filter(Boolean);
  const crumbs: { label: string; to: string }[] = [];
  parts.forEach((part, index) => {
    if (part === "app") return;
    const to = `/${parts.slice(0, index + 1).join("/")}`;
    const previous = parts[index - 1];
    const label = LABELS[part] ?? (previous === "migrations" ? "Migration" : previous === "invoices" ? "Invoice" : part);
    crumbs.push({ label, to });
  });
  return crumbs;
}
