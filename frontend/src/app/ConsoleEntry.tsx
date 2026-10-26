import { Loader2, LogOut } from "lucide-react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Logo } from "@/components/Logo";
import { SiteLink } from "@/components/SiteLink";
import { Button } from "@/components/ui/button";
import { useAuth, useSessionCheck } from "@/lib/auth";
import { usePageMeta } from "@/lib/seo";

/** Everything under /app needs a session. The server enforces it as well; this only sends a signed out person to the sign in page. */
export function ProtectedRoute() {
  useSessionCheck();
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center" role="status">
        <Loader2 className="h-8 w-8 animate-spin text-primary" aria-hidden="true" />
        <span className="sr-only">Loading</span>
      </div>
    );
  }
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  return <Outlet />;
}

/**
 * The entry point of the console. The console itself (migrations, the wizard, analytics, billing, team and settings) is the next
 * phase; this page confirms who is signed in, with which role, and where each role will land.
 */
export function ConsoleEntry() {
  usePageMeta({ title: "Console", description: "The LegacyLift console.", noindex: true });
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  if (!me) return null;
  async function signOut() {
    // Leave first: ending the session re-renders the protected route, which would send a signed out person to the sign in page.
    navigate("/", { replace: true });
    await logout();
  }
  return (
    <div className="min-h-screen bg-surface-muted">
      <header className="border-b border-border bg-background">
        <div className="mx-auto flex h-16 max-w-site items-center justify-between px-4 md:px-8">
          <SiteLink to="/" aria-label="LegacyLift, home">
            <Logo />
          </SiteLink>
          <Button variant="outline" size="sm" onClick={() => void signOut()}>
            <LogOut className="h-4 w-4" aria-hidden="true" />
            Sign out
          </Button>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto max-w-3xl px-4 py-16 md:px-8">
        <p className="text-sm font-semibold uppercase tracking-wider text-teal-text">Signed in</p>
        <h1 className="mt-2 text-3xl font-bold">Welcome, {me.full_name}</h1>
        <dl className="mt-6 grid gap-4 rounded-lg border border-border bg-background p-6 sm:grid-cols-3">
          <div>
            <dt className="text-sm text-foreground-muted">Organization</dt>
            <dd className="font-semibold">{me.org_name}</dd>
          </div>
          <div>
            <dt className="text-sm text-foreground-muted">Role</dt>
            <dd className="font-semibold capitalize">{me.role}</dd>
          </div>
          <div>
            <dt className="text-sm text-foreground-muted">You asked for</dt>
            <dd className="break-all font-mono text-sm">{location.pathname}</dd>
          </div>
        </dl>
        <p className="mt-8 text-lg text-foreground-muted">
          The console (migrations, the new migration wizard, analytics, billing, team and settings) is built in the next phase. Everything it will use already exists in the API, which you can
          explore in the <a href="/docs" className="font-semibold text-primary underline">API reference</a>.
        </p>
      </main>
    </div>
  );
}
