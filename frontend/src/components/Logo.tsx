import { cn } from "@/lib/cn";

/** The mark: a stack of old records lifted into a new square. Drawn here, so there is nothing to download. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("h-8 w-8", className)} role="img" aria-label="LegacyLift mark">
      <rect width="32" height="32" rx="8" fill="rgb(15 98 254)" />
      <path d="M8 22.5h11M8 18h11M8 13.5h11" stroke="#fff" strokeOpacity=".55" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M21 25V9m0 0-4.5 4.5M21 9l4.5 4.5" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}

export function Logo({ className, onDark = false }: { className?: string; onDark?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark />
      <span className={cn("text-lg font-bold tracking-tight", onDark ? "text-white" : "text-navy")}>LegacyLift</span>
    </span>
  );
}
