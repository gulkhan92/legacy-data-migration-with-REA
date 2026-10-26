import { useEffect, useRef } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Footer } from "@/components/Footer";
import { Header, UtilityBar } from "@/components/Header";
import { usePageLoading } from "@/lib/pageLoading";

/** On a route change: back to the top (or to the anchor), and announce the new page to screen readers. */
function RouteEffects() {
  const { pathname, hash } = useLocation();
  const announcer = useRef<HTMLDivElement>(null);
  const first = useRef(true);

  useEffect(() => {
    if (hash) {
      const target = document.getElementById(decodeURIComponent(hash.slice(1)));
      if (target) {
        target.scrollIntoView();
        return;
      }
    }
    window.scrollTo(0, 0);
    if (first.current) {
      first.current = false;
      return;
    }
    // Wait for the page's own title, then say it.
    const timer = window.setTimeout(() => {
      if (announcer.current) announcer.current.textContent = document.title;
      document.getElementById("main")?.focus({ preventScroll: true });
    }, 120);
    return () => window.clearTimeout(timer);
  }, [pathname, hash]);

  return <div ref={announcer} role="status" aria-live="polite" className="sr-only" />;
}

export function SiteLayout() {
  const loading = usePageLoading();
  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only z-50 rounded-md bg-primary px-4 py-2 font-semibold text-primary-foreground focus:not-sr-only focus:fixed focus:left-4 focus:top-4">
        Skip to content
      </a>
      <UtilityBar />
      <Header />
      <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
        <Outlet />
      </main>
      <div style={loading ? { visibility: "hidden" } : undefined} aria-hidden={loading || undefined}>
        <Footer />
      </div>
      <RouteEffects />
    </div>
  );
}
