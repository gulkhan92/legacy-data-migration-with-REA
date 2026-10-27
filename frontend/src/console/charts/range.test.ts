import { describe, expect, it } from "vitest";
import { resolveRange } from "./range";

const state = (query: string) => resolveRange(new URLSearchParams(query));

describe("resolveRange", () => {
  it("defaults to the last 90 days by day", () => {
    const r = state("");
    expect(r).toMatchObject({ preset: "90d", granularity: "day", compare: false, valid: true });
    expect(r.params).toEqual({ granularity: "day", days: 90 });
  });

  it("counts a preset back from the latest data, which the server knows", () => {
    expect(state("range=30d").params).toEqual({ granularity: "day", days: 30 });
    expect(state("range=12m").params).toEqual({ granularity: "week", days: 365 });
    expect(state("range=24m").params).toEqual({ granularity: "month", days: 730 });
  });

  it("lets the person choose the bucket and the comparison", () => {
    expect(state("range=90d&granularity=month&compare=1").params).toEqual({ granularity: "month", compare: true, days: 90 });
  });

  it("ignores values it does not know", () => {
    expect(state("range=forever&granularity=hour").params).toEqual({ granularity: "day", days: 90 });
  });

  it("takes a custom range only when both days are valid and in order", () => {
    expect(state("range=custom&from=2026-01-01&to=2026-03-31").params).toEqual({ granularity: "day", from: "2026-01-01", to: "2026-03-31" });
    expect(state("range=custom&from=2026-03-31&to=2026-01-01").valid).toBe(false);
    expect(state("range=custom&from=2026-01-01").valid).toBe(false);
    expect(state("range=custom").params).toEqual({ granularity: "day" });
  });
});
