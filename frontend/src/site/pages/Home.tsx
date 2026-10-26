import { ArrowRight } from "lucide-react";
import { Container, Section, SectionHeading } from "@/components/layout";
import { MetricBand } from "@/components/MetricBand";
import { HeroPreview } from "@/components/ProductPreview";
import { SiteLink } from "@/components/SiteLink";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PipelineIllustration } from "@/components/diagrams";
import { CapabilityStrip, CaseStudies, CtaBand, FaqSection, IndustryTiles, ProductTour, SecuritySummary } from "@/components/sections";
import { usePageMeta } from "@/lib/seo";

export default function Home() {
  usePageMeta({
    title: "LegacyLift",
    description: "Move decades of legacy data into modern systems, without a rewrite. Discovery, mapping, transformation, validation and reconciliation, with proof that nothing was lost.",
    path: "/",
  });
  return (
    <>
      <section className="relative overflow-hidden bg-gradient-to-b from-primary-soft/70 to-background" aria-labelledby="hero-heading">
        <Container className="grid items-center gap-12 py-14 md:py-20 lg:grid-cols-12">
          <div className="lg:col-span-6">
            <Badge variant="teal" className="mb-5">Demonstration project with sample data</Badge>
            <h1 id="hero-heading" className="text-5xl font-bold">
              Move decades of legacy data into modern systems, without a rewrite
            </h1>
            <p className="mt-6 max-w-xl text-xl text-foreground-muted">
              LegacyLift reads old databases and desktop applications without running them, maps every column to a clean target schema, and loads the result with a report that
              accounts for every row.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button asChild size="lg">
                <SiteLink to="/login">
                  Open console <ArrowRight className="h-5 w-5" aria-hidden="true" />
                </SiteLink>
              </Button>
              <Button asChild size="lg" variant="outline">
                <SiteLink to="/platform">Explore the platform</SiteLink>
              </Button>
            </div>
            <p className="mt-5 text-sm text-foreground-muted">Static analysis only. Nothing you upload is ever executed.</p>
          </div>
          <div className="lg:col-span-6">
            <HeroPreview />
          </div>
        </Container>
      </section>

      <CapabilityStrip />
      <MetricBand />

      <Section aria-labelledby="how-heading">
        <SectionHeading eyebrow="How it works" title={<span id="how-heading">From an old file to a proven result in four steps</span>} />
        <PipelineIllustration />
      </Section>

      <IndustryTiles />
      <ProductTour />
      <SecuritySummary />
      <CaseStudies />
      <FaqSection />
      <CtaBand />
    </>
  );
}
