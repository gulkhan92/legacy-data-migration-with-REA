import { ArrowRight, BarChart3, GitMerge, Scale, ScanSearch, ShieldCheck, Wand2, type LucideIcon } from "lucide-react";
import { Container, Section, SectionHeading } from "@/components/layout";
import { DiscoveryPanel, MappingPanel, ReconciliationPanel, TransformPanel } from "@/components/ProductPreview";
import { SiteLink } from "@/components/SiteLink";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cases } from "@/site/content/cases";
import { faq } from "@/site/content/faq";
import { industries } from "@/site/content/industries";
import { stages } from "@/site/content/platform";
import { practices } from "@/site/content/security";

export const stageIcons: Record<string, LucideIcon> = {
  discovery: ScanSearch,
  mapping: GitMerge,
  transformation: Wand2,
  validation: ShieldCheck,
  reconciliation: Scale,
  analytics: BarChart3,
};

export function CapabilityStrip() {
  return (
    <Section tone="muted" aria-labelledby="capabilities-heading">
      <SectionHeading
        eyebrow="The platform"
        title={<span id="capabilities-heading">Six stages, one pipeline</span>}
        lede="Every step produces something a person can check: evidence, a confidence, a before and after, a reason, a report."
      />
      <ul className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {stages.map((stage) => {
          const Icon = stageIcons[stage.id] ?? ScanSearch;
          return (
            <li key={stage.id}>
              <SiteLink to={`/platform#${stage.id}`} className="group block h-full rounded-lg focus-visible:ring-offset-surface-muted">
                <Card className="h-full transition-shadow group-hover:shadow-md">
                  <CardHeader>
                    <span className="mb-2 flex h-11 w-11 items-center justify-center rounded-lg bg-primary-soft text-primary">
                      <Icon className="h-6 w-6" aria-hidden="true" />
                    </span>
                    <CardTitle>{stage.name}</CardTitle>
                    <CardDescription className="text-base">{stage.tagline}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <span className="inline-flex items-center gap-1 text-sm font-semibold text-primary">
                      Learn more<span className="sr-only"> about {stage.name.toLowerCase()}</span> <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                    </span>
                  </CardContent>
                </Card>
              </SiteLink>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

export function IndustryTiles() {
  return (
    <Section aria-labelledby="industries-heading">
      <SectionHeading eyebrow="Solutions" title={<span id="industries-heading">For systems nobody wants to touch</span>} lede="The same pipeline, applied to the kinds of software that outlive the people who built it." />
      <ul className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {industries.map((industry) => (
          <li key={industry.slug}>
            <SiteLink to={`/solutions/${industry.slug}`} className="group block overflow-hidden rounded-lg border border-border bg-surface-raised shadow-sm transition-shadow hover:shadow-md">
              <img src={industry.image} width={640} height={400} alt="" loading="lazy" decoding="async" className="media-16x9 w-full object-cover" />
              <div className="p-5">
                <h3 className="text-xl font-semibold group-hover:text-primary">{industry.name}</h3>
                <p className="mt-1.5 text-foreground-muted">{industry.short}</p>
              </div>
            </SiteLink>
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function ProductTour() {
  return (
    <Section tone="muted" aria-labelledby="tour-heading">
      <SectionHeading
        eyebrow="Product tour"
        title={<span id="tour-heading">See what a reviewer sees</span>}
        lede="Illustrations built with the real design system and filled with real examples from the two sample systems."
      />
      <Tabs defaultValue="discovery">
        <TabsList aria-label="Product tour">
          <TabsTrigger value="discovery">Discovery</TabsTrigger>
          <TabsTrigger value="mapping">Mapping</TabsTrigger>
          <TabsTrigger value="transform">Rules</TabsTrigger>
          <TabsTrigger value="reconcile">Reconciliation</TabsTrigger>
        </TabsList>
        <TabsContent value="discovery" className="min-h-[360px]">
          <DiscoveryPanel className="max-w-3xl" />
        </TabsContent>
        <TabsContent value="mapping" className="min-h-[360px]">
          <MappingPanel className="max-w-4xl" />
        </TabsContent>
        <TabsContent value="transform" className="min-h-[360px]">
          <TransformPanel className="max-w-3xl" />
        </TabsContent>
        <TabsContent value="reconcile" className="min-h-[360px]">
          <ReconciliationPanel className="max-w-3xl" />
        </TabsContent>
      </Tabs>
    </Section>
  );
}

export function SecuritySummary() {
  return (
    <Section tone="dark" aria-labelledby="security-heading">
      <SectionHeading
        tone="dark"
        eyebrow="Security"
        title={<span id="security-heading">Controls that exist today</span>}
        lede="Only what is built and tested, described plainly. No certification is claimed."
      />
      <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {practices.slice(0, 4).map((practice) => (
          <li key={practice.id} className="rounded-lg border border-white/15 bg-white/5 p-5">
            <h3 className="text-lg font-semibold">{practice.title}</h3>
            <p className="mt-2 text-white/80">{practice.summary}</p>
          </li>
        ))}
      </ul>
      <Button asChild variant="onDark" className="mt-8">
        <SiteLink to="/security">
          Read the security practices <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </SiteLink>
      </Button>
    </Section>
  );
}

export function CaseStudies() {
  return (
    <Section aria-labelledby="cases-heading">
      <SectionHeading
        eyebrow="Sample scenarios"
        title={<span id="cases-heading">What a migration looks like</span>}
        lede="These are not customer stories. They are scenarios run on the two sample systems that ship with the project, with the measured results."
      />
      <ul className="grid gap-6 lg:grid-cols-3">
        {cases.map((item) => (
          <li key={item.id}>
            <Card className="flex h-full flex-col">
              <CardHeader>
                <Badge variant="teal" className="self-start">{item.label}</Badge>
                <CardTitle className="mt-2">{item.title}</CardTitle>
                <CardDescription className="text-base">{item.summary}</CardDescription>
              </CardHeader>
              <CardContent className="flex-1">
                <dl className="space-y-3">
                  {item.facts.map((fact) => (
                    <div key={fact.label} className="flex items-baseline justify-between gap-4 border-t border-border pt-3">
                      <dt className="text-sm text-foreground-muted">{fact.label}</dt>
                      <dd className="text-right font-semibold tabular-nums text-foreground">{fact.value}</dd>
                    </div>
                  ))}
                </dl>
              </CardContent>
              <div className="p-6 pt-0">
                <SiteLink to={item.to} className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">
                  Read more<span className="sr-only"> about {item.title.toLowerCase()}</span> <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </SiteLink>
              </div>
            </Card>
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function FaqSection({ title = "Questions, answered" }: { title?: string }) {
  return (
    <Section tone="muted" aria-labelledby="faq-heading">
      <div className="grid gap-10 lg:grid-cols-12">
        <div className="lg:col-span-4">
          <SectionHeading eyebrow="FAQ" title={<span id="faq-heading">{title}</span>} lede="Straight answers about what the platform does and does not do." className="mb-0" />
        </div>
        <div className="lg:col-span-8">
          <Accordion type="single" collapsible className="rounded-lg border border-border bg-surface-raised px-6">
            {faq.map((item) => (
              <AccordionItem key={item.id} value={item.id} className="last:border-b-0">
                <AccordionTrigger>{item.question}</AccordionTrigger>
                <AccordionContent>{item.answer}</AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
      </div>
    </Section>
  );
}

export function CtaBand({ title = "See a migration from start to finish", text = "Open the console and run one of the sample systems. It takes a few minutes." }: { title?: string; text?: string }) {
  return (
    <section className="relative overflow-hidden bg-primary py-16 text-primary-foreground" aria-labelledby="cta-heading">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(40rem_20rem_at_100%_0%,rgb(0_163_163/0.5),transparent)]" aria-hidden="true" />
      <Container className="relative flex flex-col items-start justify-between gap-6 md:flex-row md:items-center">
        <div className="max-w-2xl">
          <h2 id="cta-heading" className="text-3xl font-bold text-white">{title}</h2>
          <p className="mt-2 text-lg text-white/90">{text}</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Button asChild variant="onDark" size="lg">
            <SiteLink to="/login">Open console</SiteLink>
          </Button>
          <Button asChild variant="outlineOnDark" size="lg">
            <SiteLink to="/demo">Request a demo</SiteLink>
          </Button>
        </div>
      </Container>
    </section>
  );
}
