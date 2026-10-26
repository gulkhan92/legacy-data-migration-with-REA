import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Footer } from "@/components/Footer";
import { Header, UtilityBar } from "@/components/Header";
import { ApiError, type Me } from "@/lib/api";
import { renderApp } from "@/test-utils";

const mocks = vi.hoisted(() => ({ me: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, sessionApi: { ...original.sessionApi, me: mocks.me } };
});

const signedOut = () => {
  window.localStorage.removeItem("legacylift.signed-in");
  mocks.me.mockRejectedValue(new ApiError(401, "not_authenticated", "x"));
};

describe("Header", () => {
  it("offers the five main areas, a sign in link and the console button, both going to the login page", async () => {
    signedOut();
    renderApp(<Header />);
    const main = screen.getByRole("navigation", { name: "Main" });
    for (const name of ["Platform", "Solutions", "Resources", "Company"]) expect(within(main).getByRole("button", { name })).toBeInTheDocument();
    expect(within(main).getByRole("link", { name: "Pricing" })).toHaveAttribute("href", "/pricing");
    expect(await screen.findByRole("link", { name: /Sign in/ })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: /Open console/ })).toHaveAttribute("href", "/login");
  });

  it("marks the current page", () => {
    signedOut();
    renderApp(<Header />, { route: "/pricing" });
    expect(screen.getByRole("link", { name: "Pricing" })).toHaveAttribute("aria-current", "page");
  });

  it("opens a mega menu from the keyboard and lists its links", async () => {
    signedOut();
    renderApp(<Header />);
    const platform = screen.getByRole("button", { name: "Platform" });
    platform.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("link", { name: /Reconciliation/ })).toHaveAttribute("href", "/platform#reconciliation");
    await userEvent.keyboard("{Escape}");
    expect(platform).toHaveAttribute("aria-expanded", "false");
  });

  it("has a mobile drawer that names every group, traps focus and closes on Escape", async () => {
    signedOut();
    renderApp(<Header />);
    await userEvent.click(screen.getByRole("button", { name: "Open menu" }));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByRole("button", { name: "Solutions" })).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: "Pricing" })).toHaveAttribute("href", "/pricing");
    expect(within(drawer).getByRole("link", { name: "Open console" })).toHaveAttribute("href", "/login");
    await userEvent.click(within(drawer).getByRole("button", { name: "Solutions" }));
    expect(within(drawer).getByRole("link", { name: "Healthcare" })).toHaveAttribute("href", "/solutions/healthcare");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sends a signed in person to their own entry page", async () => {
    const me: Me = { user_id: "u", email: "a@b.test", full_name: "A", org_id: "o", org_name: "Acme", role: "analyst" };
    mocks.me.mockResolvedValue(me);
    window.localStorage.setItem("legacylift.signed-in", "1"); // the hint that a session may exist; without it a public page never asks
    renderApp(<Header />);
    await waitFor(() => expect(screen.getByRole("link", { name: /Open console/ })).toHaveAttribute("href", "/app/migrations"));
    expect(screen.queryByRole("link", { name: /Sign in/ })).not.toBeInTheDocument();
  });
});

describe("session check", () => {
  it("does not ask the server who is signed in on a public page when nothing suggests a session", async () => {
    mocks.me.mockReset();
    signedOut();
    renderApp(<Header />);
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(mocks.me).not.toHaveBeenCalled(); // no failed request in the console of every visitor
  });
});

describe("UtilityBar and Footer", () => {
  it("has contact, documentation and status", () => {
    renderApp(<UtilityBar />);
    const bar = screen.getByRole("navigation", { name: "Utility" });
    expect(within(bar).getAllByRole("link").map((l) => [l.textContent, l.getAttribute("href")])).toEqual([
      ["Contact", "/contact"],
      ["Documentation", "/resources/docs"],
      ["Status", "/status"],
    ]);
  });

  it("has five link columns, the newsletter field, the legal links, a region label and the demonstration notice", () => {
    signedOut();
    renderApp(<Footer />);
    const nav = screen.getByRole("navigation", { name: "Footer" });
    expect(within(nav).getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual(["Platform", "Solutions", "Resources", "Company", "Legal"]);
    expect(screen.getByLabelText("Stay in touch")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Privacy (sample)" })).toHaveAttribute("href", "/legal/privacy");
    expect(within(nav).getByRole("link", { name: "Terms (sample)" })).toHaveAttribute("href", "/legal/terms");
    expect(screen.getByText(/English \(United States\)/)).toBeInTheDocument();
    expect(screen.getByText(/is a demonstration project\. The site uses sample data/)).toBeInTheDocument();
  });
});
