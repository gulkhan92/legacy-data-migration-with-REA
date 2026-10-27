import { describe, expect, it } from "vitest";
import { formatBytes, formatCents, formatCentsCompact, formatCompact, formatDate, formatDay, formatDuration, formatInt, formatMs, formatPercent, formatRate, formatSignedPercent, timeAgo } from "@/lib/format";

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

describe("console formats", () => {
  it("prints cents as compact dollars", () => {
    expect(formatCentsCompact(7_996_000)).toBe("$80K");
    expect(formatCentsCompact(250_000)).toBe("$2.5K");
    expect(formatCentsCompact(0)).toBe("$0");
  });

  it("signs a change", () => {
    expect(formatSignedPercent(0.0597)).toBe("+6.0%");
    expect(formatSignedPercent(-0.025)).toBe("-2.5%");
    expect(formatSignedPercent(0)).toBe("0.0%");
  });

  it("picks the unit of a duration", () => {
    expect(formatMs(850)).toBe("850 ms");
    expect(formatMs(42_000)).toBe("42 s");
    expect(formatMs(330_000)).toBe("5.5 min");
    expect(formatMs(8_280_000)).toBe("2.3 h");
  });

  it("says how long ago, without moving with the clock", () => {
    const now = Date.parse("2026-10-26T12:00:00Z");
    expect(timeAgo("2026-10-26T11:59:30Z", now)).toBe("just now");
    expect(timeAgo("2026-10-26T11:55:00Z", now)).toBe("5 min ago");
    expect(timeAgo("2026-10-26T09:00:00Z", now)).toBe("3 h ago");
    expect(timeAgo("2026-10-24T12:00:00Z", now)).toBe("2 d ago");
  });

  it("prints a day without the time zone moving it", () => {
    expect(formatDay("2026-10-22")).toBe("Oct 22, 2026");
    expect(formatDay("2026-10-22T23:30:00+00:00")).toBe("Oct 22, 2026");
  });

  it("prints sizes", () => {
    expect(formatBytes(null)).toBe("");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(385_024)).toBe("376 KB");
    expect(formatBytes(26_000_000)).toBe("24.8 MB");
  });
});
