import { Eye, FileCheck2, ShieldCheck, Undo2 } from "lucide-react";
import { PageHero, Section, SectionHeading } from "@/components/layout";
import { CtaBand } from "@/components/sections";
import { usePageMeta } from "@/lib/seo";

const principles = [
  { icon: Eye, title: "Show the evidence", text: "Every finding keeps what it was based on. A person can always ask why." },
  { icon: FileCheck2, title: "A person approves", text: "Mappings and rules are reviewed before anything is loaded. Automation proposes, people decide." },
  { icon: ShieldCheck, title: "Prove the result", text: "A migration is finished when every row is accounted for, not when the progress bar ends." },
  { icon: Undo2, title: "Keep the way back", text: "Everything a job does can be undone by that job's identifier." },
];

const stack = [
  ["Backend", "Python, FastAPI, SQLAlchemy, Celery"],
  ["Data", "PostgreSQL with Row Level Security and pgvector, Redis"],
  ["Analysis", "REA, an open source reverse engineering command line, in an isolated container"],
  ["Mapping", "A small open embedding model on the CPU, and an optional free tier model router"],
  ["Frontend", "React, TypeScript, Vite, Tailwind CSS, Radix UI"],
];

export default function Company() {
  usePageMeta({ title: "About", description: "What LegacyLift is, the principles it is built on, and what it is built with.", path: "/company" });
  return (
    <>
      <PageHero eyebrow="Company" title="A migration platform built around proof" lede="LegacyLift is a demonstration project: a complete, working product built to show how legacy data can be discovered, mapped, moved and verified." />
      <Section aria-labelledby="what-heading">
        <div className="grid gap-10 lg:grid-cols-12">
          <div className="lg:col-span-5">
            <SectionHeading eyebrow="About" title={<span id="what-heading">What this is</span>} className="mb-0" />
          </div>
          <div className="space-y-4 text-lg text-foreground-muted lg:col-span-7">
            <p>
              Old software keeps data that matters, in formats nobody documented. Moving it is usually done by hand, with spreadsheets and hope. LegacyLift automates the work that can be
              automated safely, and puts a person in front of every decision that cannot.
            </p>
            <p>
              It is a demonstration. There are no real customers on this site, the customers and revenue in the demo environment are simulated and labelled as such, and no certifications are
              claimed. The software itself is real: every figure on the dashboards is computed from tables, and the checks described here are covered by automated tests.
            </p>
          </div>
        </div>
      </Section>
      <Section tone="muted" aria-labelledby="principles-heading">
        <SectionHeading eyebrow="Principles" title={<span id="principles-heading">How it is designed</span>} />
        <ul className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {principles.map(({ icon: Icon, title, text }) => (
            <li key={title} className="rounded-lg border border-border bg-surface-raised p-6 shadow-sm">
              <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-lg bg-primary-soft text-primary">
                <Icon className="h-6 w-6" aria-hidden="true" />
              </span>
              <h3 className="text-lg font-semibold">{title}</h3>
              <p className="mt-2 text-foreground-muted">{text}</p>
            </li>
          ))}
        </ul>
      </Section>
      <Section aria-labelledby="stack-heading">
        <SectionHeading eyebrow="Built with" title={<span id="stack-heading">Free and open source tools</span>} lede="No paid services. Language model use stays within free tiers, and is optional." />
        <dl className="divide-y divide-border rounded-lg border border-border">
          {stack.map(([name, value]) => (
            <div key={name} className="grid gap-1 px-5 py-4 sm:grid-cols-[180px_1fr]">
              <dt className="font-semibold text-foreground">{name}</dt>
              <dd className="text-foreground-muted">{value}</dd>
            </div>
          ))}
        </dl>
      </Section>
      <CtaBand />
    </>
  );
}
