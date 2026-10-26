import { ChevronRight, Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { Logo, LogoMark } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { ErrorBoundary } from "../ui/ErrorBoundary";
import { ToastProvider } from "../ui/toast";
import { crumbsFor, navFor } from "./nav";
import { CommandPalette } from "./palette";
import { useConsoleTheme } from "./theme";
import { NotificationsBell, OrgSwitcher, PaletteButton, ThemeToggle, UserMenu } from "./topbar";

const COLLAPSED = "legacylift.sidebar-collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED) === "1";
  } catch {
    return false;
  }
}

function NavList({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const { me } = useAuth();
  if (!me) return null;
  return (
    <nav aria-label="Console" className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
      {navFor(me.role).map((group) => (
        <div key={group.group}>
          <p className={cn("mb-1 px-3 text-xs font-semibold uppercase tracking-wider text-foreground-muted", collapsed && "sr-only")}>{group.group}</p>
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === "/app/migrations" || item.to === "/app/overview"}
                    onClick={onNavigate}
                    title={collapsed ? item.label : undefined}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                        isActive ? "bg-primary-soft text-primary-ink" : "text-foreground-muted hover:bg-surface-muted hover:text-foreground",
                        collapsed && "justify-center px-0",
                      )
                    }
                  >
                    <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
                    <span className={cn(collapsed && "sr-only")}>{item.label}</span>
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function Breadcrumbs() {
  const { pathname } = useLocation();
  const crumbs = crumbsFor(pathname);
  if (crumbs.length < 2) return null;
  return (
    <nav aria-label="Breadcrumb" className="mb-3 print:hidden">
      <ol className="flex flex-wrap items-center gap-1 text-sm text-foreground-muted">
        {crumbs.map((c, i) => (
          <li key={c.to} className="flex items-center gap-1">
            {i > 0 ? <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" /> : null}
            {i === crumbs.length - 1 ? (
              <span aria-current="page" className="font-medium text-foreground">
                {c.label}
              </span>
            ) : (
              <Link to={c.to} className="hover:text-foreground hover:underline">
                {c.label}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

function PageFallback() {
  return (
    <div role="status" aria-label="Loading the page" className="space-y-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-4 w-96 max-w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** The frame of every console page: a collapsible sidebar, a top bar with the organization switcher, the command palette, notifications and the user menu, then the page. */
export function ConsoleLayout() {
  useConsoleTheme();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function toggleCollapsed() {
    setCollapsed((value) => {
      try {
        window.localStorage.setItem(COLLAPSED, value ? "0" : "1");
      } catch {
        /* the choice then lasts until the page closes */
      }
      return !value;
    });
  }

  return (
    <ToastProvider>
      <div className="min-h-screen bg-surface-muted text-foreground">
        <a
          href="#console-main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[70] focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
        >
          Skip to content
        </a>
        <aside
          className={cn("fixed inset-y-0 left-0 z-30 hidden flex-col print:!hidden border-r border-border bg-surface transition-[width] md:flex", collapsed ? "w-16" : "w-60")}
        >
          <div className={cn("flex h-16 items-center border-b border-border", collapsed ? "justify-center" : "px-5")}>
            <Link to="/app/overview" aria-label="LegacyLift console home">
              {collapsed ? <LogoMark /> : <Logo />}
            </Link>
          </div>
          <NavList collapsed={collapsed} />
          <div className="border-t border-border p-3">
            <Button
              variant="ghost"
              size="sm"
              className={cn("w-full", collapsed ? "px-0" : "justify-start")}
              onClick={toggleCollapsed}
              aria-expanded={!collapsed}
              aria-label={collapsed ? "Expand the sidebar" : "Collapse the sidebar"}
            >
              {collapsed ? <PanelLeftOpen className="h-5 w-5" aria-hidden="true" /> : <PanelLeftClose className="h-5 w-5" aria-hidden="true" />}
              {collapsed ? null : <span>Collapse</span>}
            </Button>
          </div>
        </aside>

        <div className={cn("transition-[padding] print:!pl-0", collapsed ? "md:pl-16" : "md:pl-60")}>
          <header className="sticky top-0 z-20 flex h-16 items-center gap-2 border-b border-border bg-surface px-3 print:hidden md:px-6">
            <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
              <SheetTrigger asChild>
                <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open the menu">
                  <Menu className="h-5 w-5" aria-hidden="true" />
                </Button>
              </SheetTrigger>
              <SheetContent className="gap-0 p-0">
                <SheetTitle className="sr-only">Console menu</SheetTitle>
                <SheetDescription className="sr-only">Pages of the console</SheetDescription>
                <div className="flex h-16 items-center border-b border-border px-5">
                  <Logo />
                </div>
                <NavList collapsed={false} onNavigate={() => setMobileOpen(false)} />
              </SheetContent>
            </Sheet>
            <OrgSwitcher />
            <div className="ml-auto flex items-center gap-1">
              <PaletteButton onOpen={() => setPaletteOpen(true)} />
              <ThemeToggle />
              <NotificationsBell />
              <UserMenu />
            </div>
          </header>
          <main id="console-main" tabIndex={-1} className="mx-auto max-w-[1400px] px-4 py-6 focus:outline-none md:px-8">
            <Breadcrumbs />
            <ErrorBoundary resetKey={location.pathname}>
              <Suspense fallback={<PageFallback />}>
                <Outlet />
              </Suspense>
            </ErrorBoundary>
          </main>
        </div>
        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      </div>
    </ToastProvider>
  );
}
