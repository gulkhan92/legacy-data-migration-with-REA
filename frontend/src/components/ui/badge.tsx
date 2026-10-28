import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold", {
  variants: {
    variant: {
      neutral: "bg-surface-muted text-foreground-muted",
      primary: "bg-primary-soft text-primary-ink",
      teal: "bg-teal-soft text-teal-text",
      violet: "bg-violet-soft text-violet",
      success: "bg-success-soft text-success",
      warning: "bg-warning-soft text-warning",
      danger: "bg-danger-soft text-danger",
      onDark: "bg-white/10 text-white",
    },
  },
  defaultVariants: { variant: "neutral" },
});

export function Badge({ className, variant, ...props }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
