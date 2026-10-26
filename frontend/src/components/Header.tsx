import { ArrowRight, ExternalLink, LogIn, Menu } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { Logo } from "@/components/Logo";
import { SiteLink } from "@/components/SiteLink";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { NavigationMenu, NavigationMenuContent, NavigationMenuItem, NavigationMenuLink, NavigationMenuList, NavigationMenuTrigger, NavigationMenuViewport, navTriggerClass } from "@/components/ui/navigation-menu";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { homeFor, useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { mainNav, utilityLinks, type NavGroup, type NavLink } from "@/site/content/nav";

export function UtilityBar() {
  return (
    <div className="hidden bg-navy text-sm text-white/85 md:block">
      <div className="mx-auto flex h-9 w-full max-w-site items-center justify-between px-8">
        <p className="text-white/70">A demonstration project with sample data</p>
        <nav aria-label="Utility" className="flex items-center gap-5">
          {utilityLinks.map((link) => (
            <SiteLink key={link.to} to={link.to} className="text-white/85 hover:text-white hover:underline">
              {link.label}
            </SiteLink>
          ))}
        </nav>
      </div>
    </div>
  );
}

function MegaLink({ link }: { link: NavLink }) {
  return (
    <NavigationMenuLink asChild>
      <SiteLink to={link.to} external={link.external} className="block rounded-md p-3 transition-colors hover:bg-surface-muted focus-visible:bg-surface-muted">
        <span className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
          {link.label}
          {link.external ? <ExternalLink className="h-3.5 w-3.5 text-foreground-subtle" aria-hidden="true" /> : null}
        </span>
        {link.description ? <span className="mt-0.5 block text-sm text-foreground-muted">{link.description}</span> : null}
      </SiteLink>
    </NavigationMenuLink>
  );
}

function TopLink({ to, children }: { to: string; children: string }) {
  const { pathname } = useLocation();
  const active = pathname === to || pathname.startsWith(`${to}/`);
  return (
    <NavigationMenuLink asChild>
      <SiteLink to={to} aria-current={active ? "page" : undefined} className={cn(navTriggerClass, active && "text-primary")}>
        {children}
      </SiteLink>
    </NavigationMenuLink>
  );
}

function DesktopNav() {
  return (
    <NavigationMenu aria-label="Main" className="hidden lg:flex">
      <NavigationMenuList>
        {mainNav.map((group) =>
          group.sections ? (
            <NavigationMenuItem key={group.label}>
              <NavigationMenuTrigger>{group.label}</NavigationMenuTrigger>
              <NavigationMenuContent>
                <div className={cn("grid gap-6 p-5", group.sections.length > 1 ? "w-[640px] grid-cols-[1.4fr_1fr]" : "w-[420px]")}>
                  {group.sections.map((section) => (
                    <div key={section.title}>
                      <p className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">{section.title}</p>
                      <ul className="space-y-0.5">
                        {section.links.map((link) => (
                          <li key={link.to + link.label}>
                            <MegaLink link={link} />
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </NavigationMenuContent>
            </NavigationMenuItem>
          ) : (
            <NavigationMenuItem key={group.label}>
              <TopLink to={group.to ?? "/"}>{group.label}</TopLink>
            </NavigationMenuItem>
          ),
        )}
      </NavigationMenuList>
      <NavigationMenuViewport />
    </NavigationMenu>
  );
}

function MobileGroup({ group }: { group: NavGroup }) {
  if (!group.sections) {
    return (
      <SheetClose asChild>
        <SiteLink to={group.to ?? "/"} className="block border-b border-border py-4 text-lg font-semibold text-foreground">
          {group.label}
        </SiteLink>
      </SheetClose>
    );
  }
  return (
    <AccordionItem value={group.label}>
      <AccordionTrigger className="py-4">{group.label}</AccordionTrigger>
      <AccordionContent>
        <ul className="space-y-1">
          {group.sections.flatMap((section) => section.links).map((link) => (
            <li key={link.to + link.label}>
              <SheetClose asChild>
                <SiteLink to={link.to} external={link.external} className="block rounded-md px-2 py-2.5 text-base font-medium text-foreground hover:bg-surface-muted">
                  {link.label}
                </SiteLink>
              </SheetClose>
            </li>
          ))}
        </ul>
      </AccordionContent>
    </AccordionItem>
  );
}

function MobileMenu() {
  const { me } = useAuth();
  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu">
          <Menu className="h-6 w-6" aria-hidden="true" />
        </Button>
      </SheetTrigger>
      <SheetContent aria-describedby="mobile-menu-description">
        <SheetTitle className="text-lg font-bold text-navy">Menu</SheetTitle>
        <SheetDescription id="mobile-menu-description" className="sr-only">
          Site navigation
        </SheetDescription>
        <nav aria-label="Mobile">
          <Accordion type="single" collapsible>
            {mainNav.map((group) => (
              <MobileGroup key={group.label} group={group} />
            ))}
          </Accordion>
          <ul className="mt-4 space-y-1 border-t border-border pt-4">
            {utilityLinks.map((link) => (
              <li key={link.to}>
                <SheetClose asChild>
                  <SiteLink to={link.to} className="block px-2 py-2 text-base text-foreground-muted hover:text-foreground">
                    {link.label}
                  </SiteLink>
                </SheetClose>
              </li>
            ))}
          </ul>
        </nav>
        <div className="mt-auto flex flex-col gap-3 pt-4">
          <SheetClose asChild>
            <Button asChild size="lg">
              <SiteLink to={me ? homeFor(me.role) : "/login"}>Open console</SiteLink>
            </Button>
          </SheetClose>
          {me ? null : (
            <SheetClose asChild>
              <Button asChild variant="outline" size="lg">
                <SiteLink to="/login">Sign in</SiteLink>
              </Button>
            </SheetClose>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

/**
 * The sticky header. It condenses when the page is scrolled (a smaller mark, a shadow, a translucent background) without changing
 * its height, so nothing below it ever moves.
 */
export function Header() {
  const [condensed, setCondensed] = useState(false);
  const { me } = useAuth();

  useEffect(() => {
    const onScroll = () => setCondensed(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      data-condensed={condensed}
      className={cn("sticky top-0 z-40 border-b transition-[background-color,box-shadow,border-color] duration-200", condensed ? "border-border bg-background/90 shadow-md backdrop-blur" : "border-transparent bg-background")}
    >
      <div className="mx-auto flex h-16 w-full max-w-site items-center justify-between gap-4 px-4 md:px-8">
        <div className="flex items-center gap-2 lg:gap-8">
          <MobileMenu />
          <SiteLink to="/" aria-label="LegacyLift, home" className="origin-left rounded-md">
            <Logo className={cn("origin-left transition-transform duration-200", condensed && "scale-90")} />
          </SiteLink>
          <DesktopNav />
        </div>
        <div className="flex items-center gap-2">
          {me ? null : (
            <SiteLink to="/login" className="hidden items-center gap-1.5 rounded-md px-3 py-2 text-sm font-semibold text-foreground hover:bg-surface-muted sm:inline-flex">
              <LogIn className="h-4 w-4" aria-hidden="true" />
              Sign in
            </SiteLink>
          )}
          <Button asChild size="sm" className="hidden sm:inline-flex">
            <SiteLink to={me ? homeFor(me.role) : "/login"}>
              Open console
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </SiteLink>
          </Button>
        </div>
      </div>
    </header>
  );
}
