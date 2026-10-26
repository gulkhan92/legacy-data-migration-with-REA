import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpen, Code2, Newspaper } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Container, PageHero, Section, SectionHeading } from "@/components/layout";
import { CtaBand } from "@/components/sections";
import { SiteLink } from "@/components/SiteLink";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";
import { PageLoadingMarker } from "@/lib/pageLoading";
import { usePageMeta } from "@/lib/seo";
import { articleBySlug, articles } from "@/site/content/articles";
import NotFound from "@/site/pages/NotFound";

type Doc = { slug: string; title: string; summary: string; html: string; headings: { id: string; level: number; text: string }[] };

/** The documents are generated from /docs at build time and loaded only when someone opens the viewer. */
function useDocs() {
  return useQuery({
    queryKey: ["docs"],
    queryFn: async () => (await import("@/generated/docs.json")).default as Doc[],
    staleTime: Infinity,
  });
}

export function ResourcesIndex() {
  usePageMeta({ title: "Resources", description: "Documentation, the API reference and technical articles about discovery, mapping and reconciliation.", path: "/resources" });
  return (
    <>
      <PageHero eyebrow="Resources" title="Documentation and technical notes" lede="Written from the project itself: the architecture notes, the security notes and three articles about how the hard parts work." />
      <Section aria-labelledby="docs-heading">
        <SectionHeading title={<span id="docs-heading">Documentation</span>} />
        <ul className="grid gap-6 md:grid-cols-3">
          <li>
            <Card className="h-full">
              <CardHeader>
                <BookOpen className="h-7 w-7 text-primary" aria-hidden="true" />
                <CardTitle>Project documentation</CardTitle>
                <CardDescription className="text-base">Architecture and security notes, rendered from the repository.</CardDescription>
              </CardHeader>
              <CardContent>
                <SiteLink to="/resources/docs" className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">
                  Open the documentation <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </SiteLink>
              </CardContent>
            </Card>
          </li>
          <li>
            <Card className="h-full">
              <CardHeader>
                <Code2 className="h-7 w-7 text-primary" aria-hidden="true" />
                <CardTitle>API reference</CardTitle>
                <CardDescription className="text-base">Every route, its parameters, examples and the roles that may call it (OpenAPI).</CardDescription>
              </CardHeader>
              <CardContent>
                <a href="/docs" className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">
                  Open the API reference <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </a>
              </CardContent>
            </Card>
          </li>
          <li>
            <Card className="h-full">
              <CardHeader>
                <Newspaper className="h-7 w-7 text-primary" aria-hidden="true" />
                <CardTitle>Technical articles</CardTitle>
                <CardDescription className="text-base">How discovery, mapping and reconciliation work, and what they achieve.</CardDescription>
              </CardHeader>
              <CardContent>
                <a href="#articles" className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">
                  Read the articles <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </a>
              </CardContent>
            </Card>
          </li>
        </ul>
      </Section>
      <Section tone="muted" id="articles" aria-labelledby="articles-heading">
        <SectionHeading eyebrow="Articles" title={<span id="articles-heading">From the project</span>} />
        <ul className="grid gap-6 md:grid-cols-3">
          {articles.map((article) => (
            <li key={article.slug}>
              <Card className="flex h-full flex-col">
                <CardHeader>
                  <Badge variant="teal" className="self-start">{article.tag}</Badge>
                  <CardTitle className="mt-2">
                    <Link to={`/resources/articles/${article.slug}`} className="text-foreground hover:text-primary hover:underline">{article.title}</Link>
                  </CardTitle>
                  <CardDescription className="text-base">{article.description}</CardDescription>
                </CardHeader>
                <CardContent className="mt-auto text-sm text-foreground-muted">
                  <time dateTime={article.date}>{formatDate(article.date)}</time> &middot; {article.minutes} min read
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      </Section>
      <CtaBand />
    </>
  );
}

export function DocsIndex() {
  const { data, isPending } = useDocs();
  usePageMeta({ title: "Documentation", description: "The architecture and security documentation of LegacyLift.", path: "/resources/docs" });
  return (
    <>
      <PageHero eyebrow="Documentation" title="Project documentation" lede="These pages are generated from the documents in the repository, so they are always the ones the code is tested against." />
      <Section>
        {isPending || !data ? (
          <>
            <Skeleton className="h-40 w-full" />
            <PageLoadingMarker />
          </>
        ) : (
          <ul className="grid gap-6 md:grid-cols-2">
            {data.map((doc) => (
              <li key={doc.slug}>
                <Card className="h-full">
                  <CardHeader>
                    <CardTitle>
                      <Link to={`/resources/docs/${doc.slug}`} className="text-foreground hover:text-primary hover:underline">{doc.title}</Link>
                    </CardTitle>
                    <CardDescription className="text-base">{doc.summary}</CardDescription>
                  </CardHeader>
                  <CardContent className="text-sm text-foreground-muted">{doc.headings.filter((h) => h.level === 2).length} sections</CardContent>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}

export function DocViewer() {
  const { slug } = useParams();
  const { data, isPending } = useDocs();
  const doc = data?.find((d) => d.slug === slug);
  usePageMeta({ title: doc ? doc.title : "Documentation", description: doc?.summary ?? "Project documentation.", path: doc ? `/resources/docs/${doc.slug}` : undefined, noindex: !isPending && !doc });
  if (isPending) {
    return (
      <Container className="py-16">
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="mt-6 h-96 w-full" />
        <PageLoadingMarker />
      </Container>
    );
  }
  if (!doc) return <NotFound />;
  const toc = doc.headings.filter((h) => h.level === 2);
  return (
    <Container className="grid gap-10 py-12 lg:grid-cols-[260px_minmax(0,1fr)]">
      <nav aria-label="On this page" className="lg:sticky lg:top-24 lg:max-h-[calc(100vh-8rem)] lg:self-start lg:overflow-y-auto">
        <p className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-subtle">
          <Link to="/resources/docs" className="text-foreground-subtle hover:text-primary">Documentation</Link>
        </p>
        <ul className="space-y-1 border-l border-border">
          {toc.map((h) => (
            <li key={h.id}>
              <a href={`#${h.id}`} className="-ml-px block border-l-2 border-transparent py-1 pl-3 text-sm text-foreground-muted hover:border-primary hover:text-primary">
                {h.text}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <article className="min-w-0">
        {/* Trusted: generated at build time from this repository's own documents (scripts/build-docs.mjs). */}
        <div className="prose-site prose-wide max-w-4xl [&_h1]:text-4xl [&_h1]:font-bold" dangerouslySetInnerHTML={{ __html: doc.html }} />
      </article>
    </Container>
  );
}

export function ArticlePage() {
  const { slug } = useParams();
  const article = articleBySlug(slug);
  usePageMeta({ title: article?.title ?? "Not found", description: article?.description ?? "This article does not exist.", path: article ? `/resources/articles/${article.slug}` : undefined, noindex: !article });
  if (!article) return <NotFound />;
  const others = articles.filter((a) => a.slug !== article.slug);
  return (
    <>
      <PageHero
        eyebrow={article.tag}
        title={article.title}
        lede={article.description}
        breadcrumb={
          <nav aria-label="Breadcrumb">
            <ol className="flex flex-wrap items-center gap-2">
              <li><Link to="/resources" className="text-white/80 underline hover:text-white">Resources</Link></li>
              <li aria-hidden="true">/</li>
              <li aria-current="page" className="text-white">Article</li>
            </ol>
          </nav>
        }
      />
      <Section>
        <article className="mx-auto max-w-3xl">
          <p className="mb-8 text-sm text-foreground-muted">
            <time dateTime={article.date}>{formatDate(article.date)}</time> &middot; {article.minutes} min read &middot; Written from the project's own design notes
          </p>
          <div className="prose-site">
            {article.sections.map((section) => (
              <section key={section.heading}>
                <h2>{section.heading}</h2>
                {section.paragraphs.map((p) => (
                  <p key={p}>{p}</p>
                ))}
                {section.list ? (
                  <ul>
                    {section.list.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ))}
          </div>
        </article>
        <nav aria-label="More articles" className="mx-auto mt-14 max-w-3xl border-t border-border pt-8">
          <p className="mb-3 text-sm font-semibold uppercase tracking-wider text-foreground-subtle">More articles</p>
          <ul className="space-y-2">
            {others.map((other) => (
              <li key={other.slug}>
                <Link to={`/resources/articles/${other.slug}`} className="font-semibold text-primary hover:underline">{other.title}</Link>
              </li>
            ))}
          </ul>
        </nav>
      </Section>
      <CtaBand />
    </>
  );
}
