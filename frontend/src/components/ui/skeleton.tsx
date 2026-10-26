import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/** A placeholder of a fixed size: the page keeps its layout while data loads. Decorative, so hidden from assistive technology. */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-md bg-surface-muted", className)} {...props} />;
}
