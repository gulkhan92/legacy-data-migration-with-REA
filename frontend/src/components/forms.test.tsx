import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LeadForm, NewsletterForm, describeError } from "@/components/forms";
import { ApiError } from "@/lib/api";
import { renderApp } from "@/test-utils";

const mocks = vi.hoisted(() => ({ lead: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, publicApi: { ...original.publicApi, lead: mocks.lead }, sessionApi: { ...original.sessionApi, me: vi.fn().mockRejectedValue(new original.ApiError(401, "not_authenticated", "x")) } };
});

beforeEach(() => mocks.lead.mockReset().mockResolvedValue({ received: true }));

describe("describeError", () => {
  it("speaks plainly for the cases people meet", () => {
    expect(describeError(new ApiError(429, "rate_limited", "x"))).toMatch(/Too many requests/);
    expect(describeError(new ApiError(500, "internal_error", "boom"))).toMatch(/our side/);
    expect(describeError(new ApiError(0, "network_error", "The server could not be reached"))).toBe("The server could not be reached");
    expect(describeError(new ApiError(422, "validation_error", "The request is not valid"))).toBe("The request is not valid");
    expect(describeError(new Error("x"))).toBe("Something went wrong. Please try again.");
  });
});

describe("LeadForm", () => {
  it("asks for an email before it sends anything", async () => {
    renderApp(<LeadForm kind="demo" submitLabel="Request a demo" messageLabel="What would you like to see?" />);
    await userEvent.click(screen.getByRole("button", { name: "Request a demo" }));
    expect(await screen.findByText("Enter your email address")).toBeInTheDocument();
    expect(screen.getByLabelText(/Work email/)).toHaveAttribute("aria-invalid", "true");
    expect(mocks.lead).not.toHaveBeenCalled();
  });

  it("rejects an address that is not one", async () => {
    renderApp(<LeadForm kind="demo" submitLabel="Send" messageLabel="Message" />);
    await userEvent.type(screen.getByLabelText(/Work email/), "not-an-email");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
  });

  it("sends the kind and what was typed, leaves out what was not, and thanks the person", async () => {
    renderApp(<LeadForm kind="demo" submitLabel="Request a demo" messageLabel="What would you like to see?" />);
    await userEvent.type(screen.getByLabelText("Name"), "Grace Hopper");
    await userEvent.type(screen.getByLabelText(/Work email/), "grace@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Request a demo" }));
    await waitFor(() => expect(mocks.lead).toHaveBeenCalledTimes(1));
    expect(mocks.lead).toHaveBeenCalledWith({ kind: "demo", email: "grace@example.com", name: "Grace Hopper" });
    expect(await screen.findByRole("status")).toHaveTextContent("Thank you");
  });

  it("requires a message where the page says so", async () => {
    renderApp(<LeadForm kind="contact" submitLabel="Send message" messageLabel="Your message" requireMessage />);
    await userEvent.type(screen.getByLabelText(/Work email/), "grace@example.com");
    await userEvent.type(screen.getByLabelText(/Your message/), "short");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText(/at least 10 characters/)).toBeInTheDocument();
    expect(mocks.lead).not.toHaveBeenCalled();
  });

  it("shows a failure and keeps what was typed so it can be sent again", async () => {
    mocks.lead.mockRejectedValueOnce(new ApiError(429, "rate_limited", "x"));
    renderApp(<LeadForm kind="contact" submitLabel="Send message" messageLabel="Your message" />);
    await userEvent.type(screen.getByLabelText(/Work email/), "grace@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests");
    expect(screen.getByLabelText(/Work email/)).toHaveValue("grace@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Thank you");
  });

  it("hides the trap field from people and sends it when a bot fills it", async () => {
    const { container } = renderApp(<LeadForm kind="contact" submitLabel="Send message" messageLabel="Your message" />);
    const trap = container.querySelector('input[name="website"]') as HTMLInputElement;
    expect(trap.closest("[aria-hidden='true']")).not.toBeNull();
    expect(trap.tabIndex).toBe(-1);
    await userEvent.type(screen.getByLabelText(/Work email/), "bot@example.com");
    await userEvent.type(trap, "http://spam.example");
    await userEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mocks.lead).toHaveBeenCalledWith(expect.objectContaining({ website: "http://spam.example" })));
  });
});

describe("NewsletterForm", () => {
  it("stores a lead of the kind newsletter", async () => {
    renderApp(<NewsletterForm />);
    await userEvent.type(screen.getByLabelText("Stay in touch"), "reader@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));
    await waitFor(() => expect(mocks.lead).toHaveBeenCalledWith({ kind: "newsletter", email: "reader@example.com" }));
    expect(await screen.findByText(/Thank you, we will be in touch/)).toBeInTheDocument();
  });

  it("explains an invalid address next to the field", async () => {
    renderApp(<NewsletterForm />);
    await userEvent.type(screen.getByLabelText("Stay in touch"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));
    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(mocks.lead).not.toHaveBeenCalled();
  });
});
