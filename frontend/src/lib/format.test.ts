import { describe, expect, it } from "vitest";
import { formatCents, formatCompact, formatDate, formatDuration, formatInt, formatPercent, formatRate } from "@/lib/format";

describe("format", () => {
  it("prints whole dollars without cents and others with them", () => {
    expect(formatCents(49_900)).toBe("$499");
    expect(formatCents(250_000)).toBe("$2,500");
    expect(formatCents(0)).toBe("$0");
    expect(formatCents(1_250)).toBe("$12.50");
    expect(formatCents(95_000)).toBe("$950");
  });

  it("prints a price per thousand records from cents", () => {
    expect(formatRate(20)).toBe("$0.20");
    expect(formatRate(50)).toBe("$0.50");
    expect(formatRate(8)).toBe("$0.08");
  });

  it("groups digits and compacts big numbers", () => {
    expect(formatInt(25_000_000)).toBe("25,000,000");
    expect(formatCompact(2_000_000)).toBe("2M");
    expect(formatCompact(100_000)).toBe("100K");
    expect(formatCompact(6_340_468_794)).toBe("6.3B");
  });

  it("formats ratios, durations and dates", () => {
    expect(formatPercent(0.9388)).toBe("93.9%");
    expect(formatPercent(1, 0)).toBe("100%");
    expect(formatDuration(41)).toBe("41 s");
    expect(formatDuration(293.2)).toBe("4.9 min");
    expect(formatDate("2026-10-22")).toBe("October 22, 2026");
  });
});
