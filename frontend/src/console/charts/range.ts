import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { Granularity, RangeParams } from "../api";

export type Preset = "30d" | "90d" | "12m" | "24m" | "custom";

export const PRESETS: { value: Preset; label: string; days?: number; granularity: Granularity }[] = [
  { value: "30d", label: "30 days", days: 30, granularity: "day" },
  { value: "90d", label: "90 days", days: 90, granularity: "day" },
  { value: "12m", label: "12 months", days: 365, granularity: "week" },
  { value: "24m", label: "24 months", days: 730, granularity: "month" },
  { value: "custom", label: "Custom", granularity: "day" },
];

export type RangeState = { preset: Preset; from: string; to: string; granularity: Granularity; compare: boolean; params: RangeParams; valid: boolean };

const GRANULARITIES: Granularity[] = ["day", "week", "month"];
const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The dashboard controls as the address holds them, and the query for the API they stand for. A preset counts back from the latest day of data, which the server knows. */
export function resolveRange(search: URLSearchParams): RangeState {
  const asked = search.get("range") as Preset | null;
  const preset: Preset = PRESETS.some((p) => p.value === asked) ? (asked as Preset) : "90d";
  const base = PRESETS.find((p) => p.value === preset)!;
  const g = search.get("granularity") as Granularity | null;
  const granularity: Granularity = g && GRANULARITIES.includes(g) ? g : base.granularity;
  const compare = search.get("compare") === "1";
  const from = search.get("from") ?? "";
  const to = search.get("to") ?? "";
  const customOk = DAY.test(from) && DAY.test(to) && from <= to;
  const params: RangeParams = { granularity, ...(compare ? { compare: true } : {}) };
  if (preset === "custom") {
    if (customOk) Object.assign(params, { from, to });
  } else if (base.days) {
    params.days = base.days;
  }
  return { preset, from, to, granularity, compare, params, valid: preset !== "custom" || customOk };
}

export function useDashboardRange() {
  const [search, setSearch] = useSearchParams();
  const state = useMemo(() => resolveRange(search), [search]);
  const update = useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(search);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      setSearch(next, { replace: true });
    },
    [search, setSearch],
  );
  return { ...state, update };
}
