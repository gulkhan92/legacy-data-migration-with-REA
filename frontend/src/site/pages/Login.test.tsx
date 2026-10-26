import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type Me } from "@/lib/api";
import Login, { safeNext } from "@/site/pages/Login";
import { renderApp } from "@/test-utils";

const mocks = vi.hoisted(() => ({ me: vi.fn(), login: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, sessionApi: { ...original.sessionApi, me: mocks.me, login: mocks.login } };
});

const user = (role: Me["role"]): Me => ({ user_id: "u1", email: `${role}@example.test`, full_name: "Test User", org_id: "o1", org_name: "Acme", role });

function page(route = "/login") {
  return renderApp(
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/app/overview" element={<p>Overview page</p>} />
      <Route path="/app/migrations" element={<p>Migrations page</p>} />
      <Route path="/app/billing" element={<p>Billing page</p>} />
      <Route path="*" element={<p>Elsewhere</p>} />
    </Routes>,
    { route },
  );
}

beforeEach(() => {
  window.localStorage.removeItem("legacylift.signed-in");
  mocks.me.mockReset().mockRejectedValue(new ApiError(401, "not_authenticated", "Not authenticated"));
  mocks.login.mockReset();
});

describe("safeNext", () => {
  it.each([
    ["/app/billing", "/app/billing"],
    ["/app/migrations?status=open", "/app/migrations?status=open"],
    ["//evil.example", null],
    ["https://evil.example", null],
    ["/\\evil.example", null],
    ["javascript:alert(1)", null],
    ["", null],
    [null, null],
    [undefined, null],
  ])("accepts only paths on this site: %j", (value, expected) => expect(safeNext(value)).toBe(expected));
});

describe("Login page", () => {
  it("validates before it asks the server", async () => {
    page();
    await userEvent.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your email address")).toBeInTheDocument();
    expect(screen.getByText("Enter your password")).toBeInTheDocument();
    expect(mocks.login).not.toHaveBeenCalled();
  });

  it("says plainly when the credentials are wrong, and mentions the lockout", async () => {
    mocks.login.mockRejectedValue(new ApiError(401, "not_authenticated", "Invalid credentials"));
    page();
    await userEvent.type(await screen.findByLabelText(/Email/), "analyst@example.test");
    await userEvent.type(screen.getByLabelText(/Password/), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The email or password is not correct");
    expect(screen.getByRole("alert")).toHaveTextContent("locked for 15 minutes");
  });

  it("tells the person to wait when sign in is rate limited", async () => {
    mocks.login.mockRejectedValue(new ApiError(429, "rate_limited", "x"));
    page();
    await userEvent.type(await screen.findByLabelText(/Email/), "analyst@example.test");
    await userEvent.type(screen.getByLabelText(/Password/), "whatever-it-is");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests");
  });

  it.each([
    ["analyst", "Migrations page"],
    ["admin", "Overview page"],
    ["viewer", "Overview page"],
  ] as const)("sends a %s to their own entry page", async (role, landing) => {
    mocks.login.mockResolvedValue(user(role));
    page();
    await userEvent.type(await screen.findByLabelText(/Email/), `${role}@example.test`);
    await userEvent.type(screen.getByLabelText(/Password/), "a-long-enough-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(landing)).toBeInTheDocument();
    expect(mocks.login).toHaveBeenCalledWith(`${role}@example.test`, "a-long-enough-password");
  });

  it("returns to the page that asked for a sign in", async () => {
    mocks.login.mockResolvedValue(user("admin"));
    page("/login?next=%2Fapp%2Fbilling");
    await userEvent.type(await screen.findByLabelText(/Email/), "admin@example.test");
    await userEvent.type(screen.getByLabelText(/Password/), "a-long-enough-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Billing page")).toBeInTheDocument();
  });

  it("ignores a next address that leaves the site", async () => {
    mocks.login.mockResolvedValue(user("analyst"));
    page("/login?next=https%3A%2F%2Fevil.example");
    await userEvent.type(await screen.findByLabelText(/Email/), "analyst@example.test");
    await userEvent.type(screen.getByLabelText(/Password/), "a-long-enough-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Migrations page")).toBeInTheDocument();
  });

  it("does not ask the server about a session when nothing suggests there is one", async () => {
    window.localStorage.removeItem("legacylift.signed-in");
    page();
    await screen.findByLabelText(/Email/);
    expect(mocks.me).not.toHaveBeenCalled(); // a visitor makes no failing request
  });

  it("does not show the form to someone who is already signed in", async () => {
    window.localStorage.setItem("legacylift.signed-in", "1"); // set at the last sign in
    mocks.me.mockResolvedValue(user("analyst"));
    page();
    expect(await screen.findByText("Migrations page")).toBeInTheDocument();
  });

  it("shows and hides the password, and explains the forgotten password honestly", async () => {
    page();
    const password = await screen.findByLabelText(/Password/);
    expect(password).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide password" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Forgot your password?" }));
    expect(screen.getByText(/not built in this demonstration/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Forgot your password?" })).toHaveAttribute("aria-expanded", "true"));
  });
});
