import { Check } from "lucide-react";
import { ArchitectureDiagram } from "@/components/diagrams";
import { PageHero, Section, SectionHeading } from "@/components/layout";
import { CtaBand, stageIcons } from "@/components/sections";
import { Badge } from "@/components/ui/badge";
import { usePageMeta } from "@/lib/seo";
import { sourceTypes, stages } from "@/site/content/platform";
import { ScanSearch } from "lucide-react";

export default function Platform() {
  usePageMeta({ title: "Platform", description: "The six stages of the LegacyLift pipeline: discovery, mapping, transformation, validation, reconciliation and analytics.", path: "/platform" });
  return (
    <>
      <PageHero eyebrow="Platform" title="A pipeline you can inspect at every step" lede="Six stages take a legacy system to a clean target schema. Each one shows its work, and nothing is loaded until a person has approved it." />
      {stages.map((stage, index) => {
        const Icon = stageIcons[stage.id] ?? ScanSearch;
        return (
          <Section key={stage.id} id={stage.id} tone={index % 2 === 0 ? "default" : "muted"} aria-labelledby={`${stage.id}-heading`}>
            <div className="grid gap-10 lg:grid-cols-12">
              <div className="lg:col-span-5">
                <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-primary-soft text-primary">
                  <Icon className="h-7 w-7" aria-hidden="true" />
                </span>
                <p className="text-sm font-semibold uppercase tracking-wider text-teal-text">Stage {index + 1} of {stages.length}</p>
                <h2 id={`${stage.id}-heading`} className="mt-1 text-3xl font-bold">{stage.name}</h2>
                <p className="mt-2 text-xl font-medium text-foreground">{stage.tagline}</p>
                <p className="mt-4 text-lg text-foreground-muted">{stage.summary}</p>
                <Badge variant="neutral" className="mt-5">{stage.evidence}</Badge>
              </div>
              <ul className="space-y-4 lg:col-span-7">
                {stage.points.map((point) => (
                  <li key={point} className="flex gap-3 rounded-lg border border-border bg-surface-raised p-4 shadow-sm">
                    <Check className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
                    <span className="text-foreground-muted">{point}</span>
                  </li>
                ))}
              </ul>
            </div>
          </Section>
        );
      })}

      <Section aria-labelledby="sources-heading">
        <SectionHeading eyebrow="Supported sources" title={<span id="sources-heading">What can be read today</span>} lede="Only what is built is listed as supported." />
        <ul className="grid gap-5 md:grid-cols-2">
          {sourceTypes.map((source) => (
            <li key={source.name} className="rounded-lg border border-border bg-surface-raised p-5 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <h3 className="text-lg font-semibold">{source.name}</h3>
                <Badge variant={source.status === "Supported" ? "success" : "neutral"}>{source.status}</Badge>
              </div>
              <p className="mt-2 text-foreground-muted">{source.detail}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section tone="muted" aria-labelledby="architecture-heading">
        <SectionHeading eyebrow="Architecture" title={<span id="architecture-heading">How the parts fit together</span>} lede="A web application and API, workers that run the stages, one PostgreSQL database with per organization isolation, and an isolated analysis runner. The model router is optional." />
        <div className="rounded-xl border border-border bg-surface-raised p-4 shadow-sm md:p-8">
          <ArchitectureDiagram />
        </div>
      </Section>
      <CtaBand />
    </>
  );
}
