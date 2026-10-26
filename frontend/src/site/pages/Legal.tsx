import { Info } from "lucide-react";
import { Container, PageHero } from "@/components/layout";
import { usePageMeta } from "@/lib/seo";

function SampleNotice() {
  return (
    <div role="note" className="mb-8 flex gap-3 rounded-lg border border-warning/40 bg-warning-soft p-4 text-foreground">
      <Info className="mt-0.5 h-5 w-5 shrink-0 text-warning" aria-hidden="true" />
      <p>
        <strong>Sample template.</strong> This page is an example written for a demonstration project. It is not legal advice and has not been reviewed by a lawyer. Replace it before
        using the software for real.
      </p>
    </div>
  );
}

export function Privacy() {
  usePageMeta({ title: "Privacy policy (sample)", description: "A sample privacy policy for the LegacyLift demonstration site.", path: "/legal/privacy" });
  return (
    <>
      <PageHero eyebrow="Legal" title="Privacy policy (sample)" lede="What this demonstration site stores, and why." />
      <Container className="py-12">
        <SampleNotice />
        <div className="prose-site">
          <h2>What the public site collects</h2>
          <p>The contact, demo and newsletter forms store what you type: your email address and, if you choose to give them, your name, company and message. They are stored in the demo database and are readable by the administrator of the demonstration.</p>
          <p>The public site sets no tracking cookies and loads no advertising or analytics scripts. Fonts and images are served from the same site.</p>
          <h2>What signing in stores</h2>
          <p>
            Signing in sets two cookies that the browser sends back to the site: a short lived access token and a refresh token. They cannot be read by scripts on the page. The server stores
            only a hash of the refresh token. Sign ins, uploads and similar actions are recorded in an audit log for the organization.
          </p>
          <h2>Data you upload</h2>
          <p>Uploaded files are encrypted before they are stored, are analysed without being executed, and are visible only to your organization. Personal data found by profiling is not kept in stored profiles and is not sent to a language model.</p>
          <h2>Retention and deletion</h2>
          <p>Messages from the forms stay until the administrator deletes them. A migration job and what it loaded can be removed by rolling it back. Source files can be deleted from the console.</p>
          <h2>Contact</h2>
          <p>Use the contact form. Because this is a demonstration, there is no data protection officer.</p>
        </div>
      </Container>
    </>
  );
}

export function Terms() {
  usePageMeta({ title: "Terms of use (sample)", description: "Sample terms of use for the LegacyLift demonstration site.", path: "/legal/terms" });
  return (
    <>
      <PageHero eyebrow="Legal" title="Terms of use (sample)" lede="The rules for using this demonstration." />
      <Container className="py-12">
        <SampleNotice />
        <div className="prose-site">
          <h2>A demonstration</h2>
          <p>LegacyLift is a demonstration project. It is provided as it is, without any promise of availability, accuracy or fitness for a purpose, and it should not be used for data you cannot afford to lose.</p>
          <h2>Sample data</h2>
          <p>The sample systems, the customers, the usage and the revenue shown in the demo environment are synthetic or simulated. Nothing on this site describes a real customer or a real payment.</p>
          <h2>Prices</h2>
          <p>The plans and prices are illustrative. No payment is taken, and no invoice has any legal effect.</p>
          <h2>Acceptable use</h2>
          <p>Do not upload data you have no right to process, do not try to reach other organizations' data, and do not use the service to attack other systems.</p>
          <h2>Changes</h2>
          <p>These sample terms may change as the project develops.</p>
        </div>
      </Container>
    </>
  );
}
