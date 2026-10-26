import { useEffect } from "react";

const SITE = "LegacyLift";
const ORIGIN = "https://legacylift.example";

type Meta = { title: string; description: string; path?: string; noindex?: boolean };

function setMeta(selector: string, attribute: "name" | "property", key: string, content: string): void {
  let element = document.head.querySelector<HTMLMetaElement>(selector);
  if (!element) {
    element = document.createElement("meta");
    element.setAttribute(attribute, key);
    document.head.appendChild(element);
  }
  element.setAttribute("content", content);
}

/** Title, description, canonical and social tags for the current page. A single page application cannot be crawled the way a static page is, so the shell in index.html carries sensible defaults too. */
export function usePageMeta({ title, description, path, noindex }: Meta): void {
  useEffect(() => {
    const full = title === SITE ? title : `${title} | ${SITE}`;
    document.title = full;
    setMeta('meta[name="description"]', "name", "description", description);
    setMeta('meta[property="og:title"]', "property", "og:title", full);
    setMeta('meta[property="og:description"]', "property", "og:description", description);
    setMeta('meta[name="robots"]', "name", "robots", noindex ? "noindex, nofollow" : "index, follow");
    if (path !== undefined) {
      setMeta('meta[property="og:url"]', "property", "og:url", `${ORIGIN}${path}`);
      let link = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
      if (!link) {
        link = document.createElement("link");
        link.rel = "canonical";
        document.head.appendChild(link);
      }
      link.href = `${ORIGIN}${path}`;
    }
  }, [title, description, path, noindex]);
}
