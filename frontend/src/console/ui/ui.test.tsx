import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";
import { OrgSwitcher } from "../shell/topbar";
import { ErrorBoundary } from "./ErrorBoundary";
import { EmptyState, ErrorState, QueryView, describeError } from "./states";
import { ToastProvider, useToast } from "./toast";

const mocks = vi.hoisted(() => ({ organizations: vi.fn(), switchOrg: vi.fn(), me: vi.fn() }));
vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return { ...original, consoleApi: { ...original.consoleApi, organizations: mocks.organizations, switchOrg: mocks.switchOrg } };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, sessionApi: { ...original.sessionApi, me: mocks.me } };
});

const me = { user_id: "u1", email: "analyst@example.com", full_name: "Demo Analyst", org_id: "o1", org_name: "Acme", role: "analyst" as const };

function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }));
  return (
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={["/app/overview"]}>
          <ToastProvider>{children}</ToastProvider>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  window.localStorage.setItem("legacylift.signed-in", "1");
  mocks.organizations.mockReset();
  mocks.switchOrg.mockReset();
  mocks.me.mockReset().mockResolvedValue(me);
});

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error("the page broke");
  return <p>The page works</p>;
}

describe("ErrorBoundary", () => {
  it("shows a message instead of an empty window, and the rest of the page stays", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <div>
        <nav aria-label="Console">still here</nav>
        <ErrorBoundary>
          <Bomb explode />
        </ErrorBoundary>
      </div>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong on this page");
    expect(screen.getByRole("navigation", { name: "Console" })).toBeInTheDocument();
    quiet.mockRestore();
  });

  it("recovers when the route changes", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <ErrorBoundary resetKey="/a">
        <Bomb explode />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    rerender(
      <ErrorBoundary resetKey="/b">
        <Bomb explode={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("The page works")).toBeInTheDocument();
    quiet.mockRestore();
  });

  it("lets a person try again", async () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    let explode = true;
    function Flaky() {
      return <Bomb explode={explode} />;
    }
    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    explode = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("The page works")).toBeInTheDocument();
    quiet.mockRestore();
  });
});

describe("states", () => {
  it("explains an error in the words of the API, and offers a retry", async () => {
    const retry = vi.fn();
    render(<ErrorState error={new ApiError(403, "forbidden", "You do not have access")} onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("You do not have access");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalled();
    expect(describeError(new Error("plain"))).toBe("plain");
    expect(describeError("odd")).toBe("Something went wrong");
  });

  it("has an empty state with an action", () => {
    render(<EmptyState title="Nothing yet" action={<button type="button">Start</button>}>Create one to begin.</EmptyState>);
    expect(screen.getByRole("heading", { name: "Nothing yet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start" })).toBeInTheDocument();
  });

  it("shows a placeholder while loading, the error when it fails and the data when it arrives", () => {
    const base = { refetch: vi.fn() };
    const { rerender } = render(<QueryView query={{ ...base, isPending: true, isError: false } as never}>{() => <p>data</p>}</QueryView>);
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    rerender(<QueryView query={{ ...base, isPending: false, isError: true, error: new Error("nope") } as never}>{() => <p>data</p>}</QueryView>);
    expect(screen.getByRole("alert")).toHaveTextContent("nope");
    rerender(<QueryView query={{ ...base, isPending: false, isError: false, data: 5 } as never}>{(n: number) => <p>data {n}</p>}</QueryView>);
    expect(screen.getByText("data 5")).toBeInTheDocument();
  });
});

describe("toasts", () => {
  function Trigger() {
    const toast = useToast();
    return (
      <>
        <button onClick={() => toast.success("Saved", "The plan changed")}>ok</button>
        <button onClick={() => toast.error("Failed", "Try again later")}>bad</button>
      </>
    );
  }

  it("announce a success politely and an error assertively, and can be dismissed", async () => {
    render(<ToastProvider><Trigger /></ToastProvider>);
    await userEvent.click(screen.getByRole("button", { name: "ok" }));
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
    await userEvent.click(screen.getByRole("button", { name: "bad" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Failed");
    await userEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Dismiss message" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("OrgSwitcher", () => {
  it("shows the name without a menu when there is only one organization", async () => {
    mocks.organizations.mockResolvedValue([{ org_id: "o1", name: "Acme", role: "analyst", current: true }]);
    render(<Providers><OrgSwitcher /></Providers>);
    expect(await screen.findByText("Acme")).toBeInTheDocument();
    await waitFor(() => expect(mocks.organizations).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /Switch organization/ })).not.toBeInTheDocument();
  });

  it("lists the organizations of a person who belongs to several, and switches", async () => {
    mocks.organizations.mockResolvedValue([
      { org_id: "o1", name: "Acme", role: "analyst", current: true },
      { org_id: "o2", name: "Globex", role: "admin", current: false },
    ]);
    mocks.switchOrg.mockResolvedValue({});
    mocks.me.mockResolvedValueOnce(me).mockResolvedValue({ ...me, org_id: "o2", org_name: "Globex", role: "admin" });
    render(<Providers><OrgSwitcher /></Providers>);
    await userEvent.click(await screen.findByRole("button", { name: /Switch organization/ }));
    expect(await screen.findByRole("menuitem", { name: /Acme/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /Globex/ }));
    await waitFor(() => expect(mocks.switchOrg).toHaveBeenCalledWith("o2"));
    expect(await screen.findByText("Switched to Globex")).toBeInTheDocument();
  });
});
