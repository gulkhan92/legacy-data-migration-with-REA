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
