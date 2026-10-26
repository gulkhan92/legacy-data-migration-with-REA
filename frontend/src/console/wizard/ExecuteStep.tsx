import { Rocket } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Job, JobEvent } from "../api";
import { Modal } from "../ui/controls";
import { describeError } from "../ui/states";
import { useStageRunner } from "./hooks";
import { EventLog, StageTimeline, StepFrame, StepNote } from "./shared";

const LOAD_STATES = new Set(["LOADING", "RECONCILING"]);

export function ExecuteStep({
  job,
  events,
  live,
  reconnect,
  working,
  onNext,
  onBack,
}: {
  job: Job;
  events: JobEvent[];
  live: boolean;
  reconnect: () => void;
  working: boolean;
  onNext: () => void;
  onBack: () => void;
}) {
  const runner = useStageRunner(job.id, reconnect);
  const [confirming, setConfirming] = useState(false);
  const [queued, setQueued] = useState(false);
  const finished = job.status === "COMPLETED" || job.status === "ROLLED_BACK";
  // A load was started and the job has not yet reached an end: it counts as running even before the state says LOADING.
  const running = (queued && !finished && job.status !== "FAILED") || runner.isPending || LOAD_STATES.has(job.status);

  const start = () => {
    setConfirming(false);
    runner.mutate({ stages: ["load"] }, { onSuccess: () => setQueued(true) });
  };

  return (
    <StepFrame
      title="Execute and monitor"
      intro="The load writes the transformed rows to the target, quarantines the ones that fail with their reasons, and then reconciles the result against the source. If any check fails the load is rolled back and nothing is kept."
      footer={
        <>
          <Button variant="outline" onClick={onBack} disabled={running}>
            Back
          </Button>
          <Button disabled={!finished} onClick={onNext}>
            View the reconciliation report
          </Button>
        </>
      }
    >
      {job.status === "READY" && !running ? (
        <div className="rounded-lg border border-dashed border-border-strong p-8 text-center">
          <Rocket className="mx-auto h-8 w-8 text-foreground-subtle" aria-hidden="true" />
          <p className="mt-3 font-semibold">Ready to load</p>
          <p className="mx-auto mt-1 max-w-md text-sm text-foreground-muted">
            The dry run found nothing that blocks the load. This writes to the target tables of this organization.
          </p>
          {working ? (
            <Button className="mt-5" onClick={() => setConfirming(true)}>
              Load the data
            </Button>
          ) : (
            <p className="mt-4 text-sm text-foreground-muted">Your role can not start it.</p>
          )}
        </div>
      ) : null}

      {!["READY", "LOADING", "RECONCILING", "COMPLETED", "ROLLED_BACK", "FAILED"].includes(job.status) ? (
        <StepNote tone="warning">The migration is not ready to load yet. Finish the review and run the dry run first.</StepNote>
      ) : null}

      {runner.isError ? <StepNote tone="danger">{describeError(runner.error)}</StepNote> : null}
      {finished ? (
        <StepNote tone="success">
          {job.status === "ROLLED_BACK"
            ? "This migration was rolled back: the loaded rows were removed from the target."
            : "The load finished and was reconciled against the source."}
        </StepNote>
      ) : null}

      {running || finished || job.status === "FAILED" ? (
        <div className="grid gap-6 lg:grid-cols-2" aria-busy={running}>
          <div className="rounded-lg border border-border bg-surface p-5">
            <h3 className="mb-4 flex items-center gap-2 font-semibold">
              Stage timeline
              {running ? <Badge variant={live ? "success" : "neutral"}>{live ? "Live" : "Connecting"}</Badge> : null}
            </h3>
            <StageTimeline events={events} label="Pipeline stages" />
          </div>
          <EventLog events={events} />
        </div>
      ) : null}

      <Modal
        open={confirming}
        onOpenChange={setConfirming}
        title="Load the data?"
        description="The transformed rows are written to the target and checked against the source. A failed check rolls everything back."
        footer={
          <>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Not yet
            </Button>
            <Button onClick={start}>Load the data</Button>
          </>
        }
      />
    </StepFrame>
  );
}
