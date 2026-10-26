import { Check, Info } from "lucide-react";
import { PageHero, Section, SectionHeading } from "@/components/layout";
import { CtaBand } from "@/components/sections";
import { SiteLink } from "@/components/SiteLink";
import { usePageMeta } from "@/lib/seo";
import { notClaimed, practices } from "@/site/content/security";

export default function Security() {
  usePageMeta({ title: "Security", description: "The security controls LegacyLift implements today: tenant isolation, encrypted uploads, role based access, an audit log, static analysis only and safe rules.", path: "/security" });
  return (
    <>
      <PageHero eyebrow="Security" title="What is implemented, and nothing more" lede="Each control below exists in the code and is covered by an automated test. We list what we do not have as plainly as what we do." />
      <Section aria-labelledby="practices-heading">
        <SectionHeading title={<span id="practices-heading">Security practices</span>} />
        <ul className="grid gap-6 lg:grid-cols-2">
          {practices.map((practice) => (
            <li key={practice.id} id={practice.id} className="rounded-lg border border-border bg-surface-raised p-6 shadow-sm">
              <h3 className="text-xl font-semibold">{practice.title}</h3>
              <p className="mt-1 text-foreground">{practice.summary}</p>
              <ul className="mt-4 space-y-2.5">
                {practice.details.map((detail) => (
                  <li key={detail} className="flex gap-2.5 text-foreground-muted">
                    <Check className="mt-1 h-4 w-4 shrink-0 text-success" aria-hidden="true" />
                    <span>{detail}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </Section>
      <Section tone="muted" aria-labelledby="not-claimed-heading">
        <SectionHeading eyebrow="Honesty" title={<span id="not-claimed-heading">What is not claimed</span>} />
        <ul className="max-w-3xl space-y-3">
          {notClaimed.map((item) => (
            <li key={item} className="flex gap-3 rounded-lg border border-warning/30 bg-warning-soft p-4 text-foreground">
              <Info className="mt-0.5 h-5 w-5 shrink-0 text-warning" aria-hidden="true" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
        <p className="mt-8 max-w-3xl text-foreground-muted">
          The full notes, with the tests that cover each statement, are in the <SiteLink to="/resources/docs/security" className="font-semibold underline">security documentation</SiteLink>.
        </p>
      </Section>
      <CtaBand />
    </>
  );
}
