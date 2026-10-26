import { renderHook, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MetricBand, statsFrom } from "@/components/MetricBand";
import { ApiError, type PublicMetrics } from "@/lib/api";
import { useCountUp } from "@/lib/useCountUp";
import { renderApp } from "@/test-utils";

const metrics: PublicMetrics = {
  demo: true,
  as_of: "2026-10-22",
  days: 721,
  organizations: 101,
  jobs_run: 6404,
  records_migrated: 6_340_468_794,
  success_rate: 0.9388,
  average_quality_score: 96.2,
  average_duration_seconds: 293.2,
};

const mocks = vi.hoisted(() => ({ metrics: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, publicApi: { ...original.publicApi, metrics: mocks.metrics }, sessionApi: { ...original.sessionApi, me: vi.fn().mockRejectedValue(new original.ApiError(401, "x", "x")) } };
});

beforeEach(() => mocks.metrics.mockReset());

describe("statsFrom", () => {
  it("formats every figure the way the band shows it", () => {
    const stats = statsFrom(metrics);
    expect(stats.map((s) => [s.id, s.render(s.value)])).toEqual([
      ["records", "6.3B"],
      ["jobs", "6,404"],
      ["success", "93.9%"],
      ["quality", "96.2"],
      ["duration", "4.9 min"],
    ]);
  });

  it("leaves out a figure that does not exist yet", () => {
    const stats = statsFrom({ ...metrics, success_rate: null, average_quality_score: null, average_duration_seconds: null });
    expect(stats.map((s) => s.id)).toEqual(["records", "jobs"]);
  });
});

describe("MetricBand", () => {
  it("labels the figures as the demo environment's and shows the true values", async () => {
    mocks.metrics.mockResolvedValue(metrics);
    renderApp(<MetricBand />);
    expect(screen.getByRole("heading", { name: "Demo environment figures" })).toBeInTheDocument();
    expect(await screen.findByText("6,404", { selector: ".sr-only" })).toBeInTheDocument();
    expect(screen.getByText("6.3B", { selector: ".sr-only" })).toBeInTheDocument();
    expect(screen.getByText("Migration jobs run")).toBeInTheDocument();
    expect(screen.getByText(/As of October 22, 2026. Simulated organizations/)).toBeInTheDocument();
  });

  it("says so when the figures cannot be loaded", async () => {
    // one retry, so two failures
    mocks.metrics.mockRejectedValueOnce(new ApiError(0, "network_error", "down")).mockRejectedValueOnce(new ApiError(0, "network_error", "down"));
    renderApp(<MetricBand />);
    expect(await screen.findByText("The demo figures are unavailable right now.", {}, { timeout: 4000 })).toBeInTheDocument();
  });

  it("says so when the demo environment is empty", async () => {
    mocks.metrics.mockResolvedValue({ ...metrics, jobs_run: 0, records_migrated: 0, as_of: null });
    renderApp(<MetricBand />);
    expect(await screen.findByText("The demo environment has no figures yet.")).toBeInTheDocument();
  });
});

describe("useCountUp", () => {
  it("shows the real value from the start, so a page that is never scrolled never shows a made up zero", () => {
    const { result } = renderHook(() => useCountUp<HTMLParagraphElement>(1234));
    expect(result.current.value).toBe(1234);
  });

  it("shows the real value at once when the person prefers reduced motion", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true, addEventListener() {}, removeEventListener() {} } as unknown as MediaQueryList);
    const { result } = renderHook(() => useCountUp<HTMLParagraphElement>(99));
    expect(result.current.value).toBe(99);
    vi.restoreAllMocks();
  });

  it("is zero while there is nothing to show", () => {
    const { result } = renderHook(() => useCountUp<HTMLParagraphElement>(null));
    expect(result.current.value).toBe(0);
  });
});
