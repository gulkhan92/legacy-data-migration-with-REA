import { describe, expect, it } from "vitest";
import { barOption, heatmapOption, lineOption, timeLineOption, tooltipHtml, waterfallOption } from "./builders";
import { chrome, colorOf, IDENTITY, RAMP, SLOTS } from "./palette";

const theme = chrome("light");
type Loose = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

describe("the palette", () => {
  it("has eight hues in each mode and no ninth", () => {
    expect(SLOTS.light).toHaveLength(8);
    expect(SLOTS.dark).toHaveLength(8);
    expect(new Set(SLOTS.light).size).toBe(8);
  });

  it("gives an identity the same slot in every chart", () => {
    expect(colorOf("professional", "light")).toBe(SLOTS.light[1]);
    expect(colorOf("professional", "dark")).toBe(SLOTS.dark[1]);
    expect(IDENTITY.groq).toBe(0);
    expect(IDENTITY.gemini).toBe(1);
    expect(colorOf("something-unknown", "light")).toBe(SLOTS.light[0]);
  });

  it("runs the ramp light to dark on the light surface and dark to light on the dark one", () => {
    expect(RAMP.light[0]).toBe("#cde2fb");
    expect(RAMP.dark[0]).toBe("#184f95");
    expect(RAMP.light).toHaveLength(RAMP.dark.length);
  });

  it("falls back to fixed ink where there is no stylesheet", () => {
    expect(theme.surface).toMatch(/^#|^rgb/);
    expect(chrome("dark").surface).not.toBe(theme.surface);
  });
});

describe("tooltipHtml", () => {
  it("escapes every label and value", () => {
    const html = tooltipHtml(theme, "<b>title</b>", [{ name: "<script>x</script>", color: "#000", value: "1 & 2" }]);
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<b>title");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("1 &amp; 2");
  });

  it("puts the value in the strong element and the name in the quiet one", () => {
    const html = tooltipHtml(theme, "Oct 22", [{ name: "MRR", color: "#2a78d6", value: "$80K" }]);
    expect(html).toMatch(/<strong[^>]*>\$80K<\/strong>/);
  });
});

describe("bars", () => {
  const spec = { categories: ["a", "b"], series: [{ key: "x", label: "X", color: "#111", values: [1, 2] }, { key: "y", label: "Y", color: "#222", values: [3, 4] }], unit: "Things", format: String };

  it("are at most 24 px thick", () => {
    const series = barOption(theme, spec).series as Loose[];
    expect(series.every((s) => s.barMaxWidth === 24)).toBe(true);
  });

  it("round the data end only, square at the baseline", () => {
    const vertical = (barOption(theme, spec).series as Loose[])[0]!;
    expect(vertical.itemStyle.borderRadius).toEqual([4, 4, 0, 0]);
    const horizontal = (barOption(theme, { ...spec, horizontal: true }).series as Loose[])[0]!;
    expect(horizontal.itemStyle.borderRadius).toEqual([0, 4, 4, 0]);
  });

  it("round only the outermost segment of a stack and leave a surface gap between segments", () => {
    const series = barOption(theme, { ...spec, stacked: true }).series as Loose[];
    expect(series[0]!.itemStyle.borderRadius).toBe(0);
    expect(series[1]!.itemStyle.borderRadius).toEqual([4, 4, 0, 0]);
    expect(series.every((s) => s.itemStyle.borderColor === theme.surface && s.stack === "total")).toBe(true);
  });

  it("draw hairline solid gridlines in the grid color", () => {
    const axis = barOption(theme, spec).yAxis as Loose;
    expect(axis.splitLine.lineStyle).toMatchObject({ width: 1, type: "solid", color: theme.grid });
  });

  it("label the end of a few bars on request only", () => {
    expect((barOption(theme, spec).series as Loose[])[0]!.label).toBeUndefined();
    expect((barOption(theme, { ...spec, endLabels: true }).series as Loose[])[0]!.label.show).toBe(true);
  });
});

describe("lines", () => {
  const spec = { periods: ["2026-01-01", "2026-02-01", "2026-03-01"], series: [{ key: "mrr", label: "MRR", color: "#2a78d6", values: [1, 2, null] as (number | null)[] }], granularity: "month" as const, unit: "USD", format: String };

  it("are 2 px with round joins, and area is a wash of 10 percent", () => {
    const [s] = lineOption(theme, { ...spec, area: true }).series as Loose[];
    expect(s!.lineStyle).toMatchObject({ width: 2, cap: "round", join: "round" });
    expect(s!.areaStyle.opacity).toBe(0.1);
  });

  it("mark only the last real point, with a ring in the surface color", () => {
    const [s] = lineOption(theme, spec).series as Loose[];
    const size = s!.symbolSize as (v: unknown, p: { dataIndex: number }) => number;
    expect([0, 1, 2].map((i) => size(null, { dataIndex: i }))).toEqual([0, 8, 0]);
    expect(s!.itemStyle).toMatchObject({ borderColor: theme.surface, borderWidth: 2 });
  });

  it("draw a projection dashed and a real series solid", () => {
    const real = lineOption(theme, spec).series as Loose[];
    expect(real[0]!.lineStyle.type).toBe("solid");
    const projected = lineOption(theme, { ...spec, series: [{ ...spec.series[0]!, dashed: true }] }).series as Loose[];
    expect(projected[0]!.lineStyle.type).toBe("dashed");
  });

  it("share one tooltip across the series at the pointed period", () => {
    expect((lineOption(theme, spec).tooltip as Loose).trigger).toBe("axis");
  });
});

describe("time lines", () => {
  const spec = {
    unit: "USD",
    format: String,
    labels: "month" as const,
    series: [
      { key: "mrr", label: "MRR", color: "#2a78d6", points: [["2026-01-01", 1], ["2026-02-01", 2]] as [string, number | null][] },
      { key: "projection", label: "Projection", color: "#eb6834", dashed: true, quietUntil: "2026-02-01", points: [["2026-02-01", 2], ["2026-03-01", 3]] as [string, number | null][] },
    ],
  };

  it("runs on a time axis so a projection can extend past the data", () => {
    const option = timeLineOption(theme, spec);
    expect((option.xAxis as Loose).type).toBe("time");
    expect((option.series as Loose[])[1]!.data[1][0]).toBe(Date.parse("2026-03-01T00:00:00Z"));
  });

  it("stacks parts of a whole without an end label on each", () => {
    const series = timeLineOption(theme, { ...spec, stack: true }).series as Loose[];
    expect(series.every((s) => s.stack === "total" && s.endLabel.show === false)).toBe(true);
  });

  it("hides the repeated start of a projection in the tooltip", () => {
    const formatter = (timeLineOption(theme, spec).tooltip as Loose).formatter as (items: unknown) => string;
    const at = Date.parse("2026-02-01T00:00:00Z");
    const html = formatter([
      { axisValue: at, seriesName: "MRR", seriesIndex: 0, color: "#2a78d6", value: [at, 2] },
      { axisValue: at, seriesName: "Projection", seriesIndex: 1, color: "#eb6834", value: [at, 2] },
    ]);
    expect(html).toContain("MRR");
    expect(html).not.toContain("Projection");
    const later = Date.parse("2026-03-01T00:00:00Z");
    expect(formatter([{ axisValue: later, seriesName: "Projection", seriesIndex: 1, color: "#eb6834", value: [later, 3] }])).toContain("Projection");
  });
});

describe("waterfall and heatmap", () => {
  it("lifts each bar with an invisible base series", () => {
    const steps = [
      { label: "Start", base: 0, value: 10, color: "#111", signed: 10 },
      { label: "New", base: 10, value: 5, color: "#222", signed: 5 },
    ];
    const series = waterfallOption(theme, steps, String, "USD").series as Loose[];
    expect(series[0]!.itemStyle.color).toBe("transparent");
    expect(series[0]!.data).toEqual([0, 10]);
    expect(series[1]!.data.map((d: Loose) => d.value)).toEqual([10, 5]);
  });

  it("uses one hue from the ramp for a scale of zero to one hundred", () => {
    const option = heatmapOption(theme, [{ x: 0, y: 0, value: 80 }], ["2025-01"], 1);
    const map = option.visualMap as Loose;
    expect([map.min, map.max]).toEqual([0, 100]);
    expect(map.inRange.color).toEqual([...RAMP.light]);
  });
});
