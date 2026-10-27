// Turns the project's markdown documents (../docs/*.md) into src/generated/docs.json, which the documentation viewer renders.
// Runs before dev and build. The documents are part of this repository, so the HTML is trusted, but the script still refuses
// anything that looks like an embedded script or a long dash (a team standard).
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Marked } from "marked";
// The two long dash characters, written as character codes so that this file does not contain them.
const LONG_DASH = new RegExp(`[${String.fromCharCode(0x2013, 0x2014)}]`);

const here = dirname(fileURLToPath(import.meta.url));
const docsDir = resolve(here, "../../docs");
const outFile = resolve(here, "../src/generated/docs.json");

const documents = [
  { slug: "architecture", file: "architecture.md", summary: "How each part of the platform is built: discovery, profiling, mapping, the model router, transformation, jobs, billing and analytics." },
  { slug: "security", file: "security.md", summary: "The security controls that are implemented, each covered by an automated test." },
  { slug: "security-review", file: "security-review.md", summary: "The review of those controls against OWASP ASVS, the scans that were run, what they found and what was fixed." },
  { slug: "evaluation", file: "evaluation.md", summary: "What the platform achieves on the two sample systems, measured: discovery, defect detection, mapping, the loaded data, time and tokens." },
  { slug: "load-check", file: "load-check.md", summary: "A file of 200,000 rows migrated with the memory of the process measured." },
];
const slugs = new Set(documents.map((d) => d.slug));

const slugify = (text) =>
  text
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/&[a-z]+;/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

const marked = new Marked({ gfm: true });
const result = [];

for (const doc of documents) {
  const source = readFileSync(resolve(docsDir, doc.file), "utf8");
  if (LONG_DASH.test(source)) throw new Error(`${doc.file} contains a long dash`);
  const headings = [];
  const used = new Map();
  let html = marked.parse(source, { async: false });
  if (/<script/i.test(html) || /\son\w+\s*=/i.test(html)) throw new Error(`${doc.file} contains active content`);
  html = html.replace(/<h([1-4])>([\s\S]*?)<\/h\1>/g, (_, level, inner) => {
    const base = slugify(inner) || "section";
    const count = used.get(base) ?? 0;
    used.set(base, count + 1);
    const id = count === 0 ? base : `${base}-${count + 1}`;
    if (Number(level) <= 3) headings.push({ id, level: Number(level), text: inner.replace(/<[^>]+>/g, "") });
    return `<h${level} id="${id}">${inner}</h${level}>`;
  });
  // A table or a code block that scrolls sideways must be reachable with the keyboard (WCAG 2.1.1).
  html = html.replace(/<table>/g, '<div class="table-scroll" role="region" aria-label="Table" tabindex="0"><table>').replace(/<\/table>/g, "</table></div>").replace(/<pre>/g, '<pre tabindex="0">');
  // A link from one document to another (a relative link to a .md file) goes to the viewer page of that document.
  html = html.replace(/<a href="([a-z0-9-]+)\.md(#[^"]*)?"/g, (_, slug, hash = "") => {
    if (!slugs.has(slug)) throw new Error(`${doc.file} links to ${slug}.md, which is not one of the documents of the viewer`);
    return `<a href="/resources/docs/${slug}${hash}"`;
  });
  html = html.replace(/<a href="(https?:[^"]+)"/g, '<a href="$1" rel="noopener noreferrer" target="_blank"');
  const title = (headings.find((h) => h.level === 1)?.text ?? doc.slug).trim();
  result.push({ slug: doc.slug, title, summary: doc.summary, html, headings: headings.filter((h) => h.level > 1) });
}

mkdirSync(dirname(outFile), { recursive: true });
writeFileSync(outFile, JSON.stringify(result));
console.log(`docs: ${result.map((d) => `${d.slug} (${d.headings.length} headings)`).join(", ")}`);
