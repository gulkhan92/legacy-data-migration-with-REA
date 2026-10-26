export type Practice = { id: string; title: string; summary: string; details: string[] };

/** Only controls that exist in the code and are covered by tests. Nothing here is a certification or a promise about the future. */
export const practices: Practice[] = [
  {
    id: "isolation",
    title: "Tenant isolation in the database",
    summary: "One organization cannot read another's rows, even if application code has a bug.",
    details: [
      "PostgreSQL Row Level Security is enforced on every table that holds customer data, including the migrated tables.",
      "The application connects as a role that is not a superuser, does not own the tables and cannot bypass the policies.",
      "A request that arrives without an organization sees nothing: the default is closed.",
    ],
  },
  {
    id: "encryption",
    title: "Encrypted uploads",
    summary: "Uploaded files are encrypted before they are written to disk.",
    details: [
      "AES-256-GCM with a fresh nonce per file, bound to the file's storage path, under a key that comes from the environment.",
      "Files are stored under random names; the original name is kept only as cleaned metadata.",
      "The platform encrypts uploaded files. It does not encrypt the database volume: that is a deployment choice.",
    ],
  },
  {
    id: "access",
    title: "Role based access",
    summary: "Administrator, analyst and viewer, enforced by the server.",
    details: [
      "Every route requires a sign in, and a test fails if a route is added without a guard.",
      "Passwords are hashed with argon2id; five failed sign ins lock the account for 15 minutes; refresh tokens rotate and a reused token ends every session of that user.",
      "Sessions use HttpOnly, Secure cookies; state changing requests carry a header a cross site form cannot add.",
    ],
  },
  {
    id: "audit",
    title: "Audit log",
    summary: "Who did what, appended and never edited.",
    details: [
      "Sign ins and failures, uploads, discovery, mapping approvals, loads, rollbacks, plan and invoice changes, user and settings changes are recorded.",
      "The table is append only for the application, and an organization's admins read only their own organization's entries.",
    ],
  },
  {
    id: "static",
    title: "Static analysis only",
    summary: "Uploaded software is data. It is never executed.",
    details: [
      "The application contains no dynamic import or evaluation of uploaded content, and a test checks it.",
      "Analysis runs in a container with no network, a read only file system, a non root user, all capabilities dropped and limits on CPU, memory, processes and time.",
      "Uploads are checked by extension, size and file signature, and executable content disguised as data is refused.",
    ],
  },
  {
    id: "safe-rules",
    title: "Rules that cannot run code",
    summary: "Transformations are data from a fixed list of operations.",
    details: [
      "There is no eval and no user supplied SQL: identifiers come from the target schema and values are bound parameters.",
      "Regular expressions that could run away are refused.",
      "Failures are recorded by class, not message, so paths and data do not reach users through the job log.",
    ],
  },
  {
    id: "llm",
    title: "Minimal data to language models",
    summary: "Optional, masked, budgeted and logged.",
    details: [
      "Provider keys are read from the environment and never stored, returned or logged.",
      "A guard allows no rows and nothing that looks like personal data; columns with personal data contribute only a pattern class.",
      "Token budgets per job and per organization bound what a runaway job can send, and every request is recorded without its content.",
    ],
  },
];

export const notClaimed = [
  "No security certification or attestation is held (for example SOC 2, ISO 27001 or HIPAA). This is a demonstration project.",
  "No independent penetration test has been performed.",
  "Revenue and customer figures in the demo environment are simulated.",
  "Dependency scanning and automated release checks are planned and not yet part of the project.",
];
