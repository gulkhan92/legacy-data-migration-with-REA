import { Container } from "@/components/layout";
import { SiteLink } from "@/components/SiteLink";
import { Button } from "@/components/ui/button";
import { usePageMeta } from "@/lib/seo";

export default function NotFound() {
  usePageMeta({ title: "Page not found", description: "This page does not exist.", noindex: true });
  return (
    <Container className="py-24 text-center">
      <p className="text-sm font-semibold uppercase tracking-wider text-teal-text">Error 404</p>
      <h1 className="mt-3 text-4xl font-bold">This page does not exist</h1>
      <p className="mx-auto mt-4 max-w-xl text-lg text-foreground-muted">The address may be mistyped, or the page may have moved. These places will get you back on track.</p>
      <div className="mt-8 flex flex-wrap justify-center gap-3">
        <Button asChild>
          <SiteLink to="/">Go to the home page</SiteLink>
        </Button>
        <Button asChild variant="outline">
          <SiteLink to="/platform">Explore the platform</SiteLink>
        </Button>
        <Button asChild variant="outline">
          <SiteLink to="/contact">Contact us</SiteLink>
        </Button>
      </div>
    </Container>
  );
}
