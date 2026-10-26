import { useMemo } from "react";
import { useTheme } from "../shell/theme";
import { chrome, SLOTS } from "./palette";

/**
 * A line of at most twelve points with no axes, in the quiet ink color, and the latest point in the accent. It stands next to a number
 * that says the same thing in words, so it carries a short text alternative and no tooltip of its own.
 */
export function Sparkline({ values, label, width = 120, height = 32 }: { values: number[]; label: string; width?: number; height?: number }) {
  const { resolved } = useTheme();
  const theme = useMemo(() => chrome(resolved), [resolved]);
  const accent = SLOTS[resolved][0] ?? "#2a78d6";
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 4;
  const x = (i: number) => pad + (i * (width - 2 * pad)) / (values.length - 1);
  const y = (v: number) => height - pad - ((v - min) * (height - 2 * pad)) / span;
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = values[values.length - 1] as number;
  return (
    <svg role="img" aria-label={label} viewBox={`0 0 ${width} ${height}`} width={width} height={height} className="block max-w-full">
      <polyline points={points} fill="none" stroke={theme.inkMuted} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(last)} r="4" fill={accent} stroke={theme.surface} strokeWidth="2" />
    </svg>
  );
}
