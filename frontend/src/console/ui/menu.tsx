import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/cn";

export const DropdownMenu = DropdownPrimitive.Root;
export const DropdownTrigger = DropdownPrimitive.Trigger;
export const DropdownLabel = DropdownPrimitive.Label;
export const DropdownSeparator = forwardRef<ElementRef<typeof DropdownPrimitive.Separator>, ComponentPropsWithoutRef<typeof DropdownPrimitive.Separator>>(
  ({ className, ...props }, ref) => <DropdownPrimitive.Separator ref={ref} className={cn("my-1 h-px bg-border", className)} {...props} />,
);
DropdownSeparator.displayName = "DropdownSeparator";

export const DropdownContent = forwardRef<ElementRef<typeof DropdownPrimitive.Content>, ComponentPropsWithoutRef<typeof DropdownPrimitive.Content>>(
  ({ className, sideOffset = 6, ...props }, ref) => (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        className={cn("z-50 min-w-56 animate-menu-in rounded-lg border border-border bg-surface-raised p-1 shadow-lg", className)}
        {...props}
      />
    </DropdownPrimitive.Portal>
  ),
);
DropdownContent.displayName = "DropdownContent";

export const DropdownItem = forwardRef<ElementRef<typeof DropdownPrimitive.Item>, ComponentPropsWithoutRef<typeof DropdownPrimitive.Item>>(({ className, ...props }, ref) => (
  <DropdownPrimitive.Item
    ref={ref}
    className={cn(
      "flex cursor-pointer select-none items-center gap-2 rounded-md px-3 py-2 text-sm text-foreground outline-none data-[disabled]:opacity-50 data-[highlighted]:bg-surface-muted",
      className,
    )}
    {...props}
  />
));
DropdownItem.displayName = "DropdownItem";

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;

export const PopoverContent = forwardRef<ElementRef<typeof PopoverPrimitive.Content>, ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>>(
  ({ className, sideOffset = 6, align = "end", ...props }, ref) => (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        ref={ref}
        sideOffset={sideOffset}
        align={align}
        className={cn("z-50 w-[min(24rem,calc(100vw-2rem))] animate-menu-in rounded-lg border border-border bg-surface-raised shadow-lg", className)}
        {...props}
      />
    </PopoverPrimitive.Portal>
  ),
);
PopoverContent.displayName = "PopoverContent";
