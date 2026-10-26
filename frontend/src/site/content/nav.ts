export type NavLink = { label: string; to: string; description?: string; external?: boolean };
export type NavGroup = { label: string; to?: string; sections?: { title: string; links: NavLink[] }[] };

export const SITE_NAME = "LegacyLift";
export const DEMO_NOTICE =
  "LegacyLift is a demonstration project. The site uses sample data, the figures are from a demo environment, and no real customers, payments or certifications are implied.";

export const utilityLinks: NavLink[] = [
  { label: "Contact", to: "/contact" },
  { label: "Documentation", to: "/resources/docs" },
  { label: "Status", to: "/status" },
];

export const mainNav: NavGroup[] = [
  {
    label: "Platform",
    sections: [
      {
        title: "The pipeline",
        links: [
          { label: "Discovery", to: "/platform#discovery", description: "Recover how a legacy system stores its data, without running it" },
          { label: "Mapping", to: "/platform#mapping", description: "Match every column to a target field, with a person in the loop" },
          { label: "Transformation", to: "/platform#transformation", description: "Safe, reviewable rules for dates, money and codes" },
          { label: "Validation", to: "/platform#validation", description: "Quality scoring, PII detection and a quarantine for bad rows" },
          { label: "Reconciliation", to: "/platform#reconciliation", description: "Prove the result, row by row, and undo it if needed" },
          { label: "Analytics", to: "/platform#analytics", description: "Usage, success rate, cost and revenue from real tables" },
        ],
      },
      {
        title: "Explore",
        links: [
          { label: "Platform overview", to: "/platform", description: "Source types and architecture" },
          { label: "Security", to: "/security", description: "What is implemented today" },
        ],
      },
    ],
  },
  {
    label: "Solutions",
    sections: [
      {
        title: "By industry",
        links: [
          { label: "Healthcare", to: "/solutions/healthcare", description: "Clinic desktop software and patient records" },
          { label: "Retail and Commerce", to: "/solutions/retail-commerce", description: "Order, product and customer history" },
          { label: "Financial Services", to: "/solutions/financial-services", description: "Ledgers that must reconcile to the cent" },
          { label: "Manufacturing", to: "/solutions/manufacturing", description: "Shop floor systems and part histories" },
          { label: "Public Sector", to: "/solutions/public-sector", description: "Decades of records on aging software" },
        ],
      },
    ],
  },
  { label: "Pricing", to: "/pricing" },
  {
    label: "Resources",
    sections: [
      {
        title: "Learn",
        links: [
          { label: "Documentation", to: "/resources/docs", description: "The architecture and security notes of this project" },
          { label: "Technical articles", to: "/resources#articles", description: "How discovery, mapping and reconciliation work" },
          { label: "API reference", to: "/docs", description: "Interactive OpenAPI documentation", external: true },
        ],
      },
    ],
  },
  {
    label: "Company",
    sections: [
      {
        title: "LegacyLift",
        links: [
          { label: "About", to: "/company", description: "What this project is and why it exists" },
          { label: "Contact", to: "/contact", description: "Ask a question" },
          { label: "Request a demo", to: "/demo", description: "See a migration end to end" },
        ],
      },
    ],
  },
];

export const footerColumns: { title: string; links: NavLink[] }[] = [
  {
    title: "Platform",
    links: [
      { label: "Overview", to: "/platform" },
      { label: "Discovery", to: "/platform#discovery" },
      { label: "Mapping", to: "/platform#mapping" },
      { label: "Reconciliation", to: "/platform#reconciliation" },
      { label: "Security", to: "/security" },
    ],
  },
  {
    title: "Solutions",
    links: [
      { label: "Healthcare", to: "/solutions/healthcare" },
      { label: "Retail and Commerce", to: "/solutions/retail-commerce" },
      { label: "Financial Services", to: "/solutions/financial-services" },
      { label: "Manufacturing", to: "/solutions/manufacturing" },
      { label: "Public Sector", to: "/solutions/public-sector" },
    ],
  },
  {
    title: "Resources",
    links: [
      { label: "Documentation", to: "/resources/docs" },
      { label: "Technical articles", to: "/resources#articles" },
      { label: "API reference", to: "/docs", external: true },
      { label: "Pricing", to: "/pricing" },
      { label: "System status", to: "/status" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "About", to: "/company" },
      { label: "Contact", to: "/contact" },
      { label: "Request a demo", to: "/demo" },
      { label: "Sign in", to: "/login" },
    ],
  },
  {
    title: "Legal",
    links: [
      { label: "Privacy (sample)", to: "/legal/privacy" },
      { label: "Terms (sample)", to: "/legal/terms" },
    ],
  },
];
