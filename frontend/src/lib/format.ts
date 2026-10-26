const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usdCents = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** Whole dollars when the amount has no cents, otherwise dollars and cents. */
export function formatCents(cents: number): string {
  return cents % 100 === 0 ? usd.format(cents / 100) : usdCents.format(cents / 100);
}

export function formatInt(value: number): string {
  return integer.format(value);
}

/** 2,000,000 becomes "2M", 100,000 becomes "100K". */
export function formatCompact(value: number): string {
  return compact.format(value);
}

export function formatPercent(ratio: number, digits = 1): string {
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** A price per 1,000 records from cents, for example 20 cents becomes "$0.20". */
export function formatRate(centsPer1000: number): string {
  return usdCents.format(centsPer1000 / 100);
}

export function formatDuration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`;
  return `${(seconds / 60).toFixed(1)} min`;
}

export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" }).format(new Date(iso));
}

const usdCompact = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });

/** Cents as compact dollars for charts and tiles: 7,996,000 cents become "$80K". */
export function formatCentsCompact(cents: number): string {
  return usdCompact.format(cents / 100);
}

/** A change as a signed percentage: 0.0597 becomes "+6.0%", a fall "-2.5%". */
export function formatSignedPercent(ratio: number, digits = 1): string {
  const text = `${Math.abs(ratio * 100).toFixed(digits)}%`;
  return ratio > 0 ? `+${text}` : ratio < 0 ? `-${text}` : text;
}

/** Milliseconds as the largest sensible unit: "850 ms", "42 s", "5.5 min", "2.3 h". */
export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

const dateTime = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso));
}

/** A calendar day ("2026-10-22") as "Oct 22, 2026", without the time zone moving it. */
export function formatDay(day: string): string {
  return shortDate.format(new Date(`${day.slice(0, 10)}T00:00:00Z`));
}

/** "just now", "5 min ago", "3 h ago", "2 d ago". */
export function timeAgo(iso: string, now = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86_400)} d ago`;
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
