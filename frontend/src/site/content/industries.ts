export type Industry = {
  slug: string;
  name: string;
  short: string;
  image: string;
  challenge: string[];
  approach: string[];
  /** Real figures exist only where a sample system exists; the others describe what would be verified. */
  outcome: { kind: "measured" | "illustrative"; title: string; items: { label: string; value: string }[]; note: string };
  demo: { title: string; description: string; sample: "commerce" | "clinic" };
};

export const industries: Industry[] = [
  {
    slug: "healthcare",
    name: "Healthcare",
    short: "Clinic software and patient records on aging desktop applications",
    image: "/images/healthcare.svg",
    challenge: [
      "A small clinic runs on a Windows application from the early 2000s. There is no database server, only a binary data file, and the vendor is gone.",
      "Patient, visit, condition, medication and observation records must move to a modern system without losing a visit, and without exposing patient data along the way.",
    ],
    approach: [
      "Discovery reads the application's metadata and instructions to recover the record layout, then proves it against the data file.",
      "Personal data is detected by rules, kept out of stored profiles, and never shown to a language model.",
      "Dates stored as day counts, flags packed into single numbers and cents stored as integers become proper types through reviewed rules.",
      "Rows that cannot be loaded wait in a quarantine with their reason, so the clinic can correct the source and not guess.",
    ],
    outcome: {
      kind: "measured",
      title: "Sample scenario: the ClinicDesk system",
      items: [
        { label: "Records read", value: "25,900" },
        { label: "Loaded", value: "20,662" },
        { label: "Quarantined, each with a reason", value: "5,238" },
        { label: "Unexplained difference", value: "0" },
      ],
      note: "Measured on the synthetic ClinicDesk sample with injected defects. The quarantine includes visits, conditions and medications whose patient was itself quarantined.",
    },
    demo: { title: "Run the clinic sample", description: "A .NET application and its binary data file, migrated end to end.", sample: "clinic" },
  },
  {
    slug: "retail-commerce",
    name: "Retail and Commerce",
    short: "Order, product and customer history from legacy shop systems",
    image: "/images/retail.svg",
    challenge: [
      "Years of orders live in a database with names like CUST_MST and inv_dt, dates in three different formats, and prices stored as text with a dollar sign.",
      "The new platform needs clean customers, products, orders and order lines, and the finance team needs to know that no sale was lost.",
    ],
    approach: [
      "Mapping expands the abbreviations and matches columns to target fields by meaning, so most of the work is a review, not typing.",
      "Rules parse every date format and every price, and a person says once what order status codes 1, 2 and 3 mean.",
      "Duplicates are found by key and content; orders that point at a missing customer are quarantined, not attached to the wrong one.",
      "Reconciliation compares counts, distinct keys and sums of amounts with the source.",
    ],
    outcome: {
      kind: "measured",
      title: "Sample scenario: a small shop database",
      items: [
        { label: "Rows read", value: "6,338" },
        { label: "Loaded", value: "5,515" },
        { label: "Quarantined, each with a reason", value: "823" },
        { label: "Data quality score", value: "91.3 to 99.5" },
      ],
      note: "Measured on the commerce sample (a public music store database with realistic defects injected). All 70 injected duplicate products were found.",
    },
    demo: { title: "Run the commerce sample", description: "A SQLite shop database, migrated end to end.", sample: "commerce" },
  },
  {
    slug: "financial-services",
    name: "Financial Services",
    short: "Ledgers and account histories that must reconcile to the cent",
    image: "/images/finance.svg",
    challenge: [
      "A branch system holds account and transaction history in a format only the old software reads, and an audit will ask for proof that the totals survived the move.",
      "Amounts stored as integers of cents, dates as serial numbers and references between accounts and transactions all need exact handling.",
    ],
    approach: [
      "Numeric sums are compared with exact decimal arithmetic between the source and the loaded data.",
      "Every row is either loaded or quarantined with a reason; the report states that nothing is unexplained.",
      "Loads are idempotent by job and reversible by job, so a failed attempt leaves nothing behind.",
      "An audit log records who approved which mapping and who ran which load.",
    ],
    outcome: {
      kind: "illustrative",
      title: "What you would verify",
      items: [
        { label: "Row accounting", value: "source = loaded + quarantined" },
        { label: "Amount totals", value: "equal to the cent" },
        { label: "Reversal", value: "by job, in one step" },
        { label: "Approvals", value: "in the audit log" },
      ],
      note: "There is no financial sample system yet, so no figures are claimed here. The checks above are the same ones run on the commerce and clinic samples.",
    },
    demo: { title: "See the checks on the commerce sample", description: "The same reconciliation, on a shop database with orders and amounts.", sample: "commerce" },
  },
  {
    slug: "manufacturing",
    name: "Manufacturing",
    short: "Shop floor systems, part histories and supplier records",
    image: "/images/manufacturing.svg",
    challenge: [
      "A plant keeps parts, work orders and supplier records in a locally written application. The people who knew its quirks have retired.",
      "Part numbers, units and codes are inconsistent, and some records refer to parts that no longer exist.",
    ],
    approach: [
      "Discovery documents the structure so the migration does not depend on anyone's memory.",
      "Codes whose meaning is not in the data are explained once by a person and remembered for the next system.",
      "References that point at nothing are quarantined with the missing key, ready to be fixed at the source.",
      "The mapping memory makes the second plant's system faster than the first.",
    ],
    outcome: {
      kind: "illustrative",
      title: "What you would verify",
      items: [
        { label: "Orphan references", value: "listed with the missing key" },
        { label: "Unit and code maps", value: "approved by a person" },
        { label: "Repeat systems", value: "reuse approved mappings" },
        { label: "Result", value: "reconciled and reversible" },
      ],
      note: "There is no manufacturing sample system yet, so no figures are claimed here.",
    },
    demo: { title: "See the review step on the commerce sample", description: "Mapping review and code maps, with the same controls.", sample: "commerce" },
  },
  {
    slug: "public-sector",
    name: "Public Sector",
    short: "Decades of records on software nobody can maintain",
    image: "/images/public-sector.svg",
    challenge: [
      "A department keeps permits, cases or assets in software that only runs on one old machine, with data that must be kept for decades.",
      "The migration must be explainable to an auditor: what was moved, what was not, and why.",
    ],
    approach: [
      "Static analysis only: the old software is never executed, so a fragile machine is not put at risk.",
      "Every decision (a mapping, a rule, a code map) is reviewed by a person and recorded.",
      "The quarantine and the reconciliation report are exportable evidence of what happened to every record.",
      "Data stays in your own PostgreSQL database, and the code is in a public repository you can read.",
    ],
    outcome: {
      kind: "illustrative",
      title: "What you would verify",
      items: [
        { label: "Software executed", value: "never" },
        { label: "Every record", value: "loaded or explained" },
        { label: "Decisions", value: "reviewed and recorded" },
        { label: "Evidence", value: "exportable" },
      ],
      note: "There is no public sector sample system yet, so no figures are claimed here.",
    },
    demo: { title: "See the evidence on the clinic sample", description: "Reconciliation report and quarantine export for a desktop application.", sample: "clinic" },
  },
];

export const industryBySlug = (slug: string | undefined): Industry | undefined => industries.find((industry) => industry.slug === slug);
