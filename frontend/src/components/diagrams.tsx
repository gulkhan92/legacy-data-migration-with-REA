import { architecture } from "@/site/content/platform";

/** Four steps in a row (a column on small screens). A hand built figure: nothing to download, sharp at every size. */
const steps = [
  { n: 1, title: "Upload", text: "A database, or a program and its data file. Encrypted at once, never executed." },
  { n: 2, title: "Discover and map", text: "The structure is recovered, every column is matched to a target field." },
  { n: 3, title: "Review", text: "You approve the mappings and the rules, and see a dry run on a sample." },
  { n: 4, title: "Load and prove", text: "One transaction, a reconciliation report, a quarantine, and a way back." },
];

export function PipelineIllustration() {
  return (
    <ol className="grid gap-6 md:grid-cols-4" aria-label="How it works">
      {steps.map((step, index) => (
        <li key={step.n} className="relative">
          {index < steps.length - 1 ? <span className="absolute left-12 top-6 hidden h-0.5 w-[calc(100%-1rem)] bg-gradient-to-r from-primary/60 to-teal/40 md:block" aria-hidden="true" /> : null}
          <span className="relative flex h-12 w-12 items-center justify-center rounded-full bg-primary text-lg font-bold text-primary-foreground shadow-md">{step.n}</span>
          <h3 className="mt-4 text-xl font-semibold">{step.title}</h3>
          <p className="mt-2 text-foreground-muted">{step.text}</p>
        </li>
      ))}
    </ol>
  );
}

const nodes: Record<string, { x: number; y: number }> = {
  browser: { x: 20, y: 30 },
  api: { x: 250, y: 30 },
  worker: { x: 480, y: 30 },
  db: { x: 250, y: 150 },
  rea: { x: 480, y: 150 },
  llm: { x: 710, y: 90 },
};
const links: [string, string][] = [
  ["browser", "api"],
  ["api", "worker"],
  ["api", "db"],
  ["worker", "db"],
  ["worker", "rea"],
  ["worker", "llm"],
];

/** How the parts fit together. Alternative text for the figure is the list below it. */
export function ArchitectureDiagram() {
  const box = { w: 190, h: 76 };
  const centre = (id: string) => ({ x: (nodes[id]?.x ?? 0) + box.w / 2, y: (nodes[id]?.y ?? 0) + box.h / 2 });
  return (
    <figure>
      <svg viewBox="0 0 920 260" className="h-auto w-full" role="img" aria-label="Architecture: the browser talks to the API; the API uses PostgreSQL and hands work to workers; workers use PostgreSQL, an isolated analysis runner and an optional model router.">
        {links.map(([a, b]) => (
          <line key={a + b} x1={centre(a).x} y1={centre(a).y} x2={centre(b).x} y2={centre(b).y} stroke="rgb(15 98 254)" strokeOpacity=".45" strokeWidth="2" strokeDasharray="6 5" />
        ))}
        {architecture.map((item) => {
          const at = nodes[item.id] ?? { x: 0, y: 0 };
          const dashed = item.id === "llm";
          return (
            <g key={item.id}>
              <rect x={at.x} y={at.y} width={box.w} height={box.h} rx="12" fill="#fff" stroke={dashed ? "rgb(75 88 106)" : "rgb(15 98 254)"} strokeWidth="2" strokeDasharray={dashed ? "5 4" : undefined} />
              <text x={at.x + 16} y={at.y + 32} fontSize="18" fontWeight="700" fill="rgb(11 31 58)">
                {item.label}
              </text>
              <text x={at.x + 16} y={at.y + 54} fontSize="13" fill="rgb(75 88 106)">
                {item.sub}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="sr-only">
        {architecture.map((item) => `${item.label}: ${item.sub}`).join(". ")}
      </figcaption>
    </figure>
  );
}
