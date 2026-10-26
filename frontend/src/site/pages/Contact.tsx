import { Calendar, MessageSquare } from "lucide-react";
import { LeadForm } from "@/components/forms";
import { PageHero, Section } from "@/components/layout";
import { SiteLink } from "@/components/SiteLink";
import { usePageMeta } from "@/lib/seo";

export function Contact() {
  usePageMeta({ title: "Contact", description: "Send a question about LegacyLift. Messages are stored for the administrator of the demo to read.", path: "/contact" });
  return (
    <>
      <PageHero eyebrow="Contact" title="Ask a question" lede="Tell us what you are trying to move and what worries you about it." />
      <Section>
        <div className="grid gap-12 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <h2 className="sr-only">Contact form</h2>
            <LeadForm kind="contact" submitLabel="Send message" messageLabel="Your message" requireMessage messageHint="What system do you have, and what do you need from the new one?" />
          </div>
          <aside className="space-y-5 lg:col-span-5" aria-label="Other ways to get in touch">
            <div className="rounded-lg border border-border bg-surface-muted p-6">
              <MessageSquare className="h-6 w-6 text-primary" aria-hidden="true" />
              <h2 className="mt-3 text-lg font-semibold">What happens to your message</h2>
              <p className="mt-2 text-foreground-muted">It is stored in the demo database, where the administrator of this demonstration can read it. There is no sales team behind this form.</p>
            </div>
            <div className="rounded-lg border border-border bg-surface-muted p-6">
              <Calendar className="h-6 w-6 text-primary" aria-hidden="true" />
              <h2 className="mt-3 text-lg font-semibold">Prefer to see it first?</h2>
              <p className="mt-2 text-foreground-muted">
                <SiteLink to="/demo" className="font-semibold underline">Request a demo</SiteLink>, or open the console and run a sample system yourself.
              </p>
            </div>
          </aside>
        </div>
      </Section>
    </>
  );
}

export function Demo() {
  usePageMeta({ title: "Request a demo", description: "Ask for a walkthrough of a sample migration, from upload to reconciliation report.", path: "/demo" });
  return (
    <>
      <PageHero eyebrow="Demo" title="See a migration from start to finish" lede="A walkthrough of one of the sample systems: discovery, mapping review, the dry run, the load and the reconciliation report." />
      <Section>
        <div className="grid gap-12 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <h2 className="sr-only">Request a demo</h2>
            <LeadForm kind="demo" submitLabel="Request a demo" messageLabel="What would you like to see?" messageHint="For example: the clinic sample, or how the review step works." />
          </div>
          <aside className="lg:col-span-5" aria-label="What the demo covers">
            <h2 className="text-xl font-semibold">What the demo covers</h2>
            <ol className="mt-4 list-decimal space-y-3 pl-5 text-foreground-muted">
              <li>Upload a legacy database, or pick a sample system.</li>
              <li>Watch discovery recover the structure, with its evidence.</li>
              <li>Review the proposed mappings and the generated rules.</li>
              <li>Run the dry run, load, and read the reconciliation report.</li>
              <li>Look at the quarantine, and roll the job back.</li>
            </ol>
            <p className="mt-6 text-foreground-muted">
              You do not have to wait: <SiteLink to="/login" className="font-semibold underline">open the console</SiteLink> and run the sample yourself.
            </p>
          </aside>
        </div>
      </Section>
    </>
  );
}
