import { lazy, Suspense } from "react";
import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from "react-router-dom";
import { ConsoleEntry, ProtectedRoute } from "@/app/ConsoleEntry";
import { Container } from "@/components/layout";
import { SiteLayout } from "@/components/SiteLayout";
import { Skeleton } from "@/components/ui/skeleton";
import { PageLoadingMarker } from "@/lib/pageLoading";
import Home from "@/site/pages/Home";

// Every page except the home page is its own chunk, loaded when it is first visited.
const Platform = lazy(() => import("@/site/pages/Platform"));
const Pricing = lazy(() => import("@/site/pages/Pricing"));
const Security = lazy(() => import("@/site/pages/Security"));
const Company = lazy(() => import("@/site/pages/Company"));
const Status = lazy(() => import("@/site/pages/Status"));
const Login = lazy(() => import("@/site/pages/Login"));
const NotFound = lazy(() => import("@/site/pages/NotFound"));
const SolutionsIndex = lazy(() => import("@/site/pages/Solutions").then((m) => ({ default: m.SolutionsIndex })));
const Industry = lazy(() => import("@/site/pages/Solutions").then((m) => ({ default: m.Industry })));
const ResourcesIndex = lazy(() => import("@/site/pages/Resources").then((m) => ({ default: m.ResourcesIndex })));
const DocsIndex = lazy(() => import("@/site/pages/Resources").then((m) => ({ default: m.DocsIndex })));
const DocViewer = lazy(() => import("@/site/pages/Resources").then((m) => ({ default: m.DocViewer })));
const ArticlePage = lazy(() => import("@/site/pages/Resources").then((m) => ({ default: m.ArticlePage })));
const Contact = lazy(() => import("@/site/pages/Contact").then((m) => ({ default: m.Contact })));
const Demo = lazy(() => import("@/site/pages/Contact").then((m) => ({ default: m.Demo })));
const Privacy = lazy(() => import("@/site/pages/Legal").then((m) => ({ default: m.Privacy })));
const Terms = lazy(() => import("@/site/pages/Legal").then((m) => ({ default: m.Terms })));

/** Reserves the space of a page while its chunk loads, so the footer does not jump. */
function PageFallback() {
  return (
    <Container className="min-h-[70vh] py-16" role="status" aria-label="Loading">
      <Skeleton className="h-12 w-2/3 max-w-xl" />
      <Skeleton className="mt-6 h-5 w-full max-w-2xl" />
      <Skeleton className="mt-3 h-5 w-5/6 max-w-2xl" />
      <PageLoadingMarker />
    </Container>
  );
}

const page = (element: JSX.Element): JSX.Element => <Suspense fallback={<PageFallback />}>{element}</Suspense>;

export const routes: RouteObject[] = [
  {
    element: <SiteLayout />,
    children: [
      { index: true, element: <Home /> },
      { path: "platform", element: page(<Platform />) },
      { path: "solutions", element: page(<SolutionsIndex />) },
      { path: "solutions/:slug", element: page(<Industry />) },
      { path: "pricing", element: page(<Pricing />) },
      { path: "security", element: page(<Security />) },
      { path: "resources", element: page(<ResourcesIndex />) },
      { path: "resources/docs", element: page(<DocsIndex />) },
      { path: "resources/docs/:slug", element: page(<DocViewer />) },
      { path: "resources/articles/:slug", element: page(<ArticlePage />) },
      { path: "company", element: page(<Company />) },
      { path: "contact", element: page(<Contact />) },
      { path: "demo", element: page(<Demo />) },
      { path: "legal/privacy", element: page(<Privacy />) },
      { path: "legal/terms", element: page(<Terms />) },
      { path: "status", element: page(<Status />) },
      { path: "*", element: page(<NotFound />) },
    ],
  },
  { path: "login", element: page(<Login />) },
  { path: "app", element: <ProtectedRoute />, children: [{ index: true, element: <Navigate to="/app/overview" replace /> }, { path: "*", element: <ConsoleEntry /> }] },
];

export function createRouter() {
  return createBrowserRouter(routes);
}

export function App() {
  return <RouterProvider router={createRouter()} />;
}
