export type ArticleSection = { heading: string; paragraphs: string[]; list?: string[] };

export type Article = {
  slug: string;
  title: string;
  description: string;
  date: string;
  minutes: number;
  tag: string;
  sections: ArticleSection[];
};

/** Written from the project's own design notes. Figures are the ones measured on the two sample systems. */
export const articles: Article[] = [
  {
    slug: "how-discovery-works",
    title: "Discovery: reading a program you cannot run",
    description: "How LegacyLift recovers the structure of a legacy database or a desktop application's binary file with static analysis only.",
    date: "2026-10-23",
    minutes: 5,
    tag: "Discovery",
    sections: [
      {
        heading: "The problem",
        paragraphs: [
          "The first question in any migration is the least glamorous one: how is the data stored? Documentation is gone, the vendor may be gone, and the software may only run on a single machine nobody dares to touch. Running it to find out is a risk. Reading it is not.",
          "LegacyLift treats everything uploaded as data. Discovery is static analysis: files are opened and decoded, never executed.",
        ],
      },
      {
        heading: "Two kinds of source",
        paragraphs: [
          "A SQLite database describes itself. The tables, the columns with their declared types, the indexes, views and triggers are all in the file, and a bounded sample of rows shows what the columns really hold.",
          "A desktop application written for .NET is harder. Its data lives in a binary file with no header that names anything. But the program that wrote the file has to know the layout, and a managed program carries its metadata and its instructions with it. Discovery reads the record types, follows the instructions that read and write each field, and reconstructs a layout: for every field its name, offset, length, type, encoding and byte order.",
        ],
      },
      {
        heading: "Proving the layout",
        paragraphs: [
          "A layout recovered from code is a claim. The next step tests it against the data file. If the claim does not fit the bytes, the result says so, with a lower confidence, rather than presenting a guess as a fact. When there is no program at all, only a data file, the layout can still be inferred statistically, and it is reported as less certain.",
        ],
      },
      {
        heading: "Evidence you can inspect",
        paragraphs: [
          "Every finding keeps its evidence: the exact command that was run, the tool version, the duration and the output. A reviewer who wonders why a field was named LastNm can open the evidence and see the instructions that decided it.",
        ],
        list: [
          "Analysis runs in a container with no network, a read only file system, a non root user and limits on time and memory.",
          "Only a short list of analysis commands is allowed; runtime capture and debugging are not exposed.",
          "The tool is an open source reverse engineering command line, pinned to one version.",
        ],
      },
      {
        heading: "What it does not do",
        paragraphs: [
          "Single file hosts, ahead of time compiled programs and native binaries are not supported by the analyzer, and are reported as unsupported instead of guessed. Electron application stores and native binaries are on the list for after the supported sources are complete.",
        ],
      },
    ],
  },
  {
    slug: "token-efficient-mapping",
    title: "Token efficient mapping: ask the model last",
    description: "How rules, embeddings and a memory of past decisions map most columns, and why a language model sees only what is left.",
    date: "2026-10-23",
    minutes: 6,
    tag: "Mapping",
    sections: [
      {
        heading: "A budget, not a habit",
        paragraphs: [
          "It is easy to hand every column to a language model and ask where it belongs. It is also slow, costly, and hard to review. LegacyLift inverts the order: cheap, deterministic steps go first, and the model sees only the columns they cannot settle, in one batched request with a hard token limit.",
        ],
      },
      {
        heading: "The order of decisions",
        paragraphs: ["For each source column, in this order:"],
        list: [
          "Memory. If the organization approved a similar column before, the same mapping is reused at no cost.",
          "A type gate. A date can never feed a numeric field, a reference only feeds references. Impossible matches are removed before anything is ranked.",
          "The target table. The table whose fields fit the source table's columns best is chosen for the whole table.",
          "One to one matching. Pairs are taken best first, and a field belongs to the column that fits it best, which keeps two columns from claiming the same field.",
          "Accept or ask. A match is accepted when its similarity and its margin over the next best free field are high enough. Otherwise the column goes to the model with its top three candidates.",
        ],
      },
      {
        heading: "Meaning, not spelling",
        paragraphs: [
          "Columns like cust_no and l_nm are expanded with an abbreviation dictionary (customer number, last name) and described together with their table, their type in words, the parent they refer to and up to three masked example values. Target fields are described with their purpose and synonyms. Both are embedded with a small open model that runs on the CPU, and compared by cosine similarity.",
        ],
      },
      {
        heading: "What it achieves",
        paragraphs: [
          "The thresholds were tuned on part of the data and checked on tables the search never saw: 96.2 percent correct on the tuning set and 91.3 percent on the held out tables. Over all 76 columns of the two sample systems, 72 were mapped correctly without any model call, which is 94.7 percent.",
          "The three accepted errors are instructive: two product columns competed for the same field, and two measurement columns swapped. Matching by meaning cannot tell columns apart that mean almost the same thing, which is exactly why a person approves the mappings before anything is loaded.",
        ],
      },
      {
        heading: "Honest about the model step",
        paragraphs: [
          "The model hand over (batching, the token cap, validation of every answer, failover between providers, a cache and budgets) is built and tested with scripted providers. The reported figure for the model step is a ceiling plus a stand in resolver, not a measurement of a real model. After approval, a second job on the same kind of system takes its columns from memory with no tokens at all.",
        ],
      },
    ],
  },
  {
    slug: "reconciliation-method",
    title: "Reconciliation: proving that nothing was lost",
    description: "The checks that turn a migration from something you hope worked into something you can show an auditor.",
    date: "2026-10-23",
    minutes: 6,
    tag: "Reconciliation",
    sections: [
      {
        heading: "Hope is not a method",
        paragraphs: [
          "Most migrations end with someone counting rows in two places and feeling reassured. Counts are a start, and not enough: a migration can lose one record and duplicate another and keep the count. LegacyLift reconciles on several independent measures and, importantly, computes the expectations before the data moves.",
        ],
      },
      {
        heading: "Load in one transaction",
        paragraphs: [
          "The source is streamed into temporary staging tables. Required values, types and references are validated in the database. Valid rows move to the warehouse tables; invalid rows move to a quarantine with a specific reason and their original values. If the checks below fail, the whole load is rolled back and the job is marked failed. Nothing half done remains.",
        ],
      },
      {
        heading: "The checks",
        paragraphs: ["For every table, the report states:"],
        list: [
          "Row accounting: source rows equal loaded rows plus quarantined rows, and every quarantined row has a reason.",
          "Distinct keys: the legacy keys read from the source against those loaded, with the rest accounted for in the quarantine.",
          "Null counts per field, after the transformation, against what was loaded.",
          "Sums of numeric fields, with exact decimal arithmetic, so money does not drift.",
          "A deterministic sample of rows compared field by field.",
        ],
      },
      {
        heading: "Independent expectations",
        paragraphs: [
          "The expected numbers come from the pass that reads the source, not from the SQL that moves the rows. A bug in the move therefore cannot hide itself: the two sides are computed by different code.",
        ],
      },
      {
        heading: "A result you can read",
        paragraphs: [
          "On the commerce sample the 6,338 source rows end as 5,515 loaded and 823 quarantined, each with a reason, and nothing unexplained. The data quality score rises from 91.3 to 99.5. Part of the quarantine is children of quarantined parents: an order whose customer was rejected cannot be loaded either, and says so.",
        ],
      },
      {
        heading: "And the way back",
        paragraphs: [
          "Every loaded row carries its job's identifier. A job loads once, enforced by the database, and a rollback deletes exactly the rows of that job together with its quarantine and report. The audit log records who ran the load and who reversed it.",
        ],
      },
    ],
  },
];

export const articleBySlug = (slug: string | undefined): Article | undefined => articles.find((article) => article.slug === slug);
