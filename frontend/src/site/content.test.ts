import { matchRoutes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { routes } from "@/App";
import { articles } from "@/site/content/articles";
import { cases } from "@/site/content/cases";
import { faq } from "@/site/content/faq";
import { industries } from "@/site/content/industries";
import { footerColumns, mainNav, utilityLinks, type NavLink } from "@/site/content/nav";
import { notClaimed, practices } from "@/site/content/security";
import { stages } from "@/site/content/platform";

const LONG_DASH = new RegExp(`[${String.fromCharCode(0x2013, 0x2014)}]`);

/** A link points at a real page when a route other than the catch all matches it. Addresses served by the API (the documentation) are external. */
function resolves(to: string, external?: boolean): boolean {
  if (external || /^(https?:|mailto:)/.test(to)) return true;
  const path = to.split("#")[0] || "/";
  const matches = matchRoutes(routes, path) ?? [];
  return matches.some((m) => m.route.path !== "*" && (m.route.index === true || m.route.path !== undefined));
}

const allLinks = (): NavLink[] => [
  ...utilityLinks,
  ...mainNav.flatMap((g) => [...(g.to ? [{ label: g.label, to: g.to }] : []), ...(g.sections ?? []).flatMap((s) => s.links)]),
  ...footerColumns.flatMap((c) => c.links),
];

describe("site content", () => {
  it("every navigation and footer link points at a page that exists", () => {
    const broken = allLinks().filter((l) => !resolves(l.to, l.external)).map((l) => `${l.label} -> ${l.to}`);
    expect(broken).toEqual([]);
  });

  it("every link in the content points at a page that exists", () => {
    const targets = [...cases.map((c) => c.to), ...industries.map((i) => `/solutions/${i.slug}`), ...articles.map((a) => `/resources/articles/${a.slug}`), ...stages.map((s) => `/platform#${s.id}`)];
    expect(targets.filter((t) => !resolves(t))).toEqual([]);
  });

  it("has the five industries of the plan, and the home page's three scenarios", () => {
    expect(industries.map((i) => i.name)).toEqual(["Healthcare", "Retail and Commerce", "Financial Services", "Manufacturing", "Public Sector"]);
    expect(cases).toHaveLength(3);
    expect(stages.map((s) => s.name)).toEqual(["Discovery", "Mapping", "Transformation", "Validation", "Reconciliation", "Analytics"]);
  });

  it("claims numbers only where a sample system exists", () => {
    for (const industry of industries) {
      if (industry.outcome.kind === "illustrative") expect(industry.outcome.note).toMatch(/no .* sample system yet/i);
      else expect(["healthcare", "retail-commerce"]).toContain(industry.slug);
    }
  });

  it("has no long dash anywhere in its text", () => {
    const text = JSON.stringify([articles, cases, faq, industries, mainNav, footerColumns, practices, notClaimed, stages]);
    expect(LONG_DASH.test(text)).toBe(false);
  });

  it("never claims a certification it does not hold", () => {
    const text = JSON.stringify([practices, faq, industries, cases]).toLowerCase();
    for (const word of ["soc 2 certified", "iso 27001 certified", "hipaa compliant", "gdpr compliant", "pci certified", "penetration tested"]) expect(text).not.toContain(word);
    expect(notClaimed.join(" ")).toMatch(/no security certification/i);
  });

  it("has unique ids and slugs", () => {
    for (const list of [articles.map((a) => a.slug), industries.map((i) => i.slug), faq.map((f) => f.id), stages.map((s) => s.id), practices.map((p) => p.id)]) {
      expect(new Set(list).size).toBe(list.length);
    }
  });

  it("has three articles with substance", () => {
    expect(articles.map((a) => a.slug)).toEqual(["how-discovery-works", "token-efficient-mapping", "reconciliation-method"]);
    for (const article of articles) {
      const words = article.sections.flatMap((s) => [...s.paragraphs, ...(s.list ?? [])]).join(" ").split(/\s+/).length;
      expect(words).toBeGreaterThan(300);
    }
  });
});
