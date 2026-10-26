import * as NavigationMenuPrimitive from "@radix-ui/react-navigation-menu";
import { ChevronDown } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/cn";

export const NavigationMenu = forwardRef<ElementRef<typeof NavigationMenuPrimitive.Root>, ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Root>>(
  ({ className, children, ...props }, ref) => (
    <NavigationMenuPrimitive.Root ref={ref} className={cn("relative flex items-center", className)} {...props}>
      {children}
    </NavigationMenuPrimitive.Root>
  ),
);
NavigationMenu.displayName = "NavigationMenu";

export const NavigationMenuList = forwardRef<ElementRef<typeof NavigationMenuPrimitive.List>, ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.List>>(
  ({ className, ...props }, ref) => <NavigationMenuPrimitive.List ref={ref} className={cn("flex list-none items-center gap-1", className)} {...props} />,
);
NavigationMenuList.displayName = "NavigationMenuList";

export const NavigationMenuItem = NavigationMenuPrimitive.Item;

export const navTriggerClass =
  "group inline-flex h-10 items-center justify-center gap-1 rounded-md px-3 text-sm font-semibold text-foreground transition-colors hover:bg-surface-muted hover:text-primary data-[state=open]:bg-surface-muted data-[state=open]:text-primary";

export const NavigationMenuTrigger = forwardRef<ElementRef<typeof NavigationMenuPrimitive.Trigger>, ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Trigger>>(
  ({ className, children, ...props }, ref) => (
    <NavigationMenuPrimitive.Trigger ref={ref} className={cn(navTriggerClass, className)} {...props}>
      {children}
      <ChevronDown className="h-4 w-4 transition-transform duration-200 group-data-[state=open]:rotate-180" aria-hidden="true" />
    </NavigationMenuPrimitive.Trigger>
  ),
);
NavigationMenuTrigger.displayName = "NavigationMenuTrigger";

export const NavigationMenuContent = forwardRef<ElementRef<typeof NavigationMenuPrimitive.Content>, ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Content>>(
  ({ className, ...props }, ref) => (
    <NavigationMenuPrimitive.Content ref={ref} className={cn("absolute left-0 top-0 w-full md:w-auto", className)} {...props} />
  ),
);
NavigationMenuContent.displayName = "NavigationMenuContent";

export const NavigationMenuLink = NavigationMenuPrimitive.Link;

/** The panel under the bar. It is positioned by the viewport element so that mega menus of different widths sit under their trigger. */
export const NavigationMenuViewport = forwardRef<ElementRef<typeof NavigationMenuPrimitive.Viewport>, ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Viewport>>(
  ({ className, ...props }, ref) => (
    <div className="absolute left-0 top-full flex justify-start">
      <NavigationMenuPrimitive.Viewport
        ref={ref}
        className={cn(
          "relative mt-2 h-[var(--radix-navigation-menu-viewport-height)] w-[var(--radix-navigation-menu-viewport-width)] origin-top overflow-hidden rounded-lg border border-border bg-surface-raised shadow-lg data-[state=open]:animate-menu-in",
          className,
        )}
        {...props}
      />
    </div>
  ),
);
NavigationMenuViewport.displayName = "NavigationMenuViewport";
