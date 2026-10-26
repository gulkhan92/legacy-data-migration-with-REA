import { PageHero, Section, SectionHeading } from "@/components/layout";
import { CostCalculator, PlanCards, PlanComparison } from "@/components/pricing";
import { CtaBand } from "@/components/sections";
import { usePageMeta } from "@/lib/seo";

export default function Pricing() {
  usePageMeta({ title: "Pricing", description: "Three illustrative plans: Starter is free for 100,000 records a month. Estimate your monthly cost with the calculator.", path: "/pricing" });
  return (
    <>
      <PageHero eyebrow="Pricing" title="Pay for the records you load" lede="A flat monthly fee with an allowance of records, and a small price per thousand beyond it. The prices are illustrative: this is a demonstration project." />
      <Section aria-labelledby="plans-heading">
        <SectionHeading title={<span id="plans-heading">Three plans</span>} lede="The same platform in every plan. Only the allowance and the prices differ." />
        <PlanCards />
      </Section>
      <Section tone="muted" aria-labelledby="calc-heading">
        <SectionHeading eyebrow="Calculator" title={<span id="calc-heading">Estimate your monthly cost</span>} lede="Enter how many records you expect to load in a month. The estimate comes from the same pricing function that creates the invoices." />
        <CostCalculator />
      </Section>
      <Section aria-labelledby="compare-heading">
        <SectionHeading eyebrow="Compare" title={<span id="compare-heading">Plans side by side</span>} />
        <PlanComparison />
        <p className="mt-6 max-w-3xl text-sm text-foreground-muted">
          A record is one row loaded into the new system. Usage is counted when a load completes, the month is billed on the plan that is active on its last day, and a load that is rolled back before
          its month is invoiced is not counted. There is no payment processor in this demonstration.
        </p>
      </Section>
      <CtaBand title="Start with the free plan" text="Run a sample system through the whole pipeline and look at the reconciliation report yourself." />
    </>
  );
}
