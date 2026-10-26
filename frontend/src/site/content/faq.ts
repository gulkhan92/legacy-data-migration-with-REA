export const faq: { id: string; question: string; answer: string }[] = [
  {
    id: "execute",
    question: "Does LegacyLift run my old software?",
    answer:
      "No. Discovery is static analysis: files are read and decoded, never executed. The analysis runs in a container with no network access, a read only file system and limited resources.",
  },
  {
    id: "sources",
    question: "Which legacy systems can it read?",
    answer:
      "SQLite databases, Windows desktop applications written for .NET together with their binary data file, and CSV or delimited text. Electron application stores and native binaries are not supported yet.",
  },
  {
    id: "llm",
    question: "Is my data sent to a language model?",
    answer:
      "Only if you configure a provider, and only for the few columns that rules and embeddings cannot settle. What leaves the system is metadata: column names, patterns and at most three masked example values, never rows and never columns that hold personal data. Without a provider key the platform works and leaves those columns for you to decide.",
  },
  {
    id: "wrong",
    question: "What if a mapping is wrong?",
    answer:
      "Nothing is loaded until a person approves the mappings and the rules. Each mapping shows its confidence and its candidates, and can be overridden or rejected. On the sample systems the automatic mapping was right for 94.7 percent of columns, and the review step exists for the rest.",
  },
  {
    id: "bad-rows",
    question: "What happens to rows that cannot be loaded?",
    answer:
      "They are quarantined, not dropped. Each one keeps its original values and a specific reason, such as an invalid date or a reference to a record that does not exist. The quarantine can be filtered and exported.",
  },
  {
    id: "undo",
    question: "Can a migration be undone?",
    answer: "Yes. Everything a job loaded carries the job's identifier, and a rollback removes exactly that. A job can be loaded only once, so a repeated request cannot create duplicates.",
  },
  {
    id: "proof",
    question: "How do I know nothing was lost?",
    answer:
      "The reconciliation report accounts for every source row as loaded or quarantined, compares distinct keys, null counts and sums of numeric fields, and compares a sample of rows field by field. The expected values come from reading the source, not from the code that moved the data.",
  },
  {
    id: "price",
    question: "Is there a free plan?",
    answer: "The Starter plan is free for up to 100,000 records a month. The prices on this site are illustrative: this is a demonstration project with no payment processor.",
  },
  {
    id: "demo",
    question: "Is this a real service?",
    answer:
      "It is a demonstration. The site uses sample data, the customers and revenue in the demo environment are simulated and labelled as such, and no certifications are claimed. The code, though, is real, and every figure on the dashboards is computed from tables.",
  },
];
