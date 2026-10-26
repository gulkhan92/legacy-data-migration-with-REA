import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, Inbox } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";

export function EmptyState({ title, children, action, icon, className }: { title: string; children?: ReactNode; action?: ReactNode; icon?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center rounded-lg border border-dashed border-border-strong px-6 py-12 text-center", className)}>
      <div className="text-foreground-subtle" aria-hidden="true">
        {icon ?? <Inbox className="h-10 w-10" />}
      </div>
      <h3 className="mt-3 text-lg font-semibold">{title}</h3>
      {children ? <div className="mt-1 max-w-md text-sm text-foreground-muted">{children}</div> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : "Something went wrong";
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn("flex items-start gap-3 rounded-lg border border-danger/40 bg-danger-soft p-4", className)}>
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-danger" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-danger">This could not be loaded</p>
        <p className="mt-0.5 break-words text-sm text-foreground">{describeError(error)}</p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

/** Rows of placeholders with the height of the content they stand in for. */
export function LoadingRows({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div role="status" aria-label="Loading" className={cn("space-y-3", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

/** Shows a skeleton while a query loads for the first time, the error with a retry when it fails, and the content otherwise. */
export function QueryView<T>({ query, children, skeleton, className }: { query: UseQueryResult<T>; children: (data: T) => ReactNode; skeleton?: ReactNode; className?: string }) {
  if (query.isPending) return <>{skeleton ?? <LoadingRows className={className} />}</>;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} className={className} />;
  return <>{children(query.data)}</>;
}
