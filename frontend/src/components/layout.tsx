import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

/** The one content width of the site (1280 px) and its side gutters. */
export function Container({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mx-auto w-full max-w-site px-4 md:px-8", className)} {...props} />;
}

type Tone = "default" | "muted" | "dark";

const tones: Record<Tone, string> = {
  default: "bg-background",
  muted: "bg-surface-muted",
  dark: "bg-navy text-white [&_h1]:text-white [&_h2]:text-white [&_h3]:text-white",
};

export function Section({ tone = "default", className, children, id, ...props }: HTMLAttributes<HTMLElement> & { tone?: Tone }) {
  return (
    <section id={id} className={cn("py-16 md:py-24", tones[tone], className)} {...props}>
      <Container>{children}</Container>
    </section>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  lede,
  align = "left",
  tone = "default",
  as: Tag = "h2",
  className,
}: {
  eyebrow?: string;
  title: ReactNode;
  lede?: ReactNode;
  align?: "left" | "center";
  tone?: "default" | "dark";
  as?: "h1" | "h2" | "h3";
  className?: string;
}) {
  const dark = tone === "dark";
  return (
    <div className={cn("mb-10 max-w-3xl md:mb-14", align === "center" && "mx-auto text-center", className)}>
      {eyebrow ? <p className={cn("mb-3 text-sm font-semibold uppercase tracking-wider", dark ? "text-teal" : "text-teal-text")}>{eyebrow}</p> : null}
      <Tag className={cn("text-3xl font-bold md:text-4xl", dark && "text-white")}>{title}</Tag>
      {lede ? <p className={cn("mt-4 text-lg", dark ? "text-white/80" : "text-foreground-muted")}>{lede}</p> : null}
    </div>
  );
}

/** The top of an inner page: eyebrow, h1, lede, optional actions. */
export function PageHero({ eyebrow, title, lede, children, breadcrumb }: { eyebrow?: string; title: ReactNode; lede?: ReactNode; children?: ReactNode; breadcrumb?: ReactNode }) {
  return (
    <div className="relative overflow-hidden bg-navy text-white">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_30rem_at_85%_-10%,rgb(15_98_254/0.45),transparent),radial-gradient(40rem_24rem_at_0%_120%,rgb(0_163_163/0.25),transparent)]" aria-hidden="true" />
      <Container className="relative py-14 md:py-20">
        {breadcrumb ? <div className="mb-4 text-sm text-white/70">{breadcrumb}</div> : null}
        {eyebrow ? <p className="mb-3 text-sm font-semibold uppercase tracking-wider text-teal">{eyebrow}</p> : null}
        <h1 className="max-w-4xl text-4xl font-bold text-white md:text-5xl">{title}</h1>
        {lede ? <p className="mt-5 max-w-3xl text-lg text-white/85 md:text-xl">{lede}</p> : null}
        {children ? <div className="mt-8 flex flex-wrap gap-3">{children}</div> : null}
      </Container>
    </div>
  );
}
