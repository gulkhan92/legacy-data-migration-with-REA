import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { useEffect, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { jobsApi, pipelineApi } from "../api";
import { PageHeader } from "../ui/page";
import { canWork } from "../ui/roles";
import { describeError, ErrorState, LoadingRows } from "../ui/states";
import { JobStatusBadge } from "../ui/status";
import { useToast } from "../ui/toast";
import { DiscoveryStep } from "./DiscoveryStep";
import { DryRunStep } from "./DryRunStep";
import { ExecuteStep } from "./ExecuteStep";
import { MappingStep } from "./MappingStep";
import { ReportStep } from "./ReportStep";
import { SourceStep } from "./SourceStep";
import { TransformStep } from "./TransformStep";
import { useJobEvents } from "./hooks";
import { StepNote } from "./shared";
import { reachable, STEPS, stepForStatus, stepIndex, type StepId } from "./steps";

const TERMINAL = new Set(["COMPLETED", "FAILED", "ROLLED_BACK"]);
const RUNNING = new Set(["DISCOVERING", "DRY_RUN", "LOADING", "RECONCILING"]);

function Stepper({ current, status, onGo }: { current: StepId; status: Parameters<typeof reachable>[0]; onGo: (id: StepId) => void }) {
  const at = stepIndex(current);
  return (
    <nav aria-label="Migration steps" className="mb-8">
      <ol className="grid gap-2 sm:grid-cols-4 lg:grid-cols-7">
        {STEPS.map((step, index) => {
          const done = index < at;
          const isCurrent = index === at;
          const open = reachable(status, step.id);
          return (
            <li key={step.id}>
              <button
                type="button"
                disabled={!open}
                aria-current={isCurrent ? "step" : undefined}
                onClick={() => onGo(step.id)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm font-medium transition-colors",
                  isCurrent ? "border-primary bg-primary-soft text-primary-ink" : "border-border bg-surface text-foreground-muted",
                  open && !isCurrent && "hover:border-border-strong hover:text-foreground",
                  !open && "cursor-not-allowed opacity-60",
                )}
              >
                <span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                    done ? "bg-success text-white" : isCurrent ? "bg-primary text-primary-foreground" : "bg-surface-muted text-foreground-muted",
                  )}
                  aria-hidden="true"
                >
                  {done ? <Check className="h-3.5 w-3.5" /> : index + 1}
                </span>
                <span className="min-w-0 truncate">
                  <span className="sr-only">Step {index + 1}: </span>
                  {step.short}
                  {done ? <span className="sr-only"> (done)</span> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** The new migration wizard. A job is created at the first step; from then on the address holds its id and the step, so that a person can come back to the same place. */
export default function MigrationWizard() {
  const { jobId } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { me } = useAuth();
  const toast = useToast();
  const client = useQueryClient();
  const working = me ? canWork(me.role) : false;
  const { events, live, reconnect } = useJobEvents(jobId, !!jobId);

  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: ({ signal }) => jobsApi.get(jobId as string, signal),
    enabled: !!jobId,
    // Quick while something is running on the server or about to start, slow otherwise.
    refetchInterval: (query) => {
      const state = query.state.data?.status;
      if (!state) return 6000;
      if (RUNNING.has(state)) return 1500;
      return ["CREATED", "UPLOADED", "PROFILED", "MAPPED"].includes(state) ? 2500 : 6000;
    },
  });
  const schemas = useQuery({ queryKey: ["target-schemas"], queryFn: ({ signal }) => pipelineApi.schemas(signal), staleTime: 10 * 60_000 });

  const requested = params.get("step") as StepId | null;
  const status = job.data?.status ?? null;
  const defaulted = useRef(false);

  // The first time the job is known, open the step it has reached (the address then keeps it).
  useEffect(() => {
    if (!jobId || !job.data || defaulted.current) return;
    defaulted.current = true;
    if (!requested || !reachable(job.data.status, requested)) setParams({ step: stepForStatus(job.data.status) }, { replace: true });
  }, [jobId, job.data, requested, setParams]);
  useEffect(() => {
    defaulted.current = false;
  }, [jobId]);

  const current: StepId = !jobId
    ? "source"
    : requested && STEPS.some((s) => s.id === requested) && reachable(status, requested)
      ? requested
      : status
        ? stepForStatus(status)
        : "discovery";

  function go(id: StepId) {
    if (!jobId) return;
    setParams({ step: id });
    window.scrollTo({ top: 0 });
  }
  const next = () => go(STEPS[Math.min(STEPS.length - 1, stepIndex(current) + 1)]?.id ?? "report");
  const previous = () => go(STEPS[Math.max(0, stepIndex(current) - 1)]?.id ?? "source");

  const cancel = useMutation({
    mutationFn: () => jobsApi.cancel(jobId as string),
    onSuccess: () => {
      toast.success("The migration was cancelled");
      void client.invalidateQueries({ queryKey: ["job", jobId] });
      void client.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (error) => toast.error("It could not be cancelled", describeError(error)),
  });

  const schemaName = schemas.data?.find((s) => s.id === job.data?.target_schema_id)?.name;

  return (
    <>
      <PageHeader
        title={jobId ? (job.data?.source_name ?? "Migration") : "New migration"}
        description={
          jobId
            ? "Follow the migration from the structure of the old system to the reconciled data."
            : "Bring a legacy system in: choose a source, let the platform discover and map it, review, try it on a sample, then load it."
        }
        actions={
          <>
            {job.data ? <JobStatusBadge status={job.data.status} /> : null}
            {job.data && working && !TERMINAL.has(job.data.status) ? (
              <Button variant="outline" size="sm" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
                Cancel the migration
              </Button>
            ) : null}
          </>
        }
      />
      <Stepper current={current} status={status} onGo={go} />

      {jobId && job.isPending ? (
        <LoadingRows rows={4} />
      ) : jobId && job.isError ? (
        <ErrorState error={job.error} onRetry={() => void job.refetch()} />
      ) : (
        <div className="space-y-6">
          {job.data?.status === "FAILED" ? (
            <StepNote tone="danger">
              <strong>This migration failed.</strong> {job.data.error_message ?? "No reason was recorded."} A failed migration cannot continue; start a new one from the same
              source.
            </StepNote>
          ) : null}
          {current === "source" ? <SourceStep job={job.data ?? null} working={working} onCreated={(id) => navigate(`/app/migrations/${id}?step=discovery`)} onNext={next} /> : null}
          {job.data && current === "discovery" ? (
            <DiscoveryStep job={job.data} events={events} live={live} reconnect={reconnect} working={working} onNext={next} onBack={previous} />
          ) : null}
          {job.data && current === "mapping" ? (
            <MappingStep job={job.data} events={events} reconnect={reconnect} schemaName={schemaName} working={working} onNext={next} onBack={previous} />
          ) : null}
          {job.data && current === "transform" ? <TransformStep job={job.data} working={working} onNext={next} onBack={previous} /> : null}
          {job.data && current === "dryrun" ? <DryRunStep job={job.data} working={working} onNext={next} onBack={previous} /> : null}
          {job.data && current === "execute" ? (
            <ExecuteStep job={job.data} events={events} live={live} reconnect={reconnect} working={working} onNext={next} onBack={previous} />
          ) : null}
          {job.data && current === "report" ? <ReportStep job={job.data} working={working} onBack={previous} /> : null}
        </div>
      )}
    </>
  );
}
