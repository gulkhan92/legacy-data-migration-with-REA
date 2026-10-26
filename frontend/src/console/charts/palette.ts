/**
 * The colors of every chart. The eight categorical hues are the validated reference palette of the data visualization method
 * (adjacent colorblind separation, lightness band, contrast on the surface they sit on); the order is what keeps them apart, so a
 * series always takes its slot by identity and never by rank. Run `validate_palette.js` against the surface before changing a value.
 *
 * Light surface #ffffff (the console's `surface`), dark surface #0c1c34. Three light hues (aqua, yellow, magenta) are below 3:1 on the
 * light surface, which the method allows only with visible labels or a table view: every chart has the legend, the tooltip and a table twin.
 */
export type Mode = "light" | "dark";

export const SLOTS: Record<Mode, readonly string[]> = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
};

/**
 * Identity to slot. The same thing has the same color on every chart: a plan, a provider, a mapping method, a kind of MRR movement.
 * Each group takes the first slots in order, which is the order the palette was validated in.
 */
export const IDENTITY: Record<string, number> = {
  // plans
  starter: 0,
  professional: 1,
  enterprise: 2,
  // language model providers
  groq: 0,
  gemini: 1,
  // how a column was mapped
  memory: 0,
  embedding: 1,
  llm: 2,
  human: 3,
  // MRR movements
  new: 0,
  expansion: 1,
  contraction: 2,
  churned: 3,
  // revenue kinds
  subscription: 0,
  usage: 1,
  // percentiles
  p50: 0,
  p95: 1,
};

export function colorOf(identity: string, mode: Mode, fallback = 0): string {
  const slots = SLOTS[mode];
  return slots[IDENTITY[identity] ?? fallback] ?? slots[0] ?? "#2a78d6";
}

/** The sequential blue ramp (one hue). Light mode runs light to dark; on the dark surface the low end must recede, so it runs dark to light. */
export const RAMP: Record<Mode, readonly string[]> = {
  light: ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#104281"],
  dark: ["#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#6da7ec", "#9ec5f4"],
};

/** Ink, grid and surface of a chart, from the console's own tokens so that a theme change repaints them (with fixed fallbacks where there is no document). */
export type Chrome = { surface: string; ink: string; inkSecondary: string; inkMuted: string; grid: string; axis: string; mode: Mode };

const FALLBACK: Record<Mode, Chrome> = {
  light: { surface: "#ffffff", ink: "#0b1f3a", inkSecondary: "#4b586a", inkMuted: "#5c6879", grid: "#dde3ea", axis: "#c3ccd7", mode: "light" },
  dark: { surface: "#0c1c34", ink: "#ecf1f8", inkSecondary: "#aab8cc", inkMuted: "#8696ae", grid: "#243a5c", axis: "#365078", mode: "dark" },
};

function token(name: string): string | null {
  if (typeof document === "undefined") return null;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  return /^\d+\s+\d+\s+\d+$/.test(raw) ? `rgb(${raw})` : null;
}

export function chrome(mode: Mode): Chrome {
  const base = FALLBACK[mode];
  return {
    mode,
    surface: token("surface") ?? base.surface,
    ink: token("foreground") ?? base.ink,
    inkSecondary: token("foreground-muted") ?? base.inkSecondary,
    inkMuted: token("foreground-subtle") ?? base.inkMuted,
    grid: token("border") ?? base.grid,
    axis: token("border-strong") ?? base.axis,
  };
}
