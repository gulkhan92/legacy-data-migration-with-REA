export type CaseStudy = {
  id: string;
  label: string;
  title: string;
  summary: string;
  facts: { label: string; value: string }[];
  to: string;
};

/** Sample scenarios built from the two datasets in the repository. They are not customer stories. */
export const cases: CaseStudy[] = [
  {
    id: "shop",
    label: "Sample scenario",
    title: "A shop database with cryptic names",
    summary: "Tables called CUST_MST and INV_HDR, dates in three formats, prices with a dollar sign, and defects injected on purpose.",
    facts: [
      { label: "Rows accounted for", value: "6,338 of 6,338" },
      { label: "Quarantined with a reason", value: "823" },
      { label: "Quality score", value: "91.3 to 99.5" },
    ],
    to: "/solutions/retail-commerce",
  },
  {
    id: "clinic",
    label: "Sample scenario",
    title: "A clinic application with no database",
    summary: "A .NET program and a binary data file. The record layout is recovered from the program and proven against the file.",
    facts: [
      { label: "Records accounted for", value: "25,900 of 25,900" },
      { label: "Quarantined with a reason", value: "5,238" },
      { label: "Unexplained difference", value: "0" },
    ],
    to: "/solutions/healthcare",
  },
  {
    id: "memory",
    label: "Sample scenario",
    title: "The second system is cheaper than the first",
    summary: "Mappings you approved are remembered. A second job on the same kind of system takes them from memory, with no model call.",
    facts: [
      { label: "Columns mapped without a model", value: "94.7%" },
      { label: "Model tokens on a repeat run", value: "0" },
      { label: "First run budget", value: "under 6,000 tokens" },
    ],
    to: "/resources/articles/token-efficient-mapping",
  },
];
