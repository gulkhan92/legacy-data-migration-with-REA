import { ArrowRight, Check } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { PageHero, Section, SectionHeading } from "@/components/layout";
import { CtaBand, IndustryTiles } from "@/components/sections";
import { SiteLink } from "@/components/SiteLink";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { usePageMeta } from "@/lib/seo";
import { industries, industryBySlug } from "@/site/content/industries";
import NotFound from "@/site/pages/NotFound";

export function SolutionsIndex() {
  usePageMeta({ title: "Solutions", description: "How LegacyLift applies to healthcare, retail, financial services, manufacturing and the public sector.", path: "/solutions" });
  return (
    <>
      <PageHero eyebrow="Solutions" title="The same pipeline, for the systems that outlive their makers" lede="Choose an industry to see the challenge, the approach and what you would verify." />
      <IndustryTiles />
    </>
  );
}

export function Industry() {
  const { slug } = useParams();
  const industry = industryBySlug(slug);
  usePageMeta({
    title: industry ? `${industry.name} solutions` : "Not found",
    description: industry ? `${industry.short}. How LegacyLift approaches ${industry.name.toLowerCase()} migrations.` : "This page does not exist.",
    path: industry ? `/solutions/${industry.slug}` : undefined,
    noindex: !industry,
  });
  if (!industry) return <NotFound />;
  const others = industries.filter((i) => i.slug !== industry.slug);
  return (
    <>
      <PageHero
        eyebrow={industry.name}
        title={industry.short}
        breadcrumb={
          <nav aria-label="Breadcrumb">
            <ol className="flex flex-wrap items-center gap-2">
              <li><Link to="/solutions" className="text-white/80 underline hover:text-white">Solutions</Link></li>
              <li aria-hidden="true">/</li>
              <li aria-current="page" className="text-white">{industry.name}</li>
            </ol>
          </nav>
        }
      >
        <Button asChild variant="onDark" size="lg">
          <SiteLink to="/demo">Request a demo</SiteLink>
        </Button>
      </PageHero>

      <Section aria-labelledby="challenge-heading">
        <div className="grid gap-12 lg:grid-cols-2">
          <div>
            <SectionHeading eyebrow="The challenge" title={<span id="challenge-heading">What makes it hard</span>} className="mb-6" />
            <div className="space-y-4 text-lg text-foreground-muted">
              {industry.challenge.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
            </div>
          </div>
          <img src={industry.image} width={640} height={400} alt="" decoding="async" className="media-16x9 w-full rounded-xl border border-border object-cover shadow-md" />
        </div>
      </Section>

      <Section tone="muted" aria-labelledby="approach-heading">
        <SectionHeading eyebrow="The approach" title={<span id="approach-heading">How LegacyLift handles it</span>} />
        <ul className="grid gap-4 md:grid-cols-2">
          {industry.approach.map((item) => (
            <li key={item} className="flex gap-3 rounded-lg border border-border bg-surface-raised p-5 shadow-sm">
              <Check className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
              <span className="text-foreground-muted">{item}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section aria-labelledby="outcome-heading">
        <SectionHeading
          eyebrow={industry.outcome.kind === "measured" ? "Sample outcome" : "What you would verify"}
          title={<span id="outcome-heading">{industry.outcome.title}</span>}
          lede={industry.outcome.note}
        />
        <dl className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {industry.outcome.items.map((item) => (
            <div key={item.label} className="rounded-lg border border-border bg-surface-raised p-5 shadow-sm">
              <dt className="text-sm text-foreground-muted">{item.label}</dt>
              <dd className="mt-1 text-2xl font-bold tabular-nums text-foreground">{item.value}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-6">
          <Badge variant={industry.outcome.kind === "measured" ? "teal" : "neutral"}>{industry.outcome.kind === "measured" ? "Measured on a sample system" : "No sample system yet: no figures claimed"}</Badge>
        </div>
      </Section>

      <Section tone="muted" aria-labelledby="demo-heading">
        <div className="flex flex-col items-start justify-between gap-6 rounded-xl border border-border bg-surface-raised p-8 shadow-md md:flex-row md:items-center">
          <div className="max-w-2xl">
            <h2 id="demo-heading" className="text-2xl font-bold">{industry.demo.title}</h2>
            <p className="mt-2 text-lg text-foreground-muted">{industry.demo.description}</p>
          </div>
          <Button asChild size="lg">
            <SiteLink to="/login">
              Open console <ArrowRight className="h-5 w-5" aria-hidden="true" />
            </SiteLink>
          </Button>
        </div>
        <nav aria-label="Other industries" className="mt-10">
          <p className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-subtle">Other industries</p>
          <ul className="flex flex-wrap gap-3">
            {others.map((other) => (
              <li key={other.slug}>
                <Button asChild variant="outline" size="sm">
                  <SiteLink to={`/solutions/${other.slug}`}>{other.name}</SiteLink>
                </Button>
              </li>
            ))}
          </ul>
        </nav>
      </Section>
      <CtaBand />
    </>
  );
}
