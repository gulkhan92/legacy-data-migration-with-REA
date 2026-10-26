import { Ban, Brain, CheckCircle2, CircleDashed, Clock, Database, Loader2, Undo2, User, XCircle, Zap } from "lucide-react";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import type { JobStatus } from "../api";

type Tone = "neutral" | "primary" | "teal" | "success" | "warning" | "danger";

const STATUS: Record<JobStatus, { label: string; tone: Tone; icon: typeof Clock; spin?: boolean }> = {
  CREATED: { label: "Created", tone: "neutral", icon: CircleDashed },
  UPLOADED: { label: "Uploaded", tone: "neutral", icon: CircleDashed },
  DISCOVERING: { label: "Discovering", tone: "primary", icon: Loader2, spin: true },
  PROFILED: { label: "Profiled", tone: "primary", icon: CheckCircle2 },
  MAPPED: { label: "Mapped", tone: "primary", icon: CheckCircle2 },
  AWAITING_REVIEW: { label: "Needs review", tone: "warning", icon: Clock },
  DRY_RUN: { label: "Dry run", tone: "primary", icon: Loader2, spin: true },
  READY: { label: "Ready to load", tone: "teal", icon: CheckCircle2 },
  LOADING: { label: "Loading", tone: "primary", icon: Loader2, spin: true },
  RECONCILING: { label: "Reconciling", tone: "primary", icon: Loader2, spin: true },
  COMPLETED: { label: "Completed", tone: "success", icon: CheckCircle2 },
  FAILED: { label: "Failed", tone: "danger", icon: XCircle },
  ROLLED_BACK: { label: "Rolled back", tone: "neutral", icon: Undo2 },
};

export const STATUS_OPTIONS = (Object.keys(STATUS) as JobStatus[]).map((value) => ({ value, label: STATUS[value].label }));

export function statusLabel(status: JobStatus): string {
  return STATUS[status]?.label ?? status;
}

/** A state is always a word with an icon, never only a color. */
export function JobStatusBadge({ status }: { status: JobStatus }) {
  const info = STATUS[status] ?? { label: status, tone: "neutral" as const, icon: CircleDashed };
  const Icon = info.icon;
  return (
    <Badge variant={info.tone}>
      <Icon className={cn("h-3.5 w-3.5", info.spin && "animate-spin motion-reduce:animate-none")} aria-hidden="true" />
      {info.label}
    </Badge>
  );
}

const METHODS: Record<string, { label: string; tone: Tone; icon: typeof Brain }> = {
  memory: { label: "Memory", tone: "teal", icon: Database },
  embedding: { label: "Embedding", tone: "primary", icon: Zap },
  llm: { label: "LLM", tone: "warning", icon: Brain },
  human: { label: "Reviewed", tone: "success", icon: User },
};

export function methodLabel(method: string): string {
  return METHODS[method]?.label ?? method;
}

export function MethodBadge({ method }: { method: string }) {
  const info = METHODS[method] ?? { label: method, tone: "neutral" as const, icon: Ban };
  const Icon = info.icon;
  return (
    <Badge variant={info.tone}>
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {info.label}
    </Badge>
  );
}

/** A bar and the number beside it: the length carries the magnitude, the number the exact value. */
export function ConfidenceBar({ value, label = "Confidence" }: { value: number | null; label?: string }) {
  if (value === null) return <span className="text-foreground-subtle">none</span>;
  const pct = Math.round(value * 100);
  const tone = value >= 0.85 ? "bg-success" : value >= 0.65 ? "bg-warning" : "bg-danger";
  return (
    <div className="flex items-center gap-2" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-valuetext={`${pct} percent`}>
      <div className="h-2 w-20 overflow-hidden rounded-full bg-surface-muted">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-9 text-right text-sm tabular-nums">{pct}%</span>
    </div>
  );
}

/** A usage meter: the fill carries the severity, the track is a lighter step of the same hue. */
export function Meter({ value, max, label, children }: { value: number; max: number; label: string; children?: ReactNode }) {
  const ratio = max > 0 ? Math.min(1, value / max) : 0;
  const tone = ratio >= 1 ? "bg-danger" : ratio >= 0.8 ? "bg-warning" : "bg-primary";
  const track = ratio >= 1 ? "bg-danger-soft" : ratio >= 0.8 ? "bg-warning-soft" : "bg-primary-soft";
  return (
    <div>
      <div className={cn("h-3 overflow-hidden rounded-full", track)} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.min(value, max)}>
        <div className={cn("h-full rounded-full transition-[width]", tone)} style={{ width: `${Math.round(ratio * 100)}%` }} />
      </div>
      {children}
    </div>
  );
}
