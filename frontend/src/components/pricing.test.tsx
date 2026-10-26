import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CostCalculator, PlanCards, parseRecords } from "@/components/pricing";
import { ApiError, type Estimate, type PublicPlan } from "@/lib/api";
import { renderApp } from "@/test-utils";

const plans: PublicPlan[] = [
  { code: "starter", name: "Starter", monthly_fee_cents: 0, included_records: 100_000, overage_cents_per_1000: 50 },
  { code: "professional", name: "Professional", monthly_fee_cents: 49_900, included_records: 2_000_000, overage_cents_per_1000: 20 },
  { code: "enterprise", name: "Enterprise", monthly_fee_cents: 250_000, included_records: 25_000_000, overage_cents_per_1000: 8 },
];

const priced = (p: PublicPlan, records: number): Estimate => {
  const over = Math.max(0, records - p.included_records);
  const overage = Math.round((over / 1000) * p.overage_cents_per_1000);
  return { plan: p.code, currency: "USD", records, included_records: p.included_records, overage_records: over, base_cents: p.monthly_fee_cents, overage_cents: overage, total_cents: p.monthly_fee_cents + overage };
};

const mocks = vi.hoisted(() => ({ plans: vi.fn(), estimate: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, publicApi: { ...original.publicApi, plans: mocks.plans, estimate: mocks.estimate }, sessionApi: { ...original.sessionApi, me: vi.fn().mockRejectedValue(new original.ApiError(401, "not_authenticated", "Not authenticated")) } };
});

beforeEach(() => {
  mocks.plans.mockReset().mockResolvedValue(plans);
  mocks.estimate.mockReset().mockImplementation(async (plan: string, records: number) => priced(plans.find((p) => p.code === plan) as PublicPlan, records));
});

describe("parseRecords", () => {
  it.each([
    ["2,000,000", 2_000_000],
    ["2 000 000", 2_000_000],
    ["2_000_000", 2_000_000],
    ["0", 0],
    ["10000000000", 10_000_000_000],
  ])("reads %s", (text, expected) => expect(parseRecords(text)).toBe(expected));

  it.each(["", "abc", "1.5", "-3", "1e6", "10000000001", "99999999999999999999"])("refuses %j", (text) => expect(parseRecords(text)).toBeNull());
});

describe("PlanCards", () => {
  it("shows the three plans from the API with their prices and allowances", async () => {
    renderApp(<PlanCards />);
    expect(await screen.findByRole("heading", { name: "Professional" })).toBeInTheDocument();
    expect(screen.getByText("Free")).toBeInTheDocument();
    expect(screen.getByText("$499")).toBeInTheDocument();
    expect(screen.getByText("$2,500")).toBeInTheDocument();
    expect(screen.getByText("2,000,000")).toBeInTheDocument();
    expect(screen.getByText("$0.08")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start free" })).toHaveAttribute("href", "/login");
  });

  it("says so, and offers a retry, when the plans cannot be loaded", async () => {
    // the page tries twice (one retry) before it gives up
    mocks.plans.mockRejectedValueOnce(new ApiError(0, "network_error", "down")).mockRejectedValueOnce(new ApiError(0, "network_error", "down"));
    renderApp(<PlanCards />);
    expect(await screen.findByRole("alert", {}, { timeout: 4000 })).toHaveTextContent("The plans could not be loaded");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Starter" })).toBeInTheDocument();
  });
});

describe("CostCalculator", () => {
  it("prices every plan for the volume and marks the lowest", async () => {
    renderApp(<CostCalculator />);
    // 2,000,000 records: Starter 0 + 1.9M over at $0.50 = $950, Professional $499, Enterprise $2,500
    expect(await screen.findByText("$950")).toBeInTheDocument();
    expect(screen.getByText("$499")).toBeInTheDocument();
    expect(screen.getByText("$2,500")).toBeInTheDocument();
    const lowest = screen.getByText("Lowest").closest("li") as HTMLElement;
    expect(within(lowest).getByText("professional")).toBeInTheDocument();
  });

  it("re-prices when a preset is chosen", async () => {
    renderApp(<CostCalculator />);
    await screen.findByText("$950");
    await userEvent.click(screen.getByRole("button", { name: "50K" }));
    await waitFor(() => expect(mocks.estimate).toHaveBeenCalledWith("starter", 50_000, expect.anything()));
    const lowest = (await screen.findByText("Lowest")).closest("li") as HTMLElement;
    expect(within(lowest).getByText("starter")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "50K" })).toHaveAttribute("aria-pressed", "true");
  });

  it("explains what is wrong with an input that is not a number, and does not call the API for it", async () => {
    renderApp(<CostCalculator />);
    await screen.findByText("$950");
    const calls = mocks.estimate.mock.calls.length;
    const input = screen.getByLabelText("Records loaded per month");
    await userEvent.clear(input);
    await userEvent.type(input, "lots");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a whole number of records");
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(mocks.estimate.mock.calls.length).toBe(calls);
  });

  it("reports a failure to load the prices", async () => {
    mocks.estimate.mockRejectedValue(new ApiError(503, "unavailable", "down"));
    renderApp(<CostCalculator />);
    expect(await screen.findByText(/could not be loaded from the API/, {}, { timeout: 4000 })).toBeInTheDocument();
  });
});
