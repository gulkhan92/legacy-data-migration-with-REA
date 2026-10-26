import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-60 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-sm hover:bg-primary-hover",
        secondary: "bg-navy text-white shadow-sm hover:bg-navy-800",
        outline: "border border-border-strong bg-background text-foreground hover:bg-surface-muted",
        ghost: "text-foreground hover:bg-surface-muted",
        link: "h-auto p-0 text-primary underline-offset-4 hover:underline",
        onDark: "bg-white text-navy shadow-sm hover:bg-primary-soft",
        outlineOnDark: "border border-white/50 text-white hover:bg-white/10",
      },
      size: { sm: "h-9 px-3 text-sm", md: "h-11 px-5 text-base", lg: "h-12 px-6 text-base", icon: "h-10 w-10" },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, type, ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...(asChild ? {} : { type: type ?? "button" })} {...props} />;
});
Button.displayName = "Button";
