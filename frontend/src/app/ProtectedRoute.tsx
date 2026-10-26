import { Loader2, ShieldAlert } from "lucide-react";
import { Link, Navigate, Outlet, useLocation } from "react-router-dom";
import { PageHeader } from "@/console/ui/page";
import { canVisit } from "@/console/shell/nav";
import { homeFor, useAuth, useSessionCheck } from "@/lib/auth";

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

/** A page that this role may not open. The server refuses the same requests; this explains it instead of showing an empty page of errors. */
export function RoleGate() {
  const { me } = useAuth();
  const { pathname } = useLocation();
  if (!me || canVisit(me.role, pathname)) return <Outlet />;
  return (
    <div role="alert" className="mx-auto max-w-lg py-16 text-center">
      <ShieldAlert className="mx-auto h-10 w-10 text-foreground-subtle" aria-hidden="true" />
      <PageHeader title="You do not have access to this page" description={`Your role is ${me.role}. Ask an administrator of ${me.org_name} if you need it.`} />
      <Link to={homeFor(me.role)} className="font-semibold text-primary underline">
        Go to your start page
      </Link>
    </div>
  );
}
